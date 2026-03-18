# -*- coding: utf-8 -*-
import json
import os
import unicodedata
import numpy as np

from engine.fft_analyzer import analyze, DEFAULT_GAMMATONE_CENTERS_HZ
from engine.decision_engine import decide
from engine.loudness import get_lufs
from control.web_api_client import (
    configure_from_config,
    get_tracks_db,
    get_tracks_lufs_rms,
    set_track_db,
    auto_configure_dry_run,
)
from config_manager import (
    load_config,
    get_track_db_limits,
    get_track_fader_db,
    get_enabled_tracks,
    get_analysis_settings,
    get_master_track,
    is_dry_run_enabled,
)

SAMPLE_RATE = 48000

# Load configuration from config.json
_config = load_config()
MASTER_TRACK = get_master_track(_config)
TRACK_DB_LIMITS = get_track_db_limits(_config)
TRACK_CONFIG_FADER_DB = get_track_fader_db(_config)
ENABLED_TRACKS = get_enabled_tracks(_config)
FROZEN_TRACKS = {
    int(track_id)
    for track_id, track_data in (_config.get("tracks", {}) or {}).items()
    if isinstance(track_data, dict) and bool(track_data.get("frozen", False))
}

settings = get_analysis_settings(_config)
LUFS_WARNING_THRESHOLD = settings.get("lufs_warning_threshold", -14)
ERROR_GAIN_UP = settings.get("error_gain_up", 1.2)
ERROR_GAIN_DOWN = settings.get("error_gain_down", 2.2)
MAX_STEP_UP_DB = settings.get("max_step_up_db", 0.10)
MAX_STEP_DOWN_DB = settings.get("max_step_down_db", 0.35)
ERROR_DEADBAND = settings.get("error_deadband", 0.18)
MAX_TRACKS_RAISE_PER_CYCLE = settings.get("max_tracks_raise_per_cycle", 1)
SILENCE_FLOOR_RMS = settings.get("silence_floor_rms", 1e-6)
CONTROL_BLEND_SPEC = settings.get("control_blend_spec", 0.78)
CONTROL_BLEND_LUFS = settings.get("control_blend_lufs", 0.22)
LEVEL_GAIN = settings.get("level_gain", 0.45)
LEVEL_ERROR_CLIP_DB = settings.get("level_error_clip_db", 6.0)
LEVEL_DEADBAND_DB = settings.get("level_deadband_db", 0.75)
LEVEL_SOURCE = str(settings.get("level_source", "lufs")).strip().lower()
LEVEL_ROLE_TARGETS_LUFS = settings.get("level_role_targets_lufs", {})
LEVEL_ROLE_TARGETS_RMS = settings.get("level_role_targets_rms", {})

# ── meter_fusion (structured tuning block) ─────────────────────────────────
_mf = settings.get("meter_fusion", {})
if not isinstance(_mf, dict):
    _mf = {}
METER_ALPHA_SPEC          = _mf.get("alpha_spectral",          settings.get("control_blend_spec", 0.5))
METER_ALPHA_LUFS          = _mf.get("alpha_lufs",              settings.get("control_blend_lufs", 0.5))
METER_GAIN_LUFS           = _mf.get("gain_lufs",               settings.get("level_gain", 0.6))
METER_MAX_LUFS_CORRECTION_DB = _mf.get("max_lufs_correction_db", settings.get("level_error_clip_db", 1.2))
METER_DEADBAND_LUFS       = _mf.get("deadband_lufs",           settings.get("level_deadband_db", 0.7))
METER_MIN_ACTIVITY_DB     = _mf.get("min_activity_db", -50.0)
METER_MIN_VALID_SECONDS   = _mf.get("min_valid_seconds", 2.0)
METER_TARGETS_RAW = settings.get("meter_targets", {})
SPECTRAL_NOISE_FLOOR_DB = settings.get("spectral_noise_floor_db", -40.0)
TONAL_PEAK_RATIO = settings.get("tonal_peak_ratio", 4.0)
ENABLE_TONAL_PEAK_GUARD = settings.get("enable_tonal_peak_guard", True)
ENABLE_SINGLE_SOURCE_SPECTRAL_GUARD = settings.get("enable_single_source_spectral_guard", True)

try:
    SILENCE_FLOOR_RMS = float(SILENCE_FLOOR_RMS)
except (TypeError, ValueError):
    SILENCE_FLOOR_RMS = 1e-6
if not np.isfinite(SILENCE_FLOOR_RMS) or SILENCE_FLOOR_RMS < 0.0 or SILENCE_FLOOR_RMS > 1e-2:
    SILENCE_FLOOR_RMS = 1e-6


