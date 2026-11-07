"""Real-time playback and audition (PortAudio via ``sounddevice``, imported lazily).

``sources`` holds immutable, pre-rendered buffers the audio callback reads from;
``engine`` holds the transport, the stream backends and the callback.  No Qt here.
"""

from .engine import (
    DEFAULT_SCRATCH_FRAMES,
    MAX_VOLUME,
    AudioCallback,
    FakeBackend,
    PlaybackBackend,
    PlaybackEngine,
    PlaybackState,
    SoundDeviceBackend,
)
from .sources import ArraySource, OverlaySource, PlaybackSource, SilenceSource

__all__ = [
    "PlaybackSource",
    "ArraySource",
    "OverlaySource",
    "SilenceSource",
    "PlaybackBackend",
    "SoundDeviceBackend",
    "FakeBackend",
    "PlaybackState",
    "PlaybackEngine",
    "AudioCallback",
    "DEFAULT_SCRATCH_FRAMES",
    "MAX_VOLUME",
]
