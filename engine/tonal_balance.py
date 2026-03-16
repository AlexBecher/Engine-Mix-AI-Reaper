import numpy as np

# Frequency bands (Hz)
BANDS_HZ = {
    "sub": (20, 60),
    "low": (60, 250),
    "mid": (250, 2000),
    "highmid": (2000, 6000),
    "air": (6000, 16000),
}

# If FFT magnitude energy is too low, treat the frame as silence to avoid
# normalizing tiny noise into misleading 0..1 band values.
SILENCE_FLOOR_RMS = 1e-6


def set_silence_floor_rms(value):
    """Set silence gate floor used before band normalization."""
    global SILENCE_FLOOR_RMS
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return
    if not np.isfinite(parsed) or parsed < 0.0:
        parsed = 1e-6
    # Protect against accidental values like 10.0 that would mute analysis.
    if parsed > 1e-2:
        parsed = 1e-6
    SILENCE_FLOOR_RMS = parsed


def get_silence_floor_rms():
    return float(SILENCE_FLOOR_RMS)


def _mean_in_range(mag, freqs, lo_hz, hi_hz):
    """Return the mean magnitude inside a frequency range.

    Uses a frequency array to select the correct FFT bins, so the
    band definitions remain correct regardless of FFT size.
    """

    mask = (freqs >= lo_hz) & (freqs < hi_hz)
    if not np.any(mask):
        return 0.0
    return float(np.nanmean(mag[mask]))


def bands(mag, freqs):
    """Compute band averages for a magnitude spectrum.

    Args:
        mag: magnitude spectrum (output of np.abs(np.fft.rfft(audio))).
        freqs: corresponding frequencies from np.fft.rfftfreq.

    Returns:
        dict with band names as keys and normalized magnitudes in [0, 1].
    """

    mag_arr = np.asarray(mag, dtype=np.float64)
    if mag_arr.size == 0:
        return {band: 0.0 for band in BANDS_HZ}

    rms = float(np.sqrt(np.mean(np.square(mag_arr))))
    if not np.isfinite(rms) or rms < SILENCE_FLOOR_RMS:
        return {band: 0.0 for band in BANDS_HZ}

    values = {
        band: _mean_in_range(mag, freqs, lo, hi)
        for band, (lo, hi) in BANDS_HZ.items()
    }

    max_val = max(values.values()) if values else 1.0
    if max_val <= 0:
        return values

    return {k: v / max_val for k, v in values.items()}
