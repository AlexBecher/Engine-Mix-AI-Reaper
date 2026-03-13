import numpy as np

# Frequency bands (Hz)
BANDS_HZ = {
    "sub": (20, 60),
    "low": (60, 250),
    "mid": (250, 2000),
    "highmid": (2000, 6000),
    "air": (6000, 16000),
}


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

    values = {
        band: _mean_in_range(mag, freqs, lo, hi)
        for band, (lo, hi) in BANDS_HZ.items()
    }

    max_val = max(values.values()) if values else 1.0
    if max_val <= 0:
        return values

    return {k: v / max_val for k, v in values.items()}
