# -*- coding: utf-8 -*-
import json
import os
import unicodedata
import numpy as np

from engine.fft_analyzer import analyze, DEFAULT_GAMMATONE_CENTERS_HZ
from engine.decision_engine import decide
from engine.loudness import get_lufs
from control.web_api_client import configure_from_config, get_tracks_db, set_track_db
from config_manager import (
    load_config,
    get_track_db_limits,
    get_track_fader_db,
    get_enabled_tracks,
    get_analysis_settings,
    get_master_track,
)

SAMPLE_RATE = 44100

# Load configuration from config.json
_config = load_config()
MASTER_TRACK = get_master_track(_config)
TRACK_DB_LIMITS = get_track_db_limits(_config)
TRACK_CONFIG_FADER_DB = get_track_fader_db(_config)
ENABLED_TRACKS = get_enabled_tracks(_config)

settings = get_analysis_settings(_config)
LUFS_WARNING_THRESHOLD = settings.get("lufs_warning_threshold", -14)
ERROR_GAIN_UP = settings.get("error_gain_up", 1.2)
ERROR_GAIN_DOWN = settings.get("error_gain_down", 2.2)
MAX_STEP_UP_DB = settings.get("max_step_up_db", 0.10)
MAX_STEP_DOWN_DB = settings.get("max_step_down_db", 0.35)
ERROR_DEADBAND = settings.get("error_deadband", 0.18)
MAX_TRACKS_RAISE_PER_CYCLE = settings.get("max_tracks_raise_per_cycle", 1)
SILENCE_FLOOR_RMS = settings.get("silence_floor_rms", 1e-6)
try:
    SILENCE_FLOOR_RMS = float(SILENCE_FLOOR_RMS)
except (TypeError, ValueError):
    SILENCE_FLOOR_RMS = 1e-6
if not np.isfinite(SILENCE_FLOOR_RMS) or SILENCE_FLOOR_RMS < 0.0 or SILENCE_FLOOR_RMS > 1e-2:
    SILENCE_FLOOR_RMS = 1e-6

# =============================================================================
# Web API write/read workflow
# =============================================================================
TRACK_CURRENT_DB = dict(TRACK_CONFIG_FADER_DB)
TRACK_ERROR_EMA = {}
TRACK_ROLE_BY_ID = {}

PERCEPTUAL_BAND_KEYS = tuple(f"p{int(round(float(hz)))}" for hz in DEFAULT_GAMMATONE_CENTERS_HZ)
METER_DB_FLOOR = -24.0
METER_DB_CEIL = 6.0
TRACK_ERROR_SMOOTHING = 0.35
TRACK_ERROR_SIGN_FLIP_SMOOTHING = 0.18
FAST_RESPONSE_ERROR = 0.45
FAST_RESPONSE_SMOOTHING = 0.75
FAST_RESPONSE_MAX_MULTIPLIER = 3.0
FAST_RESPONSE_MAX_STEP_UP_DB = 0.9
FAST_RESPONSE_MAX_STEP_DOWN_DB = 1.2

# Track mapping - FFT bands to Reaper tracks
DEFAULT_STEM_TRACK_MAP = {
    "drums": 154,
    "bass": 155,
    "piano": 156,
    "other": 157,
    "vocals": 160,
    "sub":     [154, 155],              
    "low":     [154, 155],              
    "mid":     [156, 157, 158, 160],    
    "highmid": [158, 160],              
    "air":     [154, 160],              
    "p20":     [154, 155],
    "p40":     [154, 155],
    "p80":     [154, 155],
    "p160":    [154, 155],
    "p320":    [154, 155],
    "p640":    [156, 157, 158, 160],
    "p1200":   [156, 157, 158, 160],
    "p2500":   [158, 160],
    "p5000":   [158, 160],
    "p10000":  [154, 160],
    "p20000":  [154, 160],
}

ACTIVE_STEM_TRACK_MAP = dict(DEFAULT_STEM_TRACK_MAP)


