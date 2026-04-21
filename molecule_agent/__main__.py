"""CLI for molecule_agent — python -m molecule_agent [command]

Commands:
    verify-sha256 <plugin-dir>    Compute the content-integrity SHA256 for a
                                  plugin directory. The hash excludes
                                  plugin.yaml (self-referential). Output the
                                  hash so you can paste it into plugin.yaml
                                  under the sha256 field.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()