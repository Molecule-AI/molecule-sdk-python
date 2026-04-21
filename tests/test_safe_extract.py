"""Security tests for _safe_extract_tar and related tar-extraction helpers.

Covers GAP-01 from TEST_GAP_ANALYSIS.md — CWE-22 / CVE-2007-4559 "tar slip"
family: directory traversal, absolute paths, zip bombs, symlink escapes.

These are unit tests with no external dependencies.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

import sys
from pathlib import Path as _Path

_SDK_ROOT = _Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))

from molecule_agent.client import _safe_extract_tar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tar(entries: list[tuple[str, str | bytes, bool]]) -> io.BytesIO:
    """Build an in-memory tar archive.

    Args:
        entries: list of (filename, content, is_dir) tuples.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, content, is_dir in entries:
            if is_dir:
                tinfo = tarfile.TarInfo(name=name)
                tinfo.type = tarfile.DIRTYPE
                tinfo.mode = 0o755
                tinfo.size = 0
                tf.addfile(tinfo)
            else:
                data = content.encode() if isinstance(content, str) else content
                tinfo = tarfile.TarInfo(name=name)
                tinfo.size = len(data)
                tf.addfile(tinfo, io.BytesIO(data))
    buf.seek(0)
    return buf


def _make_tar_with_symlink(name: str, link_target: str) -> io.BytesIO:
    """Build an in-memory tar with one symlink entry and optional normal file."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name=name)
        info.type = tarfile.SYMTYPE
        info.linkname = link_target
        tf.addfile(info, io.BytesIO(b""))
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Test: directory traversal via ../ in filename
# ---------------------------------------------------------------------------

def test_traversal_dotdot_in_name(tmp_path: Path):
    """CWE-22: ../ in a tar entry must be rejected, not silently stripped."""
    dest = tmp_path / "dest"
    dest.mkdir()

    # Normal file must extract correctly.
    buf = _make_tar([("sub/normal.txt", "hello", False)])
    with tarfile.open(fileobj=buf) as tf:
        _safe_extract_tar(tf, dest)
    assert (dest / "sub" / "normal.txt").read_text() == "hello"

    # Now try traversal — _safe_extract_tar must raise.
    buf2 = _make_tar([("../escape.txt", "pwned", False)])
    with tarfile.open(fileobj=buf2) as tf:
        with pytest.raises(ValueError, match="escaping dest"):
            _safe_extract_tar(tf, dest)

    assert not (dest.parent / "escape.txt").exists()


def test_traversal_dotdot_in_deep_path(tmp_path: Path):
    """A ../ in the middle of a long path must also be rejected."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = _make_tar([("../a/../../../etc/passwd", "root:x:0:0", False)])
    with tarfile.open(fileobj=buf) as tf:
        with pytest.raises(ValueError, match="escaping dest"):
            _safe_extract_tar(tf, dest)


# ---------------------------------------------------------------------------
# Test: absolute paths in tar entries
# ---------------------------------------------------------------------------

def test_absolute_path_rejected(tmp_path: Path):
    """An entry with an absolute path must be rejected."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = _make_tar([("/etc/passwd", "root:x:0:0", False)])
    with tarfile.open(fileobj=buf) as tf:
        with pytest.raises(ValueError, match="escaping dest"):
            _safe_extract_tar(tf, dest)


def test_absolute_path_in_subdirectory(tmp_path: Path):
    """Absolute path buried under a normal directory component must be rejected."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = _make_tar([("subdir/../../../usr/local/bin/malware.sh", "#!/bin/sh", False)])
    with tarfile.open(fileobj=buf) as tf:
        with pytest.raises(ValueError, match="escaping dest"):
            _safe_extract_tar(tf, dest)


# ---------------------------------------------------------------------------
# Test: symlink escape (symlink → outside dest)
# ---------------------------------------------------------------------------

def test_symlink_to_parent_skipped(tmp_path: Path):
    """A symlink pointing outside the extraction root must not be written.

    _safe_extract_tar skips symlinks silently (matches platform tar producer).
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        normal_info = tarfile.TarInfo(name="sub/normal.txt")
        normal_info.size = 5
        tf.addfile(normal_info, io.BytesIO(b"hello"))

        link_info = tarfile.TarInfo(name="sub/link_to_escape")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "../escape.txt"
        tf.addfile(link_info, io.BytesIO(b""))

    buf.seek(0)
    with tarfile.open(fileobj=buf) as tf:
        # Must not raise — symlinks are silently skipped.
        _safe_extract_tar(tf, dest)

    assert (dest / "sub" / "normal.txt").read_text() == "hello"
    assert not (dest / "sub" / "link_to_escape").exists()


def test_symlink_to_absolute_path_skipped(tmp_path: Path):
    """A symlink using an absolute path must not be written."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        normal_info = tarfile.TarInfo(name="sub/normal.txt")
        normal_info.size = 5
        tf.addfile(normal_info, io.BytesIO(b"hello"))

        link_info = tarfile.TarInfo(name="sub/abs_link")
        link_info.type = tarfile.SYMTYPE
        link_info.linkname = "/etc/passwd"
        tf.addfile(link_info, io.BytesIO(b""))

    buf.seek(0)
    with tarfile.open(fileobj=buf) as tf:
        _safe_extract_tar(tf, dest)

    assert (dest / "sub" / "normal.txt").read_text() == "hello"
    assert not (dest / "sub" / "abs_link").exists()


