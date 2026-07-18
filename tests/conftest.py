"""Shared pytest fixtures for Stem.

STEM_HOME isolation (D002): every test gets a fresh temp Stem home so RPC
mailboxes, config.json, and generated audio never touch ~/.stem.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_stem_home(tmp_path, monkeypatch):
    home = tmp_path / "stem_home"
    home.mkdir()
    monkeypatch.setenv("STEM_HOME", str(home))
    return home


@pytest.fixture
def mock_ctx():
    from stem.agent.loop import ToolContext
    from stem.bridge.mock import MockBridge
    return ToolContext(bridge=MockBridge())
