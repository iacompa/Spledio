"""Generate the Spledio app-icon concept SVGs (stdlib only; all geometry and colours live here).

Same grid as the other apps' icons: 1024 canvas, 824 x 824 tile at (100, 100), corner radius 185,
soft drop shadow in the margin. Everything is drawn from hand-built paths: no fonts, no raster images.

    python3 design/icon/tools/gen_icons.py            # writes design/icon/variant-*/Spledio-*.svg
    python3 design/icon/tools/gen_icons.py --install  # writes the app's spledio-icon.svg and
                                                      # spledio-wordmark.svg (concept E)

Concepts (each shows what the app does: sound drawn as a picture, one sound picked out of it):
  A  Buddy   – a cute sound blob with a face, picked out by a dashed selection box with handles,
               on a striped "picture of sound" (spectrogram) background.
  B  Marker  – a chunky cartoon marker draws a loop around one glowing sound on the stripes.
  C  Hush    – a glossy candy sound wave; inside the selection box the wave is turned down.
  D  Spectrum (navy)   – the original icon's idea redrawn: a harmonic stack whose bands look like
                         sound (round attack, vibrato, fading decay); the selection box turns the
                         ringing 2nd harmonic down.
  E  Spectrum (indigo) – D on a deeper indigo-violet tile. This is the one the app uses.
"""

from __future__ import annotations

import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)

TILE = '<rect x="100" y="100" width="824" height="824" rx="185"/>'
INK = "#3A2346"


