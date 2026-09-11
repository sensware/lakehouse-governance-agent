"""Simulate a pipeline change so the contract-drift detector has something to find.

Applies, in place, to data/lakehouse.duckdb:

  1. ADD COLUMN silver_customers.marketing_consent BOOLEAN   (additive, mostly null)
  2. ADD COLUMN silver_customers.customer_segment  VARCHAR   (additive, backfilled)
  3. Relabel ~half of REJECTED kyc_status -> 'EXPIRED'       (enum drift: breaks
     the contract's kyc_status_enum rule, which only allows VERIFIED/PENDING/REJECTED)

Run:  uv run python data/evolve_lakehouse.py     (or:  uv run lga evolve)
Then: uv run lga contract-status silver_customers
      uv run lga contract-revise silver_customers
Undo: uv run lga build-data
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DB_PATH = Path(__file__).parent / "lakehouse.duckdb"


def main() -> None:
    con = duckdb.connect(str(DB_PATH))
    cols = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='silver_customers'"
    ).fetchall()}

    if "marketing_consent" not in cols:
        con.execute("ALTER TABLE silver_customers ADD COLUMN marketing_consent BOOLEAN")
        # consent captured for only ~40% of customers so far
        con.execute(
            "UPDATE silver_customers SET marketing_consent = (customer_id % 5 < 2) "
            "WHERE customer_id % 5 <> 4"
        )

    if "customer_segment" not in cols:
        con.execute("ALTER TABLE silver_customers ADD COLUMN customer_segment VARCHAR")
        con.execute(
            """
            UPDATE silver_customers SET customer_segment = CASE
                WHEN risk_rating >= 4 THEN 'BUSINESS'
                WHEN risk_rating = 3  THEN 'PREMIER'
                ELSE 'RETAIL'
            END
            """
        )

    changed = con.execute(
        "UPDATE silver_customers SET kyc_status='EXPIRED' "
        "WHERE kyc_status='REJECTED' AND customer_id % 2 = 0"
    )
    n_expired = con.execute("SELECT count(*) FROM silver_customers WHERE kyc_status='EXPIRED'").fetchone()[0]

    print("Evolved silver_customers:")
    print("  + marketing_consent BOOLEAN (additive)")
    print("  + customer_segment VARCHAR (additive)")
    print(f"  ~ kyc_status: {n_expired} rows REJECTED -> EXPIRED (enum drift)")
    con.close()


if __name__ == "__main__":
    main()
