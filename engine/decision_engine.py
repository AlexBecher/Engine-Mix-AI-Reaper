DEFAULT_REFERENCE = {
    "sub": 0.7,
    "low": 0.8,
    "mid": 0.9,
    "highmid": 0.7,
    "air": 0.6,
}


def decide(current, reference=None, threshold=0.1):
    """Decide which bands need volume adjustment.

    Args:
        current: dict of band -> value (e.g., output of engine.tonal_balance.bands)
        reference: dict of band -> target value. If None, uses DEFAULT_REFERENCE.
        threshold: minimum absolute error required to trigger an action.

    Returns:
        List of (band, error) tuples for adjustments.
    """

    if reference is None:
        reference = DEFAULT_REFERENCE

    actions = []
    for band in reference:
        error = reference[band] - current.get(band, 0.0)
        if abs(error) > threshold:
            actions.append((band, error))

    return actions