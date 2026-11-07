"""Dark theme (original palette) with scalable text and visible focus states."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#14171c"
PANEL = "#1b1f26"
PANEL_2 = "#232833"
BORDER = "#323a47"
TEXT = "#dfe4ec"
TEXT_DIM = "#9aa4b2"
ACCENT = "#4fb3ff"
WARN = "#f0a050"
DANGER = "#ff6b6b"


def apply_theme(app: QApplication, font_scale: float = 1.0) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(PANEL))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL_2))
    pal.setColor(QPalette.ToolTipBase, QColor(PANEL_2))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(PANEL_2))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#0b0d10"))
    pal.setColor(QPalette.PlaceholderText, QColor(TEXT_DIM))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#5d6673"))
    app.setPalette(pal)
    f = app.font()
    base = f.pointSizeF() if f.pointSizeF() > 0 else 12.0
    f.setPointSizeF(base * font_scale)
    app.setFont(f)
    app.setStyleSheet(
        f"""
        QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
        QDockWidget::title {{ background: {PANEL}; padding: 4px 6px; border-bottom: 1px solid {BORDER}; }}
        QToolBar {{ background: {PANEL}; border: none; spacing: 2px; padding: 2px; }}
        QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 3px 5px; }}
        QToolButton:hover {{ background: {PANEL_2}; border-color: {BORDER}; }}
        QToolButton:checked {{ background: #24405a; border-color: {ACCENT}; }}
        QToolButton:focus, QPushButton:focus, QComboBox:focus, QLineEdit:focus,
        QDoubleSpinBox:focus, QSpinBox:focus, QSlider:focus, QCheckBox:focus, QListWidget:focus {{
            outline: none; border: 1px solid {ACCENT}; }}
        QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 8px; }}
        QPushButton:hover {{ border-color: {ACCENT}; }}
        QPushButton:disabled {{ color: #5d6673; }}
        QGroupBox {{ border: 1px solid {BORDER}; border-radius: 5px; margin-top: 10px; padding-top: 6px; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 8px; color: {TEXT_DIM}; }}
        QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
        QStatusBar QLabel {{ padding: 0 6px; }}
        QMenu::item:disabled {{ color: #5d6673; }}
        """
    )