def smooth_path(points: list[tuple[float, float]]) -> str:
    """Catmull-Rom spline through the points, as cubic Béziers."""
    d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    for i in range(len(points) - 1):
        p0 = points[max(i - 1, 0)]
        p1, p2 = points[i], points[i + 1]
        p3 = points[min(i + 2, len(points) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}"
    return d


def inside(y: float, m: float) -> tuple[float, float]:
    """x range of the tile at height y, inset by m from its edge (rounded corners included)."""
    r, top, bot = 185.0, 100.0, 924.0
    cy = top + r if y < top + r else bot - r if y > bot - r else y
    dy = abs(y - cy)
    dx = math.sqrt(max(r * r - dy * dy, 0.0))
    return 100 + r - dx + m, 924 - r + dx - m


def band(y, amp, period, phase, m, **kw):
    """A wavy stripe that starts and ends inside the tile (worst case of its wobble)."""
    edge = y - amp if y < 512 else y + amp
    x0, x1 = inside(edge, m)
    n = max(int((x1 - x0) / (period / 8)), 2)
    step = (x1 - x0) / n
    return wave(x0, x1, y, amp, period, phase, step=step, **kw)


def wave(x0, x1, y, amp, period, phase=0.0, step=None, env=None):
    step = step or period / 8
    pts = []
    x = x0
    while x <= x1 + 0.01:
        a = amp * (env(x) if env else 1.0)
        pts.append((x, y - a * math.sin(2 * math.pi * (x - x0) / period + phase)))
        x += step
    return smooth_path(pts)


def shell(title: str, defs: str, body: str, bg_top: str, bg_bottom: str, shadow: str) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">
<title>{title}</title>
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{bg_top}"/><stop offset="1" stop-color="{bg_bottom}"/>
</linearGradient>
<radialGradient id="sheen" cx="0.25" cy="0.08" r="0.85">
  <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.42"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
</radialGradient>
<clipPath id="tile-clip">{TILE}</clipPath>
<filter id="tile-shadow" x="-15%" y="-15%" width="130%" height="135%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="22" result="b"/>
  <feOffset in="b" dy="16" result="o"/>
  <feFlood flood-color="{shadow}" flood-opacity="0.35"/>
  <feComposite in2="o" operator="in" result="s"/>
  <feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>
<filter id="soft" x="-30%" y="-30%" width="160%" height="160%">
  <feGaussianBlur stdDeviation="14"/>
</filter>
{defs}
</defs>
<g filter="url(#tile-shadow)"><rect x="100" y="100" width="824" height="824" rx="185" fill="url(#bg)"/></g>
<g clip-path="url(#tile-clip)">
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#sheen)"/>
{body}
</g>
</svg>
"""


def face(cx, cy, s=1.0, ink=INK, blush="#FF5C8A"):
    """Kawaii face: two shiny eyes, a small smile, pink cheeks. s scales it."""
    ex, ey = 46 * s, 16 * s
    out = []
    for sx in (-1, 1):
        x = cx + sx * ex
        out.append(f'<ellipse cx="{x:.1f}" cy="{cy - ey:.1f}" rx="{19 * s:.1f}" ry="{25 * s:.1f}" fill="{ink}"/>')
        out.append(f'<circle cx="{x + 7 * s:.1f}" cy="{cy - ey - 9 * s:.1f}" r="{7.5 * s:.1f}" fill="#FFFFFF"/>')
        out.append(f'<ellipse cx="{cx + sx * 84 * s:.1f}" cy="{cy + 26 * s:.1f}" rx="{24 * s:.1f}" ry="{13 * s:.1f}" '
                   f'fill="{blush}" opacity="0.55"/>')
    out.append(f'<path d="M{cx - 20 * s:.1f},{cy + 18 * s:.1f} Q{cx:.1f},{cy + 40 * s:.1f} {cx + 20 * s:.1f},{cy + 18 * s:.1f}" '
               f'fill="none" stroke="{ink}" stroke-width="{9 * s:.1f}" stroke-linecap="round"/>')
    return "\n".join(out)


def handle(x, y, ring):
    return (f'<circle cx="{x}" cy="{y}" r="30" fill="#FFFFFF"/>'
            f'<circle cx="{x}" cy="{y}" r="15" fill="{ring}"/>')


# ----------------------------------------------------------------------------- A: Buddy
def variant_a() -> str:
    defs = """
<radialGradient id="a-blob" cx="0.38" cy="0.3" r="0.8">
  <stop offset="0" stop-color="#FFD27A"/><stop offset="0.55" stop-color="#FF9A5A"/><stop offset="1" stop-color="#FF6A63"/>
</radialGradient>
<filter id="a-drop" x="-30%" y="-30%" width="160%" height="170%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="14" result="b"/>
  <feOffset in="b" dy="14" result="o"/>
  <feFlood flood-color="#2A1170" flood-opacity="0.35"/>
  <feComposite in2="o" operator="in" result="s"/>
  <feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>"""
    bands = []
    rows = [(212, "#8FEAFF", 0.85, 0.0), (318, "#FFB0E6", 0.8, 1.3), (706, "#FFE98F", 0.85, 2.1),
            (812, "#9CF5D0", 0.8, 0.6)]
    for y, col, op, ph in rows:
        bands.append(f'<path d="{band(y, 16, 300, ph, 62)}" fill="none" stroke="{col}" stroke-width="46" '
                     f'stroke-linecap="round" opacity="{op}"/>')
    # the band the sound sits on, brighter where it passes the blob
    bands.append(f'<path d="{band(512, 22, 280, 0.4, 62)}" fill="none" stroke="#B9A6FF" stroke-width="46" '
                 f'stroke-linecap="round" opacity="0.65"/>')
    sel = ('<rect x="262" y="300" width="500" height="424" rx="70" fill="#FFFFFF" fill-opacity="0.16" '
           'stroke="#FFFFFF" stroke-width="20" stroke-dasharray="54 34" stroke-linecap="round"/>')
    handles = "".join(handle(x, y, "#7A5CF2") for x, y in ((262, 300), (762, 300), (262, 724), (762, 724)))
    # sound "rings" coming off the blob
    rings = "".join(
        f'<path d="M{512 + sx * r * math.cos(a0):.1f},{528 - r * math.sin(a0):.1f} '
        f'A{r},{r} 0 0 {1 if sx > 0 else 0} {512 + sx * r * math.cos(a0):.1f},{528 + r * math.sin(a0):.1f}" '
        f'fill="none" stroke="#FFFFFF" stroke-width="16" stroke-linecap="round" opacity="{op}"/>'
        for sx in (-1, 1) for r, a0, op in ((178, 0.55, 0.9), (214, 0.42, 0.55)))
    blob = (f'<g filter="url(#a-drop)"><path d="M512,388 C604,388 650,448 650,528 C650,612 598,664 512,664 '
            f'C426,664 374,612 374,528 C374,448 420,388 512,388 Z" fill="url(#a-blob)"/></g>'
            f'<ellipse cx="452" cy="446" rx="44" ry="24" fill="#FFFFFF" opacity="0.55" transform="rotate(-28 452 446)"/>'
            + face(512, 548, 1.0))
    body = "\n".join(bands) + "\n" + sel + "\n" + rings + "\n" + blob + "\n" + handles
    return shell("Spledio icon, concept A (Buddy)", defs, body, "#9C86FF", "#5A3BD6", "#2A1170")


# ----------------------------------------------------------------------------- B: Marker
def variant_b() -> str:
    defs = """
<radialGradient id="b-sound" cx="0.4" cy="0.35" r="0.7">
  <stop offset="0" stop-color="#FFF4C2"/><stop offset="0.45" stop-color="#FF9BCB"/><stop offset="1" stop-color="#FF5C9A"/>
</radialGradient>
<linearGradient id="b-pen" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="#FFE36B"/><stop offset="1" stop-color="#FFB938"/>
</linearGradient>
<filter id="b-drop" x="-30%" y="-30%" width="160%" height="170%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="12" result="b"/>
  <feOffset in="b" dy="12" result="o"/>
  <feFlood flood-color="#0B4A55" flood-opacity="0.35"/>
  <feComposite in2="o" operator="in" result="s"/>
  <feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>"""
    bands = []
    for i, y in enumerate((230, 330, 430, 530, 630, 730, 830)):
        bands.append(f'<path d="{band(y, 14, 320, i * 0.9, 58)}" fill="none" stroke="#FFFFFF" '
                     f'stroke-width="40" stroke-linecap="round" opacity="{0.16 if i % 2 else 0.24}"/>')
    sound = (f'<circle cx="440" cy="500" r="150" fill="#FF7FB6" opacity="0.45" filter="url(#soft)"/>'
             f'<path d="M440,392 C510,392 552,440 552,500 C552,566 506,610 440,610 C372,610 328,566 328,500 '
             f'C328,440 372,392 440,392 Z" fill="url(#b-sound)"/>'
             f'<ellipse cx="398" cy="444" rx="34" ry="18" fill="#FFFFFF" opacity="0.6" transform="rotate(-30 398 444)"/>')
    # hand-drawn loop around the sound, ending where the marker tip is
    loop = ('<path d="M598,430 C612,330 520,276 432,284 C318,294 250,384 256,486 C262,596 352,664 452,662 '
            'C552,660 628,598 636,512 C640,470 626,446 606,436" fill="none" stroke="#FFFFFF" stroke-width="26" '
            'stroke-linecap="round" stroke-linejoin="round"/>')
    # chunky marker, tip at about (606, 436), body going down-right
    pen = f"""<g filter="url(#b-drop)" transform="translate(612 430) rotate(-38) scale(0.74)">
  <path d="M0,0 L44,-26 L44,26 Z" fill="{INK}" stroke="{INK}" stroke-width="10" stroke-linejoin="round"/>
  <rect x="40" y="-46" width="70" height="92" rx="16" fill="#FFFFFF" stroke="{INK}" stroke-width="14"/>
  <rect x="104" y="-60" width="270" height="120" rx="36" fill="url(#b-pen)" stroke="{INK}" stroke-width="14"/>
  <rect x="132" y="-38" width="200" height="22" rx="11" fill="#FFFFFF" opacity="0.55"/>
  <rect x="300" y="-66" width="96" height="132" rx="40" fill="#FF6A8A" stroke="{INK}" stroke-width="14"/>
</g>"""
    body = "\n".join(bands) + "\n" + sound + "\n" + loop + "\n" + pen
    return shell("Spledio icon, concept B (Marker)", defs, body, "#6EE7D2", "#2398B0", "#0B4A55")


# ----------------------------------------------------------------------------- C: Hush
def variant_c() -> str:
    x0, x1, yc, period = 150, 874, 512, 240
    sel_l, sel_r = 392, 632

    def env(x):  # the wave is turned down inside the selection
        return 0.32 if sel_l + 10 < x < sel_r - 10 else 1.0

    ph = lambda x: 2 * math.pi * (x - x0) / period  # noqa: E731
    left = wave(x0, sel_l, yc, 150, period, 0.0, step=(sel_l - x0) / 16)
    right = wave(sel_r, x1, yc, 150, period, ph(sel_r), step=(x1 - sel_r) / 16)
    quiet = wave(sel_l, sel_r, yc, 150 * 0.32, period, ph(sel_l), step=(sel_r - sel_l) / 16)
    defs = f"""
<linearGradient id="c-wave" gradientUnits="userSpaceOnUse" x1="{x0}" y1="0" x2="{x1}" y2="0">
  <stop offset="0" stop-color="#3FB8FF"/><stop offset="0.5" stop-color="#8B6CFF"/><stop offset="1" stop-color="#FF5FA8"/>
</linearGradient>
<filter id="c-drop" x="-20%" y="-30%" width="140%" height="160%">
  <feGaussianBlur in="SourceAlpha" stdDeviation="12" result="b"/>
  <feOffset in="b" dy="14" result="o"/>
  <feFlood flood-color="#4B3A9E" flood-opacity="0.28"/>
  <feComposite in2="o" operator="in" result="s"/>
  <feMerge><feMergeNode in="s"/><feMergeNode in="SourceGraphic"/></feMerge>
</filter>"""
    sel = (f'<rect x="{sel_l}" y="300" width="{sel_r - sel_l}" height="424" rx="44" fill="#8B6CFF" fill-opacity="0.12" '
           f'stroke="{INK}" stroke-width="16" stroke-dasharray="44 30" stroke-linecap="round"/>')
    handles = "".join(f'<circle cx="{x}" cy="{y}" r="26" fill="#FFFFFF" stroke="{INK}" stroke-width="12"/>'
                      for x, y in ((sel_l, 300), (sel_r, 300), (sel_l, 724), (sel_r, 724)))
    wave_loud = "".join(
        f'<g filter="url(#c-drop)"><path d="{d}" fill="none" stroke="url(#c-wave)" stroke-width="64" '
        f'stroke-linecap="round"/></g>'
        f'<path d="{d}" fill="none" stroke="#FFFFFF" stroke-width="16" stroke-linecap="round" opacity="0.45" '
        f'transform="translate(-6 -14)"/>' for d in (left, right))
    wave_quiet = (f'<g filter="url(#c-drop)"><path d="{quiet}" fill="none" stroke="#B9A9FF" stroke-width="64" '
                  f'stroke-linecap="round"/></g>')
    # a tiny sleeping face on the quiet part: it has been told to hush
    zz = (f'<path d="M{sel_l + 150},{360} h34 l-34,40 h34" fill="none" stroke="{INK}" stroke-width="12" '
          f'stroke-linecap="round" stroke-linejoin="round"/>'
          f'<path d="M{sel_l + 196},{322} h24 l-24,28 h24" fill="none" stroke="{INK}" stroke-width="10" '
          f'stroke-linecap="round" stroke-linejoin="round" opacity="0.7"/>')
    body = sel + "\n" + wave_loud + "\n" + wave_quiet + "\n" + zz + "\n" + handles
    return shell("Spledio icon, concept C (Hush)", defs, body, "#FFFFFF", "#E4DEFF", "#4B3A9E")


# ----------------------------------------------------------------------------- D/E: Spectrum (refined original)
def _cont(points: list[tuple[float, float]]) -> str:
    """smooth_path without the leading move-to, to continue an open path."""
    return smooth_path(points).split(" ", 1)[1]


def smoothstep(a: float, b: float, x: float) -> float:
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3 - 2 * t)


VIB_AMP, VIB_PERIOD = 6.5, 196.0  # vibrato: on a log-frequency axis every harmonic moves by the same amount


def centre(x: float, x0: float, yc: float) -> float:
    """Centre line of a harmonic: steady at the attack, then vibrato develops (same phase for all)."""
    grow = smoothstep(x0 + 40, x0 + 190, x)
    return yc - VIB_AMP * grow * math.sin(2 * math.pi * (x - 150) / VIB_PERIOD)


def harmonic(x0, x1, yc, w0, decay=0.55, scoop=None, n=64) -> str:
    """Closed outline of one harmonic: round attack on the left, thinning as it decays to the right.
    scoop(x) -> width factor lets a stretch of the band be turned down."""
    xs = [x0 + (x1 - x0) * (1 - math.cos(math.pi * i / n)) / 2 for i in range(n + 1)]
    top, bot = [], []
    for x in xs:
        t = (x - x0) / (x1 - x0)
        w = w0 * (1 - decay * t ** 1.4)
        w_end = w0 * (1 - decay)
        cap0 = math.sqrt(max(0.0, 1 - ((x0 + w0 - x) / w0) ** 2)) if x < x0 + w0 else 1.0
        cap1 = math.sqrt(max(0.0, 1 - ((x - (x1 - w_end)) / w_end) ** 2)) if x > x1 - w_end else 1.0
        w *= cap0 * cap1 * (scoop(x) if scoop else 1.0)
        c = centre(x, x0, yc)
        top.append((x, c - w))
        bot.append((x, c + w))
    return smooth_path(top) + " L" + f"{bot[-1][0]:.1f},{bot[-1][1]:.1f} " + _cont(bot[::-1]) + " Z"


def core_line(x0, x1, yc, n=40) -> str:
    xs = [x0 + (x1 - x0) * i / n for i in range(n + 1)]
    return smooth_path([(x, centre(x, x0 - 14, yc)) for x in xs])


def grad(gid: str, x0: float, x1: float, stops: list[tuple[float, str, float]]) -> str:
    s = "".join(f'<stop offset="{o:.3f}" stop-color="{c}" stop-opacity="{a}"/>' for o, c, a in stops)
    return f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{x0}" y1="0" x2="{x1}" y2="0">{s}</linearGradient>'


SEL_L, SEL_R, SEL_T, SEL_B = 384, 660, 500, 620


def spectrum_parts(tile: tuple[str, str, str], floor: str, shadow: bool = True) -> tuple[str, str]:
    """defs and body of the refined spectrogram icon on the 1024 grid (ids unprefixed)."""
    # (x0, x1, y, half-width, decay, colours from attack to tail)
    H = [
        (240, 712, 240, 6.5, 0.5, ("#6F9BFF", "#5A82F4", "#4A6EE6")),
        (228, 752, 286, 10, 0.5, ("#8C78FF", "#6E60EE", "#5E5AE6")),
        (218, 790, 346, 15, 0.5, ("#C06BF0", "#9A52DE", "#7E4AD6")),
        (210, 818, 430, 22, 0.5, ("#F46AAE", "#DC4F9C", "#B84AAE")),
        (204, 840, 560, 30, 0.42, ("#FF9A62", "#FF7468", "#F2507E")),
        (200, 852, 748, 38, 0.45, ("#FFD36A", "#FFA145", "#EC5486")),
    ]
    sl, sr = SEL_L, SEL_R

    def scoop(x):  # the resonance, turned down inside the selection
        return 1 - 0.72 * smoothstep(sl - 4, sl + 40, x) * (1 - smoothstep(sr - 40, sr + 4, x))

    defs, glow, bodies, cores = [], [], [], []
    for i, (x0, x1, y, w0, dec, (c0, c1, c2)) in enumerate(H):
        gid = f"h{6 - i}"
        span = x1 - x0
        if i == 4:  # resonance: hot outside, cooled to violet inside the selection
            a, b = (sl + 10 - x0) / span, (sr - 10 - x0) / span
            stops = [(0, c0, 1), (a - 0.03, c1, 1), (a + 0.05, "#A27BFF", 1), (b - 0.05, "#A27BFF", 1),
                     (b + 0.03, c1, 1), (0.8, c2, 0.8), (1, c2, 0.0)]
            gstops = [(0, c1, 0.9), (a - 0.04, c1, 0.9), (a + 0.05, c1, 0.08), (b - 0.05, c1, 0.08),
                      (b + 0.04, c1, 0.9), (1, c2, 0.2)]
            shape = harmonic(x0, x1, y, w0, dec, scoop)
        else:
            stops = [(0, c0, 1), (0.3, c0, 1), (0.65, c1, 0.9), (1, c2, 0.0)]
            gstops = [(0, c0, 0.9), (0.5, c1, 0.7), (1, c2, 0.0)]
            shape = harmonic(x0, x1, y, w0, dec)
        defs.append(grad(f"{gid}Body", x0, x1, stops))
        defs.append(grad(f"{gid}Glow", x0, x1, gstops))
        glow.append(f'<path d="{shape}" fill="url(#{gid}Glow)"/>')
        bodies.append(f'<path d="{shape}" fill="url(#{gid}Body)"/>')
        if w0 >= 20:  # bright core along the louder harmonics
            if i == 4:
                for cx0, cx1 in ((x0 + 14, sl - 16), (sr + 16, x1 - 120)):
                    cores.append(f'<path d="{core_line(cx0, cx1, y)}" fill="none" stroke="#FFE6D2" '
                                 f'stroke-width="9" stroke-linecap="round" opacity="0.7"/>')
            else:
                defs.append(grad(f"{gid}Core", x0, x1, [(0, "#FFF6E6", 0.95), (0.45, "#FFF6E6", 0.6), (0.9, "#FFF6E6", 0)]))
                cores.append(f'<path d="{core_line(x0 + 14, x1 - 60, y)}" fill="none" stroke="url(#{gid}Core)" '
                             f'stroke-width="{w0 * 0.42:.1f}" stroke-linecap="round"/>')
    defs_s = f"""
<linearGradient id="tileFill" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="{tile[0]}"/><stop offset="0.5" stop-color="{tile[1]}"/><stop offset="1" stop-color="{tile[2]}"/>
</linearGradient>
<radialGradient id="floor" gradientUnits="userSpaceOnUse" cx="470" cy="600" r="460">
  <stop offset="0" stop-color="{floor}" stop-opacity="0.32"/><stop offset="1" stop-color="{floor}" stop-opacity="0"/>
</radialGradient>
<radialGradient id="sheen" gradientUnits="userSpaceOnUse" cx="360" cy="60" r="620">
  <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.16"/><stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
</radialGradient>
<linearGradient id="rim" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#FFFFFF" stop-opacity="0.28"/><stop offset="0.3" stop-color="#FFFFFF" stop-opacity="0.05"/>
  <stop offset="1" stop-color="#FFFFFF" stop-opacity="0.06"/>
</linearGradient>
<filter id="tileShadow" filterUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">
  <feGaussianBlur stdDeviation="18"/>
</filter>
<filter id="glow" filterUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">
  <feGaussianBlur stdDeviation="20"/>
</filter>
<filter id="soft" filterUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">
  <feGaussianBlur stdDeviation="4"/>
</filter>
<filter id="handleShadow" filterUnits="userSpaceOnUse" x="0" y="0" width="1024" height="1024">
  <feGaussianBlur stdDeviation="5"/>
</filter>
""" + "\n".join(defs)
    sel_w, sel_h = SEL_R - SEL_L, SEL_B - SEL_T
    hs = 30  # handle size
    corners = ((SEL_L, SEL_T), (SEL_R, SEL_T), (SEL_L, SEL_B), (SEL_R, SEL_B))
    handles = "".join(
        f'<rect x="{x - hs / 2 + 1}" y="{y - hs / 2 + 5}" width="{hs}" height="{hs}" rx="8" fill="#05061A" '
        f'opacity="0.45" filter="url(#handleShadow)"/>' for x, y in corners) + "".join(
        f'<rect x="{x - hs / 2}" y="{y - hs / 2}" width="{hs}" height="{hs}" rx="8" fill="#F4FAFF"/>'
        f'<rect x="{x - hs / 2 + 9}" y="{y - hs / 2 + 9}" width="{hs - 18}" height="{hs - 18}" rx="3" fill="#7FCBFF"/>'
        for x, y in corners)
    tile_shadow = ('<rect x="100" y="114" width="824" height="824" rx="185" fill="#000000" opacity="0.34" '
                   'filter="url(#tileShadow)"/>') if shadow else ""
    body = f"""{tile_shadow}
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#tileFill)"/>
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#floor)"/>
<rect x="100" y="100" width="824" height="824" rx="185" fill="url(#sheen)"/>
<g filter="url(#glow)" opacity="0.75">{''.join(glow)}</g>
<g>{''.join(bodies)}</g>
<g filter="url(#soft)">{''.join(cores)}</g>
<rect x="{SEL_L}" y="{SEL_T}" width="{sel_w}" height="{sel_h}" rx="20" fill="none" stroke="#7FDBFF" stroke-width="18" opacity="0.5" filter="url(#soft)"/>
<rect x="{SEL_L}" y="{SEL_T}" width="{sel_w}" height="{sel_h}" rx="20" fill="#CFEFFF" fill-opacity="0.08" stroke="#F4FAFF" stroke-width="10"/>
{handles}
<rect x="100" y="100" width="824" height="824" rx="185" fill="none" stroke="url(#rim)" stroke-width="4"/>"""
    return defs_s, body


TILE_NAVY = ("#1C2560", "#0E1438", "#090C24")
TILE_INDIGO = ("#3A2E96", "#1E1858", "#120E36")


def spectrum_svg(tile, floor, title) -> str:
    defs, body = spectrum_parts(tile, floor)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">\n'
            f'<title>{title}</title>\n<defs>{defs}\n</defs>\n{body}\n</svg>\n')


def variant_d() -> str:
    return spectrum_svg(TILE_NAVY, "#3A3CB0", "Spledio icon, concept D (Spectrum, navy)")


def variant_e() -> str:
    return spectrum_svg(TILE_INDIGO, "#6A4CFF", "Spledio icon, concept E (Spectrum, indigo)")


# ----------------------------------------------------------------------------- the app's files (concept E)
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(OUT)), "src", "spledio", "resources", "icons", "app")

MASTER_NOTE = """<!--
  Master app icon (used for 48 px and larger; see spledio-icon-small.svg for 16/32 px).
  Generated by design/icon/tools/gen_icons.py (concept E); edit the generator, not this file.
  macOS Big Sur+ grid: 1024 canvas, 824x824 tile at (100,100) with corner radius 185, soft
  drop shadow in the margin.
  Motif: a harmonic stack as it appears in a spectrogram (time on x, log frequency on y,
  colour = level). Each harmonic starts with a round attack, develops a vibrato (the same
  displacement for every harmonic, as on a log-frequency axis) and fades as it decays. The
  2nd harmonic is a ringing resonance: inside the selection box it is thinned and cooled
  from coral to violet, i.e. it has been reduced.
  Original artwork, licensed with the project under GPL-3.0-only.
-->"""

WORDMARK_LETTERS = """  <!-- name: monoline geometric letters, stroke-drawn paths -->
  <g transform="translate(140 14.4) scale(0.8)" fill="none" stroke="currentColor" stroke-width="12" stroke-linecap="round" stroke-linejoin="round">
    <path d="M35.29 36.4A15 15 0 1 0 23 60A17 17 0 1 1 9.07 86.75"/>
    <path d="M64 54V118M64 74A20 20 0 1 0 104 74A20 20 0 1 0 64 74Z"/>
    <path d="M128 30V94"/>
    <path d="M152 74H192A20 20 0 1 0 187.32 86.86"/>
    <path d="M214 74A20 20 0 1 0 254 74A20 20 0 1 0 214 74ZM254 30V94"/>
    <path d="M280 54V94"/>
    <path d="M304 74A20 20 0 1 0 344 74A20 20 0 1 0 304 74Z"/>
    <circle cx="280" cy="32" r="7.5" fill="currentColor" stroke="none"/>
  </g>"""


def prefixed(text: str, p: str) -> str:
    import re
    text = re.sub(r'id="([\w-]+)"', lambda m: f'id="{p}{m.group(1)}"', text)
    return re.sub(r"url\(#([\w-]+)\)", lambda m: f"url(#{p}{m.group(1)})", text)


def master_svg() -> str:
    defs, body = spectrum_parts(TILE_INDIGO, "#6A4CFF")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">\n'
            f'<title>App icon</title>\n{MASTER_NOTE}\n<defs>{defs}\n</defs>\n{body}\n</svg>\n')


def wordmark_svg() -> str:
    defs, body = spectrum_parts(TILE_INDIGO, "#6A4CFF", shadow=False)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="424" height="128" viewBox="0 0 424 128" color="#141B3D">
  <title>Wordmark</title>
  <!--
    Horizontal lockup: the app tile (the master icon without its drop shadow) + the product
    name drawn as monoline geometric strokes (no font). Generated by
    design/icon/tools/gen_icons.py. The letters use currentColor, defaulting to ink #141B3D;
    set CSS `color` on the element to recolour them (for example #F2F5FF on dark backgrounds).
  -->
  <defs>{prefixed(defs, "m-")}
  </defs>
  <g transform="translate(-13.5922 -5.5922) scale(0.135922)">
{prefixed(body, "m-")}
  </g>

{WORDMARK_LETTERS}
</svg>
"""


def install():
    for name, text in (("spledio-icon.svg", master_svg()), ("spledio-wordmark.svg", wordmark_svg())):
        path = os.path.join(APP_DIR, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(path)


def main():
    import sys
    if "--install" in sys.argv:
        install()
        return
    for key, fn in (("A", variant_a), ("B", variant_b), ("C", variant_c), ("D", variant_d), ("E", variant_e)):
        d = os.path.join(OUT, f"variant-{key}")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"Spledio-{key}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(fn())
        print(path)


if __name__ == "__main__":
    main()