def _safe_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(parsed):
        return float(default)
    return float(parsed)


def _safe_bool(value, default):
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    return str(value).strip().lower() not in {"", "0", "false", "no", "off"}


def _normalize_blend_weights(alpha_spec, alpha_level):
    spec = max(0.0, _safe_float(alpha_spec, 0.78))
    level = max(0.0, _safe_float(alpha_level, 0.22))
    total = spec + level
    if total <= 1e-9:
        return 1.0, 0.0
    return spec / total, level / total


def _sanitize_role_targets(raw_targets, fallback):
    out = dict(fallback)
    if not isinstance(raw_targets, dict):
        return out
    for role, target in raw_targets.items():
        role_key = str(role).strip().lower()
        if not role_key:
            continue
        out[role_key] = _safe_float(target, out.get(role_key, -22.0))
    return out


DEFAULT_LEVEL_ROLE_TARGETS = {
    "vocals": -18.0,
    "lead": -18.0,
    "lead_vocal": -18.0,
    "backing_vocals": -22.0,
    "piano": -23.0,
    "bass": -20.0,
    "drums": -20.0,
    "other": -23.0,
}

CONTROL_BLEND_SPEC, CONTROL_BLEND_LUFS = _normalize_blend_weights(CONTROL_BLEND_SPEC, CONTROL_BLEND_LUFS)
LEVEL_GAIN = max(0.0, _safe_float(LEVEL_GAIN, 0.45))
LEVEL_ERROR_CLIP_DB = max(0.1, _safe_float(LEVEL_ERROR_CLIP_DB, 6.0))
LEVEL_DEADBAND_DB = max(0.0, _safe_float(LEVEL_DEADBAND_DB, 0.75))
if LEVEL_SOURCE not in ("lufs", "rms", "rms_db"):
    LEVEL_SOURCE = "lufs"
LEVEL_ROLE_TARGETS_LUFS = _sanitize_role_targets(LEVEL_ROLE_TARGETS_LUFS, DEFAULT_LEVEL_ROLE_TARGETS)
LEVEL_ROLE_TARGETS_RMS = _sanitize_role_targets(LEVEL_ROLE_TARGETS_RMS, DEFAULT_LEVEL_ROLE_TARGETS)

# ── meter_targets per-role LUFS defaults (short-term ~3s, musical para automação ao vivo) ──
DEFAULT_METER_TARGETS = {
    "vocals":         -28.0,
    "lead":           -28.0,
    "lead_vocal":     -28.0,
    "backing_vocals": -30.5,
    "piano":          -30.0,
    "guitar":         -30.5,
    "violao":         -30.0,
    "bass":           -30.0,
    "drums":          -29.5,
    "other":          -30.5,
}
METER_ALPHA_SPEC, METER_ALPHA_LUFS = _normalize_blend_weights(METER_ALPHA_SPEC, METER_ALPHA_LUFS)
METER_GAIN_LUFS              = max(0.0, _safe_float(METER_GAIN_LUFS, 0.6))
METER_MAX_LUFS_CORRECTION_DB = max(0.1, _safe_float(METER_MAX_LUFS_CORRECTION_DB, 1.2))
METER_DEADBAND_LUFS          = max(0.0, _safe_float(METER_DEADBAND_LUFS, 0.7))
METER_MIN_ACTIVITY_DB        = _safe_float(METER_MIN_ACTIVITY_DB, -50.0)
METER_MIN_VALID_SECONDS      = max(0.0, _safe_float(METER_MIN_VALID_SECONDS, 2.0))
METER_TARGETS = _sanitize_role_targets(METER_TARGETS_RAW, DEFAULT_METER_TARGETS)
SPECTRAL_NOISE_FLOOR_DB = _safe_float(SPECTRAL_NOISE_FLOOR_DB, -40.0)
TONAL_PEAK_RATIO = max(1.25, _safe_float(TONAL_PEAK_RATIO, 4.0))
ENABLE_TONAL_PEAK_GUARD = _safe_bool(ENABLE_TONAL_PEAK_GUARD, True)
ENABLE_SINGLE_SOURCE_SPECTRAL_GUARD = _safe_bool(ENABLE_SINGLE_SOURCE_SPECTRAL_GUARD, True)

# =============================================================================
# Web API write/read workflow
# =============================================================================
TRACK_CURRENT_DB = dict(TRACK_CONFIG_FADER_DB)
TRACK_ERROR_EMA = {}
TRACK_ROLE_BY_ID = {}
ACTIVE_LINEUP_SCENE = ""

