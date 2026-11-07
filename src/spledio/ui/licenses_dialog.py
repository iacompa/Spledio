"""Help ▸ Licenses and Notices: read Spledio's license and every third-party notice in the app.

The texts are plain files, never fetched from the network:

* packaged app: ``Spledio.app/Contents/Resources/licenses/`` (filled by ``scripts/release_macos.sh``
  with ``LICENSE``, ``THIRD_PARTY_NOTICES.md`` and the files in ``licenses/``);
* source tree: ``LICENSE`` and ``THIRD_PARTY_NOTICES.md`` at the repository root plus ``licenses/``.

Spledio's own license comes first, then the notices overview and the source/replacing-libraries
notes, then the other files alphabetically. Markdown files are rendered; a link to another file in
the list opens that file, and a web link opens in the default browser only when clicked.
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from spledio.branding import APP_NAME

FIRST = ("LICENSE", "THIRD_PARTY_NOTICES.md", "SOURCE-AND-REPLACING-LIBRARIES.md")


def _source_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


def license_files(frozen: bool | None = None, executable: str | None = None, root: str | None = None) -> list[str]:
    """Absolute paths of the license and notice files, in display order (empty if none found)."""
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    paths: list[str] = []
    if frozen:
        exe = os.path.abspath(executable or sys.executable)  # .../Spledio.app/Contents/MacOS/Spledio
        folder = os.path.join(os.path.dirname(os.path.dirname(exe)), "Resources", "licenses")
        if os.path.isdir(folder):
            paths = [os.path.join(folder, n) for n in os.listdir(folder)]
    else:
        root = root or _source_root()
        paths = [os.path.join(root, n) for n in ("LICENSE", "THIRD_PARTY_NOTICES.md")]
        folder = os.path.join(root, "licenses")
        if os.path.isdir(folder):
            paths += [os.path.join(folder, n) for n in os.listdir(folder)]
    paths = [p for p in paths if os.path.isfile(p) and not os.path.basename(p).startswith(".")]

    def key(p: str):
        n = os.path.basename(p)
        return (FIRST.index(n), "") if n in FIRST else (len(FIRST), n.lower())

    return sorted(paths, key=key)


def display_name(path: str) -> str:
    n = os.path.basename(path)
    return {
        "LICENSE": f"{APP_NAME} – GPL-3.0-only (LICENSE)",
        "THIRD_PARTY_NOTICES.md": "Third-party notices (overview)",
        "SOURCE-AND-REPLACING-LIBRARIES.md": "Source code and replacing libraries",
    }.get(n, n)


class LicensesDialog(QDialog):
    """A file list on the left, the selected text on the right."""

    def __init__(self, parent=None, files: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Licenses and Notices")
        self.setObjectName("LicensesDialog")
        self.resize(980, 640)
        self.files = license_files() if files is None else list(files)
        self._by_name = {os.path.basename(p): p for p in self.files}

        lay = QVBoxLayout(self)
        intro = QLabel(f"{APP_NAME} is free software under the GNU General Public License version 3 only. "
                       "It includes the third-party components listed here; each file is that component's license "
                       "or notice, exactly as published by its authors.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter files…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        ll.addWidget(self.filter_edit)
        self.list = QListWidget()
        self.list.setObjectName("licensesList")
        for p in self.files:
            it = QListWidgetItem(display_name(p))
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            self.list.addItem(it)
        self.list.currentItemChanged.connect(lambda cur, _prev: self.show_file(cur.data(Qt.UserRole)) if cur else None)
        ll.addWidget(self.list, 1)
        split.addWidget(left)

        self.view = QTextBrowser()
        self.view.setObjectName("licenseText")
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_link)
        split.addWidget(self.view)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([280, 700])
        lay.addWidget(split, 1)

        self.message = QLabel()
        self.message.setObjectName("licensesMessage")
        self.message.setWordWrap(True)
        lay.addWidget(self.message)

        bottom = QHBoxLayout()
        self.reveal_button = QPushButton("Show in Finder")
        self.reveal_button.setToolTip("Open the folder that holds these files")
        self.reveal_button.setAutoDefault(False)  # Return in the filter box must not open Finder
        self.reveal_button.clicked.connect(self.reveal)
        bottom.addWidget(self.reveal_button)
        bottom.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        bottom.addWidget(box)
        lay.addLayout(bottom)

        self.current: str | None = None
        if self.files:
            self.list.setCurrentRow(0)
            self.list.setFocus()
        else:
            self.reveal_button.setEnabled(False)
            self.view.setPlainText(
                "No license files were found next to the app.\n\n"
                "They are in the source repository: LICENSE, THIRD_PARTY_NOTICES.md and the licenses/ folder.")

    # ------------------------------------------------------------------ viewing
    def show_file(self, path: str) -> None:
        self.current = path
        self.message.clear()
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            self.view.setPlainText(f"Could not read {path}:\n{e}")
            return
        if path.lower().endswith(".md"):
            self.view.document().setDefaultFont(QFontDatabase.systemFont(QFontDatabase.GeneralFont))
            self.view.setMarkdown(text)
        else:
            self.view.document().setDefaultFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
            self.view.setPlainText(text)
        self.view.moveCursor(self.view.textCursor().MoveOperation.Start)

    def select_file(self, name: str) -> bool:
        """Select the file called ``name`` (a basename); False if it is not in the list."""
        path = self._by_name.get(name)
        if path is None:
            return False
        if self.filter_edit.text():
            self.filter_edit.clear()
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == path:
                self.list.setCurrentRow(i)
                return True
        return False

    def _on_link(self, url: QUrl) -> None:
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        if not url.path() and url.fragment():
            self.view.scrollToAnchor(url.fragment())
            return
        name = os.path.basename(url.path())
        if not self.select_file(name):
            self.message.setText(f"“{url.path()}” is a document in {APP_NAME}'s source repository, not in the app.")

    def _apply_filter(self, text: str) -> None:
        q = text.lower().strip()
        for i in range(self.list.count()):
            it = self.list.item(i)
            p = it.data(Qt.UserRole)
            it.setHidden(bool(q) and q not in os.path.basename(p).lower() and q not in it.text().lower())

    def visible_names(self) -> list[str]:
        return [os.path.basename(self.list.item(i).data(Qt.UserRole))
                for i in range(self.list.count()) if not self.list.item(i).isHidden()]

    def reveal(self) -> None:
        if self.current:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.current)))