def _is_dry_run_enabled(debug=False):
    raw_value = os.environ.get("MIX_ROBO_DRY_RUN")
    if raw_value is None:
        return bool(debug)
    return str(raw_value).strip().lower() not in {"", "0", "false", "no", "off"}

def _clamp(value, minimum=0.0, maximum=1.0):
    return max(minimum, min(value, maximum))


def _normalize_track_name(name):
    raw = str(name or "")
    ascii_name = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_name.lower().replace("_", " ").replace("-", " ").split())


def _is_backing_vocal_name(name):
    norm = _normalize_track_name(name)
    if not norm:
        return False
    tokens = norm.split()
    direct_aliases = {"back", "backing", "backing vocal", "backing vocals", "bv", "choir", "coro"}
    if norm in direct_aliases:
        return True
    return any(token.startswith("back") for token in tokens)


def _infer_track_role(name):
    norm = _normalize_track_name(name)
    if not norm:
        return "other"
    if _is_backing_vocal_name(norm):
        return "backing_vocals"
    if any(alias in norm for alias in ("vocals", "vocal", "voz", "lead", "vox")):
        return "vocals"
    if any(alias in norm for alias in ("drums", "drum", "bateria")):
        return "drums"
    if any(alias in norm for alias in ("bass", "baixo")):
        return "bass"
    if any(alias in norm for alias in ("keys", "key", "piano", "teclado", "synth", "pad", "kbd")):
        return "piano"
    return "other"


def _build_track_roles_from_config(config):
    tracks_cfg = config.get("tracks", {})
    out = {}
    for track_id_str, track_data in tracks_cfg.items():
        try:
            track_id = int(track_id_str)
        except (TypeError, ValueError):
            continue
        out[track_id] = _infer_track_role(track_data.get("name", ""))
    return out


def _build_track_map_from_config(config):
    tracks_cfg = config.get("tracks", {})
    master = int(config.get("master_track", 153))

    enabled_tracks = []
    normalized_names = {}
    for track_id_str, track_data in tracks_cfg.items():
        try:
            track_id = int(track_id_str)
        except (TypeError, ValueError):
            continue

        if track_id == master:
            continue
        if not bool(track_data.get("enabled", True)):
            continue

        enabled_tracks.append(track_id)
        normalized_names[track_id] = _normalize_track_name(track_data.get("name", ""))

    enabled_tracks = sorted(set(enabled_tracks))

    def _find_track(aliases):
        for tid in enabled_tracks:
            name = normalized_names.get(tid, "")
            for alias in aliases:
                if alias in name:
                    return tid
        return None

    def _find_tracks(aliases):
        found = []
        for tid in enabled_tracks:
            name = normalized_names.get(tid, "")
            if any(alias in name for alias in aliases):
                found.append(tid)
        return found

    def _fallback(index):
        if 0 <= index < len(enabled_tracks):
            return enabled_tracks[index]
        return None

    def _unique(items):
        out = []
        for value in items:
            if value is None:
                continue
            if value not in out:
                out.append(value)
        return out

    drums = _find_track(["drums", "drum", "bateria"]) or _fallback(0)
    bass = _find_track(["bass", "baixo"]) or _fallback(1) or drums

    key_aliases = ["keys", "key", "piano", "teclado", "synth", "pad", "kbd"]
    vocal_aliases = ["vocals", "vocal", "voz", "lead", "vox"]

    keys = _find_track(key_aliases) or _fallback(2) or drums
    keys_layers = _find_tracks(key_aliases)

    vocals = _find_track(vocal_aliases) or _fallback(3) or keys or drums

    backing_vocals = _find_tracks(["back", "backing", "bv", "choir", "coro"])
    for tid in enabled_tracks:
        if _is_backing_vocal_name(normalized_names.get(tid, "")) and tid not in backing_vocals:
            backing_vocals.append(tid)

    guitar = _find_track(["guitar", "gtr", "guitarra", "violao"]) or _fallback(4)

    other = _find_track(["other", "outros", "fx", "sfx"]) or guitar or keys


    # -------- TARGET GROUPS --------

    bass_targets = _unique([bass])
    drum_targets = _unique([drums])
    key_targets = _unique([keys] + keys_layers)
    vocal_targets = _unique([vocals] + backing_vocals)
    guitar_targets = _unique([guitar])

    air_targets = _unique(drum_targets + vocal_targets)


    # -------- DYNAMIC MAP --------

    dynamic_map = {

        "drums": drums,
        "bass": bass,
        "piano": keys,
        "other": other,
        "vocals": vocals,
        "backing_vocals": vocal_targets,

        # SUB
        "p20": bass_targets,
        "p40": bass_targets,

        # LOW
        "p80": _unique(bass_targets + drum_targets),
        "p160": drum_targets,

        # LOW MID
        "p320": _unique(guitar_targets + key_targets),

        # MID
        "p640": _unique(key_targets + vocal_targets),

        # VOCAL BODY
        "p1200": vocal_targets,

        # PRESENCE
        "p2500": _unique(vocal_targets + guitar_targets),

        # ATTACK
        "p5000": _unique(drum_targets + vocal_targets),

        # AIR
        "p10000": air_targets,
        "p20000": drum_targets,
    }
    # Avoid empty targets for any band/stem key expected by processing paths.
    for key, fallback in DEFAULT_STEM_TRACK_MAP.items():
        value = dynamic_map.get(key)
        if value is None:
            dynamic_map[key] = fallback
            continue
        if isinstance(value, list) and not value:
            dynamic_map[key] = fallback

    return dynamic_map