PERCEPTUAL_BAND_KEYS = tuple(f"p{int(round(float(hz)))}" for hz in DEFAULT_GAMMATONE_CENTERS_HZ)
METER_DB_FLOOR = -96.0
METER_DB_CEIL = 12.0
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
    """Check if DRY-RUN is enabled via environment variable or config."""
    # Check environment variable first (takes precedence)
    raw_value = os.environ.get("MIX_ROBO_DRY_RUN")
    if raw_value is not None:
        is_env_dry_run = str(raw_value).strip().lower() not in {"", "0", "false", "no", "off"}
        if is_env_dry_run:
            return True
    
    # Then check config
    try:
        return is_dry_run_enabled()
    except Exception:
        return False


def _is_cut_first_enabled():
    raw_value = os.environ.get("MIX_ROBO_CUT_FIRST")
    if raw_value is None:
        return True
    return str(raw_value).strip().lower() not in {"", "0", "false", "no", "off"}


def _is_vocal_focus_enabled():
    raw_value = os.environ.get("MIX_ROBO_VOCAL_FOCUS")
    if raw_value is None:
        return False
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

    lineup_cfg = config.get("lineup", {}) if isinstance(config.get("lineup", {}), dict) else {}
    active_scene_name = str(lineup_cfg.get("active_scene", "")).strip()
    scenes_cfg = lineup_cfg.get("scenes", {}) if isinstance(lineup_cfg.get("scenes", {}), dict) else {}
    active_scene = scenes_cfg.get(active_scene_name, {}) if active_scene_name else lineup_cfg
    if not isinstance(active_scene, dict):
        active_scene = {}

    present_roles = None
    present_roles_raw = active_scene.get("present_roles")
    if isinstance(present_roles_raw, (list, tuple, set)) and present_roles_raw:
        present_roles = {str(role).strip().lower() for role in present_roles_raw if str(role).strip()}

    band_targets_override = active_scene.get("band_targets", {})
    if not isinstance(band_targets_override, dict):
        band_targets_override = {}

    lineup_scene_enabled = bool(active_scene_name) or bool(present_roles) or bool(band_targets_override)

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

    def _role_allowed(role):
        if present_roles is None:
            return True
        return str(role).strip().lower() in present_roles

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

    drums = _find_track(["drums", "drum", "bateria"])
    if drums is None and _role_allowed("drums"):
        drums = _fallback(0)

    bass = _find_track(["bass", "baixo"])
    if bass is None and _role_allowed("bass"):
        bass = _fallback(1)
    if bass is None and _role_allowed("bass"):
        bass = drums

    key_aliases = ["keys", "key", "piano", "teclado", "synth", "pad", "kbd"]
    vocal_aliases = ["vocals", "vocal", "voz", "lead", "vox"]

    keys = _find_track(key_aliases)
    if keys is None and _role_allowed("piano"):
        keys = _fallback(2)
    if keys is None and _role_allowed("piano"):
        keys = drums
    keys_layers = _find_tracks(key_aliases)

    vocals = _find_track(vocal_aliases)
    if vocals is None and _role_allowed("vocals"):
        vocals = _fallback(3)
    if vocals is None and _role_allowed("vocals"):
        vocals = keys or drums

    backing_vocals = _find_tracks(["back", "backing", "bv", "choir", "coro"])
    for tid in enabled_tracks:
        if _is_backing_vocal_name(normalized_names.get(tid, "")) and tid not in backing_vocals:
            backing_vocals.append(tid)

    guitar = _find_track(["guitar", "gtr", "guitarra", "violao"])
    if guitar is None and _role_allowed("guitar"):
        guitar = _fallback(4)

    other = _find_track(["other", "outros", "fx", "sfx"])
    if other is None and _role_allowed("other"):
        other = guitar or keys


    # -------- TARGET GROUPS --------

    bass_targets = _unique([bass])
    drum_targets = _unique([drums])
    key_targets = _unique([keys] + keys_layers)
    vocal_targets = _unique([vocals] + backing_vocals)
    guitar_targets = _unique([guitar])

    air_targets = _unique(drum_targets + vocal_targets)
    vocal_focus_enabled = _is_vocal_focus_enabled()


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
        "p640": vocal_targets if vocal_focus_enabled else _unique(key_targets + vocal_targets),

        # VOCAL BODY
        "p1200": vocal_targets,

        # PRESENCE
        "p2500": vocal_targets if vocal_focus_enabled else _unique(vocal_targets + guitar_targets),

        # ATTACK
        "p5000": vocal_targets if vocal_focus_enabled else _unique(drum_targets + vocal_targets),

        # AIR
        "p10000": vocal_targets if vocal_focus_enabled else air_targets,
        "p20000": drum_targets,
    }

    role_targets = {
        "drums": drum_targets,
        "bass": bass_targets,
        "piano": key_targets,
        "vocals": _unique([vocals]),
        "backing_vocals": _unique(backing_vocals),
        "guitar": guitar_targets,
        "other": _unique([other]),
    }

    if band_targets_override:
        for band, spec in band_targets_override.items():
            if not isinstance(spec, (list, tuple)):
                continue
            custom_targets = []
            for token in spec:
                if isinstance(token, int):
                    candidate = int(token)
                    if candidate != master and candidate in enabled_tracks:
                        custom_targets.append(candidate)
                    continue

                token_str = str(token).strip().lower()
                if not token_str:
                    continue
                if token_str in role_targets:
                    custom_targets.extend(role_targets[token_str])
                    continue
                try:
                    candidate = int(token_str)
                except ValueError:
                    continue
                if candidate != master and candidate in enabled_tracks:
                    custom_targets.append(candidate)

            dynamic_map[str(band)] = _unique(custom_targets)

    # Avoid empty targets for any band/stem key expected by processing paths.
    for key, fallback in DEFAULT_STEM_TRACK_MAP.items():
        value = dynamic_map.get(key)
        if value is None:
            if lineup_scene_enabled:
                dynamic_map[key] = [] if isinstance(fallback, list) else None
            else:
                dynamic_map[key] = fallback
            continue
        if isinstance(value, list) and not value:
            if not lineup_scene_enabled:
                dynamic_map[key] = fallback

    return dynamic_map

