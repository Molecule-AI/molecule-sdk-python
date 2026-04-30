"""Tests for `python -m molecule_agent connect` CLI handler resolution.

Run-loop integration is covered by tests/test_inbound.py — these tests only
exercise the CLI's argument parsing, handler resolution, and the
register-on-missing-token behavior. We do not start the full loop because
that's already covered, and starting it from a CLI test runs into signal
+ event-loop interactions that aren't worth reproducing here.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from molecule_agent.__main__ import _resolve_handler


# ---------------------------------------------------------------------------
# _resolve_handler
# ---------------------------------------------------------------------------


def _write_handler_module(tmp_path: Path, name: str, body: str) -> None:
    """Drop a handler module into tmp_path and prepend tmp_path to sys.path."""
    p = tmp_path / f"{name}.py"
    p.write_text(textwrap.dedent(body))
    if str(tmp_path) not in sys.path:
        sys.path.insert(0, str(tmp_path))


def test_resolve_handler_happy_path(tmp_path: Path):
    _write_handler_module(
        tmp_path,
        "ok_handler_mod",
        """
        def echo(msg, client):
            return msg.text
        """,
    )
    fn = _resolve_handler("ok_handler_mod:echo")
    assert callable(fn)
    # Sanity-check the resolved callable's name.
    assert fn.__name__ == "echo"


def test_resolve_handler_missing_colon_exits(tmp_path: Path):
    with pytest.raises(SystemExit, match="must be of the form"):
        _resolve_handler("not_a_spec_no_colon")


def test_resolve_handler_empty_module_exits():
    with pytest.raises(SystemExit, match="malformed"):
        _resolve_handler(":fn")


def test_resolve_handler_empty_function_exits():
    with pytest.raises(SystemExit, match="malformed"):
        _resolve_handler("mod:")


def test_resolve_handler_import_error_exits():
    with pytest.raises(SystemExit, match="could not import"):
        _resolve_handler("definitely_not_a_real_module_xyzzy:fn")


def test_resolve_handler_attribute_error_exits(tmp_path: Path):
    _write_handler_module(
        tmp_path,
        "no_func_mod",
        """
        OTHER = 1
        """,
    )
    with pytest.raises(SystemExit, match="no attribute"):
        _resolve_handler("no_func_mod:not_there")


def test_resolve_handler_not_callable_exits(tmp_path: Path):
    _write_handler_module(
        tmp_path,
        "not_callable_mod",
        """
        IT_IS_AN_INT = 42
        """,
    )
    with pytest.raises(SystemExit, match="not callable"):
        _resolve_handler("not_callable_mod:IT_IS_AN_INT")


# ---------------------------------------------------------------------------
# _connect_command — registration / token-loading branches
# ---------------------------------------------------------------------------


def test_connect_command_register_failure_returns_2(tmp_path: Path, monkeypatch):
    _write_handler_module(
        tmp_path,
        "rcfail_mod",
        """
        def fn(msg, client):
            return None
        """,
    )

    from molecule_agent import __main__ as cli_mod

    args = MagicMock()
    args.handler = "rcfail_mod:fn"
    args.platform_url = "http://platform.test"
    args.workspace_id = "ws-zzz"
    args.token = None
    args.agent_name = None
    args.reported_url = ""
    args.poll_interval = 1.0
    args.cursor_file = None
    args.verbose = False

    fake_client = MagicMock()
    fake_client.load_token.return_value = None  # no cached token
    fake_client.register.side_effect = RuntimeError("network sad")

    with patch("molecule_agent.client.RemoteAgentClient", return_value=fake_client):
        rc = cli_mod._connect_command(args)
    assert rc == 2


def test_connect_command_uses_provided_token_skips_register(tmp_path: Path, monkeypatch):
    _write_handler_module(
        tmp_path,
        "tokset_mod",
        """
        def fn(msg, client):
            return None
        """,
    )

    from molecule_agent import __main__ as cli_mod

    args = MagicMock()
    args.handler = "tokset_mod:fn"
    args.platform_url = "http://platform.test"
    args.workspace_id = "ws-zzz"
    args.token = "explicit-token"
    args.agent_name = None
    args.reported_url = ""
    args.poll_interval = 1.0
    args.cursor_file = None
    args.verbose = False

    fake_client = MagicMock()
    # Once save_token has been called, load_token should return the token,
    # so register is NOT called.
    fake_client.load_token.return_value = "explicit-token"
    # run_agent_loop returns a terminal status — paused — so the function
    # exits 0 cleanly without us having to signal-break the loop.
    fake_client.run_agent_loop.return_value = "paused"

    with patch("molecule_agent.client.RemoteAgentClient", return_value=fake_client):
        rc = cli_mod._connect_command(args)

    assert rc == 0
    fake_client.save_token.assert_called_once_with("explicit-token")
    fake_client.register.assert_not_called()
    fake_client.run_agent_loop.assert_called_once()