def _reload_config():
    """Reload configuration from config.json - useful for hot-reloading."""
    global _config, MASTER_TRACK, TRACK_DB_LIMITS, TRACK_CONFIG_FADER_DB
    global ENABLED_TRACKS, ACTIVE_STEM_TRACK_MAP, TRACK_ROLE_BY_ID
    global LUFS_WARNING_THRESHOLD, ERROR_GAIN_UP, ERROR_GAIN_DOWN
    global MAX_STEP_UP_DB, MAX_STEP_DOWN_DB, ERROR_DEADBAND, MAX_TRACKS_RAISE_PER_CYCLE
    global SILENCE_FLOOR_RMS
    
    _config = load_config()
    configure_from_config(_config)
    ACTIVE_STEM_TRACK_MAP = _build_track_map_from_config(_config)
    TRACK_ROLE_BY_ID = _build_track_roles_from_config(_config)
    MASTER_TRACK = get_master_track(_config)
    TRACK_DB_LIMITS = get_track_db_limits(_config)
    configured_faders = get_track_fader_db(_config)
    ENABLED_TRACKS = get_enabled_tracks(_config)

    valid_tracks = set(TRACK_DB_LIMITS.keys())
    for track in list(TRACK_CURRENT_DB.keys()):
        if track not in valid_tracks:
            TRACK_CURRENT_DB.pop(track, None)
    for track in list(TRACK_ERROR_EMA.keys()):
        if track not in valid_tracks:
            TRACK_ERROR_EMA.pop(track, None)

    for track, configured_db in configured_faders.items():
        configured_db = float(configured_db)
        if track not in TRACK_CURRENT_DB:
            TRACK_CURRENT_DB[track] = configured_db
            continue
        previous_config_db = TRACK_CONFIG_FADER_DB.get(track)
        if previous_config_db is None or float(previous_config_db) != configured_db:
            TRACK_CURRENT_DB[track] = configured_db

    TRACK_CONFIG_FADER_DB = configured_faders
    
    settings = get_analysis_settings(_config)
    LUFS_WARNING_THRESHOLD = settings.get("lufs_warning_threshold", -14)
    ERROR_GAIN_UP = settings.get("error_gain_up", 1.2)
    ERROR_GAIN_DOWN = settings.get("error_gain_down", 2.2)
    MAX_STEP_UP_DB = settings.get("max_step_up_db", 0.10)
    MAX_STEP_DOWN_DB = settings.get("max_step_down_db", 0.35)
    ERROR_DEADBAND = settings.get("error_deadband", 0.18)
    MAX_TRACKS_RAISE_PER_CYCLE = settings.get("max_tracks_raise_per_cycle", 1)
    SILENCE_FLOOR_RMS = settings.get("silence_floor_rms", 1e-6)
    try:
        SILENCE_FLOOR_RMS = float(SILENCE_FLOOR_RMS)
    except (TypeError, ValueError):
        SILENCE_FLOOR_RMS = 1e-6
    if not np.isfinite(SILENCE_FLOOR_RMS) or SILENCE_FLOOR_RMS < 0.0 or SILENCE_FLOOR_RMS > 1e-2:
        SILENCE_FLOOR_RMS = 1e-6

