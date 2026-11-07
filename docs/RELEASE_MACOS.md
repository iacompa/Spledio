# Releasing Spledio for macOS: signed, notarized, downloadable

> **Status: plan + script, NOT RUN YET.** No `.app` or `.dmg` has been built, signed or notarized.
> `scripts/release_macos.sh` has passed `bash -n` (with macOS's bash 3.2) and `shellcheck` only.
> Expect fixes on the first real run, and record them here.

Why this matters: macOS Gatekeeper blocks apps downloaded from the internet unless they are **signed with a
Developer ID** and **notarized by Apple**. With both, users double-click and the app opens normally.

## One-time setup (owner of the release)

1. **Join the Apple Developer Program** at developer.apple.com, as an individual or an organization.
   It is a paid yearly membership. The name on the membership is what macOS shows as the developer.
2. **Create a "Developer ID Application" certificate.** In Xcode: *Settings → Accounts → Manage
   Certificates → + → Developer ID Application*. Or create it in the developer portal and install it in the
   login keychain. Check it is there:
   ```bash
   security find-identity -v -p codesigning
   ```
   You should see `Developer ID Application: Your Name (TEAMID)`. Copy that exact text.
3. **Store notarization credentials** in the keychain once. Use an app-specific password from
   account.apple.com → *Sign-In and Security → App-Specific Passwords*:
   ```bash
   xcrun notarytool store-credentials spledio-notary --apple-id "you@example.com" --team-id TEAMID
   ```
   It asks for the app-specific password. The script refers to these credentials as `spledio-notary` and never
   sees the password.
4. **Decide the bundle identifier before the first release.** Today it is `org.spledio.Spledio`
   (`APP_ID` in `src/spledio/branding.py`). If you own a domain, use its reverse form, for example
   `com.yourdomain.spledio`. Changing it after release makes macOS treat it as a different app.
   Pass `BUNDLE_ID=...` to the script and update `branding.py` to match.
5. **Install a python.org CPython 3.14** (macOS installer from python.org) to build with. The Homebrew Python
   on the development Mac targets macOS 26+, so an app frozen from it would not run on older macOS.
   See [PACKAGING.md](PACKAGING.md).

## Every release

```bash
SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
NOTARY_PROFILE=spledio-notary \
PYTHON=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14 \
scripts/release_macos.sh
```

What the script does, in order:

1. Creates or updates a separate build venv (`.venv-build`), installs the pinned requirements and PyInstaller,
   and records `dist-build-freeze.txt`.
2. Runs the test suite (set `SKIP_TESTS=1` to skip).
3. Freezes `Spledio.app` with PyInstaller in **onedir** mode. Never use onefile: Qt and libsndfile must
   stay replaceable files under the LGPL.
4. Sets the version in `Info.plist` and copies `LICENSE`, `THIRD_PARTY_NOTICES.md` and `licenses/` into the bundle.
5. Checks the bundle: no FFmpeg, WebEngine, Pdf or Multimedia; libsndfile and PortAudio present; `--version` runs.
6. Signs everything inside-out with the hardened runtime and a secure timestamp (`packaging/entitlements.plist`),
   then verifies with `codesign --verify --deep --strict`.
7. Builds `dist/Spledio-macOS-arm64.dmg` (app + Applications shortcut) and signs it.
8. Submits it to Apple's notary service and waits. If Apple rejects it, the script downloads the log to
   `dist/notarization-log.json` and stops.
9. Staples the ticket, validates it, and asks Gatekeeper (`spctl`) to assess the DMG.
10. Writes `dist/Spledio-macOS-arm64.dmg.sha256`.

Then:

```bash
git tag v0.1.0
```

```bash
git push origin v0.1.0
```

```bash
gh release create v0.1.0 dist/Spledio-macOS-arm64.dmg dist/Spledio-macOS-arm64.dmg.sha256 --title "Spledio 0.1.0" --notes-file release-notes.md
```

Keep the asset name `Spledio-macOS-arm64.dmg`: the website's download button links to
`releases/latest/download/Spledio-macOS-arm64.dmg`.

Release notes should state: version, minimum macOS, Apple Silicon only, SHA-256, what changed, known issues,
and "Source code for this release: the tag `vX.Y.Z` in this repository (GPL-3.0-only)".

## Test before publishing

* Copy the DMG to a **clean Mac or a new macOS user account**, download it through a browser so it gets the
  quarantine flag, open it, drag to Applications, and launch. It must open with no warning beyond the normal
  "downloaded from the internet" confirmation.
* Open audio, play (**this exercises the PortAudio/cffi callback under the hardened runtime**), adjust an
  area, preview, apply, undo, export WAV and FLAC, save, quit, reopen.
* If it crashes when playback starts, add `com.apple.security.cs.allow-unsigned-executable-memory` to
  `packaging/entitlements.plist` (the comment there explains), rebuild, and note it here.

## A local test build without a certificate

```bash
UNSIGNED=1 scripts/release_macos.sh
```

This produces an ad-hoc signed DMG for checking the packaging on your own Mac. It is **not notarized**:
other Macs will block it, so never publish it.

## Troubleshooting

| Symptom | Likely fix |
|---|---|
| `signing identity not found` | Run `security find-identity -v -p codesigning` and pass the exact string. The certificate must be in the login keychain together with its private key. |
| Notarization "Invalid": *not signed with a valid Developer ID* / *no secure timestamp* | A nested binary was missed. Check the log for its path, and make sure it is signed in step 6 (frameworks are signed after their contents). |
| Notarization "Invalid": *hardened runtime not enabled* | The binary was signed without `--options runtime`. Same as above. |
| App opens on the build Mac but not elsewhere | It was built with the Homebrew Python (macOS 26+ only). Use a python.org Python. |
| "Spledio is damaged and can't be opened" | The DMG or app was modified after signing, or stapling failed. Rebuild. Do not edit files inside the signed app. |

## Licensing notes for distribution

* GPL-3.0-only allows selling and distributing binaries. Recipients must be able to get the
  **corresponding source** for the exact version. Keep the repository public, tag every release, and keep the
  build scripts (`scripts/release_macos.sh`, `packaging/`) in the repository.
* Developer ID signing and notarization are fine with the GPL. They do not stop users from modifying and
  running their own build, which they sign ad hoc.
* **Do not distribute through the Mac App Store.** Its terms conflict with the GPL.
* Qt/PySide6 are LGPL-3.0: ship them as separate frameworks (onedir build), include their license and a
  link to their source (already in `THIRD_PARTY_NOTICES.md` and `licenses/`).
