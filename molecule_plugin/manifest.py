"""Plugin + skill manifest schema and validators.

Two layers:

1. **Plugin-level** (`plugin.yaml`) — Molecule AI's superset: name, version,
   description, declared `runtimes:`, skill list, rule list. The spec has
   no concept of bundling; this is our own.
2. **Skill-level** (`skills/<skill>/SKILL.md`) — follows the
   `agentskills.io` open standard (name, description, optional license,
   compatibility, metadata, allowed-tools). Validated against the spec
   so our skills are installable in Claude Code, Cursor, Codex, and
   every other skills-compatible agent product.

A plugin that validates locally will also load cleanly in the Molecule AI
platform AND be installable as-is into any agentskills-compatible tool.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

PLUGIN_YAML_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string"},
        "version": {"type": "string"},
        "description": {"type": "string"},
        "author": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "skills": {"type": "array", "items": {"type": "string"}},
        "rules": {"type": "array", "items": {"type": "string"}},
        "prompt_fragments": {"type": "array", "items": {"type": "string"}},
        "runtimes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Declared supported runtimes (e.g. claude_code, deepagents).",
        },
        "sha256": {
            "type": "string",
            "description": (
                "Optional content integrity hash (SHA256) of the plugin directory "
                "as a content-addressed manifest. If present, install_plugin() verifies "
                "the unpacked tarball matches before running setup.sh. "
                "Format: 64 lowercase hex characters. "
                "Generate with: python -m molecule_agent verify-sha256 <plugin-dir>"
            ),
        },
    },
}


def validate_manifest(path: str | Path) -> list[str]:
    """Return a list of validation error messages. Empty list = valid.

    Deliberately simple — no jsonschema dependency so SDK consumers don't
    pick up an extra transitive dep just to lint their plugin.
    """
    path = Path(path)
    if not path.is_file():
        return [f"manifest not found: {path}"]

    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        return [f"yaml parse error: {exc}"]

    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["manifest root must be a mapping"]

    if "name" not in raw or not isinstance(raw.get("name"), str) or not raw["name"].strip():
        errors.append("`name` is required and must be a non-empty string")

    for field_name in ("tags", "skills", "rules", "prompt_fragments", "runtimes"):
        if field_name in raw and not isinstance(raw[field_name], list):
            errors.append(f"`{field_name}` must be a list")

    if "runtimes" in raw and isinstance(raw["runtimes"], list):
        known = {"claude_code", "deepagents", "langgraph", "crewai", "autogen", "openclaw"}
        for r in raw["runtimes"]:
            if not isinstance(r, str):
                errors.append(f"`runtimes` entry must be string, got {type(r).__name__}")
            elif r.replace("-", "_") not in known:
                errors.append(
                    f"unknown runtime '{r}' — supported: {sorted(known)} "
                    f"(use underscore form, e.g. 'claude_code')"
                )

    # sha256 — must be a 64-char lowercase hex string if present
    sha256_val = raw.get("sha256")
    if sha256_val is not None:
        if not isinstance(sha256_val, str):
            errors.append("`sha256` must be a string (64 lowercase hex characters)")
        elif len(sha256_val) != 64:
            errors.append(
                f"`sha256` must be exactly 64 hex characters, got {len(sha256_val)}"
            )
        elif not re.fullmatch(r"[0-9a-f]{64}", sha256_val):
            errors.append(
                "`sha256` must contain only lowercase hex characters (0–9, a–f)"
            )

    # Secrets scan — no credentials may appear in any string value
    for field_name, field_value in raw.items():
        _scan_for_secrets(field_name, field_value, errors)

    return errors


# ---------------------------------------------------------------------------
# Secrets scanning
# ---------------------------------------------------------------------------

# Patterns matching common credential formats.
# Anchored with word boundaries where possible to avoid false positives
# in legitimate content (e.g. "sk" inside "ask").
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # AWS / GCP / Azure keys
    ("AWS key pattern", re.compile(r"AKIA[0-9A-Z]{16}")),
    # GitHub fine-grained / classic tokens
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
    # GitHub app token
    ("GitHub app token", re.compile(r"gho_[A-Za-z0-9_]{36}")),
    # OpenAI / Anthropic / generic sk- keys
    ("OpenAI/Anthropic key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    # Bearer tokens in JSON/YAML values
    ("Bearer token", re.compile(r'"[Bb]earer\s+[A-Za-z0-9_.-]+"')),
    # Long hex strings that look like cryptographic keys (32+ bytes)
    ("long hex secret", re.compile(r"\b[0-9a-fA-F]{64,}\b")),
    # Password assignment patterns in YAML values
    ("password assignment", re.compile(r"(?i)password\s*[=:]\s*['\"]?[A-Za-z0-9_/+=.-]{8,}")),
    # API key assignment patterns
    ("api_key assignment", re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_/+=.-]{16,}")),
    # Secret / token assignment patterns
    ("secret assignment", re.compile(r"(?i)secret\s*[=:]\s*['\"]?[A-Za-z0-9_/+=.-]{16,}")),
    # Generic bearer string in raw text
    ("raw bearer", re.compile(r"Bearer [A-Za-z0-9_.-]{20,}")),
]


def _scan_for_secrets(key: str, value: Any, errors: list[str]) -> None:
    """Recursively scan `value` (and all nested values) for secret patterns.

    Appends error messages to `errors` when a match is found.
    Skips `sha256` field since it's a content-addressed hash, not a secret.
    """
    if key in ("sha256",):
        return
    if isinstance(value, str):
        for description, pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                errors.append(
                    f"possible secret detected in `{key}`: {description} — "
                    "bundles must not contain credentials; use platform secrets instead"
                )
                break  # report first match only to keep noise minimal
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_for_secrets(k, v, errors)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _scan_for_secrets(f"{key}[{i}]", item, errors)


# ---------------------------------------------------------------------------
# agentskills.io spec — SKILL.md validation
# ---------------------------------------------------------------------------

# Spec limits — public so tooling/tests/docs can import them rather than
# duplicate magic numbers. Source: https://agentskills.io/specification
SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SKILL_NAME_MAX = 64
SKILL_DESC_MAX = 1024
SKILL_COMPAT_MAX = 500


def parse_skill_md(path: str | Path) -> tuple[dict[str, Any], str, list[str]]:
    """Parse a SKILL.md into (frontmatter, body, errors).

    Returns ``({}, "", [error])`` if the file can't be read or doesn't have
    valid frontmatter. Never raises.
    """
    path = Path(path)
    if not path.is_file():
        return {}, "", [f"SKILL.md not found: {path}"]

    text = path.read_text()
    if not text.startswith("---"):
        return {}, text, ["SKILL.md must start with YAML frontmatter (---)"]

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text, ["malformed frontmatter — expected opening and closing '---'"]

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return {}, parts[2], [f"frontmatter yaml parse error: {exc}"]

    if not isinstance(fm, dict):
        return {}, parts[2], ["frontmatter must be a YAML mapping"]

    return fm, parts[2].strip(), []


def validate_skill(path: str | Path) -> list[str]:
    """Validate a single skill directory against agentskills.io/specification.

    `path` should be the skill directory (its parent of `SKILL.md`). Returns
    an empty list when the skill is spec-compliant.
    """
    path = Path(path)
    if not path.is_dir():
        return [f"skill path is not a directory: {path}"]

    fm, _body, errors = parse_skill_md(path / "SKILL.md")
    if errors:
        return errors

    # name — required
    name = fm.get("name")
    if not name:
        errors.append("`name` is required in SKILL.md frontmatter")
    elif not isinstance(name, str):
        errors.append(f"`name` must be a string, got {type(name).__name__}")
    else:
        if len(name) > SKILL_NAME_MAX:
            errors.append(f"`name` length must be ≤{SKILL_NAME_MAX}, got {len(name)}")
        if not SKILL_NAME_RE.match(name):
            errors.append(
                f"`name` '{name}' must be lowercase alphanumeric with single hyphens, "
                f"no leading/trailing/consecutive hyphens"
            )
        if name != path.name:
            errors.append(
                f"`name` '{name}' must match directory name '{path.name}' "
                f"(agentskills.io spec)"
            )

    # description — required
    desc = fm.get("description")
    if not desc:
        errors.append("`description` is required in SKILL.md frontmatter")
    elif not isinstance(desc, str):
        errors.append(f"`description` must be a string, got {type(desc).__name__}")
    elif len(desc) > SKILL_DESC_MAX:
        errors.append(f"`description` length must be ≤{SKILL_DESC_MAX}, got {len(desc)}")

    # compatibility — optional, ≤500 chars
    compat = fm.get("compatibility")
    if compat is not None:
        if not isinstance(compat, str):
            errors.append(f"`compatibility` must be a string, got {type(compat).__name__}")
        elif len(compat) > SKILL_COMPAT_MAX:
            errors.append(
                f"`compatibility` length must be ≤{SKILL_COMPAT_MAX}, got {len(compat)}"
            )

    # metadata — optional, string→string map
    meta = fm.get("metadata")
    if meta is not None:
        if not isinstance(meta, dict):
            errors.append(f"`metadata` must be a mapping, got {type(meta).__name__}")
        else:
            for k, v in meta.items():
                if not isinstance(k, str):
                    errors.append(f"`metadata` keys must be strings, got {type(k).__name__}")
                # values may be stringified — spec says "string-to-string" but is lenient

    # allowed-tools — optional, space-separated string (experimental in spec)
    allowed = fm.get("allowed-tools")
    if allowed is not None and not isinstance(allowed, str):
        errors.append(f"`allowed-tools` must be a space-separated string, got {type(allowed).__name__}")

    # license — optional, free-form string
    lic = fm.get("license")
    if lic is not None and not isinstance(lic, str):
        errors.append(f"`license` must be a string, got {type(lic).__name__}")

    return errors


def validate_plugin(path: str | Path) -> dict[str, list[str]]:
    """Validate an entire Molecule AI plugin: plugin.yaml + all skills.

    Returns a dict mapping source (``"plugin.yaml"`` or ``"skills/<name>"``)
    to a list of error messages. Empty dict means fully valid.
    """
    path = Path(path)
    results: dict[str, list[str]] = {}

    manifest_errs = validate_manifest(path / "plugin.yaml")
    if manifest_errs:
        results["plugin.yaml"] = manifest_errs

    skills_dir = path / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_errs = validate_skill(entry)
            if skill_errs:
                results[f"skills/{entry.name}"] = skill_errs

    return results