def _reload_config():
    """Reload configuration from config.json - useful for hot-reloading."""
    global _config, MASTER_TRACK, TRACK_DB_LIMITS, TRACK_CONFIG_FADER_DB
    global ENABLED_TRACKS, ACTIVE_STEM_TRACK_MAP, TRACK_ROLE_BY_ID, ACTIVE_LINEUP_SCENE
    global FROZEN_TRACKS
    global LUFS_WARNING_THRESHOLD, ERROR_GAIN_UP, ERROR_GAIN_DOWN
    global MAX_STEP_UP_DB, MAX_STEP_DOWN_DB, ERROR_DEADBAND, MAX_TRACKS_RAISE_PER_CYCLE
    global SILENCE_FLOOR_RMS, CONTROL_BLEND_SPEC, CONTROL_BLEND_LUFS
    global LEVEL_GAIN, LEVEL_ERROR_CLIP_DB, LEVEL_DEADBAND_DB, LEVEL_SOURCE
    global LEVEL_ROLE_TARGETS_LUFS, LEVEL_ROLE_TARGETS_RMS
    global METER_ALPHA_SPEC, METER_ALPHA_LUFS, METER_GAIN_LUFS
    global METER_MAX_LUFS_CORRECTION_DB, METER_DEADBAND_LUFS
    global METER_MIN_ACTIVITY_DB, METER_MIN_VALID_SECONDS, METER_TARGETS
    
    _config = load_config()
    lineup_cfg = _config.get("lineup", {}) if isinstance(_config.get("lineup", {}), dict) else {}
    ACTIVE_LINEUP_SCENE = str(lineup_cfg.get("active_scene", "")).strip()
    configure_from_config(_config)
    auto_configure_dry_run(_config)
    ACTIVE_STEM_TRACK_MAP = _build_track_map_from_config(_config)
    TRACK_ROLE_BY_ID = _build_track_roles_from_config(_config)
    MASTER_TRACK = get_master_track(_config)
    TRACK_DB_LIMITS = get_track_db_limits(_config)
    configured_faders = get_track_fader_db(_config)
    ENABLED_TRACKS = get_enabled_tracks(_config)
    FROZEN_TRACKS = {
        int(track_id)
        for track_id, track_data in (_config.get("tracks", {}) or {}).items()
        if isinstance(track_data, dict) and bool(track_data.get("frozen", False))
    }

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
    CONTROL_BLEND_SPEC = settings.get("control_blend_spec", 0.78)
    CONTROL_BLEND_LUFS = settings.get("control_blend_lufs", 0.22)
    LEVEL_GAIN = settings.get("level_gain", 0.45)
    LEVEL_ERROR_CLIP_DB = settings.get("level_error_clip_db", 6.0)
    LEVEL_DEADBAND_DB = settings.get("level_deadband_db", 0.75)
    LEVEL_SOURCE = str(settings.get("level_source", "lufs")).strip().lower()
    LEVEL_ROLE_TARGETS_LUFS = settings.get("level_role_targets_lufs", {})
    LEVEL_ROLE_TARGETS_RMS = settings.get("level_role_targets_rms", {})
    _rmf = settings.get("meter_fusion", {})
    if not isinstance(_rmf, dict):
        _rmf = {}
    METER_ALPHA_SPEC          = _rmf.get("alpha_spectral",          settings.get("control_blend_spec", 0.5))
    METER_ALPHA_LUFS          = _rmf.get("alpha_lufs",              settings.get("control_blend_lufs", 0.5))
    METER_GAIN_LUFS           = _rmf.get("gain_lufs",               settings.get("level_gain", 0.6))
    METER_MAX_LUFS_CORRECTION_DB = _rmf.get("max_lufs_correction_db", settings.get("level_error_clip_db", 1.2))
    METER_DEADBAND_LUFS       = _rmf.get("deadband_lufs",           settings.get("level_deadband_db", 0.7))
    METER_MIN_ACTIVITY_DB     = _rmf.get("min_activity_db", -50.0)
    METER_MIN_VALID_SECONDS   = _rmf.get("min_valid_seconds", 2.0)
    _meter_targets_raw        = settings.get("meter_targets", {})
    try:
        SILENCE_FLOOR_RMS = float(SILENCE_FLOOR_RMS)
    except (TypeError, ValueError):
        SILENCE_FLOOR_RMS = 1e-6
    if not np.isfinite(SILENCE_FLOOR_RMS) or SILENCE_FLOOR_RMS < 0.0 or SILENCE_FLOOR_RMS > 1e-2:
        SILENCE_FLOOR_RMS = 1e-6

    CONTROL_BLEND_SPEC, CONTROL_BLEND_LUFS = _normalize_blend_weights(CONTROL_BLEND_SPEC, CONTROL_BLEND_LUFS)
    LEVEL_GAIN = max(0.0, _safe_float(LEVEL_GAIN, 0.45))
    LEVEL_ERROR_CLIP_DB = max(0.1, _safe_float(LEVEL_ERROR_CLIP_DB, 6.0))
    LEVEL_DEADBAND_DB = max(0.0, _safe_float(LEVEL_DEADBAND_DB, 0.75))
    if LEVEL_SOURCE not in ("lufs", "rms", "rms_db"):
        LEVEL_SOURCE = "lufs"
    LEVEL_ROLE_TARGETS_LUFS = _sanitize_role_targets(LEVEL_ROLE_TARGETS_LUFS, DEFAULT_LEVEL_ROLE_TARGETS)
    LEVEL_ROLE_TARGETS_RMS = _sanitize_role_targets(LEVEL_ROLE_TARGETS_RMS, DEFAULT_LEVEL_ROLE_TARGETS)
    METER_ALPHA_SPEC, METER_ALPHA_LUFS = _normalize_blend_weights(METER_ALPHA_SPEC, METER_ALPHA_LUFS)
    METER_GAIN_LUFS              = max(0.0, _safe_float(METER_GAIN_LUFS, 0.6))
    METER_MAX_LUFS_CORRECTION_DB = max(0.1, _safe_float(METER_MAX_LUFS_CORRECTION_DB, 1.2))
    METER_DEADBAND_LUFS          = max(0.0, _safe_float(METER_DEADBAND_LUFS, 0.7))
    METER_MIN_ACTIVITY_DB        = _safe_float(METER_MIN_ACTIVITY_DB, -50.0)
    METER_MIN_VALID_SECONDS      = max(0.0, _safe_float(METER_MIN_VALID_SECONDS, 2.0))
    METER_TARGETS = _sanitize_role_targets(_meter_targets_raw, DEFAULT_METER_TARGETS)


