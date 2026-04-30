"""CLI for molecule_agent — python -m molecule_agent [command]

Commands:
    verify-sha256 <plugin-dir>    Compute the content-integrity SHA256 for a
                                  plugin directory. The hash excludes
                                  plugin.yaml (self-referential). Output the
                                  hash so you can paste it into plugin.yaml
                                  under the sha256 field.

    connect                       Register and run a remote agent against a
                                  Molecule platform — heartbeat + state-poll
                                  + inbound message poll, all in one process.
                                  Loads a user-supplied handler module:func
                                  and dispatches every inbound A2A message.
                                  Designed for hermes / codex / any third-party
                                  runtime that can't expose a reachable URL.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import os
import signal
import sys
from pathlib import Path


def _walk_files(root: Path) -> list[str]:
    """Yield relative file paths under ``root`` (directories excluded)."""
    rel: list[str] = []
    for p in root.rglob("*"):
        if p.is_file():
            rel.append(p.relative_to(root).as_posix())
    return rel


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_plugin_sha256(plugin_dir: Path) -> str:
    """Compute the content-integrity SHA256 for a plugin directory.

    The manifest is the SHA256 of the canonical JSON of
    ``sorted((relative_path, SHA256(file_content)) for every file
    EXCEPT plugin.yaml``.

    ``plugin.yaml`` is excluded from its own hash because it contains the
    hash — otherwise the bootstrap is circular and convergence is impossible.
    """
    file_hashes: list[tuple[str, str]] = []
    for relpath in sorted(_walk_files(plugin_dir)):
        if relpath == "plugin.yaml":
            continue
        file_hashes.append((relpath, _sha256_file(plugin_dir / relpath)))
    manifest_bytes = json.dumps(file_hashes, sort_keys=True).encode()
    return hashlib.sha256(manifest_bytes).hexdigest()


def _resolve_handler(spec: str):
    """Resolve a ``module.path:function`` spec into the callable.

    Mirrors the convention used by gunicorn / uvicorn / celery for app
    references — a single string the user can put in a config file or env
    var. Raises ``SystemExit`` with a readable message on any failure
    (import, attribute lookup, non-callable result) so the CLI's exit
    surface is clean.
    """
    if ":" not in spec:
        raise SystemExit(
            f"error: handler spec {spec!r} must be of the form 'module.path:function'"
        )
    mod_path, func_name = spec.split(":", 1)
    if not mod_path or not func_name:
        raise SystemExit(f"error: handler spec {spec!r} is malformed")
    try:
        # Importing the user's module pulls in their code — we run it from
        # the current working directory by default so 'my_handler:fn' works
        # without setting PYTHONPATH first.
        if "" not in sys.path:
            sys.path.insert(0, "")
        module = importlib.import_module(mod_path)
    except Exception as exc:
        raise SystemExit(f"error: could not import {mod_path}: {exc}")
    try:
        func = getattr(module, func_name)
    except AttributeError:
        raise SystemExit(f"error: {mod_path} has no attribute {func_name!r}")
    if not callable(func):
        raise SystemExit(f"error: {spec} is not callable")
    return func


def _connect_command(args: argparse.Namespace) -> int:
    """Run the register + heartbeat + inbound-poll loop.

    Returns the process exit code. 0 on graceful exit (paused/removed/SIGTERM),
    non-zero on registration / handler-import failures.
    """
    # Lazy import — the connect path pulls in requests + the full client,
    # while verify-sha256 should stay light.
    from .client import RemoteAgentClient
    from .inbound import PollDelivery

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="[molecule] %(message)s",
    )

    handler = _resolve_handler(args.handler)

    client = RemoteAgentClient(
        workspace_id=args.workspace_id,
        platform_url=args.platform_url,
        agent_card={"name": args.agent_name or f"remote-{args.workspace_id[:8]}"},
        reported_url=args.reported_url or "",
    )

    if args.token:
        # User passed a token explicitly — persist it so register() can be
        # skipped on a known-tokened workspace. The platform's register
        # endpoint refuses to issue a second token when one is on file.
        client.save_token(args.token)

    # If we don't have a token yet (and one wasn't provided), call register
    # so the platform mints one. On a known-tokened workspace this still
    # succeeds and just returns the cached token.
    if client.load_token() is None:
        try:
            client.register()
        except Exception as exc:
            print(f"[molecule] register failed: {exc}", file=sys.stderr)
            return 2

    print(
        f"[molecule] connected as {args.workspace_id} "
        f"(platform={args.platform_url}, delivery=poll, interval={args.poll_interval}s)"
    )

    cursor_file = None
    if args.cursor_file:
        cursor_file = Path(args.cursor_file).expanduser()

    delivery = PollDelivery(
        client,
        interval=args.poll_interval,
        cursor_file=cursor_file,
    )

    # Graceful shutdown on SIGINT / SIGTERM. The loop's built-in stop
    # condition is platform-driven (paused / deleted), so we install a
    # signal handler that sets max_iterations to the loop counter +1
    # by raising KeyboardInterrupt — caught below.
    def _on_signal(_sig, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        terminal = client.run_agent_loop(handler, delivery=delivery)
        print(f"[molecule] platform reports workspace {terminal} — exiting")
        return 0
    except KeyboardInterrupt:
        print("[molecule] received signal — shutting down cleanly")
        try:
            delivery.stop()
        except Exception:
            pass
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="molecule_agent",
        description="Molecule AI remote-agent CLI utilities.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    vs = sub.add_parser(
        "verify-sha256",
        help="Compute the content-integrity SHA256 for a plugin directory.",
    )
    vs.add_argument(
        "plugin_dir",
        type=Path,
        help="Path to the plugin directory (must contain plugin.yaml)",
    )

    cn = sub.add_parser(
        "connect",
        help=(
            "Register and run a remote agent against a Molecule platform — "
            "heartbeat + state-poll + inbound A2A message dispatch."
        ),
    )
    cn.add_argument(
        "--platform-url",
        required=True,
        default=os.environ.get("MOLECULE_PLATFORM_URL"),
        help="Base URL of the Molecule platform (env: MOLECULE_PLATFORM_URL)",
    )
    cn.add_argument(
        "--workspace-id",
        required=True,
        default=os.environ.get("MOLECULE_WORKSPACE_ID"),
        help="UUID of the workspace this agent claims (env: MOLECULE_WORKSPACE_ID)",
    )
    cn.add_argument(
        "--token",
        default=os.environ.get("MOLECULE_WORKSPACE_TOKEN"),
        help=(
            "Pre-issued workspace bearer token (env: MOLECULE_WORKSPACE_TOKEN). "
            "If omitted, the CLI calls /registry/register and caches the issued token."
        ),
    )
    cn.add_argument(
        "--handler",
        required=True,
        help=(
            "Handler spec in 'module.path:function' form. The function receives "
            "(InboundMessage, RemoteAgentClient) and returns a reply string or None."
        ),
    )
    cn.add_argument(
        "--agent-name",
        default=os.environ.get("MOLECULE_AGENT_NAME"),
        help="Name in the agent_card (env: MOLECULE_AGENT_NAME). Defaults to remote-<id8>.",
    )
    cn.add_argument(
        "--reported-url",
        default=os.environ.get("MOLECULE_REPORTED_URL", ""),
        help=(
            "Externally-reachable URL siblings can call. Empty = poll-only mode "
            "(env: MOLECULE_REPORTED_URL)."
        ),
    )
    cn.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("MOLECULE_POLL_INTERVAL", "5.0")),
        help="Seconds between activity polls (env: MOLECULE_POLL_INTERVAL).",
    )
    cn.add_argument(
        "--cursor-file",
        default=os.environ.get("MOLECULE_CURSOR_FILE"),
        help=(
            "Path to persist the activity cursor across restarts (env: "
            "MOLECULE_CURSOR_FILE). Default: in-process only."
        ),
    )
    cn.add_argument(
        "--verbose",
        action="store_true",
        help="Enable INFO-level logging.",
    )

    args = parser.parse_args()

    if args.command == "verify-sha256":
        plugin_dir = args.plugin_dir.resolve()
        if not plugin_dir.is_dir():
            sys.exit(f"error: {plugin_dir} is not a directory")
        try:
            h = compute_plugin_sha256(plugin_dir)
            print(f"Computed SHA256: {h}")
        except Exception as exc:
            sys.exit(f"error: {exc}")
    elif args.command == "connect":
        sys.exit(_connect_command(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()