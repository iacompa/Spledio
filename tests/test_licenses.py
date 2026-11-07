"""Licenses shipped with the app: the Help ▸ Licenses and Notices dialog, the About box, and
the consistency of ``licenses/`` with the checksum table in THIRD_PARTY_NOTICES.md."""

from __future__ import annotations

import hashlib
import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QUrl  # noqa: E402

from spledio.ui.licenses_dialog import LicensesDialog, license_files  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LICENSES = os.path.join(ROOT, "licenses")

# Texts the bundled app must carry (see THIRD_PARTY_NOTICES.md); a missing one is a licensing bug.
REQUIRED = (
    "LGPL-3.0.txt",                       # Qt / PySide6 / shiboken6 (LGPL-3.0 adds to the GPL-3.0 in LICENSE)
    "libsndfile-LGPL-2.1.txt",            # libsndfile, mpg123, libquadmath
    "LAME-3.100-COPYING.txt",             # LAME: LGPL-2.0
    "libFLAC-1.4.3-COPYING.Xiph.txt",
    "libogg-1.3.5-COPYING.txt",
    "libvorbis-1.3.7-COPYING.txt",
    "libopus-1.4-COPYING.txt",
    "mpg123-1.32.3-COPYING.txt",
    "PortAudio-19.7.0-LICENSE.txt",
    "CPython-3.14-LICENSE.txt",
    "CPython-3.14.10-incorporated-software.txt",
    "OpenSSL-3.5.9-LICENSE.txt",
    "zstd-1.5.7-LICENSE.txt",
    "xz-5.2.3-COPYING.txt",
    "PyInstaller-6.22.3-COPYING.txt",     # the bootloader that starts the app
    "SOURCE-AND-REPLACING-LIBRARIES.md",
    "Qt-6.11.3-third-party-notices.md",   # code inside the Qt binaries (FreeType, HarfBuzz, libpng, PCRE2, …)
    "Qt-6.11.3-FTL.txt",
    "Qt-6.11.3-Libpng.txt",
    "PySide6-6.11.3-PSF-3.7.0.txt",
)


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_source_tree_order_puts_spledio_first(tmp_path):
    for n in ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md"):
        _touch(str(tmp_path / n))
    for n in ("zlib.txt", "Alpha.md", "SOURCE-AND-REPLACING-LIBRARIES.md", ".DS_Store"):
        _touch(str(tmp_path / "licenses" / n))
    names = [os.path.basename(p) for p in license_files(frozen=False, root=str(tmp_path))]
    assert names == ["LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE-AND-REPLACING-LIBRARIES.md", "Alpha.md", "zlib.txt"]


def test_packaged_app_reads_contents_resources_licenses(tmp_path):
    contents = tmp_path / "Spledio.app" / "Contents"
    exe = contents / "MacOS" / "Spledio"
    _touch(str(exe))
    for n in ("LICENSE", "THIRD_PARTY_NOTICES.md", "LGPL-3.0.txt"):
        _touch(str(contents / "Resources" / "licenses" / n))
    names = [os.path.basename(p) for p in license_files(frozen=True, executable=str(exe))]
    assert names == ["LICENSE", "THIRD_PARTY_NOTICES.md", "LGPL-3.0.txt"]
    assert license_files(frozen=True, executable=str(tmp_path / "elsewhere" / "MacOS" / "x")) == []


def test_required_license_texts_are_present():
    names = {os.path.basename(p) for p in license_files(frozen=False, root=ROOT)}
    missing = [n for n in REQUIRED if n not in names]
    assert not missing, f"missing license texts: {missing}"


def test_dialog_shows_texts_follows_links_and_filters(qtbot):
    dlg = LicensesDialog(files=license_files(frozen=False, root=ROOT))
    qtbot.addWidget(dlg)
    assert os.path.basename(dlg.current) == "LICENSE"
    assert "GNU GENERAL PUBLIC LICENSE" in dlg.view.toPlainText()
    # a relative link inside a notice opens that file from the list
    dlg._on_link(QUrl("licenses/LGPL-3.0.txt"))
    assert os.path.basename(dlg.current) == "LGPL-3.0.txt"
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in dlg.view.toPlainText()
    # a link to a document that is only in the repository says so instead of failing silently
    dlg._on_link(QUrl("docs/BRANDING.md"))
    assert "source repository" in dlg.message.text()
    # Markdown is rendered (no raw table pipes left in the overview)
    assert dlg.select_file("THIRD_PARTY_NOTICES.md")
    assert "Third-party notices" in dlg.view.toPlainText()
    assert "|---|" not in dlg.view.toPlainText()
    dlg.filter_edit.setText("opus")
    assert dlg.visible_names() == ["libopus-1.4-COPYING.txt"]
    assert dlg.select_file("LICENSE")  # selecting clears the filter
    assert len(dlg.visible_names()) == dlg.list.count()


def test_dialog_without_files_explains_where_they_are(qtbot):
    dlg = LicensesDialog(files=[])
    qtbot.addWidget(dlg)
    assert "No license files" in dlg.view.toPlainText()
    assert not dlg.reveal_button.isEnabled()


def test_help_menu_and_about_box(qtbot, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from spledio.playback.engine import FakeBackend
    from spledio.ui.controller import EditorController
    from spledio.ui.main_window import MainWindow

    w = MainWindow(EditorController(playback_backend=FakeBackend()))
    qtbot.addWidget(w)
    try:
        assert "help.licenses" in w.actions.specs
        assert w.actions["help.licenses"].isEnabled()
        html = w.about_html()
        assert "GPL-3.0-only" in html
        assert "LAME" in html and "www.mp3dev.org" in html  # LAME asks apps to credit it and link to its site
        # mpg123's COPYING: "Main message, to include in 'About ...' boxes"
        assert "Copyright (c) 1995-2020 by Michael Hipp and others" in html and "LGPL v2.1" in html
        assert "based in part on the work of the FreeType Team" in html  # FreeType License (FTL), via Qt
        assert "Qt 6." in html and "libsndfile" in html and "PortAudio" in html
        assert "Licenses and Notices" in html
    finally:
        w.ctl.shutdown()


def _checksum_table() -> dict[str, str]:
    with open(os.path.join(ROOT, "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as fh:
        text = fh.read()
    section = text[text.index("## License file sources and checksums"):]
    rows = re.findall(r"^\| `([^`]+)` \| .*? \| `([0-9a-f]{16})` \|$", section, flags=re.M)
    return dict(rows)


def test_every_license_file_is_in_the_checksum_table_with_its_hash():
    table = _checksum_table()
    on_disk = sorted(n for n in os.listdir(LICENSES) if not n.startswith("."))
    assert sorted(table) == on_disk
    for name, prefix in table.items():
        with open(os.path.join(LICENSES, name), "rb") as fh:
            assert hashlib.sha256(fh.read()).hexdigest()[:16] == prefix, name
