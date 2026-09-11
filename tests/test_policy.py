"""Offline tests for ABAC (Phase 6) — no API, no DB connection needed."""

import pytest

from lga import policy as P


def test_default_role_is_unrestricted_and_masked():
    role = P.ROLES["analyst"]
    assert role.can_access("bronze_customers")
    assert role.unmask_pii is False


def test_dq_auditor_can_unmask():
    role = P.ROLES["dq_auditor"]
    assert role.can_access("bronze_customers")
    assert role.unmask_pii is True


def test_branch_role_restricted_table_set():
    role = P.ROLES["branch_ops_london"]
    assert role.can_access("silver_customers")
    assert not role.can_access("bronze_customers")
    assert role.can_access("silver_accounts_rejected")
    assert role.can_access("silver_customers_rejected")


def test_branch_role_row_filter_on_rejected_customers_uses_own_city_column():
    # Unlike silver_accounts_rejected, this table carries bronze's own (untrimmed,
    # mixed-case) `city` directly — no join needed, and no silver_customers reference
    # to accidentally make it a no-op.
    role = P.ROLES["branch_ops_london"]
    filt = role.row_filters["silver_customers_rejected"]
    assert "silver_customers" not in filt
    assert filt == "upper(trim(city)) = 'LONDON'"
    rewritten = P.apply_row_filters(
        "SELECT * FROM silver_customers_rejected", role, {"silver_customers_rejected"}
    )
    assert "upper(trim(city)) = 'LONDON'" in rewritten


def test_branch_role_row_filter_on_rejected_accounts_uses_bronze_not_silver():
    # silver_accounts_rejected is an ANTI JOIN against silver_customers, so a filter
    # written against silver_customers would silently match nothing, for any city.
    role = P.ROLES["branch_ops_london"]
    filt = role.row_filters["silver_accounts_rejected"]
    assert "bronze_customers" in filt
    assert filt != role.row_filters["silver_accounts"]
    sql = "SELECT reason_code, count(*) FROM silver_accounts_rejected GROUP BY 1"
    rewritten = P.apply_row_filters(sql, role, {"silver_accounts_rejected"})
    assert "(SELECT * FROM silver_accounts_rejected WHERE customer_id IN" in rewritten
    assert "bronze_customers" in rewritten
    assert "AS silver_accounts_rejected" in rewritten


def test_current_role_reads_env(monkeypatch):
    monkeypatch.setenv("LGA_ROLE", "dq_auditor")
    assert P.current_role().name == "dq_auditor"


def test_current_role_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("LGA_ROLE", raising=False)
    assert P.current_role().name == "analyst"


def test_current_role_rejects_unknown(monkeypatch):
    monkeypatch.setenv("LGA_ROLE", "nope")
    with pytest.raises(P.PolicyError):
        P.current_role()


def test_match_tables_word_boundary():
    known = ["silver_customers", "silver_customers_rejected", "gold_customer_360"]
    found = P.match_tables("SELECT * FROM silver_customers WHERE city = 'London'", known)
    assert found == {"silver_customers"}


def test_check_allowed_raises_for_denied_table():
    role = P.ROLES["branch_ops_london"]
    with pytest.raises(P.PolicyError, match="bronze_customers"):
        P.check_allowed(role, {"silver_customers", "bronze_customers"})


def test_check_allowed_passes_for_permitted():
    role = P.ROLES["branch_ops_london"]
    P.check_allowed(role, {"silver_customers", "gold_customer_360"})  # no raise


@pytest.mark.parametrize(
    "sql,expected_alias",
    [
        ("SELECT * FROM silver_customers", "silver_customers"),
        ("SELECT * FROM silver_customers c", "c"),
        ("SELECT * FROM silver_customers AS c", "c"),
        ("SELECT * FROM silver_customers WHERE risk_rating > 3", "silver_customers"),
        ("SELECT a.* FROM silver_accounts a JOIN silver_customers c ON a.customer_id=c.customer_id", "c"),
    ],
)
def test_substitute_table_rewrites_and_preserves_alias(sql, expected_alias):
    rewritten = P._substitute_table(sql, "silver_customers", "city = 'London'")
    assert f"AS {expected_alias}" in rewritten
    assert "WHERE city = 'London'" in rewritten
    # the row filter subquery must appear BEFORE any outer WHERE the original had
    assert rewritten.index("city = 'London'") < len(rewritten)


def test_substitute_table_leaves_other_tables_untouched():
    sql = "SELECT * FROM silver_accounts a JOIN silver_customers c ON a.customer_id = c.customer_id"
    rewritten = P._substitute_table(sql, "silver_customers", "city = 'London'")
    assert "FROM silver_accounts a" in rewritten  # untouched
    assert "(SELECT * FROM silver_customers WHERE city = 'London') AS c" in rewritten


def test_apply_row_filters_noop_without_filter():
    role = P.ROLES["analyst"]  # no row_filters at all
    sql = "SELECT * FROM silver_customers"
    assert P.apply_row_filters(sql, role, {"silver_customers"}) == sql


def test_apply_row_filters_applies_configured_filter():
    role = P.ROLES["branch_ops_london"]
    sql = "SELECT count(*) FROM silver_customers"
    out = P.apply_row_filters(sql, role, {"silver_customers"})
    assert "WHERE city = 'London'" in out
    assert out != sql


def test_scoped_relation():
    role = P.ROLES["branch_ops_london"]
    assert P.scoped_relation("silver_customers", role) == (
        "(SELECT * FROM silver_customers WHERE city = 'London') AS silver_customers"
    )
    # silver_customers_rejected now has its own row filter too (added alongside the
    # Unknown Member); a table with genuinely no filter still passes through as-is.
    assert P.scoped_relation("silver_customers_rejected", role) == (
        "(SELECT * FROM silver_customers_rejected WHERE upper(trim(city)) = 'LONDON') "
        "AS silver_customers_rejected"
    )
    # scoped_relation only consults row_filters, regardless of allowed_tables — a
    # table with no configured filter passes through untouched.
    assert P.scoped_relation("some_table_without_a_filter", role) == "some_table_without_a_filter"
