"""Central configuration and paths. Loads .env if present."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"
DB_PATH = DATA_DIR / "lakehouse.duckdb"
INDEX_DIR = ARTIFACTS_DIR / "catalog_index"

ARTIFACTS_DIR.mkdir(exist_ok=True)

# Claude model used for the agent + RAG synthesis. Opus 5 runs adaptive thinking by
# default; set ANTHROPIC_MODEL=claude-sonnet-5 in .env for cheaper iteration.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
# Local embedding model (runs via onnxruntime through fastembed — no API key needed).
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")


def require_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return key