def _error_to_desired_db(error):
    """Convert band error into a relative dB delta for a track."""
    if abs(error) <= ERROR_DEADBAND:
        return 0.0

    effective_error = abs(error) - ERROR_DEADBAND

    if error > 0:
        return effective_error * ERROR_GAIN_UP

    return -(effective_error * ERROR_GAIN_DOWN)


def _command_track(track, delta_db, error_magnitude=0.0, debug=False):
    # Check if track is enabled
    if track not in ENABLED_TRACKS:
        if debug:
            print(f"[process] Track {track} disabled - skipping")
        return None

    min_db, max_db = TRACK_DB_LIMITS.get(track, (-3.0, 1.5))
    current_db = TRACK_CURRENT_DB.get(track, TRACK_CONFIG_FADER_DB.get(track, 0.0))

    # Desired point is relative to current position, not absolute.
    desired_db = _clamp(current_db + float(delta_db), min_db, max_db)

    delta = desired_db - current_db
    if abs(delta) < 1e-5:
        return None

    # Scale slew limits with error magnitude so large deviations recover faster.
    step_up = MAX_STEP_UP_DB
    step_down = MAX_STEP_DOWN_DB
    error_mag = max(0.0, float(abs(error_magnitude)) - float(ERROR_DEADBAND))
    response_span = max(1e-6, FAST_RESPONSE_ERROR - float(ERROR_DEADBAND))
    response_ratio = _clamp(error_mag / response_span)
    response_boost = 1.0 + (FAST_RESPONSE_MAX_MULTIPLIER - 1.0) * response_ratio
    step_up = min(FAST_RESPONSE_MAX_STEP_UP_DB, step_up * response_boost)
    step_down = min(FAST_RESPONSE_MAX_STEP_DOWN_DB, step_down * response_boost)

    # When a track is pinned near its limits, allow faster recovery away from the edge.
    if current_db >= (max_db - 0.25) and delta < 0:
        step_down *= 1.5
    if current_db <= (min_db + 0.25) and delta > 0:
        step_up *= 1.5

    if delta > step_up:
        command_db = current_db + step_up
    elif delta < -step_down:
        command_db = current_db - step_down
    else:
        command_db = desired_db

    TRACK_CURRENT_DB[track] = command_db

    if debug:
        print(
            f"[process] Track {track}: delta={delta_db:+.3f}dB target={desired_db:+.2f}dB "
            f"step_up={step_up:.2f} step_down={step_down:.2f} applied={command_db:+.2f}dB "
            f"transport=WEB_API"
        )

    return command_db


def _resolve_tracks_for_band(track_map, band):
    track = track_map.get(band)
    if track is None or track == MASTER_TRACK:
        return []
    tracks = track if isinstance(track, (list, tuple)) else [track]
    out = []
    for t in tracks:
        if t == MASTER_TRACK or t not in ENABLED_TRACKS:
            continue
        out.append(int(t))
    return out


def _weighted_mean(values):
    """Weighted average of error values, using absolute magnitude as weight.

    Larger errors pull the aggregate result more strongly than small ones,
    which is more musically responsive than median while still suppressing
    the effect of near-zero errors from unrelated bands.
    """
    if not values:
        return 0.0
    arr = np.asarray(values, dtype=np.float64)
    weights = np.abs(arr)
    total = float(np.sum(weights))
    if total < 1e-12:
        return float(np.mean(arr))
    return float(np.dot(arr, weights) / total)