def _track_level_target(role):
    """Return the short-term LUFS target for a given role."""
    role_key = str(role or "other").strip().lower() or "other"
    return METER_TARGETS.get(role_key, METER_TARGETS.get("other", -30.5))


def _compute_level_delta_db(track, role, track_meters):
    if LEVEL_GAIN <= 0.0:
        return 0.0

    meter = track_meters.get(int(track), {})
    if not meter:
        return 0.0

    level_source = LEVEL_SOURCE
    if level_source in ("rms", "rms_db"):
        measured = meter.get("rms_db")
    else:
        measured = meter.get("lufs")

    if measured is None and "rms_db" in meter:
        level_source = "rms_db"
        measured = meter.get("rms_db")
    elif measured is None and "lufs" in meter:
        level_source = "lufs"
        measured = meter.get("lufs")

    if measured is None:
        return 0.0

    measured_value = _safe_float(measured, np.nan)
    if not np.isfinite(measured_value):
        return 0.0

    # Gate: ignore tracks that are effectively silent
    if measured_value < METER_MIN_ACTIVITY_DB:
        return 0.0

    target = _track_level_target(role)
    level_error = float(target) - measured_value

    # Deadband: small deviations within the neutral zone are ignored
    if abs(level_error) <= METER_DEADBAND_LUFS:
        return 0.0

    # Remove the neutral-zone offset so only meaningful deviations drive control
    if level_error > 0.0:
        level_error -= METER_DEADBAND_LUFS
    else:
        level_error += METER_DEADBAND_LUFS

    level_error = float(np.clip(level_error, -METER_MAX_LUFS_CORRECTION_DB, METER_MAX_LUFS_CORRECTION_DB))
    return float(METER_GAIN_LUFS * level_error)

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
    # ... lógica que define 'command_db' a partir de step_up/step_down ...
    # GARANTIA: nunca sair dos limites da trilha
    command_db = max(min_db, min(command_db, max_db))
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
        "p640": 0.55,
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
        "p320":  0.90,  # ↓ sutil para não competir demais com violão
        "p640":  0.15,  # ligeiro ajuste
        "p1200": 0.90,  # ↑ antes: 0.70  (ataque/clareza do piano)
        "p2500": 0.65,  # novo (presença de piano)
        "p5000": 0.45,  # novo (brilho/palheta/sustain)
    },
}

