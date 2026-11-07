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

- [x] **Make the GitHub repository public** (2026-11-07, GPL-3.0-only). A public repository is also the
      simplest way to meet the GPL source requirement.
- [x] **Apple Developer Program** membership and a **Developer ID Application** certificate
      (IA COMPA LLC, team L98358T6WZ). See [RELEASE_MACOS.md](RELEASE_MACOS.md).
- [x] **First unsigned local build** with `scripts/release_macos.sh`: `.app` and `.dmg` built and passed the
      smoke test (2026-11-07; see [RELEASE_MACOS.md](RELEASE_MACOS.md#first-local-build-2026-11-07)).
- [x] **Build with the python.org Python** (`scripts/get_build_python.sh`, nothing to install). The app
      now runs on macOS 15 or later.
- [x] **License texts in the bundle** (2026-11-07): every notice is in `Contents/Resources/licenses/` and in
      Help ▸ Licenses and Notices (`THIRD_PARTY_NOTICES.md`, "Status of the notices"). Open: libquadmath's GCC version.
- [x] **First signed and notarized build**, made with `scripts/release_macos.sh` (2026-11-07; Gatekeeper
      accepts the downloaded DMG; see [RELEASE_MACOS.md](RELEASE_MACOS.md#first-signed-and-notarized-build-2026-11-07)).
- [ ] **Test the DMG on a clean Mac** (or a new user account): download from the release URL, open, edit,
      play, export, save and reopen.
- [x] **Minimum macOS: 15.0** (Sequoia). The release script reads it from the app's binaries and prints
      it; it is on the page and in the user manual. Put it in each release's notes.
- [x] **Make the repository public** (the page and the app link to it for the source code).
- [x] **Publish the GitHub release** (tag `v1.1`, 2026-11-07; then `v1.3`, the same day) with the DMG, its `.sha256` file, `Spledio-1.1-source.tar.gz`,
      the files in `dist/third-party-sources/` and release notes. The source archives are required by the GPL/LGPL.
- [x] **Page live** at [iacompa.com/apps/spledio](https://iacompa.com/apps/spledio), Spanish at
      `/es/aplicaciones/spledio`, with privacy, terms and support pages (2026-11-07). It lives in the website
      repository `iacompa/ia-compa-website`: the `spledio` entry in `src/content/apps.ts` (and `spanish/apps.ts`,
      `app-legal.ts`), the walkthrough in `src/components/apps/spledio-showcase.tsx` and the screenshots in
      `public/apps/spledio/v1-3/`. Merged as PR #114 (`6dddd84`) and deployed to production from that commit.
      All eight pages and every link on them were checked on the live site. `website/spledio/index.html` here
      stays as a standalone copy.

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
4. Tag `vX.Y.Z`, push, and create the GitHub release with `Spledio-macOS-arm64.dmg`, its `.sha256` file,
   `Spledio-X.Y.Z-source.tar.gz` and `dist/third-party-sources/*` (the script prints the `gh release create` line).
5. Update the website: the iacompa.com page links the release by its tag (download, source archive,
   license) and shows the version. In `iacompa/ia-compa-website`, change the `spledio` entry in
   `src/content/apps.ts`, its Spanish copy and `src/content/spledio.test.ts` to the new tag, and
   retake the screenshots if the window changed. Run its tests and build, open a PR and merge it. The site
   does **not** deploy `main` automatically (`vercel.json` turns it off): deploy the merge commit to production
   from a clean checkout with the Vercel CLI (`vercel deploy --prod`, project `ia-compa-website`), then open
   `/apps/spledio` and click every link. `releases/latest/download/Spledio-macOS-arm64.dmg` always gives the
   newest DMG in the meantime. Update the manual only if features changed.
