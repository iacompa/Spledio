# Branding: app icon and marks

The visible product name lives in `src/spledio/branding.py` and may change.
The app icon and the mono mark contain **no text**, so a rename only affects
`spledio-wordmark.svg`. The file names keep the `spledio-` prefix as a
stable internal identifier.

## Concept

The icon is a small spectrogram: time runs left to right, frequency bottom
to top, and colour stands for level (ink = silence, then blue, violet,
magenta, coral, amber, pale gold = loudest). It shows a harmonic stack:
the loud, warm fundamental at the bottom and quieter, cooler upper
harmonics above it, spaced closer together and fading out sooner, as they
do on a log-frequency display.

The 2nd harmonic is a ringing resonance. A selection box with corner handles
covers a narrow frequency band over a stretch of time. Inside the box the
band is scooped thin and cooled from coral to violet: the resonance has been
reduced. That is the app's core workflow, shown in one picture.

The tile follows the macOS Big Sur+ icon grid: a 1024 px canvas, an
824 x 824 continuous-corner ("squircle") tile at (100, 100) with corner
radius 185, and a soft drop shadow in the margin. Windows and Linux use the
same art.

At 16 and 32 px the master is too detailed, so `spledio-icon-small.svg`
redraws the motif with three thick, flat bands and a 1 px selection outline.
Every edge sits on a 64-unit grid, which is exactly 1 px at 16 px and 2 px at
32 px, so both sizes render crisply.

## Palette

| Role | Hex |
| --- | --- |
| Tile (top, middle, bottom) | `#18224F`, `#0D1335`, `#090C24` |
| Tile glow behind the bands | `#3A3CB0` (20% opacity) |
| Fundamental, amber | `#FFC95C`, `#FFB04A`, `#FFA145`; hot core `#FFF6DE`; glow `#FF6A3A` |
| Resonance, coral | `#FF8A5C`, `#FF775C`, `#FF7468`; hot core `#FFE6D2`; glow `#F2507E` |
| Reduced resonance, violet | `#9658EC` |
| 3rd harmonic, rose | `#EC5CA2`, `#DC4F9C` |
| 4th harmonic, purple | `#AE58E6`, `#9A52DE` |
| 5th harmonic, violet | `#7C66F4`, `#6E60EE` |
| 6th harmonic, blue | `#5A82F4`, `#5078EE` |
| Selection outline and handles | `#EAF8FF`; outer glow `#7FDBFF`; fill `#BFEFFF` at 7% |
| Small variant: tile | `#1E2A5C` to `#0A0E2A` |
| Small variant: bands | top `#D77BFF` to `#8A6CFF`; resonance `#FF8A5C` to `#FF5E78`; reduced `#47358F`; fundamental `#FFC95C` to `#FFA645` to `#FF7A57` |
| Small variant: selection | `#F2FAFF` |
| Wordmark letters | `#141B3D` (ink, default); use `#F2F5FF` on dark backgrounds |

## Files

All of these live in `src/spledio/resources/icons/app/`.

| File | Purpose |
| --- | --- |
| `spledio-icon.svg` | Master icon, 1024 x 1024 viewBox. Used for 48 px and up. |
| `spledio-icon-small.svg` | Simplified, pixel-aligned icon for 16 and 32 px. |
| `spledio-mark-mono.svg` | Single-colour glyph on a 24 x 24 grid, without the tile. Uses `currentColor`, for toolbars and the About box. |
| `spledio-wordmark.svg` | Horizontal lockup: the icon plus the name as hand-drawn monoline letter paths (no font). The letters use `currentColor`; the root `color="#141B3D"` sets the default, and CSS `color` overrides it when the SVG is inlined. |
| `png/icon_{16,32,48,64,128,256,512,1024}.png` | Raster renders. 16 and 32 come from the small variant; the rest come from the master. |
| `spledio.icns` | macOS icon, built by `iconutil` from a full `.iconset` that includes the @2x entries. |
| `spledio.ico` | Windows icon with 16, 32, 48, 64, 128 and 256 px PNG-compressed entries (Vista and later). |

The SVGs are hand-written and self-contained: no external references, no
embedded raster images and no fonts. The master uses linear and radial
gradients and `feGaussianBlur` filters, which `QSvgRenderer` (in the PySide6 6.10+ the project requires) and
browsers render.

## Regenerating the rasters

After editing either icon SVG, run this from the project root:

```sh
.venv/bin/python scripts/build_icons.py
```

The script renders with `PySide6.QtSvg.QSvgRenderer` into transparent,
antialiased `QImage`s. Master sizes are supersampled (up to 2048 px) and
then downscaled; the small variant is rendered 1:1. The script writes the
PNGs, builds `spledio.icns` with `iconutil` from a temporary `.iconset`, and
writes `spledio.ico` with its own ICONDIR/ICONDIRENTRY writer, because Qt's
ICO writer stores only one size. When `iconutil` is unavailable (for example
on Linux CI), it writes the ICNS container itself from PNG chunks instead.

To check the results:

```sh
iconutil -c iconset src/spledio/resources/icons/app/spledio.icns -o /tmp/check.iconset
file src/spledio/resources/icons/app/spledio.ico
```

The mono mark and the wordmark are used as vector files directly, so they
have no raster outputs.

## Notes

- Starting with macOS 26, Apple's preferred format is an Icon Composer
  `.icon` file. Recent macOS versions may draw legacy `.icns` icons inside a
  system tile. This project does not include a `.icon` asset yet.
- Do not add the product name, or any other text, to the icon artwork.

## Originality and licence

All of this artwork (the icon, the small variant, the mono mark and the
wordmark letterforms) was drawn from scratch for this project. It does not
copy or imitate the logo, colours or iconography of any other product, and
it contains no glyphs from any typeface. It is licensed with the rest of the
project under **GPL-3.0-only** (see `LICENSE`).
