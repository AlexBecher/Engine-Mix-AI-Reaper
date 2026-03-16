import numpy as np

DEFAULT_GAMMATONE_CENTERS_HZ = np.array(
    [20.0, 40.0, 80.0, 160.0, 320.0, 640.0, 1200.0, 2500.0, 5000.0, 10000.0, 20000.0],
    dtype=np.float64,
)

# Perceptual weights aligned with DEFAULT_GAMMATONE_CENTERS_HZ.
# Compensate for natural bass-heavy acoustic energy distribution so the analysis
# reflects what ears actually perceive, not raw acoustic power.
# Behaviour: sub-bass (20-80 Hz) is dramatically reduced because it dominates
# raw FFT energy but contributes little to perceived loudness; presence/attack
# region (1.2k-5k Hz) is kept at 1.0 as the perceptual reference point;
# air rolls off gently above 8 kHz.  Approximates A-weighting shape but tuned
# for mix monitoring (gentler sub-bass rolloff than strict A-weighting).
# Reference: 1200 Hz = 1.0
PERCEPTUAL_WEIGHTS = np.array(
    [
        0.08,  # 20 Hz   - sub rumble, mostly felt, not heard
        0.14,  # 40 Hz   - sub bass
        0.23,  # 80 Hz   - kick bottom / bass fundamental
        0.48,  # 160 Hz  - bass body / kick chest
        0.76,  # 320 Hz  - low mids / bass harmonics
        0.92,  # 640 Hz  - mids / body
        1.00,  # 1200 Hz - presence reference (1.0)
        1.00,  # 2500 Hz - attack, definition, clarity
        0.88,  # 5000 Hz - high presence / sibilance zone
        0.68,  # 10000 Hz - air / sheen
        0.40,  # 20000 Hz - ultra air
    ],
    dtype=np.float64,
)


def _erb_hz(center_hz):
    """Equivalent rectangular bandwidth in Hz for a center frequency."""
    return 24.7 * (4.37 * (center_hz / 1000.0) + 1.0)


def _gammatone_like_energy(fft_magnitude, fft_freqs, center_hz):
    """Approximate 4th-order gammatone magnitude response in the frequency domain."""
    erb = _erb_hz(center_hz)
    if erb <= 0.0:
        return 0.0

    bandwidth = 1.019 * erb
    norm = (fft_freqs - center_hz) / bandwidth
    weights = 1.0 / np.power(1.0 + np.square(norm), 2)

    weighted = fft_magnitude * weights
    denom = float(np.sum(weights))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(weighted) / denom)


def analyze(audio, sample_rate=44100, centers_hz=None):
    """Analyze audio with a perceptual gammatone-style filterbank.

    Returns:
        tuple[np.ndarray, np.ndarray]:
            - band magnitudes for each perceptual center frequency
            - corresponding center frequencies in Hz
    """
    audio_arr = np.asarray(audio, dtype=np.float64).reshape(-1)
    if audio_arr.size == 0 or sample_rate <= 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    fft_magnitude = np.abs(np.fft.rfft(audio_arr))
    fft_freqs = np.fft.rfftfreq(audio_arr.size, d=1.0 / float(sample_rate))

    centers = np.asarray(
        DEFAULT_GAMMATONE_CENTERS_HZ if centers_hz is None else centers_hz,
        dtype=np.float64,
    )
    nyquist = float(sample_rate) / 2.0
    centers = centers[(centers > 0.0) & (centers <= nyquist)]
    if centers.size == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    magnitudes = np.array(
        [_gammatone_like_energy(fft_magnitude, fft_freqs, center) for center in centers],
        dtype=np.float64,
    )

    # Apply perceptual weights when using the default 11-band centers so that the
    # returned magnitudes represent perceived loudness contributions rather than
    # raw acoustic energy.  This prevents P80 from always dominating at 1.0 and
    # ensures the presence/attack region (1.2k-5k) carries proper analytical weight.
    if centers_hz is None and magnitudes.size == PERCEPTUAL_WEIGHTS.size:
        magnitudes = magnitudes * PERCEPTUAL_WEIGHTS

    return magnitudes, centers