VOCAL_PRESENCE_BANDS = ("p1200", "p2500", "p5000", "p10000")
P640_SINGLE_NEG_DAMPING = 0.45
VOCAL_ROLES = {"vocals", "backing_vocals"}
TRACK_ACTIVITY_RMS_DB_THRESHOLD = -50.0
TRACK_ACTIVITY_LUFS_THRESHOLD = -48.0

ROLE_ACTIVITY_PROXY_BANDS = {
    "vocals": ("p640", "p1200", "p2500", "p5000", "p10000"),
    "backing_vocals": ("p640", "p1200", "p2500", "p5000", "p10000"),
    "drums": ("p80", "p160", "p5000"),
    "bass": ("p20", "p40", "p80", "p160"),
    "piano": ("p320", "p640", "p1200", "p2500"),
    "other": ("p320", "p640", "p1200", "p2500"),
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


def _is_track_meter_active(meter):
    if not isinstance(meter, dict):
        return False

    rms_db = meter.get("rms_db")
    if rms_db is not None:
        rms_val = _safe_float(rms_db, np.nan)
        if np.isfinite(rms_val) and rms_val >= METER_MIN_ACTIVITY_DB:
            return True

    lufs = meter.get("lufs")
    if lufs is not None:
        lufs_val = _safe_float(lufs, np.nan)
        if np.isfinite(lufs_val) and lufs_val >= METER_MIN_ACTIVITY_DB:
            return True

    return False


def _active_roles_from_track_meters(track_ids, track_meters):
    active_roles = set()
    for track_id in track_ids:
        meter = track_meters.get(int(track_id), {})
        if not _is_track_meter_active(meter):
            continue
        role = TRACK_ROLE_BY_ID.get(int(track_id), "other")
        active_roles.add(str(role).strip().lower() or "other")
    return active_roles


def _active_roles_from_band_meter_proxy(band_meter_db):
    active_roles = set()
    if not isinstance(band_meter_db, dict):
        return active_roles

    for role, band_keys in ROLE_ACTIVITY_PROXY_BANDS.items():
        values = [
            _safe_float(band_meter_db.get(band_key), np.nan)
            for band_key in band_keys
            if band_key in band_meter_db
        ]
        values = [value for value in values if np.isfinite(value)]
        if not values:
            continue
        mean_db = float(np.mean(values))
        if mean_db >= METER_MIN_ACTIVITY_DB:
            active_roles.add(role)
    return active_roles


def _candidate_tracks_from_actions(actions, track_map):
    candidates = []
    for band, _error in actions:
        candidates.extend(_resolve_tracks_for_band(track_map, band))
    return sorted({int(track) for track in candidates}) if candidates else []


def _build_spectral_guard_state(band_values, band_meter_db):
    pairs = []
    if isinstance(band_values, dict):
        for band, value in band_values.items():
            value_f = _safe_float(value, 0.0)
            if np.isfinite(value_f) and value_f > 0.0:
                pairs.append((str(band), value_f))

    pairs.sort(key=lambda item: item[1], reverse=True)
    dominant_band = pairs[0][0] if pairs else None
    dominant_value = pairs[0][1] if pairs else 0.0
    second_value = pairs[1][1] if len(pairs) > 1 else 0.0
    peak_ratio = dominant_value / max(1e-6, second_value)
    dominant_db = _safe_float((band_meter_db or {}).get(dominant_band), METER_DB_FLOOR) if dominant_band else METER_DB_FLOOR
    tonal_peak = (
        ENABLE_TONAL_PEAK_GUARD
        and bool(dominant_band)
        and dominant_db >= SPECTRAL_NOISE_FLOOR_DB
        and peak_ratio >= TONAL_PEAK_RATIO
    )
    return {
        "dominant_band": dominant_band,
        "dominant_value": float(dominant_value),
        "second_value": float(second_value),
        "peak_ratio": float(peak_ratio),
        "dominant_db": float(dominant_db),
        "tonal_peak": tonal_peak,
    }


def _filter_actions_with_spectral_guards(actions, track_map, band_values, band_meter_db, debug=False):
    if not actions:
        return actions, {
            "dominant_band": None,
            "peak_ratio": 0.0,
            "tonal_peak": False,
            "single_source": False,
            "active_roles": [],
        }

    guard_state = _build_spectral_guard_state(band_values, band_meter_db)
    dominant_band = guard_state["dominant_band"]
    active_roles = set()

    if ENABLE_SINGLE_SOURCE_SPECTRAL_GUARD:
        candidate_tracks = _candidate_tracks_from_actions(actions, track_map)
        if candidate_tracks:
            try:
                candidate_meters = get_tracks_lufs_rms(candidate_tracks, verbose=debug)
                active_roles = _active_roles_from_track_meters(candidate_tracks, candidate_meters)
            except Exception as exc:
                if debug:
                    print(f"[process] Spectral guard meter read failed: {exc}")

    single_source = bool(active_roles) and len(active_roles) <= 1 and bool(dominant_band)
    filtered_actions = []
    for band, error in actions:
        band_db = _safe_float((band_meter_db or {}).get(band), METER_DB_FLOOR)

        if np.isfinite(band_db) and band_db < SPECTRAL_NOISE_FLOOR_DB:
            if debug:
                print(
                    f"[DIAG] Spectral guard: band {band} ignored "
                    f"(meter={band_db:+.2f}dB < floor {SPECTRAL_NOISE_FLOOR_DB:+.2f}dB)"
                )
            continue

        if dominant_band and band != dominant_band and guard_state["tonal_peak"]:
            if debug:
                print(
                    f"[DIAG] Tonal peak guard: band {band} ignored "
                    f"(dominant={dominant_band}, ratio={guard_state['peak_ratio']:.2f})"
                )
            continue

        if dominant_band and band != dominant_band and single_source:
            if debug:
                print(
                    f"[DIAG] Single-source guard: band {band} ignored "
                    f"(dominant={dominant_band}, active_roles={sorted(active_roles)})"
                )
            continue

        filtered_actions.append((band, error))

    if debug:
        print(
            f"[DIAG] Spectral guard state: dominant={dominant_band} "
            f"ratio={guard_state['peak_ratio']:.2f} tonal_peak={guard_state['tonal_peak']} "
            f"single_source={single_source} active_roles={sorted(active_roles)}"
        )

    guard_state["single_source"] = single_source
    guard_state["active_roles"] = sorted(active_roles)
    return filtered_actions, guard_state


def _apply_actions(actions, track_map, debug=False, dry_run=False, band_meter_db=None):
    """Aggregate band errors per track and apply one coherent command per cycle."""
    presence_positive_count = sum(
        1
        for band, error in actions
        if band in VOCAL_PRESENCE_BANDS and float(error) > ERROR_DEADBAND
    )
    p640_is_negative = any(band == "p640" and float(error) < -ERROR_DEADBAND for band, error in actions)
    other_negative_presence = any(
        band in VOCAL_PRESENCE_BANDS and float(error) < -ERROR_DEADBAND
        for band, error in actions
    )
    damp_p640_for_vocals = p640_is_negative and (not other_negative_presence) and (presence_positive_count >= 2)

    if debug and damp_p640_for_vocals:
        print(
            "[DIAG] p640 negative damped for vocals/backing "
            f"(presence positives={presence_positive_count}, factor={P640_SINGLE_NEG_DAMPING:.2f})"
        )

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

            if (
                damp_p640_for_vocals
                and band == "p640"
                and role in ("vocals", "backing_vocals")
                and weighted_error < 0.0
            ):
                weighted_error *= P640_SINGLE_NEG_DAMPING

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

    track_meters = {}
    if pending and not dry_run:
        try:
            track_meters = get_tracks_lufs_rms(sorted(pending.keys()), verbose=debug)
        except Exception as exc:
            track_meters = {}
            if debug:
                print(f"[process] Track meter read failed: {exc}")

    active_roles = set()
    if pending and track_meters:
        active_roles = _active_roles_from_track_meters(pending.keys(), track_meters)
    if not active_roles:
        active_roles = _active_roles_from_band_meter_proxy(band_meter_db)

    vocal_only_content = bool(active_roles) and active_roles.issubset(VOCAL_ROLES)
    if debug:
        print(
            f"[DIAG] Active roles={sorted(active_roles)} "
            f"vocal_only_content={vocal_only_content}"
        )

    # Se existir qualquer corte relevante, congelar boosts neste ciclo
    cut_first_enabled = _is_cut_first_enabled()
    has_strong_cut = any(abs(v["agg_neg"]) >= (ERROR_DEADBAND * 1.2) for v in pending.values())
    allow_boosts = (not cut_first_enabled) or (not has_strong_cut)

    raised_tracks = 0
    for t in sorted(pending.keys()):
        if t in FROZEN_TRACKS:
            if debug:
                print(f"[process] Track {t}: frozen (command skipped)")
            continue

        pos = pending[t]["pos"]
        neg = pending[t]["neg"]

        if pos and neg:
            # Mantém sua resolução por direção dominante
            choose_negative = abs(_weighted_mean(neg)) >= abs(_weighted_mean(pos))
            aggregate_spec_error = _weighted_mean(neg) if choose_negative else _weighted_mean(pos)
        elif neg:
            aggregate_spec_error = _weighted_mean(neg)
        elif pos:
            aggregate_spec_error = _weighted_mean(pos) if allow_boosts else 0.0
        else:
            aggregate_spec_error = 0.0

        aggregate_spec_error = _smooth_track_error(t, aggregate_spec_error)
        spectral_delta_db = _error_to_desired_db(aggregate_spec_error)

        role = TRACK_ROLE_BY_ID.get(t, "other")
        level_delta_db = _compute_level_delta_db(t, role, track_meters)
        delta_db = (METER_ALPHA_SPEC * spectral_delta_db) + (METER_ALPHA_LUFS * level_delta_db)

        if abs(delta_db) < 1e-6:
            if debug:
                print(f"[process] Track {t}: fused delta inside deadband")
            continue
        if (delta_db > 0) and not allow_boosts:
            if debug:
                print(f"[process] Track {t}: boost skipped due to stronger cuts in this cycle")
            continue
        if (delta_db > 0) and vocal_only_content and (role in VOCAL_ROLES):
            if debug:
                print(f"[process] Track {t}: boost skipped (vocal-only content guard)")
            continue

        if debug:
            meter = track_meters.get(t, {})
            meter_lufs = meter.get("lufs")
            meter_rms = meter.get("rms_db")
            print(
                f"[DIAG] Track {t} role={role} spec={spectral_delta_db:+.3f}dB "
                f"level={level_delta_db:+.3f}dB fused={delta_db:+.3f}dB "
                f"meter(lufs={(f'{meter_lufs:+.2f}' if meter_lufs is not None else '--')}, "
                f"rms={(f'{meter_rms:+.2f}' if meter_rms is not None else '--')})"
            )

        current_db = TRACK_CURRENT_DB.get(t, TRACK_CONFIG_FADER_DB.get(t, 0.0))
        if delta_db > 0 and raised_tracks >= MAX_TRACKS_RAISE_PER_CYCLE:
            if debug:
                print(f"[process] Track {t} queued for next cycle (limit {MAX_TRACKS_RAISE_PER_CYCLE}/cycle)")
            continue

        command_db = _command_track(t, delta_db, error_magnitude=aggregate_spec_error, debug=debug)
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
    if debug and not _is_cut_first_enabled():
        print("[DIAG] CUT-FIRST disabled: boosts allowed even when cuts are present")
    if debug and _is_vocal_focus_enabled():
        print("[DIAG] VOCAL-FOCUS enabled: p640/p1200/p2500/p5000/p10000 mapped only to vocals/backing")
    if debug and ACTIVE_LINEUP_SCENE:
        print(f"[DIAG] Active lineup scene: {ACTIVE_LINEUP_SCENE}")

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
        _apply_actions(actions, track_map, debug=debug, dry_run=dry_run, band_meter_db=band_meter_db)

        if not dry_run:
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

    _apply_actions(actions, track_map, debug=debug, dry_run=dry_run, band_meter_db=band_meter_db)

    if not dry_run:
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
    if verbose and not _is_cut_first_enabled():
        print("[DIAG] CUT-FIRST disabled: boosts allowed even when cuts are present")
    if verbose and _is_vocal_focus_enabled():
        print("[DIAG] VOCAL-FOCUS enabled: p640/p1200/p2500/p5000/p10000 mapped only to vocals/backing")
    if verbose and ACTIVE_LINEUP_SCENE:
        print(f"[DIAG] Active lineup scene: {ACTIVE_LINEUP_SCENE}")

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

    if not dry_run:
        _refresh_track_levels(debug=verbose)
