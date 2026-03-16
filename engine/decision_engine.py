LEGACY_REFERENCE_KEYS = ("sub", "low", "mid", "highmid", "air")

# Perceptual 11-band reference map - values represent what a well-mixed worship
# track SHOULD look like AFTER the perceptual weights in fft_analyzer are applied
# and after max-normalization.  Because PERCEPTUAL_WEIGHTS suppress sub-bass and
# keep presence at 1.0, a good mix will show:
#   - sub (20-40 Hz):   low perceptual energy (0.10-0.22)
#   - bass (80-160 Hz): moderate (0.55-0.70)
#   - mids (320-640 Hz):strong (0.80-0.88)
#   - presence (1.2k-5k):high energy (0.85-0.90)  <- this should NOT be ~0 !  
#   - air (10k-20k):    rolling off (0.42-0.20)
DEFAULT_REFERENCE = {
    "p20":   0.10,
    "p40":   0.22,
    "p80":   0.55,
    "p160":  0.70,
    "p320":  0.80,
    "p640":  0.88,
    "p1200": 0.90,
    "p2500": 0.85,
    "p5000": 0.65,
    "p10000":0.42,
    "p20000":0.18,
}


def _is_legacy_reference(reference):
    if not isinstance(reference, dict) or not reference:
        return False
    return all(key in LEGACY_REFERENCE_KEYS for key in reference.keys())


def _expand_legacy_reference(reference):
    """Expand 5-band legacy references to the 11 perceptual-band layout."""
    sub = float(reference.get("sub", 0.0))
    low = float(reference.get("low", 0.0))
    mid = float(reference.get("mid", 0.0))
    highmid = float(reference.get("highmid", 0.0))
    air = float(reference.get("air", 0.0))
    return {
        "p20": sub,
        "p40": sub,
        "p80": low,
        "p160": low,
        "p320": low,
        "p640": mid,
        "p1200": mid,
        "p2500": mid,
        "p5000": highmid,
        "p10000": air,
        "p20000": air,
    }



import numpy as np
def decide(current, reference=None, threshold=0.08):
    if reference is None:
        reference = DEFAULT_REFERENCE
    elif _is_legacy_reference(reference):
        reference = _expand_legacy_reference(reference)

    # Lista de bandas na ordem do reference (garante alinhamento)
    bands = list(reference.keys())
    r = np.array([float(reference[b]) for b in bands], dtype=np.float64)
    c = np.array([float(current.get(b, 0.0)) for b in bands], dtype=np.float64)

    # Ajuste de ganho global (mínimos quadrados) para remover diferença de "nível geral"
    denom = float(np.dot(c, c))
    alpha = float(np.dot(r, c) / denom) if denom > 1e-12 else 1.0
    alpha = float(np.clip(alpha, 0.7, 1.3))  # clipe para estabilidade

    actions = []
    for i, band in enumerate(bands):
        error = r[i] - alpha * c[i]
        if abs(error) >= threshold:
            actions.append((band, float(error)))
    return actions