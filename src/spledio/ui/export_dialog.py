"""Export dialog: scope, format/bit depth, dither, clipping policy, sample rate.

Before an integer export the dialog measures the real rendered peak and, if it
exceeds full scale, requires an explicit choice (reduce level / limiter /
export float / hard clip). Nothing is silently clipped or normalised.
"""

from __future__ import annotations

import math
import os

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from spledio.audio.export import CLIP_POLICIES, EXPORT_FORMATS, ExportSettings, analyze_peaks

SUBTYPE_LABELS = {"PCM_16": "16-bit integer PCM", "PCM_24": "24-bit integer PCM", "FLOAT": "32-bit float"}
CLIP_LABELS = {
    "error": "Stop and ask (default)",
    "reduce_level": "Reduce whole-file level to −0.1 dBFS peak",
    "limit": "Basic peak limiter (−0.1 dBFS ceiling)",
    "clip": "Hard clip (explicit choice)",
}


class ExportDialog(QDialog):
    def __init__(self, session, parent=None, layer_ids: list[str] | None = None, default_dir: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export audio")
        self.session = session
        self.layer_ids = layer_ids or []
        lay = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

        self.scope = QComboBox()
        self.scope.addItem("Entire audible mix", "mix")
        if self.layer_ids:
            names = ", ".join(session.project.layer(i).name for i in self.layer_ids)
            self.scope.addItem(f"Selected layer(s): {names}", "layers")
        self.scope.addItem("All layers individually (stems, one file each)", "stems")
        if session.selection is not None:
            self.scope.addItem("Selected spectral content (aligned, full length)", "selection")
        if self.layer_ids:
            self.scope.setCurrentIndex(1)
        form.addRow("What", self.scope)

        self.range = QComboBox()
        self.range.addItem("Whole project", None)
        if session.selection is not None:
            b = session.selection.bounds(session.project.duration, session.project.nyquist)
            if b is not None:
                self.range.addItem(f"Selection time range {b[0]:.3f}–{b[1]:.3f} s", (b[0], b[1]))
        form.addRow("Time range", self.range)

        self.fmt = QComboBox()
        for f in EXPORT_FORMATS:
            self.fmt.addItem(f, f)
        form.addRow("Format", self.fmt)
        self.subtype = QComboBox()
        form.addRow("Bit depth", self.subtype)
        self.fmt.currentIndexChanged.connect(self._fill_subtypes)
        self._fill_subtypes()

        self.dither = QComboBox()
        self.dither.addItem("TPDF dither (recommended for integer formats)", "tpdf")
        self.dither.addItem("No dither", "none")
        form.addRow("Dither", self.dither)

        self.clip = QComboBox()
        for p in CLIP_POLICIES:
            self.clip.addItem(CLIP_LABELS.get(p, p), p)
        form.addRow("If peaks exceed 0 dBFS", self.clip)

        self.rate = QComboBox()
        self.rate.addItem(f"Keep project rate ({session.sample_rate} Hz)", None)
        for r in (44100, 48000, 88200, 96000):
            if r != session.sample_rate:
                self.rate.addItem(f"Convert to {r} Hz (polyphase resampling)", r)
        form.addRow("Sample rate", self.rate)

        prow = QHBoxLayout()
        self.path = QLineEdit()
        base = (session.project.name or "export") + "-edited.wav"
        self.path.setText(os.path.join(default_dir or os.path.expanduser("~"), base))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        prow.addWidget(self.path, 1)
        prow.addWidget(browse)
        pw = QWidget()
        pw.setLayout(prow)
        form.addRow("File", pw)

        self.peak = QLabel()
        self.peak.setWordWrap(True)
        lay.addWidget(self.peak)
        note = QLabel("The original source file is never overwritten. Exporting a lossy import as WAV does not make it lossless.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9aa4b2")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.scope.currentIndexChanged.connect(self._measure)
        self._measure()

    def _fill_subtypes(self):
        cur = self.subtype.currentData()
        self.subtype.clear()
        for st in EXPORT_FORMATS[self.fmt.currentData()]:
            self.subtype.addItem(SUBTYPE_LABELS.get(st, st), st)
        idx = self.subtype.findData(cur if cur else "FLOAT")
        self.subtype.setCurrentIndex(max(0, idx))
        if self.path.text() if hasattr(self, "path") else False:
            ext = ".flac" if self.fmt.currentData() == "FLAC" else ".wav"
            root = os.path.splitext(self.path.text())[0]
            self.path.setText(root + ext)

    def _browse(self):
        flt = "FLAC (*.flac)" if self.fmt.currentData() == "FLAC" else "WAV (*.wav)"
        p, _ = QFileDialog.getSaveFileName(self, "Export to", self.path.text(), flt,
                                           options=QFileDialog.DontConfirmOverwrite)
        if p:
            self.path.setText(p)

    def render_data(self):
        """What the export will write (rendered block by block as it is measured)."""
        scope = self.scope.currentData()
        return self.session.export_source(scope if scope in ("layers", "selection") else "mix", self.layer_ids)

    def _measure(self):
        try:
            rep = analyze_peaks(self.render_data()) if self.scope.currentData() != "stems" else None
        except Exception as e:  # noqa: BLE001
            self.peak.setText(f"Could not measure: {e}")
            return
        if rep is None:
            self.peak.setText("Stems: each layer is exported at its own gain/polarity (mute/solo ignored).")
            return
        db = rep.peak_dbfs
        txt = f"Measured sample peak: {db:+.2f} dBFS" if math.isfinite(db) else "Measured sample peak: silence"
        if rep.clips_integer:
            txt += f" – {rep.samples_over} samples exceed full scale. Integer formats need a choice below, or export 32-bit float."
            self.peak.setStyleSheet("color:#f0a050")
        else:
            self.peak.setStyleSheet("")
        self.peak.setText(txt + " (true peak not measured)")

    def settings(self, overwrite=False) -> ExportSettings:
        return ExportSettings(format=self.fmt.currentData(), subtype=self.subtype.currentData(),
                              dither=self.dither.currentData(), clip_policy=self.clip.currentData(),
                              sample_rate=self.rate.currentData(), overwrite=overwrite)

    def _accept(self):
        p = self.path.text().strip()
        if not p:
            return
        self.overwrite = False
        if os.path.exists(p) and self.scope.currentData() != "stems":
            r = QMessageBox.question(self, "Overwrite?", f"{os.path.basename(p)} already exists. Overwrite it?")
            if r != QMessageBox.Yes:
                return
            self.overwrite = True
        self.accept()

    def request(self) -> dict:
        return {"path": self.path.text().strip(), "scope": self.scope.currentData(), "layer_ids": self.layer_ids,
                "time_range": self.range.currentData(), "settings": self.settings(getattr(self, "overwrite", False))}
