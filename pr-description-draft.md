# PR Description Draft — Plugin Content Integrity (SHA256)

**File:** `pr-description-draft.md` in the SDK repo, to be pasted into GitHub when token recovers.

---

## feat(security): add plugin content integrity verification (SHA256)

### Problem

When a workspace installs a plugin via `GET /workspaces/:id/plugins/:name/download`, the platform can pin the tarball to a specific Git ref (PR #1019, molecule-core). However, the SDK had no content-integrity check: once a tarball was served under a valid pinned ref, the SDK would extract it and run `setup.sh` without verifying the unpacked content matched the declared SHA256 in `plugin.yaml`.

A supply-chain attacker who compromised the plugin registry or the GitHub source could serve a tampered tarball under a valid pinned ref. The install would proceed, `setup.sh` would run with plugin author credentials, and the attacker's payload would execute.

### Solution

Add a content-addressed manifest hash to `plugin.yaml` and verify it before running `setup.sh`.

**Manifest format:** SHA256 of the canonical JSON of `sorted((relative_path, SHA256(file_content)) for all files except plugin.yaml itself)`. `plugin.yaml` is excluded from its own hash because it contains the hash — otherwise the bootstrap is circular.

**Why this works:** Even if an attacker replaces a file, they cannot compute the matching manifest hash without knowing the excluded set. The platform pins the tarball by Git ref; the SDK verifies the tarball's unpacked content integrity before execution.

### Changes

| File | Change |
|------|--------|
| `molecule_agent/client.py` | Added `verify_plugin_sha256()`, `_walk_files()`, `_sha256_file()`, integrated into `install_plugin()` before `setup.sh` runs |
| `molecule_agent/__main__.py` | Added CLI: `python -m molecule_agent verify-sha256 <plugin-dir>` to compute the hash for a plugin directory |
| `molecule_plugin/manifest.py` | Added `sha256` field to `PLUGIN_YAML_SCHEMA`, validation in `validate_manifest()` |
| `molecule_agent/__init__.py` | Re-export `verify_plugin_sha256` and `compute_plugin_sha256` |
| `tests/test_remote_agent.py` | 12 new tests covering all sha256 paths, including integration with `install_plugin()` |
| `known-issues.md` | Updated KI-006 with resolution |
| `CLAUDE.md` | Added content integrity section documenting the `verify-sha256` CLI |

### API / Schema

**`plugin.yaml` additions:**
```yaml
name: my-plugin
version: "1.0"
sha256: a3f5b8c9d1e2...  # 64 lowercase hex chars; generate with: python -m molecule_agent verify-sha256 <plugin-dir>
```

**Generate the hash for a local plugin directory:**
```bash
python -m molecule_agent verify-sha256 ./my-plugin
# Outputs: "Computed SHA256: <64-char hash>"
# Copy the hash into plugin.yaml under the sha256 field.
```

### Security notes

- The hash excludes `plugin.yaml` itself to avoid circular dependency. This means `plugin.yaml` can be modified freely as long as the new hash is recomputed and stored.
- `setup.sh` is only executed after `verify_plugin_sha256()` succeeds. If verification fails, the staging directory is cleaned up and `setup.sh` is never called.
- `_safe_extract_tar()` (tar-slip protection) and `verify_plugin_sha256()` (content integrity) address two separate concerns and are applied in sequence.

### Test results

```
tests/test_remote_agent.py: 57 passed (12 new sha256 tests)
tests/test_sdk.py: 50 passed
tests/test_validators.py: 36 passed
Total: 143 passed
```

### Migration path for existing plugins

Plugin authors who want to pin their plugin must:
1. Run `python -m molecule_agent verify-sha256 <plugin-dir>` on the final directory
2. Add the hash to `plugin.yaml` under the `sha256` field
3. Commit and push; CI will verify the hash remains correct

Existing plugins without a `sha256` field are unaffected (verification is skipped with a warning log).

---

*Draft — will submit via GitHub API when auth token recovers.*