"""`lga` command-line entrypoint. Thin wrapper; real logic lives in the modules."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lga", description="Lakehouse Governance Agent")
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

    args = parser.parse_args(argv)

    if args.cmd == "build-data":
        from data.build_lakehouse import main as build  # type: ignore

        build()
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


if __name__ == "__main__":
    main()
