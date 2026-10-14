import gzip
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from deepwick_spec.ansi import style_of
from deepwick_spec.check_port import check_port
from deepwick_spec.color import normalize_hex
from deepwick_spec.palette import load_palette

REPO = Path(__file__).resolve().parents[1]
PALETTE = load_palette()
C = PALETTE.colors


TREE_ROLES = {
    "src": "directory", "run.sh": "executable", "link": "symlink", "broken": "brokenSymlink",
    "pipe": "special", "bundle.tar.gz": "archive", "photo.png": "media", "notes.pdf": "document",
    "app.py": "source", "old.bak": "temp",
}


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    header = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"")


def make_tree(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir()
    (root / "run.sh").write_text("#!/bin/sh\necho run\n")
    (root / "run.sh").chmod(0o755)
    (root / "app.py").write_text("print('hi')\n")
    (root / "link").symlink_to("app.py")
    (root / "broken").symlink_to("missing")
    os.mkfifo(root / "pipe")
    (root / "bundle.tar.gz").write_bytes(gzip.compress(b"deepwick"))
    (root / "photo.png").write_bytes(tiny_png())
    (root / "notes.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (root / "old.bak").write_text("old\n")
    return root


def assert_role(text, name, role):
    style = style_of(text, name)
    expected = PALETTE.files[role]
    assert style["fg"] == C[expected.color].lower(), (name, role, style)
    assert style["bold"] == ("bold" in expected.style), (name, role, style)


THEME = REPO / "deepwick.dircolors"
requires_dircolors = pytest.mark.skipif(shutil.which("dircolors") is None, reason="requires GNU dircolors")


def entries():
    assert THEME.is_file(), "deepwick.dircolors missing"
    pairs = []
    for line in THEME.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith(("*#", "*~")) else line.strip()
        if line:
            key, value = line.split(None, 1)
            pairs.append((key, value))
    return pairs


def ls_colors(term="xterm-256color", colorterm=""):
    out = subprocess.run(
        ["dircolors", "-b", str(THEME)], env={"TERM": term, "COLORTERM": colorterm, "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout
    return re.search(r"LS_COLORS='([^']*)'", out).group(1)


def test_every_color_is_in_the_palette():
    allowed = PALETTE.allowed_hex()
    codes = [v for k, v in entries() if k not in ("TERM", "COLORTERM")]
    for code in codes:
        for r, g, b in re.findall(r"[34]8;2;(\d+);(\d+);(\d+)", code):
            assert normalize_hex(f"#{int(r):02x}{int(g):02x}{int(b):02x}") in allowed, code


def test_only_truecolor_or_reset_codes():
    rgb = r"[34]8;2;\d+;\d+;\d+"
    pattern = re.compile(rf"0|00|(01;)?{rgb}(;{rgb})?")
    for key, code in entries():
        if key not in ("TERM", "COLORTERM"):
            assert pattern.fullmatch(code), (key, code)


@requires_dircolors
def test_covers_every_default_extension():
    default = subprocess.run(["dircolors", "-p"], capture_output=True, text=True, check=True).stdout
    def suffixes(text):
        return {line.split()[0] for line in text.splitlines() if line[:1] in (".", "*") and len(line.split()) > 1}
    ours = {k for k, _ in entries() if k[:1] in (".", "*")}
    assert suffixes(default) - ours == set()


@requires_dircolors
@pytest.mark.parametrize(("term", "colorterm"), [("xterm-256color", ""), ("tmux-256color", ""), ("dumb", "truecolor")])
def test_applies_to_common_terminals(term, colorterm):
    assert "di=01;38;2;86;156;214" in ls_colors(term, colorterm)


@requires_dircolors
def test_ls_colors_every_file_role(tmp_path):
    tree = make_tree(tmp_path / "tree")
    out = subprocess.run(
        ["ls", "-l", "--color=always", str(tree)],
        env={"LS_COLORS": ls_colors(), "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        capture_output=True, text=True, check=True,
    ).stdout
    for name, role in TREE_ROLES.items():
        assert_role(out, name, role)


def test_port_conventions():
    assert check_port(REPO, PALETTE.allowed_hex()) == []


def test_readme_installs_where_distributions_look():
    # Debian/Ubuntu's stock ~/.bashrc reads ~/.dircolors; Fedora's colorls.sh reads ~/.dir_colors, then ~/.dircolors.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "cp deepwick.dircolors ~/.dircolors" in readme
    assert "~/.dir_colors" not in readme.split("## Notes")[0].replace("no `~/.dir_colors`", "")
