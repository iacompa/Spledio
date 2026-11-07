# Spledio app icon: design work

The app ships **concept E**. The files the app uses live in
`src/spledio/resources/icons/app/`. `docs/BRANDING.md` describes them, the
palette and how to regenerate the rasters.

## Files here

| Path | What it is |
| --- | --- |
| `tools/gen_icons.py` | Stdlib-only generator for every concept. All geometry and colours live here. `--install` writes the app's `spledio-icon.svg` and `spledio-wordmark.svg` (concept E). |
| `tools/contact_sheet.py` | Renders the concept SVGs with QtSvg, the renderer the app uses, to `variant-*/Spledio-*-1024.png`, and builds `contact-sheet.png` (256, 128, 32 and 16 px on light and dark, small sizes magnified). |
| `variant-*/` | Each concept's SVG and its 1024 px render. |
| `variant-OLD/` | The previous icon (before 2026-11-07), kept for comparison. |
| `contact-sheet.png` | OLD, D and E side by side. |

```sh
python3 design/icon/tools/gen_icons.py                 # concept SVGs
python3 design/icon/tools/gen_icons.py --install       # the app's master icon and wordmark
QT_QPA_PLATFORM=offscreen .venv/bin/python design/icon/tools/contact_sheet.py OLD D E
.venv/bin/python scripts/build_icons.py                # PNG, ICNS and ICO for the app
```

The contact sheet shows the master art at 32 and 16 px. The app itself uses
the hand-drawn, pixel-aligned `spledio-icon-small.svg` at those sizes.

## Concepts

| Concept | Idea | Outcome |
| --- | --- | --- |
| A Buddy | A cute sound blob with a face, picked out by a dashed selection on a striped "picture of sound". | Too cartoony for a pro audio tool. |
| B Marker | A cartoon marker draws a loop around one glowing sound. | Too cartoony, and reads less clearly as sound. |
| C Hush | A glossy candy wave, turned down inside a selection. | Too cartoony. |
| D Spectrum, navy | The previous icon's idea redrawn so the bands look like sound: round attack, vibrato, fading decay. The selection turns the ringing 2nd harmonic down. | Alternative. |
| **E Spectrum, indigo** | D on a deeper indigo-violet tile, closer to the soft gradient tiles of the other apps. | **Shipped.** |

Feedback that shaped it: A to C followed the family's cartoony style, but the
owner wanted something more professional, close to the previous icon. D and
E keep the old motif and fix what made it look plain: straight, equal bars
read like lines of text, not sound.

## Rules

- QtSvg ignores `clip-path`, so every shape must stay inside the tile by its
  own geometry. `inside()` and `band()` in the generator handle that.
- No text and no fonts in the icon. No other product's marks or colours.
- Original artwork, licensed with the project under GPL-3.0-only.