INSTRUMENT_PRIORITY = {
    "drums": 1.05,
    "bass": 1.0,
    "piano": 0.88,
    "other": 0.9,
    "vocals": 0.65,
    "backing_vocals": 0.55,
}

ROLE_BAND_INFLUENCE = {
    "vocals": {
        "p640": 0.70,
        "p1200": 0.85,
        "p2500": 0.55,
        "p5000": 0.35,
        "p10000": 0.20,
    },
    "backing_vocals": {
        "p640": 0.60,
        "p1200": 0.72,
        "p2500": 0.45,
        "p5000": 0.25,
        "p10000": 0.15,
    },
    "drums": {
        "p80": 1.00,
        "p160": 1.00,
        "p5000": 0.90,
        "p10000": 0.75,
        "p20000": 0.70,
    },
    "piano": {
        "p320": 0.92,
        "p640": 0.88,
        "p1200": 0.70,
    },
}


def _role_weight_for_band(role, band):
    base = INSTRUMENT_PRIORITY.get(role, 1.0)
    return base * ROLE_BAND_INFLUENCE.get(role, {}).get(band, 1.0)


def _smooth_track_error(track, error):
    previous = TRACK_ERROR_EMA.get(track)
    error_value = float(error)
    if previous is None or not np.isfinite(previous):
        TRACK_ERROR_EMA[track] = error_value
        return error_value

    alpha = TRACK_ERROR_SMOOTHING
    if abs(error_value) >= FAST_RESPONSE_ERROR:
        alpha = FAST_RESPONSE_SMOOTHING

    current = error_value
    if previous * error_value < 0.0:
        if abs(error_value) < FAST_RESPONSE_ERROR:
            alpha = TRACK_ERROR_SIGN_FLIP_SMOOTHING
        current *= 0.55

    smoothed = ((1.0 - alpha) * float(previous)) + (alpha * current)
    if abs(smoothed) < (ERROR_DEADBAND * 0.75) and abs(error_value) < (ERROR_DEADBAND * 1.25):
        smoothed = 0.0

    TRACK_ERROR_EMA[track] = float(smoothed)
    return float(smoothed)


def _apply_actions(actions, track_map, debug=False, dry_run=False):
    """Aggregate band errors per track and apply one coherent command per cycle."""
    track_errors = {}
    for band, error in actions:
        tracks = _resolve_tracks_for_band(track_map, band)
        if not tracks:
            if debug:
                print(f"[process] Band '{band}' skipped (no enabled track mapping)")
            continue

        if debug:
            print(f"[process] Band '{band}' error={error:+.3f} -> tracks={tracks}")

        for t in tracks:
            role = TRACK_ROLE_BY_ID.get(t, "other")
            shared_weight = 1.0 / max(1.0, np.sqrt(float(len(tracks))))
            weight = _role_weight_for_band(role, band) * shared_weight
            weighted_error = float(error) * weight
            # >>> LOW-END BOOST DAMPER (anti-boost de graves)
            # Se o erro for POSITIVO (pede BOOST) e a banda estiver no "low end",
            # atenuamos o empurrão para evitar que baixo/bumbo disparem.
            if weighted_error > 0.0 and band in ("p20", "p40", "p80", "p160"):
                weighted_error *= 0.70  # ajuste fino: 0.6–0.85 conforme a sala/PA

            track_errors.setdefault(t, []).append(weighted_error)

    pending = {}
    for t, errors in track_errors.items():
        if not errors:
            continue
        pos = [e for e in errors if e > ERROR_DEADBAND]
        neg = [e for e in errors if e < -ERROR_DEADBAND]
        pending[t] = {
            "pos": pos,
            "neg": neg,
            "agg_pos": _weighted_mean(pos) if pos else 0.0,
            "agg_neg": _weighted_mean(neg) if neg else 0.0,
        }

    # Se existir qualquer corte relevante, congelar boosts neste ciclo
    has_strong_cut = any(abs(v["agg_neg"]) >= (ERROR_DEADBAND * 1.2) for v in pending.values())
    allow_boosts = not has_strong_cut

    raised_tracks = 0
    for t in sorted(pending.keys()):
        pos = pending[t]["pos"]
        neg = pending[t]["neg"]

        if pos and neg:
            # Mantém sua resolução por direção dominante
            choose_negative = abs(_weighted_mean(neg)) >= abs(_weighted_mean(pos))
            aggregate_error = _weighted_mean(neg) if choose_negative else _weighted_mean(pos)
        elif neg:
            aggregate_error = _weighted_mean(neg)
        elif pos:
            aggregate_error = _weighted_mean(pos) if allow_boosts else 0.0
        else:
            aggregate_error = 0.0

        aggregate_error = _smooth_track_error(t, aggregate_error)
        delta_db = _error_to_desired_db(aggregate_error)

        if abs(delta_db) < 1e-6:
            if debug:
                print(f"[process] Track {t}: aggregate error {aggregate_error:+.3f} inside deadband")
            continue
        if (delta_db > 0) and not allow_boosts:
            if debug:
                print(f"[process] Track {t}: boost skipped due to stronger cuts in this cycle")
            continue

        current_db = TRACK_CURRENT_DB.get(t, TRACK_CONFIG_FADER_DB.get(t, 0.0))
        if delta_db > 0 and raised_tracks >= MAX_TRACKS_RAISE_PER_CYCLE:
            if debug:
                print(f"[process] Track {t} queued for next cycle (limit {MAX_TRACKS_RAISE_PER_CYCLE}/cycle)")
            continue

        command_db = _command_track(t, delta_db, error_magnitude=aggregate_error, debug=debug)
        if command_db is not None:
            if command_db > current_db:
                raised_tracks += 1
            if dry_run:
                print(f"[DIAG] DRY-RUN: track {t} would be set to {command_db:+.2f}dB")
            else:
                set_track_db(t, command_db, verbose=debug)


