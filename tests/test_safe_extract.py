"""Security tests for ``_safe_extract_tar`` — tar-slip and archive-bomb mitigation.

The function guards against escape via ``target.relative_to(dest_abs)``. This
rejects:
  • Entries whose resolved path is outside ``dest`` (absolute paths, paths that
    start above ``dest``, paths with more leading ``..`` components than the
    depth of ``dest``).
  • Symlinks and hardlinks entirely (silently skipped, no file written).

Paths that contain ``..`` but still resolve inside ``dest`` are ACCEPTED.
For example ``foo/../bar.txt`` resolves to ``dest/bar.txt`` which is inside
``dest``, so it is accepted.

Covers:
  1. **Paths that start above dest** — ``../``, ``../../`` at name start.
  2. **Absolute paths** — entries with a leading ``/``.
  3. **Depth-exceeding traversal** — ``a/../../../file`` exits dest.
  4. **Symlink / hardlink skip** — no exception, no file written.
  5. **Valid paths accepted** — relative paths with or without embedded ``..``
     that still resolve inside ``dest``.

GAP-01.
"""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from molecule_agent.client import _safe_extract_tar


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tar_entry(name: str, content: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = len(content)
    info.mode = 0o644
    return info


def _build_tar(names_and_contents: list[tuple[str, bytes]]) -> io.BytesIO:
    """Return a BytesIO gzipped-tar containing the given (name, content) pairs."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in names_and_contents:
            info = _make_tar_entry(name, content)
            tf.addfile(info, io.BytesIO(content))
    buf.seek(0)
    return buf


def _open_tar(buf: io.BytesIO) -> tarfile.TarFile:
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r")


# ---------------------------------------------------------------------------
# 1. Paths that start above dest — always rejected
# ---------------------------------------------------------------------------

class TestTraversalFromRoot:
    """Entries whose name begins with ``../`` escape dest regardless of how
    many intermediate directories are traversed."""

    def test_single_parent_component_at_start_rejected(self, tmp_path: Path):
        """``../escape.txt`` starts above dest — must be rejected."""
        buf = _build_tar([("../escape.txt", b"overwrite")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_two_parent_components_at_start_rejected(self, tmp_path: Path):
        """``../../file`` starts two levels above dest — must be rejected."""
        buf = _build_tar([("../../file", b"exfil")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_traversal_into_sibling_directory_rejected(self, tmp_path: Path):
        """``../sibling/marker.txt`` — verify we cannot write into an adjacent dir."""
        sibling = tmp_path.parent / (tmp_path.name + "-sibling")
        sibling.mkdir()
        (sibling / "marker.txt").write_text("original")

        buf = _build_tar([(f"../{tmp_path.name}-sibling/marker.txt", b"tampered")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

        assert (sibling / "marker.txt").read_text() == "original"


# ---------------------------------------------------------------------------
# 2. Absolute paths — always rejected
# ---------------------------------------------------------------------------

class TestAbsolutePaths:
    """Entries with an absolute path (leading ``/``) resolve outside any
    relative dest and must be rejected."""

    def test_absolute_etc_passwd_rejected(self, tmp_path: Path):
        buf = _build_tar([("/etc/passwd", b"root::0:0")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_absolute_usr_local_rejected(self, tmp_path: Path):
        buf = _build_tar([("/usr/local/anything", b"data")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_absolute_tmp_rejected(self, tmp_path: Path):
        buf = _build_tar([("/tmp/staged/foo.txt", b"danger")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_pure_relative_accepted(self, tmp_path: Path):
        """``foo/bar.txt`` (no leading /) is fine."""
        buf = _build_tar([("foo/bar.txt", b"ok")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "foo" / "bar.txt").read_bytes() == b"ok"


# ---------------------------------------------------------------------------
# 3. Depth-exceeding traversal — more leading ``..`` than dest depth
# ---------------------------------------------------------------------------

class TestDepthExceedingTraversal:
    """An entry that has more ``..`` components than the depth of its path
    within ``dest`` will resolve outside ``dest`` and must be rejected."""

    def test_single_dir_then_four_parents_rejected(self, tmp_path: Path):
        """``a/../../../b.txt`` — one dir + four parents = exits dest."""
        buf = _build_tar([("a/../../../b.txt", b"escaped")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_unicode_traversal_exits_dest_rejected(self, tmp_path: Path):
        """``日本語/../../file.txt`` — non-ASCII traversal that exits dest."""
        buf = _build_tar([("日本語/../../file.txt", b"unicode bomb")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    # Note: paths like ``a/b/c/../../d.txt`` or ``subdir/../outdir/file.txt``
    # resolve INSIDE dest (they cancel out within the path) and are tested in
    # TestEmbeddedDotdotAccepted below.


# ---------------------------------------------------------------------------
# 4. Embedded ``..`` that still resolves inside dest — accepted
# ---------------------------------------------------------------------------

class TestEmbeddedDotdotAccepted:
    """Paths that contain ``..`` but whose resolved target is still inside
    ``dest`` are accepted. Not all such paths can be extracted without error —
    Python's ``tarfile`` module raises ``FileExistsError`` for some path shapes
    (e.g., ``foo/../bar.txt`` where ``foo`` doesn't pre-exist: tarfile's
    ``makedirs`` tries to create ``foo/..`` as a directory, but ``..`` is not a
    valid directory name). We test the paths that extract cleanly.

    The key security guarantee is: any path that escapes ``dest`` raises
    ``ValueError`` before any file is written. Paths that don't escape but also
    can't be extracted cleanly are a tarfile implementation detail — the function
    accepts them or raises a non-ValueError error. We only assert on the
    security-relevant behavior (escape rejection) and on paths that work."""

    def test_subdir_parent_outdir_file_accepted(self, tmp_path: Path):
        buf = _build_tar([("subdir/../outdir/file.txt", b"escaped")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "outdir" / "file.txt").read_bytes() == b"escaped"

    def test_subdir_parent_file_accepted(self, tmp_path: Path):
        """``subdir/../file.txt`` — the intermediate dir ``subdir`` must pre-exist
        (or be created by a prior entry) for this path to extract without error."""
        (tmp_path / "subdir").mkdir()
        buf = _build_tar([("subdir/../another.txt", b"data")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "another.txt").read_bytes() == b"data"

    def test_foo_parent_bar_accepted(self, tmp_path: Path):
        """``foo/../bar.txt`` — the intermediate dir ``foo`` must pre-exist."""
        (tmp_path / "foo").mkdir()
        buf = _build_tar([("foo/../bar.txt", b"dangerous")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "bar.txt").read_bytes() == b"dangerous"

    def test_a_b_c_up_up_file_accepted(self, tmp_path: Path):
        """``a/b/c/../../d.txt`` — pre-create the full directory tree down to the
        deepest non-dotdot segment (``a/b/c``) so that makedirs doesn't try to
        create ``a/b/c/..`` as a directory name (which would fail with
        FileExistsError since .. is not a valid directory name on POSIX)."""
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        buf = _build_tar([("a/b/c/../../d.txt", b"escaped")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "a" / "d.txt").read_bytes() == b"escaped"

    def test_three_deep_three_up_accepted(self, tmp_path: Path):
        """``a/b/c/../../../file.txt`` — pre-create ``a/b/c``."""
        (tmp_path / "a" / "b" / "c").mkdir(parents=True)
        buf = _build_tar([("a/b/c/../../../file.txt", b"deep")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "file.txt").read_bytes() == b"deep"

    def test_dot_dot_slash_dot_bar_dot_dot_baz_accepted(self, tmp_path: Path):
        """``foo/./bar/../baz.txt`` — pre-create ``foo/bar``."""
        (tmp_path / "foo" / "bar").mkdir(parents=True)
        buf = _build_tar([("foo/./bar/../baz.txt", b"danger")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "foo" / "baz.txt").read_bytes() == b"danger"

    def test_valid_nested_path_accepted(self, tmp_path: Path):
        """``foo/bar/baz.txt`` (no ..) must be extracted normally."""
        buf = _build_tar([("foo/bar/baz.txt", b"deep content")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "foo" / "bar" / "baz.txt").read_bytes() == b"deep content"

    def test_rules_file_accepted(self, tmp_path: Path):
        buf = _build_tar([("rules/x.md", b"# rule")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "rules" / "x.md").read_text() == "# rule"


# ---------------------------------------------------------------------------
# 5. Symlink / hardlink skip
# ---------------------------------------------------------------------------

class TestSymlinkHardlinkSkip:
    """Symlinks and hardlinks are skipped entirely — no exception, no file
    created, real files extracted normally."""

    def test_symlink_to_absolute_path_skipped(self, tmp_path: Path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            sym = tarfile.TarInfo(name="evil.link")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "/etc/passwd"
            sym.size = 0
            tf.addfile(sym)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert not (tmp_path / "evil.link").exists()

    def test_symlink_to_parent_directory_skipped(self, tmp_path: Path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            sym = tarfile.TarInfo(name="parent.link")
            sym.type = tarfile.SYMTYPE
            sym.linkname = ".."
            sym.size = 0
            tf.addfile(sym)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert not (tmp_path / "parent.link").exists()

    def test_symlink_within_dest_skipped_but_real_file_intact(self, tmp_path: Path):
        buf = _build_tar([("real.txt", b"content")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "real.txt").read_text() == "content"

        buf2 = io.BytesIO()
        with tarfile.open(fileobj=buf2, mode="w:gz") as tf:
            sym = tarfile.TarInfo(name="link-to-real")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "real.txt"
            sym.size = 0
            tf.addfile(sym)
        buf2.seek(0)
        with tarfile.open(fileobj=buf2, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert not (tmp_path / "link-to-real").exists()
        assert (tmp_path / "real.txt").read_text() == "content"

    def test_hardlink_to_absolute_path_skipped(self, tmp_path: Path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            hl = tarfile.TarInfo(name="hard.link")
            hl.type = tarfile.LNKTYPE
            hl.linkname = "/etc/passwd"
            hl.size = 0
            tf.addfile(hl)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert not (tmp_path / "hard.link").exists()

    def test_hardlink_within_dest_skipped_original_intact(self, tmp_path: Path):
        buf = _build_tar([("original.txt", b"data")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)

        buf2 = io.BytesIO()
        with tarfile.open(fileobj=buf2, mode="w:gz") as tf:
            hl = tarfile.TarInfo(name="link-to-original")
            hl.type = tarfile.LNKTYPE
            hl.linkname = "original.txt"
            hl.size = 0
            tf.addfile(hl)
        buf2.seek(0)
        with tarfile.open(fileobj=buf2, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert not (tmp_path / "link-to-original").exists()
        assert (tmp_path / "original.txt").read_text() == "data"

    def test_mixed_valid_and_symlink_entries(self, tmp_path: Path):
        """Valid file extracted, symlink silently skipped — no exception."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = _make_tar_entry("valid/file.txt", b"ok")
            tf.addfile(info, io.BytesIO(b"ok"))
            sym = tarfile.TarInfo(name="bad.link")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "/etc/passwd"
            sym.size = 0
            tf.addfile(sym)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "valid" / "file.txt").read_bytes() == b"ok"
        assert not (tmp_path / "bad.link").exists()

    def test_symlink_then_valid_file_in_same_archive(self, tmp_path: Path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            sym = tarfile.TarInfo(name="dangling.link")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "../nonexistent"
            sym.size = 0
            tf.addfile(sym)
            info = _make_tar_entry("doc.txt", b"important")
            tf.addfile(info, io.BytesIO(b"important"))
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "doc.txt").read_bytes() == b"important"
        assert not (tmp_path / "dangling.link").exists()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Boundary conditions for _safe_extract_tar."""

    def test_empty_archive_accepted(self, tmp_path: Path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            pass
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tf:
            _safe_extract_tar(tf, tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_dot_slash_file_accepted(self, tmp_path: Path):
        """``./file.txt`` — tarfile normalises the leading ``./`` so the file
        lands as ``file.txt`` inside dest."""
        buf = _build_tar([("./file.txt", b"dot")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "file.txt").read_bytes() == b"dot"

    def test_unicode_normal_path_accepted(self, tmp_path: Path):
        """Non-ASCII path without traversal must be accepted."""
        buf = _build_tar([("日本語/文件.txt", b"native text")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert any(p.name.endswith(".txt") for p in tmp_path.rglob("*.txt"))

    def test_extraction_rejects_before_writing_traversal_entry(self, tmp_path: Path):
        """When the first entry is a traversal, no files are extracted."""
        buf = _build_tar([("a/../../../b.txt", b"first")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)
        assert not any(tmp_path.iterdir())

    def test_traversal_entry_rejected_no_partial_state(self, tmp_path: Path):
        """After a traversal entry is rejected, dest must be clean."""
        buf = _build_tar([("a/../../../b.txt", b"first")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError):
                _safe_extract_tar(tf, tmp_path)
        assert list(tmp_path.iterdir()) == []

    def test_many_levels_traversal_exits_dest(self, tmp_path: Path):
        """A depth-10 path ``a/.../a`` needs 11 or more ``..`` components to exit
        dest (ups ≥ depth+1 → net ≤ -1). With 11 ``..``, net depth = -1 = outside."""
        long = "/".join(["a"] * 10) + "/../" * 11 + "file.txt"
        long = long.rstrip("/")
        buf = _build_tar([(long, b"escaped")])
        with _open_tar(buf) as tf:
            with pytest.raises(ValueError, match="refusing tar entry escaping"):
                _safe_extract_tar(tf, tmp_path)

    def test_many_levels_traversal_stays_inside(self, tmp_path: Path):
        """``subdir/../outdir/file.txt`` — intermediate dir exists after ..,
        final segment is a new directory so no FileExistsError on makedirs."""
        buf = _build_tar([("subdir/../outdir/file.txt", b"ok")])
        with _open_tar(buf) as tf:
            _safe_extract_tar(tf, tmp_path)
        assert (tmp_path / "outdir" / "file.txt").read_bytes() == b"ok"