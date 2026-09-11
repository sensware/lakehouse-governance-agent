"""Offline tests for the contract-diff engine (Phase 5) — no API, no network."""

import copy

import pytest

from lga import contract as C


@pytest.fixture
def base():
    # Inline, so the tests don't depend on the mutable contracts/ file
    # (which `lga contract-revise` rewrites at runtime).
    return {
        "metadata": {"name": "t", "version": "2.0.0", "domain": "D", "owner": "o@x", "change_log": []},
        "description": "d",
        "semantics": {"freshness": "daily"},
        "schema": [
            {"name": "id", "type": "BIGINT", "nullable": False, "pii": False, "description": "pk"},
            {"name": "first_name", "type": "VARCHAR", "nullable": False, "pii": True, "description": "n"},
            {"name": "city", "type": "VARCHAR", "nullable": True, "pii": False, "description": "c"},
        ],
        "quality_rules": [
            {"name": "names_present", "severity": "blocking",
             "assertion": "SELECT count(*) FROM t WHERE first_name IS NULL"},
            {"name": "kyc_status_enum", "severity": "blocking",
             "assertion": "SELECT count(*) FROM t WHERE kyc_status IS NOT NULL AND kyc_status NOT IN ('VERIFIED','PENDING','REJECTED')"},
        ],
    }


def test_bump():
    assert C._bump("2.0.0", "major") == "3.0.0"
    assert C._bump("2.3.1", "minor") == "2.4.0"
    assert C._bump("2.3.1", "patch") == "2.3.2"


def test_enum_rule_parse(base):
    rule = next(r for r in base["quality_rules"] if r["name"] == "kyc_status_enum")
    col, allowed = C._parse_enum_rule(rule["assertion"])
    assert col == "kyc_status"
    assert set(allowed) == {"VERIFIED", "PENDING", "REJECTED"}


def test_diff_additive_column(base):
    new = copy.deepcopy(base)
    new["schema"].append({"name": "note", "type": "VARCHAR", "nullable": True, "pii": False, "description": "x"})
    (chg,) = [c for c in C.diff(base, new) if c.path == "schema.note"]
    assert chg.kind == "added" and chg.classification == "additive"


def test_diff_breaking_notnull_and_removed_rule(base):
    new = copy.deepcopy(base)
    new["schema"].append({"name": "seg", "type": "VARCHAR", "nullable": False, "pii": False, "description": "x"})
    new["quality_rules"] = [r for r in new["quality_rules"] if r["name"] != "names_present"]
    cls = {c.path: c.classification for c in C.diff(base, new)}
    assert cls["schema.seg"] == "breaking"
    assert cls["quality_rules.names_present"] == "breaking"


def test_diff_pii_flip_is_breaking(base):
    new = copy.deepcopy(base)
    for c in new["schema"]:
        if c["name"] == "city":
            c["pii"] = True
    (chg,) = [c for c in C.diff(base, new) if c.path == "schema.city"]
    assert chg.classification == "breaking"


def test_summarize(base):
    new = copy.deepcopy(base)
    new["schema"].append({"name": "a", "type": "VARCHAR", "nullable": True, "pii": False, "description": ""})
    new["metadata"]["owner"] = "someone-else@bank.internal"
    s = C.summarize(C.diff(base, new))
    assert s["additive"] == 1 and s["metadata"] == 1 and s["breaking"] == 0
