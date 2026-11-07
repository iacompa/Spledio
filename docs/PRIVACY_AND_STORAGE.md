# Privacy and storage

Spledio is a local desktop editor. It has **no network code, no telemetry, no
analytics, no accounts and no uploads**. Audio and projects stay on your
computer, in the places listed below.

## Network: what was checked (2026-11-06)

Static checks on `src/` (application code):

| Check | Result |
|---|---|
| `grep -rn -E "socket\|urllib\|http\|requests" src --include=*.py` | **1 match, not network code**: `src/spledio/ui/controller.py:602`, a docstring ("Wrap a provider so requests outside [0, n) return zeros"). This was re-checked after the new `ui/` modules (`spectrogram_view.py`, `icons.py`, `overview.py`, ...) appeared. |
| Same pattern over all files in `src/` (including non-Python) | 4 additional matches, all XML namespace URIs (`http://www.w3.org/2000/svg`, `.../1999/xlink`) in the four icon SVGs in `src/spledio/resources/icons/app/`. These are identifiers, never fetched. |
| `grep -rn -E "QtNetwork\|QNetwork\|QUrl\|openUrl\|webbrowser\|subprocess\|ftplib\|smtplib\|xmlrpc\|asyncio\|telemetry\|analytics\|ssl" src --include=*.py` | 1 match: the About-box text in `ui/main_window.py:919` ("No audio is uploaded, no analytics, no telemetry, no account."). |
| Qt modules imported by `src/` | QtCore, QtGui, QtWidgets only. No QtNetwork, QtWebEngine or QtMultimedia. |
| Native libraries loaded after importing every `spledio.*` module (dyld image list) | QtCore, QtGui, QtWidgets, QtDBus, libpyside6/libshiboken6, libsndfile, scipy's GCC runtime. **No QtNetwork and no FFmpeg.** |

These are static checks plus a check of which libraries load. No runtime
traffic capture was done. The operating system's own file dialogs and
services (for example macOS iCloud Drive inside an Open dialog) are outside
Spledio's control.

## Where Spledio stores data

Paths on macOS were resolved on this machine with Qt 6.11.3
(`QCoreApplication.setOrganizationName('Spledio Project')`,
`setApplicationName('Spledio')`, as `spledio/app.py` does). The username is
shown as `~`.

| What | Where (macOS, verified) | Lifetime |
|---|---|---|
| **Projects** (`.spledio`, a ZIP container) | Wherever you save them. A normal project links to your source audio by relative and absolute path plus SHA-256. A *portable* project embeds byte-exact copies of the source files. | Until you delete them |
| Atomic-save temporary file | `.spledio-XXXXXXXX.partial` in the **same folder** as the project being saved | Renamed to the project on success, deleted on failure. It can remain only if the process is killed mid-save. |
| **Exports** (WAV/FLAC) | Wherever you choose. During writing: `.<name>.<random>.partial<ext>` in the same folder. | Renamed on success. A leftover is possible only after a kill or crash. |
| **Autosave / crash recovery** | `QStandardPaths.AppDataLocation` + `/Recovery` = **`~/Library/Application Support/Spledio Project/Spledio/Recovery/`**. Files: `<project-id>.spledio` (a non-portable project: your edited audio blocks, linked to the source files) and `<project-id>.json` (original project path, project name, timestamp). If Qt returns no location, the fallback is `~/.spledio/Recovery/`. | The folder is created at launch, when Spledio checks for recoveries. Files are written every 120 s while there are unsaved changes, and deleted on a clean save, a clean quit, or when you choose "No" at the recovery prompt. They remain after a crash, which is their purpose. |
| **Settings** (`QSettings("Spledio Project", "Spledio")`, native format; used by `ui/main_window.py` and `ui/shortcuts_dialog.py`) | **`~/Library/Preferences/com.spledio-project.Spledio.plist`**. Keys: `last_dir` (last folder used), `window_state`, `window_geometry`, and `shortcuts/<action-id>` (custom keyboard shortcuts as JSON lists). | Until removed. Resetting a shortcut removes its key. |
| **Undo patches** (spill to disk when undo data exceeds the RAM budget: 1/16 of the machine's RAM, 256–768 MiB) | `$TMPDIR/spledio-patches-XXXXXXXX/p<N>.npy`. On this machine `$TMPDIR` is `/var/folders/4t/xwt84fs5283900gvslptb61r0000gn/T/`; it is per user and differs per machine. | Created with each editing session, deleted when the session or project is closed (`PatchStore.close()`). It can remain after a crash or kill. Two empty leftover `spledio-patches-*` folders from earlier development runs exist on this machine. |
| **Extracted embedded assets** (portable projects) | `$TMPDIR/spledio-assets-XXXXXXXX/` | Created on every project open, deleted when the session closes or the open fails. It can remain after a crash. |

Not used by Spledio's code: `QStandardPaths.CacheLocation`
(`~/Library/Caches/Spledio Project/Spledio`), `AppConfigLocation`, log files
(the one `logging` logger has no file handler), the clipboard and the
keychain.

**Not verified:**

- For a future bundled `.app`, macOS may also create
  `~/Library/Saved Application State/<bundle-id>.savedState`.
- The settings file name is expected to stay
  `com.spledio-project.Spledio.plist`, because organization and application
  names are passed explicitly, but this has not been checked in a frozen app.
- Windows and Linux locations are **not verified**. With Qt's defaults they
  would be under `%APPDATA%\Spledio Project\Spledio\Recovery` with the
  registry key `HKCU\Software\Spledio Project\Spledio` (Windows), and
  `~/.local/share/Spledio Project/Spledio/Recovery` with
  `~/.config/Spledio Project/Spledio.conf` (Linux).

## Removing all Spledio data (macOS)

Quit Spledio first, so that temporary folders are not in use. Then:

```sh
# autosave / recovery (and the empty parent folder)
rm -rf ~/Library/Application\ Support/Spledio\ Project
# settings: ask cfprefsd to drop its cached copy, then remove the file
defaults delete com.spledio-project.Spledio 2>/dev/null
rm -f ~/Library/Preferences/com.spledio-project.Spledio.plist
# leftovers from crashed sessions
rm -rf "$TMPDIR"/spledio-patches-* "$TMPDIR"/spledio-assets-*
# only if the fallback was ever used
rm -rf ~/.spledio
```

Your own project files (`*.spledio`) and exported audio are wherever you
saved them. Delete those yourself if you want them gone. After a crash
during save or export, also look for hidden `.spledio-*.partial` or
`.*.partial.wav` / `.*.partial.flac` files next to them.
