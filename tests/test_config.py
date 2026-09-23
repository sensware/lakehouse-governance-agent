"""Offline tests for model/effort configuration — no API, no network, no .env."""

import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def reload_config(monkeypatch):
    """Reload lga.config under a controlled environment.

    load_dotenv is stubbed out so a developer's local .env (which may pin
    ANTHROPIC_MODEL, and does on some machines) can't leak into the assertions.
    """
    import lga.config as config

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    for var in ("ANTHROPIC_MODEL", "ANTHROPIC_EFFORT"):
        monkeypatch.delenv(var, raising=False)

    yield lambda: importlib.reload(config)

    monkeypatch.undo()
    importlib.reload(config)  # restore real env/.env values for later tests


def test_default_model_is_opus_5_5(reload_config):
    assert reload_config().ANTHROPIC_MODEL == "claude-opus-5-5"


def test_effort_is_pinned_high_by_default(reload_config):
    # Opus 5.5 defaults to "medium"; the audit/review work needs the old "high".
    cfg = reload_config()
    assert cfg.ANTHROPIC_EFFORT == "high"
    assert cfg.effort_params() == {"output_config": {"effort": "high"}}


def test_model_and_effort_overridable_from_env(reload_config, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_EFFORT", "medium")
    cfg = reload_config()
    assert cfg.ANTHROPIC_MODEL == "claude-sonnet-5"
    assert cfg.effort_params() == {"output_config": {"effort": "medium"}}


def test_empty_effort_omits_the_parameter(reload_config, monkeypatch):
    # Needed for models with no effort support (e.g. Haiku 4.5), which reject it.
    monkeypatch.setenv("ANTHROPIC_EFFORT", "")
    assert reload_config().effort_params() == {}


def test_agent_sends_configured_model_and_effort(monkeypatch):
    """run_agent must pass the configured model + effort on every request."""
    import lga.agent as agent

    seen = []

    class FakeMessages:
        def create(self, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="done")])

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr("anthropic.Anthropic", lambda *a, **k: FakeClient())

    assert agent.run_agent("noop", verbose=False) == "done"

    (kwargs,) = seen
    assert kwargs["model"] == agent.ANTHROPIC_MODEL
    assert kwargs["output_config"] == {"effort": agent.effort_params()["output_config"]["effort"]}
    # Parameters that return a 400 on Opus 5.5 must never be sent.
    for banned in ("thinking", "tool_choice", "temperature", "top_p", "top_k"):
        assert banned not in kwargs