def _refresh_track_levels(debug=False):
    levels = get_tracks_db(sorted(ENABLED_TRACKS), verbose=debug)
    for track, db_value in levels.items():
        TRACK_CURRENT_DB[track] = db_value

def _load_profiles(profiles_path="learning/profiles.json"):
    if not os.path.exists(profiles_path):
        return {}
    with open(profiles_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def _normalize_perceptual_magnitudes(magnitudes):
    mag_arr = np.asarray(magnitudes, dtype=np.float64)
    if mag_arr.size == 0:
        return np.array([], dtype=np.float64)

    rms = float(np.sqrt(np.mean(np.square(mag_arr))))
    if not np.isfinite(rms) or rms < SILENCE_FLOOR_RMS:
        return np.zeros_like(mag_arr, dtype=np.float64)

    # Em vez de dividir pelo máximo (sensível a picos),
    # divida por um percentil alto (95º), que é robusto a "um band dominar".
    p95 = float(np.percentile(mag_arr, 95))
    scale = max(p95, 1e-9)
    norm = mag_arr / scale

    # Limita no topo (mantém faixa 0..1 sem deixar um band "colar" tudo em 0)
    return np.clip(norm, 0.0, 1.0)


def _perceptual_meter_db(magnitudes):
    mag_arr = np.asarray(magnitudes, dtype=np.float64)
    if mag_arr.size == 0:
        return np.array([], dtype=np.float64)

    rms = float(np.sqrt(np.mean(np.square(mag_arr))))
    if not np.isfinite(rms) or rms < SILENCE_FLOOR_RMS:
        return np.full_like(mag_arr, METER_DB_FLOOR, dtype=np.float64)

    rel_db = 20.0 * np.log10(np.maximum(mag_arr, 1e-12) / max(rms, 1e-12))
    return np.clip(rel_db, METER_DB_FLOOR, METER_DB_CEIL)


def _perceptual_dict_from_audio(audio, sample_rate):
    spectrum, freqs = analyze(audio, sample_rate=sample_rate)
    normalized = _normalize_perceptual_magnitudes(spectrum)
    if normalized.size == 0 or freqs.size == 0:
        return {}

    out = {}
    for hz, value in zip(freqs, normalized):
        out[f"p{int(round(float(hz)))}"] = float(value)
    return out


def _perceptual_meter_dict_from_audio(audio, sample_rate):
    spectrum, freqs = analyze(audio, sample_rate=sample_rate)
    meter_db = _perceptual_meter_db(spectrum)
    if meter_db.size == 0 or freqs.size == 0:
        return {}

    out = {}
    for hz, value in zip(freqs, meter_db):
        out[f"p{int(round(float(hz)))}"] = float(value)
    return out


def _compute_band_values(audio, sample_rate):
    """Compute perceptual 11-band values from mono or stereo input.

    For stereo input, each channel is analyzed independently and band values
    are averaged so the decision stage uses true stereo content.
    """
    audio_np = np.asarray(audio)

    if audio_np.ndim <= 1:
        analysis_audio = audio_np.reshape(-1)
        if analysis_audio.size == 0:
            return {}
        return _perceptual_dict_from_audio(analysis_audio, sample_rate=sample_rate)

    channel_count = int(audio_np.shape[1])
    if channel_count <= 0:
        return {}

    per_channel = []
    for ch_idx in range(channel_count):
        channel_audio = np.asarray(audio_np[:, ch_idx]).reshape(-1)
        if channel_audio.size == 0:
            continue
        per_channel.append(_perceptual_dict_from_audio(channel_audio, sample_rate=sample_rate))

    if not per_channel:
        return {}

    band_keys = per_channel[0].keys()
    return {
        band: float(np.mean([channel_values.get(band, 0.0) for channel_values in per_channel]))
        for band in band_keys
    }


def _compute_band_meter_db(audio, sample_rate):
    audio_np = np.asarray(audio)

    if audio_np.ndim <= 1:
        analysis_audio = audio_np.reshape(-1)
        if analysis_audio.size == 0:
            return {}
        return _perceptual_meter_dict_from_audio(analysis_audio, sample_rate=sample_rate)

    channel_count = int(audio_np.shape[1])
    if channel_count <= 0:
        return {}

    per_channel = []
    for ch_idx in range(channel_count):
        channel_audio = np.asarray(audio_np[:, ch_idx]).reshape(-1)
        if channel_audio.size == 0:
            continue
        per_channel.append(_perceptual_meter_dict_from_audio(channel_audio, sample_rate=sample_rate))

    if not per_channel:
        return {}

    band_keys = per_channel[0].keys()
    return {
        band: float(np.mean([channel_values.get(band, METER_DB_FLOOR) for channel_values in per_channel]))
        for band in band_keys
    }


def _audio_rms_db(audio):
    audio_arr = np.asarray(audio, dtype=np.float64)
    if audio_arr.size == 0:
        return float(METER_DB_FLOOR)
    rms = float(np.sqrt(np.mean(np.square(audio_arr))))
    if not np.isfinite(rms) or rms <= 0.0:
        return float(METER_DB_FLOOR)
    return float(max(METER_DB_FLOOR, 20.0 * np.log10(rms)))

def process(audio, sample_rate=SAMPLE_RATE, profile_name=None,
            profiles_path="learning/profiles.json", stem_track_map=None,
            profiles=None, verbose=False):
    import os
    
    # Reload config from file (allows hot-reloading changes from GUI)
    _reload_config()
    
    debug = verbose or os.environ.get("MIX_ROBO_DEBUG", "0") != "0"
    dry_run = _is_dry_run_enabled(debug=debug)

    if debug:
        print(f"[process] Starting audio analysis (shape={audio.shape}, duration={len(audio)/sample_rate:.2f}s)")
    if dry_run:
        print("[DIAG] DRY-RUN enabled: REAPER writes disabled for this cycle")

    lufs = None
    try:
        lufs = get_lufs(audio)
        if lufs < LUFS_WARNING_THRESHOLD:
            print(f"[WARNING LUFS] Level below {LUFS_WARNING_THRESHOLD} LUFS ({lufs:.1f} LUFS). Check master level.")
        elif debug:
            print(f"[process] Integrated LUFS: {lufs:.1f}")
    except Exception:
        pass

    band_values = _compute_band_values(audio, sample_rate)
    band_meter_db = _compute_band_meter_db(audio, sample_rate)
    rms_db = _audio_rms_db(audio)

    if debug:
        print(f"[process] Band values: {band_values}")
        print(f"[process] Band meter dB: {band_meter_db}")
        if lufs is not None:
            print(f"[process] Master meters: LUFS={lufs:.2f} RMS={rms_db:.2f}dB")
        else:
            print(f"[process] Master meters: LUFS=nan RMS={rms_db:.2f}dB")

    track_map = stem_track_map or ACTIVE_STEM_TRACK_MAP

    if profile_name is None:
        actions = decide(band_values)
        if debug:
            top = sorted(actions, key=lambda x: abs(x[1]), reverse=True)[:6]
            print(f"[DIAG] Effective track map: {track_map}")
            print(f"[DIAG] Top band errors: {top}")
            print(f"[DIAG] Band values (0..1): {band_values}")
            print(f"[DIAG] Band meter dB: {band_meter_db}")
        _apply_actions(actions, track_map, debug=debug, dry_run=dry_run)

        _refresh_track_levels(debug=debug)
        return

    profiles = profiles or _load_profiles(profiles_path)
    profile = profiles.get(profile_name)
    if not profile:
        raise ValueError(f"Profile '{profile_name}' not found in {profiles_path}")

    reference = profile.get("master") or profile.get("mix") or profile

    if debug:
        print(f"[process] Reference profile: {reference}")

    actions = decide(band_values, reference=reference)

    if debug:
        top = sorted(actions, key=lambda x: abs(x[1]), reverse=True)[:6]
        print(f"[DIAG] Effective track map: {track_map}")
        print(f"[DIAG] Top band errors: {top}")
        print(f"[DIAG] Band values (0..1): {band_values}")
        print(f"[DIAG] Band meter dB: {band_meter_db}")
        print(f"[process] Actions to execute: {actions}")

    _apply_actions(actions, track_map, debug=debug, dry_run=dry_run)

    _refresh_track_levels(debug=debug)

def process_stems(stems, profile_name=None, profiles_path="learning/profiles.json", stem_track_map=None, verbose=False):
    """Process multiple separated stems and send Web API updates for each."""
    _reload_config()
    stem_track_map = stem_track_map or ACTIVE_STEM_TRACK_MAP
    dry_run = _is_dry_run_enabled(debug=verbose)

    if verbose:
        print(f"Loaded stems: {sorted(stems.keys())}")
        print(f"Using profile: {profile_name}")
        print(f"Using stem->track map: {stem_track_map}")
    if dry_run:
        print("[DIAG] DRY-RUN enabled: REAPER writes disabled for this cycle")

    profiles = _load_profiles(profiles_path)
    profile = profiles.get(profile_name, {}) if profile_name else {}

    for stem_name, audio in stems.items():
        max_samples = SAMPLE_RATE * 10
        audio = audio[:max_samples]

        band_values = _perceptual_dict_from_audio(audio, sample_rate=SAMPLE_RATE)

        reference = profile.get(stem_name)
        actions = decide(band_values, reference=reference)

        track = stem_track_map.get(stem_name)
        if track is None:
            normalized_stem = _normalize_track_name(stem_name)
            if _is_backing_vocal_name(normalized_stem):
                track = stem_track_map.get("backing_vocals") or stem_track_map.get("vocals")
            elif "vocal" in normalized_stem or normalized_stem == "voz":
                track = stem_track_map.get("vocals")
        if track is None:
            if verbose:
                print(f"Skipping stem '{stem_name}' (no track mapping)")
            continue

        for band, error in actions:
            target_db = _error_to_desired_db(error)
            tracks = track if isinstance(track, (list, tuple)) else [track]
            for t in tracks:
                if verbose:
                    print(f"{stem_name}:{band} -> track {t} = {target_db:+.2f}dB (error {error:.3f})")
                if dry_run:
                    print(f"[DIAG] DRY-RUN: track {t} would be set to {target_db:+.2f}dB")
                else:
                    set_track_db(t, target_db, verbose=verbose)

    _refresh_track_levels(debug=verbose)
