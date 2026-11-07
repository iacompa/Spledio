"""Application entry point: ``python -m spledio [file]`` or the ``spledio`` script."""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from spledio import __version__
    from spledio.branding import APP_ID, APP_NAME, APP_ORG

    ap = argparse.ArgumentParser(prog="spledio", description=f"{APP_NAME} – free local spectral audio editor")
    ap.add_argument("file", nargs="?", help="audio file or project to open")
    ap.add_argument("--font-scale", type=float, default=float(os.environ.get("SPLEDIO_FONT_SCALE", "1.0")),
                    help="scale all interface text (e.g. 1.25)")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    ap.add_argument("--smoke-test", action="store_true",
                    help="open FILE, check panels / audio libraries / an edit / export / save+reopen, print a "
                         "report and quit (exit code 1 on any failure); used to verify packaged builds")
    args = ap.parse_args(argv)
    if args.smoke_test and not args.file:
        ap.error("--smoke-test needs an audio FILE")

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(APP_ORG)
    QApplication.setApplicationVersion(__version__)
    QApplication.setDesktopFileName(APP_ID)
    app = QApplication.instance() or QApplication(sys.argv[:1])

    from spledio.ui.main_window import MainWindow
    from spledio.ui.theme import apply_theme

    apply_theme(app, args.font_scale)
    try:
        from spledio.ui.icons import app_icon

        ic = app_icon()
        if ic is not None:
            app.setWindowIcon(ic)
    except Exception:
        pass
    win = MainWindow()
    win.show()
    if args.smoke_test:
        from spledio import smoke_test

        smoke_test.run(app, win, args.file)  # no recovery prompt / autosave: nothing modal, nothing left behind
        return app.exec()
    try:
        from spledio.persistence.recovery import offer_recovery, start_autosave

        offer_recovery(win)
        start_autosave(win)
    except Exception as e:  # recovery is a safety net, never a launch blocker
        print(f"[{APP_NAME}] recovery unavailable: {e}", file=sys.stderr)
    if args.file:
        QTimer.singleShot(0, lambda: win.open_path(os.path.abspath(args.file)))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
