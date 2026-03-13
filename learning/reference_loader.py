import os
import sys

import numpy as np
import soundfile as sf

# Ensure the project root is on sys.path when running as a script
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.fft_analyzer import analyze
from engine.tonal_balance import bands


def load_reference(file, sample_rate=44100):
    """Load an audio file and return a tonal-band profile.

    The output is a dict with keys "sub", "low", "mid", "highmid", and "air".
    """

    audio, sr = sf.read(file, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    if sr != sample_rate:
        # Simple resampling (nearest) when needed.
        factor = sample_rate / sr
        indices = (np.arange(int(len(audio) * factor)) / factor).astype(int)
        audio = audio[indices]

    mag = np.abs(np.fft.rfft(audio))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / sample_rate)
    return bands(mag, freqs)
