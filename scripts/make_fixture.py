"""Write a synthetic TEST FIXTURE WAV with a deliberate narrow resonance.

Not a real recording. Useful for trying the resonance workflow:
pink-ish noise + a soft chord + a ringing 2370 Hz resonance between 1.5 and 4.5 s.
Usage: .venv/bin/python scripts/make_fixture.py [out.wav]
"""
import sys

import numpy as np
import soundfile as sf

sr = 48000
dur = 8.0
n = int(sr * dur)
t = np.arange(n) / sr
rng = np.random.default_rng(42)
white = rng.normal(size=n)
pink = np.fft.irfft(np.fft.rfft(white) / np.sqrt(np.maximum(np.arange(n // 2 + 1), 1)), n)
pink /= np.max(np.abs(pink))
chord = sum(0.08 * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.25 * t)) for f in (220, 277.2, 329.6, 440))
env = np.clip((t - 1.5) / 0.05, 0, 1) * np.clip((4.5 - t) / 0.3, 0, 1)
reson = 0.12 * env * np.sin(2 * np.pi * 2370 * t)
left = 0.15 * pink + chord + reson
right = 0.15 * np.roll(pink, 37) + chord + 0.9 * reson
out = sys.argv[1] if len(sys.argv) > 1 else "fixture-resonance.wav"
sf.write(out, np.stack([left, right], axis=1).astype(np.float32), sr, subtype="FLOAT")
print(f"wrote synthetic test fixture {out} ({dur} s, {sr} Hz, stereo, resonance at 2370 Hz from 1.5 to 4.5 s)")
