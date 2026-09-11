"""`lga` command-line entrypoint. Thin wrapper; real logic lives in the modules."""

from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lga", description="Lakehouse Governance Agent")
    parser.add_argument(
        "--role",
        choices=None,  # validated by policy.current_role() so the choice list stays in one place
        help="ABAC role to run as (policy.py; default: $LGA_ROLE or 'analyst'). "
        "Sets LGA_ROLE for this invocation — an operator flag, never something the model sees.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build-data", help="(re)build the sample medallion lakehouse in DuckDB")
    sub.add_parser("catalog", help="print the metadata catalog + column profiles")
    sub.add_parser("index", help="embed catalog cards into the FAISS vector store")

    p_ask = sub.add_parser("ask", help="RAG Q&A over the catalog (Phase 2)")
    p_ask.add_argument("question")

    p_agent = sub.add_parser("agent", help="run the ReAct governance agent (Phase 3)")
    p_agent.add_argument("task")

    p_review = sub.add_parser("review", help="draft a data contract + peer review it (Phase 4)")
    p_review.add_argument("table")

    sub.add_parser("evolve", help="simulate a pipeline schema change (Phase 5 demo)")

    p_status = sub.add_parser(
        "contract-status", help="report drift between the live table and its approved contract (Phase 5)"
    )
    p_status.add_argument("table")

    p_revise = sub.add_parser(
        "contract-revise", help="drift-triggered A2A contract revision + promotion (Phase 5)"
    )
    p_revise.add_argument("table")

    sub.add_parser(
        "list-tables", help="list tables visible to --role, no LLM (Phase 6: demo ABAC for free)"
    )
    p_sql = sub.add_parser(
        "run-sql", help="run one read-only statement through the ABAC-enforced tool, no LLM (Phase 6)"
    )
    p_sql.add_argument("sql")

    args = parser.parse_args(argv)
    if args.role:
        os.environ["LGA_ROLE"] = args.role

    if args.cmd == "build-data":
        import runpy

        from .config import DATA_DIR

        # data/ is a plain script dir, not a package — run it by path.
        runpy.run_path(str(DATA_DIR / "build_lakehouse.py"), run_name="__main__")
    elif args.cmd == "catalog":
        from .catalog import build_catalog

        for t in build_catalog():
            print(t.to_card(), end="\n\n")
    elif args.cmd == "index":
        from .rag import build_index

        build_index()
    elif args.cmd == "ask":
        from .rag import answer

        print(answer(args.question))
    elif args.cmd == "agent":
        from .agent import run_agent

        run_agent(args.task)
    elif args.cmd == "review":
        from .a2a import contract_review

        contract_review(args.table)
    elif args.cmd == "evolve":
        import runpy

        from .config import DATA_DIR

        runpy.run_path(str(DATA_DIR / "evolve_lakehouse.py"), run_name="__main__")
    elif args.cmd == "contract-status":
        from rich.console import Console

        from . import contract as C

        con = Console()
        base = C.load(args.table)
        drifts = C.detect_drift(args.table, base)
        con.print(f"[bold]{args.table}[/] vs contract v{base['metadata']['version']}\n")
        con.print(C.render_drift(drifts))
        raise SystemExit(1 if drifts else 0)
    elif args.cmd == "contract-revise":
        from .a2a import contract_revision

        contract_revision(args.table)
    elif args.cmd == "list-tables":
        from . import tools as T

        for t in T.list_tables():
            print(f"{t['table']:32s} {t['layer']:8s} {t['domain']}")
    elif args.cmd == "run-sql":
        from rich.console import Console

        from . import tools as T

        con = Console()
        try:
            result = T.run_sql(args.sql)
        except T.ToolError as e:
            con.print(f"[red]ToolError:[/] {e}")
            raise SystemExit(1) from None
        con.print(result["columns"])
        for row in result["rows"]:
            con.print(row)


if __name__ == "__main__":
    main()
