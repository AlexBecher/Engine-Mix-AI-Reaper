import os
import sys

import numpy as np
import soundfile as sf

# Ensure the project root is on sys.path when running as a script
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.fft_analyzer import analyze


def _normalize_perceptual_magnitudes(magnitudes, silence_floor_rms=1e-6):
    mag_arr = np.asarray(magnitudes, dtype=np.float64)
    if mag_arr.size == 0:
        return np.array([], dtype=np.float64)

    rms = float(np.sqrt(np.mean(np.square(mag_arr))))
    if not np.isfinite(rms) or rms < float(silence_floor_rms):
        return np.zeros_like(mag_arr, dtype=np.float64)

    max_val = float(np.max(mag_arr))
    if not np.isfinite(max_val) or max_val <= 0.0:
        return np.zeros_like(mag_arr, dtype=np.float64)

    return np.clip(mag_arr / max_val, 0.0, 1.0)


def load_reference(file, sample_rate=44100):
    """Load an audio file and return an 11-band perceptual profile.

    The output keys follow the gammatone perceptual centers:
    p20, p40, p80, p160, p320, p640, p1200, p2500, p5000, p10000, p20000.
    """

    audio, sr = sf.read(file, dtype="float32")
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    if sr != sample_rate:
        # Simple resampling (nearest) when needed.
        factor = sample_rate / sr
        indices = (np.arange(int(len(audio) * factor)) / factor).astype(int)
        audio = audio[indices]

    mag, freqs = analyze(audio, sample_rate=sample_rate)
    normalized = _normalize_perceptual_magnitudes(mag)
    if normalized.size == 0 or freqs.size == 0:
        return {}

    return {
        f"p{int(round(float(hz)))}": float(value)
        for hz, value in zip(freqs, normalized)
    }
