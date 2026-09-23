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

# Claude model used for the agent + RAG synthesis. Opus 5.5 is the default: same 1M
# context and feature set as Opus 5, at a lower price. Thinking is always on and can't
# be disabled, so effort (below) is the only depth control. Override with
# ANTHROPIC_MODEL in .env — claude-sonnet-5 for cheaper iteration, claude-opus-5 to
# pin the previous default.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
# Opus 5.5 defaults effort to "medium" (Opus 5 defaulted to "high"). Governance audits
# and contract reviews are correctness-sensitive, so pin "high" explicitly rather than
# inherit the drop. Set ANTHROPIC_EFFORT= (empty) to omit the parameter — needed for
# models without effort support, e.g. Haiku 4.5.
ANTHROPIC_EFFORT = os.getenv("ANTHROPIC_EFFORT", "high")
# Local embedding model (runs via onnxruntime through fastembed — no API key needed).
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")


def effort_params() -> dict:
    """Extra kwargs for `messages.create`: pins effort unless ANTHROPIC_EFFORT is empty."""
    return {"output_config": {"effort": ANTHROPIC_EFFORT}} if ANTHROPIC_EFFORT else {}


def require_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return key
