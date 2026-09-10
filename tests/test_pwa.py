"""Tests for PWA assets — every static file referenced by the service
worker precache list and the web manifest must exist on disk.

Regression test: `sw.js` used to precache `/static/favicon.svg` (never
created) and `/manifest.webmanifest` (wrong path), spamming the logs
with 404s and failing the whole `addAll` precache on install.
"""

import json
import re
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _sw_shell_entries() -> list[str]:
    text = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    match = re.search(r"const SHELL = \[(.*?)\];", text, re.DOTALL)
    assert match, "SHELL precache list not found in sw.js"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_sw_shell_entries_exist():
    entries = _sw_shell_entries()
    assert entries, "SHELL precache list is empty"
    bad_paths = [p for p in entries if p != "/" and not p.startswith("/static/")]
    assert not bad_paths, f"precache entries outside /static/: {bad_paths}"
    missing = [
        path
        for path in entries
        if path.startswith("/static/")
        and not (STATIC_DIR / path.removeprefix("/static/")).is_file()
    ]
    assert not missing, f"service worker precaches missing files: {missing}"


def test_manifest_icons_exist():
    manifest = json.loads((STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8"))
    missing = [
        entry["src"]
        for entry in manifest["icons"]
        if not (STATIC_DIR / str(entry["src"]).removeprefix("/static/")).is_file()
    ]
    assert not missing, f"manifest references missing icons: {missing}"
