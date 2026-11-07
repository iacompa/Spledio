# Website plan: Spledio in the "Apps" section

Goal: one page where people can **download** Spledio for Mac, learn **how to use it**, and **view the source on
GitHub**, with the license stated correctly.

## What is ready in this repository

| File | Use on the website |
|---|---|
| [`website/spledio/index.html`](../website/spledio/index.html) + `icon.png` | Ready-made app page: download button, install steps, first edit, keys, license, help. Self-contained (no external fonts, scripts or trackers), light/dark aware. Copy it into the site's Apps section, or paste its sections into the site's own template. |
| [`docs/USER_MANUAL.md`](USER_MANUAL.md) | The full instruction manual. GitHub renders it, and the app page links to it there. It can also be converted into a site page (any Markdown → HTML step). |
| [`docs/RELEASE_MACOS.md`](RELEASE_MACOS.md) + `scripts/release_macos.sh` | How to produce the signed, notarized download. |

## Page structure (Apps → Spledio)

1. **Hero**: icon, name, one-line tagline, **Download for Mac** (primary), **View source on GitHub**, **User manual**.
2. **Meta line**: license, Apple Silicon requirement, link to release notes (version, minimum macOS, SHA-256).
3. **What it does**: 4 short cards (see / draw / change your mind / private).
4. **Install**: 3 steps, plus "only download from here or GitHub releases" and how to check the checksum.
5. **Your first edit**: 5 steps that use the new − / + level bar.
6. **Handy keys**: 6 rows, then a link to the full manual.
7. **License and source code**: GPL-3.0-only in plain words, with links to the source, LICENSE and third-party notices.
8. **Help**: GitHub Issues.

## Links (stable across releases)

| Link | URL |
|---|---|
| Download (always newest) | `https://github.com/iacompa/Spledio/releases/latest/download/Spledio-macOS-arm64.dmg` |
| Release notes / checksum | `https://github.com/iacompa/Spledio/releases/latest` |
| Source | `https://github.com/iacompa/Spledio` |
| Manual | `https://github.com/iacompa/Spledio/blob/main/docs/USER_MANUAL.md` |
| License | `https://github.com/iacompa/Spledio/blob/main/LICENSE` |
| Issues | `https://github.com/iacompa/Spledio/issues` |

The download link works for every future release **as long as the DMG asset keeps exactly the name
`Spledio-macOS-arm64.dmg`**. The release script uses that name. The page then never needs editing for a new
version.

Hosting the DMG on GitHub Releases (rather than on the website server) keeps one source of truth, gives
a public checksum next to the file, and puts the binary right beside the source it was built from, which
the GPL requires (see below).

## Must happen before the page goes live

- [ ] **Make the GitHub repository public.** It is private today, so every link above returns 404 for
      visitors. A public repository is also the simplest way to meet the GPL source requirement.
- [ ] **Apple Developer Program** membership (paid yearly by Apple) and a **Developer ID Application**
      certificate. Without them macOS blocks the downloaded app. See [RELEASE_MACOS.md](RELEASE_MACOS.md).
- [x] **First unsigned local build** with `scripts/release_macos.sh`: `.app` and `.dmg` built and passed the
      smoke test (2026-11-07; see [RELEASE_MACOS.md](RELEASE_MACOS.md#first-local-build-2026-11-07)).
- [x] **Build with the python.org Python** (`scripts/get_build_python.sh`, nothing to install). The app
      now runs on macOS 15 or later.
- [ ] **Add the missing license texts** to the bundle (list in `THIRD_PARTY_NOTICES.md`, "Gaps to close").
- [ ] **First signed and notarized build**, made with `scripts/release_macos.sh`.
- [ ] **Test the DMG on a clean Mac** (or a new user account): download from the release URL, open, edit,
      play, export, save and reopen.
- [x] **Minimum macOS: 15.0** (Sequoia). The release script reads it from the app's binaries and prints
      it; it is on the page and in the user manual. Put it in each release's notes.
- [ ] **Publish the GitHub release** (tag `v0.1.0`) with the DMG, its `.sha256` file and release notes.
- [ ] Put `website/spledio/` on the site and click every link once.

## License wording (use as-is)

> Spledio is free software under the **GNU General Public License, version 3 only (GPL-3.0-only)**.

* The SPDX identifier is `GPL-3.0-only`. It is already set in `pyproject.toml` and `LICENSE`.
* "Only" means later GPL versions do not apply automatically. (The alternative, `GPL-3.0-or-later`, would allow them.)
* **Source for every binary:** whoever receives the DMG must be able to get the matching source. Tag
  every release in the public repository, attach the DMG to that tag's release, and keep it there.
  Also keep the PyInstaller `.spec` and the build scripts in the repository, because they are part of the source.
* **Signing and notarization are compatible with the GPL.** Signing with a Developer ID does not restrict
  users. They can still build and run their own modified version, signed ad hoc.
* **Do not submit to the Mac App Store.** Its terms of use conflict with the GPL. Distribute the DMG
  directly, as planned.
* Bundled libraries keep their own licenses. Qt/PySide6 is LGPL-3.0, which is why the app is built as a
  folder bundle and not a single file. The notices file lists them all and ships inside the app.

## Privacy statement for the page

"No account, no uploads, no internet connection. Your audio never leaves your Mac." This is accurate:
the app contains no networking code and no telemetry. The page itself loads nothing from third parties.
Keep it that way if analytics are ever added to the site, or say so on the page.

## Each new release

1. Bump the version (`pyproject.toml` and `spledio/__init__.py`) and update release notes / the changelog.
2. Run the tests (`.venv/bin/python -m pytest`) and the real-app checks in [TESTING.md](TESTING.md).
3. `scripts/release_macos.sh`, which builds, signs, notarizes, staples and verifies the DMG.
4. Tag `vX.Y.Z`, push, and create the GitHub release with `Spledio-macOS-arm64.dmg` and its `.sha256` file.
5. The website needs no change, because the download link always points to the newest release.
   Update the manual only if features changed.
