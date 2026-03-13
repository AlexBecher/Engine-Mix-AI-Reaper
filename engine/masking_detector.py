def detect(band_values):

    if band_values["low"] > 1.2:
        return "kick_bass_mask"

    if band_values["mid"] > 1.2:
        return "vocal_guitar_mask"

    return None