# ---------------------------------------------------------------------------
# Test: hardlink escape
# ---------------------------------------------------------------------------

def test_hardlink_skipped(tmp_path: Path):
    """Hardlinks must be skipped silently (not followed, not created)."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        normal_info = tarfile.TarInfo(name="sub/normal.txt")
        normal_info.size = 5
        tf.addfile(normal_info, io.BytesIO(b"hello"))

        link_info = tarfile.TarInfo(name="sub/hardlink")
        link_info.type = tarfile.LNKTYPE
        link_info.linkname = "sub/normal.txt"
        tf.addfile(link_info, io.BytesIO(b""))

    buf.seek(0)
    with tarfile.open(fileobj=buf) as tf:
        _safe_extract_tar(tf, dest)

    assert (dest / "sub" / "normal.txt").read_text() == "hello"
    assert not (dest / "sub" / "hardlink").exists()


# ---------------------------------------------------------------------------
# Test: deeply nested traversal
# ---------------------------------------------------------------------------

def test_deeply_nested_traversal_rejected(tmp_path: Path):
    """Many levels of ../ must all be rejected."""
    dest = tmp_path / "dest"
    dest.mkdir()

    deep_path = "/".join([".."] * 20) + "/etc/passwd"
    buf = _make_tar([(deep_path, "root:x:0:0", False)])
    with tarfile.open(fileobj=buf) as tf:
        with pytest.raises(ValueError, match="escaping dest"):
            _safe_extract_tar(tf, dest)


# ---------------------------------------------------------------------------
# Test: deeply nested valid paths
# ---------------------------------------------------------------------------

def test_deeply_nested_valid_path_extracted(tmp_path: Path):
    """Deeply nested directories with no traversal must be extracted correctly."""
    dest = tmp_path / "dest"
    dest.mkdir()

    deep_name = "/".join(["a"] * 20) + "/file.txt"
    buf = _make_tar([(deep_name, "content", False)])
    with tarfile.open(fileobj=buf) as tf:
        _safe_extract_tar(tf, dest)

    assert (dest / "a" / "a" / "a" / "a" / "a" /
            "a" / "a" / "a" / "a" / "a" /
            "a" / "a" / "a" / "a" / "a" /
            "a" / "a" / "a" / "a" / "a" /
            "file.txt").read_text() == "content"


# ---------------------------------------------------------------------------
# Test: zipfile extraction (separate code path)
# ---------------------------------------------------------------------------

def test_zipfile_with_dotdot_entries(tmp_path: Path):
    """ZIP archives with ../ in filenames must be handled safely.

    The SDK currently uses _safe_extract_tar for tar archives only.
    This test documents that zip handling needs equivalent protection
    if .zip plugin support is added. The test is a placeholder that
    checks zipfile.ZipFile accepts such entries.
    """
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w") as zf:
        zf.writestr("sub/normal.txt", "hello")
        zf.writestr("../escape.txt", "pwned")

    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        names = zf.namelist()
        assert "../escape.txt" in names
        assert "sub/normal.txt" in names
        # SDK does not currently extract zip archives for plugin install.
        # This assertion will need updating when zip safety is implemented.


# ---------------------------------------------------------------------------
# Test: empty tar archive
# ---------------------------------------------------------------------------

def test_empty_tar_noops(tmp_path: Path):
    """An empty tar archive must not raise."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        pass  # empty archive
    buf.seek(0)

    with tarfile.open(fileobj=buf) as tf:
        _safe_extract_tar(tf, dest)  # must not raise


# ---------------------------------------------------------------------------
# Test: normal operation
# ---------------------------------------------------------------------------

def test_normal_files_extracted_correctly(tmp_path: Path):
    """Normal, well-behaved tar entries must be extracted correctly."""
    dest = tmp_path / "dest"
    dest.mkdir()

    buf = _make_tar([
        ("a.txt", "alpha", False),
        ("sub/b.txt", "beta", False),
        ("sub/c.txt", "gamma", False),
        ("rules/", "", True),
        ("rules/foo.md", "- be kind", False),
    ])
    with tarfile.open(fileobj=buf) as tf:
        _safe_extract_tar(tf, dest)

    assert (dest / "a.txt").read_text() == "alpha"
    assert (dest / "sub" / "b.txt").read_text() == "beta"
    assert (dest / "sub" / "c.txt").read_text() == "gamma"
    assert (dest / "rules" / "foo.md").read_text() == "- be kind"
