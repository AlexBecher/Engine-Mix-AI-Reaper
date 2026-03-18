import numpy as np
import sys
sys.path.insert(0, '.')
from mix_profile import _detect_tonal_fft_band, _compute_band_values, _compute_band_meter_db, PERCEPTUAL_BAND_KEYS

sr = 44100
dur = 0.8
N = int(sr * dur)
t = np.arange(N) / sr

def tst(freq_hz, assumed_sr, label=""):
    tone = 0.1 * np.sin(2 * np.pi * freq_hz * t[:int(assumed_sr * dur)])
    audio = np.stack([tone, tone], axis=1)
    band, state = _detect_tonal_fft_band(audio, assumed_sr)
    peak = state.get('peak_freq_hz', 0.0)
    print(f"  {label or str(freq_hz)+'Hz signal'} SR_passed={assumed_sr} -> {band} @ {peak:.1f} Hz")

print("=== pure tone -> FFT detected frequency ===")
for f in [160, 320, 572, 598, 640]:
    tst(f, 44100, label=f"{f} Hz signal")

print()
print("=== 320 Hz audio, passing wrong SR to FFT ===")
for test_sr in [22050, 24000, 32000, 44100, 48000, 78939, 88200]:
    tone = 0.1 * np.sin(2 * np.pi * 320 * t)
    audio = np.stack([tone, tone], axis=1)
    band, state = _detect_tonal_fft_band(audio, test_sr)
    peak = state.get('peak_freq_hz', 0.0)
    print(f"  320 Hz audio, SR_passed={test_sr} -> {band} @ {peak:.1f} Hz")

print()
print("=== _compute_band_values: dominant band for pure tones (SR=44100) ===")
print(f"  {'Tone':>10}  {'Dominant band':>14}  {'Value':>8}  {'Keys order': <50}")
for f in [160, 320, 640, 1200, 2500]:
    tone = 0.1 * np.sin(2 * np.pi * f * t)
    audio = np.stack([tone, tone], axis=1)
    vals = _compute_band_values(audio, 44100)
    dominant = max(vals, key=lambda k: vals[k]) if vals else '?'
    val = vals.get(dominant, 0.0)
    keys = list(vals.keys())
    print(f"  {f:>7} Hz  {dominant:>14}  {val:>8.4f}  {keys}")

print()
print("=== PERCEPTUAL_BAND_KEYS constant ===")
print(f"  {PERCEPTUAL_BAND_KEYS}")
