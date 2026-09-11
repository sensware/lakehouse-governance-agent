"""Build a tiny BFSI (retail bank) lakehouse in DuckDB, organised as a medallion.

    bronze_*  raw landing data, warts and all (nulls, dupes, bad enums, out-of-range)
    silver_*  cleaned / conformed / deduplicated / typed
    gold_*    business-level aggregates for analytics

The bronze layer deliberately contains data-quality defects so the governance
agent has something real to detect. Everything is seeded, so runs are reproducible.

Run:  uv run python data/build_lakehouse.py
Output:  data/lakehouse.duckdb
"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path

import duckdb

SEED = 42
N_CUSTOMERS = 300
N_TXNS = 4000
DB_PATH = Path(__file__).parent / "lakehouse.duckdb"

random.seed(SEED)

FIRST_NAMES = "Aisha Ben Chloe Diego Emma Farid Grace Hiro Ines Jamal Kira Liam Mara Noah Omar Priya Quinn Rosa Sam Tara".split()
LAST_NAMES = "Ahmed Brown Chen Diaz Evans Fischer Gupta Haddad Ivanov Jones Kim Lopez Meyer Nguyen Ortiz Patel Rossi Singh Tanaka Ueda".split()
CITIES = ["London", "Manchester", "Leeds", "Bristol", "Birmingham", "Glasgow"]
# Bronze enum chaos: the same concept spelled many ways.
ACCT_TYPES_RAW = ["CURRENT", "current", "Current", "SAVINGS", "savings", "Savings", "SAV", "CUR", None]
CHANNELS_RAW = ["ATM", "atm", "POS", "pos", "ONLINE", "Online", "BRANCH", "branch", "", None]
KYC_STATUS_RAW = ["VERIFIED", "verified", "PENDING", "pending", "REJECTED", "", None]


def _rand_date(start: dt.date, end: dt.date) -> dt.date:
    delta = (end - start).days
    return start + dt.timedelta(days=random.randint(0, delta))


def build_bronze_customers() -> list[tuple]:
    rows = []
    for cid in range(1, N_CUSTOMERS + 1):
        fn = random.choice(FIRST_NAMES)
        ln = random.choice(LAST_NAMES)
        # ~8% missing email, some malformed
        if random.random() < 0.08:
            email = None
        elif random.random() < 0.05:
            email = f"{fn.lower()}.{ln.lower()}(at)example.com"  # malformed
        else:
            email = f"{fn.lower()}.{ln.lower()}@example.com"
        # ~5% missing DOB, a few implausible (age < 10 or > 120)
        if random.random() < 0.05:
            dob = None
        elif random.random() < 0.03:
            dob = _rand_date(dt.date(2020, 1, 1), dt.date(2024, 1, 1))  # too young
        else:
            dob = _rand_date(dt.date(1945, 1, 1), dt.date(2005, 1, 1))
        city = random.choice(CITIES + [None, "  London ", "LONDON"])  # whitespace / case noise
        kyc = random.choice(KYC_STATUS_RAW)
        risk = random.choice([1, 2, 3, 4, 5, 99, None])  # 99 is an invalid sentinel
        created_at = _rand_date(dt.date(2019, 1, 1), dt.date(2024, 12, 31))
        rows.append((cid, fn, ln, email, dob, city, kyc, risk, created_at))
    # inject ~3% exact duplicate rows (same natural key, ingested twice)
    for _ in range(int(N_CUSTOMERS * 0.03)):
        rows.append(random.choice(rows))
    random.shuffle(rows)
    return rows


def build_bronze_accounts() -> list[tuple]:
    rows = []
    aid = 0
    for cid in range(1, N_CUSTOMERS + 1):
        for _ in range(random.randint(1, 3)):
            aid += 1
            acct_type = random.choice(ACCT_TYPES_RAW)
            # balance: mostly sane, some negative on savings (shouldn't happen), some huge outliers
            bal = round(random.gauss(3200, 4000), 2)
            if random.random() < 0.02:
                bal = round(random.uniform(500_000, 5_000_000), 2)  # outlier
            opened = _rand_date(dt.date(2019, 1, 1), dt.date(2024, 12, 31))
            status = random.choice(["OPEN", "OPEN", "OPEN", "CLOSED", "DORMANT", None])
            rows.append((aid, cid, acct_type, bal, "GBP", opened, status))
    # a handful of orphan accounts (customer_id that doesn't exist) -> referential integrity break
    for _ in range(5):
        aid += 1
        rows.append((aid, 999_000 + aid, "CURRENT", 100.0, "GBP", dt.date(2023, 1, 1), "OPEN"))
    return rows


def build_bronze_transactions(account_ids: list[int]) -> list[tuple]:
    rows = []
    for txid in range(1, N_TXNS + 1):
        aid = random.choice(account_ids)
        amount = round(random.gauss(0, 180), 2)
        if random.random() < 0.01:
            amount = round(random.uniform(20_000, 90_000), 2)  # large-value, AML-relevant
        ts = dt.datetime(2024, 1, 1) + dt.timedelta(minutes=random.randint(0, 525_600))
        channel = random.choice(CHANNELS_RAW)
        mcc = random.choice([5411, 5812, 6011, 4829, 5999, None])  # merchant category code
        # ~2% future-dated timestamps (clock skew on ingest)
        if random.random() < 0.02:
            ts = ts + dt.timedelta(days=400)
        cur = random.choice(["GBP", "GBP", "GBP", "EUR", "USD"])
        rows.append((txid, aid, amount, cur, ts, channel, mcc))
    # duplicate transaction ids (double-posted events)
    for _ in range(int(N_TXNS * 0.005)):
        rows.append(random.choice(rows))
    return rows


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))
    # DuckDB has no initcap(); define one for the silver-layer name normalisation.
    con.execute(
        "CREATE MACRO initcap(s) AS "
        "upper(substr(trim(s), 1, 1)) || lower(substr(trim(s), 2))"
    )

    # ---------------- BRONZE ----------------
    con.execute(
        """
        CREATE TABLE bronze_customers (
            customer_id BIGINT, first_name VARCHAR, last_name VARCHAR, email VARCHAR,
            date_of_birth DATE, city VARCHAR, kyc_status VARCHAR, risk_rating INTEGER,
            created_at DATE
        )"""
    )
    con.executemany(
        "INSERT INTO bronze_customers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        build_bronze_customers(),
    )

    con.execute(
        """
        CREATE TABLE bronze_accounts (
            account_id BIGINT, customer_id BIGINT, account_type VARCHAR, balance DOUBLE,
            currency VARCHAR, opened_date DATE, status VARCHAR
        )"""
    )
    accounts = build_bronze_accounts()
    con.executemany("INSERT INTO bronze_accounts VALUES (?, ?, ?, ?, ?, ?, ?)", accounts)
    account_ids = [a[0] for a in accounts]

    con.execute(
        """
        CREATE TABLE bronze_transactions (
            txn_id BIGINT, account_id BIGINT, amount DOUBLE, currency VARCHAR,
            txn_ts TIMESTAMP, channel VARCHAR, merchant_category_code INTEGER
        )"""
    )
    con.executemany(
        "INSERT INTO bronze_transactions VALUES (?, ?, ?, ?, ?, ?, ?)",
        build_bronze_transactions(account_ids),
    )

    # ---------------- SILVER ----------------
    # Conform enums, trim/case-normalise, drop exact dupes, enforce types, filter impossible values.
    con.execute(
        """
        CREATE TABLE silver_customers AS
        WITH deduped AS (
            SELECT DISTINCT * FROM bronze_customers
        )
        SELECT
            customer_id,
            initcap(trim(first_name))                              AS first_name,
            initcap(trim(last_name))                               AS last_name,
            CASE WHEN email LIKE '%@%.%' THEN lower(email) END     AS email,
            date_of_birth,
            initcap(trim(city))                                    AS city,
            upper(nullif(trim(kyc_status), ''))                    AS kyc_status,
            nullif(risk_rating, 99)                                AS risk_rating,
            created_at
        FROM deduped
        -- Age rule is relative to onboarding (created_at), not today: a customer who was
        -- 16 at onboarding is a KYC exception regardless of how old they are now.
        -- (First version used current_date; the reviewer agent caught it — see docs/05.)
        WHERE date_of_birth IS NULL
           OR date_of_birth BETWEEN DATE '1910-01-01' AND created_at - INTERVAL 18 YEAR
        """
    )

    # The Null Member / Unknown Member (Kimball dimensional modeling): a designated,
    # always-present sentinel row in the dimension, surrogate key 0, so a fact row that
    # can't be resolved to a real customer has somewhere *safe* to point — instead of a
    # database NULL (which silently breaks joins, GROUP BY, and equality filters) or
    # being dropped outright. This is deliberately NOT sourced from bronze_customers —
    # it's synthetic, present in every load, and excluded from the bronze<->silver
    # row-conservation check (see contracts/silver_customers.yml's row_conservation
    # rule and unknown_member_present, its companion). docs/10 has the full writeup,
    # including why this project still quarantines (docs/05/06) rather than repointing
    # existing fact rows here — the two techniques solve different problems and this
    # repo demonstrates both rather than picking one.
    con.execute(
        """
        INSERT INTO silver_customers VALUES
        (0, 'Unknown', 'Member', NULL, NULL, NULL, NULL, NULL, DATE '1900-01-01')
        """
    )

    # Quarantine: every bronze_customers ROW that did NOT reach silver, with a reason code.
    # The rejection rules now live in *data*, not just in this script — so a data contract /
    # catalog can declare them and consumers can reconcile row-for-row:
    #     count(bronze_customers) = count(silver_customers) + count(silver_customers_rejected)
    #   DUPLICATE_ROW         2nd+ copy of a byte-identical bronze row (collapsed by dedup)
    #   DOB_IMPLAUSIBLE       date_of_birth before 1910
    #   DOB_AFTER_ONBOARDING  born after the account was created (impossible)
    #   MINOR_AT_ONBOARDING   under 18 at created_at (KYC exception)
    con.execute(
        """
        CREATE TABLE silver_customers_rejected AS
        WITH ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY customer_id, first_name, last_name, email, date_of_birth,
                             city, kyc_status, risk_rating, created_at
                ORDER BY customer_id
            ) AS _rn
            FROM bronze_customers
        ),
        duplicate_rows AS (
            SELECT * EXCLUDE (_rn), 'DUPLICATE_ROW' AS reason_code FROM ranked WHERE _rn > 1
        ),
        survivors AS (SELECT * EXCLUDE (_rn) FROM ranked WHERE _rn = 1),
        rule_rejects AS (
            SELECT s.*,
                CASE
                    WHEN s.date_of_birth < DATE '1910-01-01'              THEN 'DOB_IMPLAUSIBLE'
                    WHEN s.date_of_birth > s.created_at                   THEN 'DOB_AFTER_ONBOARDING'
                    WHEN s.date_of_birth > s.created_at - INTERVAL 18 YEAR THEN 'MINOR_AT_ONBOARDING'
                    ELSE 'UNKNOWN'
                END AS reason_code
            FROM survivors s
            ANTI JOIN silver_customers sc ON s.customer_id = sc.customer_id
        )
        SELECT *, 'silver_customers' AS rejected_by, current_timestamp::TIMESTAMP AS rejected_at
        FROM duplicate_rows
        UNION ALL BY NAME
        SELECT *, 'silver_customers' AS rejected_by, current_timestamp::TIMESTAMP AS rejected_at
        FROM rule_rejects
        """
    )

    # Null Member repoint (docs/10's "next exercise", now done): every bronze account
    # loads into silver_accounts — a LEFT JOIN + COALESCE, not the SEMI JOIN this used
    # to be. An account whose customer_id doesn't resolve in silver_customers (an
    # orphan, or a customer the age rule rejected) is repointed to customer_id = 0, the
    # Null Member, instead of being dropped. This is Kimball's textbook combination,
    # not a replacement for quarantine: the SAME rows are still captured below in
    # silver_accounts_rejected with a reason code, for audit — they now simply also
    # appear here, resolved, so their balances aren't silently missing from
    # gold_customer_360's totals. Deliberately duplicated across the two tables: one is
    # the fact (always loads, always resolves), the other is the exception log.
    con.execute(
        """
        CREATE TABLE silver_accounts AS
        SELECT
            a.account_id,
            coalesce(c.customer_id, 0)                              AS customer_id,
            CASE
                WHEN upper(trim(a.account_type)) IN ('CURRENT', 'CUR') THEN 'CURRENT'
                WHEN upper(trim(a.account_type)) IN ('SAVINGS', 'SAV') THEN 'SAVINGS'
                ELSE NULL
            END                                                    AS account_type,
            round(a.balance, 2)                                     AS balance,
            a.currency,
            a.opened_date,
            upper(nullif(trim(a.status), ''))                       AS status
        FROM bronze_accounts a
        LEFT JOIN silver_customers c ON a.customer_id = c.customer_id
        """
    )

    # Quarantine: every bronze account whose customer_id did NOT resolve to a real
    # silver_customers row — i.e. every account now repointed to the Null Member above.
    # This table's job changed with the repoint: it used to be the only record that
    # these accounts existed at all; now silver_accounts carries them too (against
    # customer_id = 0), so this is the audit/reason-code log explaining *why* a given
    # account was repointed, not the sole place it survives.
    #   ORPHAN_CUSTOMER            customer_id exists nowhere in bronze_customers
    #   CUSTOMER_REJECTED_UPSTREAM customer exists in bronze but was rejected by silver_customers
    con.execute(
        """
        CREATE TABLE silver_accounts_rejected AS
        SELECT
            a.*,
            CASE
                WHEN NOT EXISTS (SELECT 1 FROM bronze_customers b WHERE b.customer_id = a.customer_id)
                    THEN 'ORPHAN_CUSTOMER'
                ELSE 'CUSTOMER_REJECTED_UPSTREAM'
            END                                                     AS reason_code,
            'silver_accounts'                                       AS rejected_by,
            current_timestamp::TIMESTAMP                            AS rejected_at  -- naive TS; TIMESTAMPTZ needs pytz to fetch
        FROM bronze_accounts a
        ANTI JOIN silver_customers c ON a.customer_id = c.customer_id
        """
    )

    con.execute(
        """
        CREATE TABLE silver_transactions AS
        WITH deduped AS (SELECT DISTINCT * FROM bronze_transactions)
        SELECT
            t.txn_id,
            t.account_id,
            round(t.amount, 2)                                      AS amount,
            upper(t.currency)                                       AS currency,
            t.txn_ts,
            upper(nullif(trim(t.channel), ''))                      AS channel,
            t.merchant_category_code
        FROM deduped t
        SEMI JOIN silver_accounts a ON t.account_id = a.account_id
        WHERE t.txn_ts <= current_timestamp                          -- drop future-dated
        """
    )

    # ---------------- GOLD ----------------
    # Aggregate accounts and transactions in their OWN grain first, then join. Doing it
    # in one pass (customers LEFT JOIN accounts LEFT JOIN transactions, GROUP BY ALL) is
    # the classic fan-out bug: a customer with 2 accounts and 10 transactions produces up
    # to 20 joined rows, and sum(a.balance) adds that account's balance in once per
    # matching transaction row, not once per account. It went unnoticed here because
    # every real customer has few enough accounts/transactions that the inflation was
    # never obviously wrong-looking — it took the Null Member repoint (below; docs/10)
    # concentrating 40 accounts and 259 transactions onto one row to make the error
    # large enough to be unmissable: total_balance came out ~9x the true sum. Fixed by
    # pre-aggregating each fact independently before combining, which is what "policy/
    # correctness before computation" (docs/07) means one layer down from access control.
    con.execute(
        """
        CREATE TABLE gold_customer_360 AS
        WITH acct_agg AS (
            SELECT customer_id,
                   count(DISTINCT account_id) AS n_accounts,
                   sum(balance)               AS total_balance
            FROM silver_accounts
            GROUP BY customer_id
        ),
        txn_agg AS (
            SELECT a.customer_id,
                   count(t.txn_id)                                          AS n_txns_2024,
                   sum(CASE WHEN t.amount >= 10000 THEN 1 ELSE 0 END)       AS n_large_txns
            FROM silver_accounts a
            JOIN silver_transactions t ON t.account_id = a.account_id
            GROUP BY a.customer_id
        )
        SELECT
            c.customer_id,
            c.first_name, c.last_name, c.city, c.kyc_status, c.risk_rating,
            coalesce(aa.n_accounts, 0)      AS n_accounts,
            coalesce(aa.total_balance, 0)   AS total_balance,
            coalesce(ta.n_txns_2024, 0)     AS n_txns_2024,
            coalesce(ta.n_large_txns, 0)    AS n_large_txns
        FROM silver_customers c
        LEFT JOIN acct_agg aa ON aa.customer_id = c.customer_id
        LEFT JOIN txn_agg ta  ON ta.customer_id = c.customer_id
        """
    )

    con.execute(
        """
        CREATE TABLE gold_monthly_channel_volume AS
        SELECT
            date_trunc('month', txn_ts)                             AS month,
            channel,
            count(*)                                                AS n_txns,
            round(sum(abs(amount)), 2)                              AS gross_value
        FROM silver_transactions
        GROUP BY ALL
        ORDER BY month, channel
        """
    )

    # ---------------- report ----------------
    tables = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main' ORDER BY table_name"
    ).fetchall()
    print(f"Built {DB_PATH}")
    for (tname,) in tables:
        n = con.execute(f"SELECT count(*) FROM {tname}").fetchone()[0]
        print(f"  {tname:32s} {n:>6d} rows")
    con.close()


if __name__ == "__main__":
    main()
