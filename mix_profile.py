# -*- coding: utf-8 -*-
import json
import os
import unicodedata
import numpy as np

from engine.fft_analyzer import analyze
from engine.tonal_balance import bands
from engine.decision_engine import decide
from engine.loudness import get_lufs
from control.osc_client import configure_from_config, set_volume
from config_manager import load_config, get_track_db_limits, get_enabled_tracks, get_analysis_settings, get_master_track

SAMPLE_RATE = 44100

# Load configuration from config.json
_config = load_config()
MASTER_TRACK = get_master_track(_config)
TRACK_DB_LIMITS = get_track_db_limits(_config)
ENABLED_TRACKS = get_enabled_tracks(_config)

settings = get_analysis_settings(_config)
LUFS_WARNING_THRESHOLD = settings.get("lufs_warning_threshold", -14)
ERROR_GAIN_UP = settings.get("error_gain_up", 1.2)
ERROR_GAIN_DOWN = settings.get("error_gain_down", 2.2)
MAX_STEP_UP_DB = settings.get("max_step_up_db", 0.10)
MAX_STEP_DOWN_DB = settings.get("max_step_down_db", 0.35)
ERROR_DEADBAND = settings.get("error_deadband", 0.18)
MAX_TRACKS_RAISE_PER_CYCLE = settings.get("max_tracks_raise_per_cycle", 1)

# =============================================================================
# Reaper OSC fader math
# =============================================================================
_REAPER_FADER_MAX_AMP_ROOT = 10.0 ** (12.0 / 80.0)

def _db_to_reaper(db):
    return 10.0 ** (db / 80.0) / _REAPER_FADER_MAX_AMP_ROOT

def _reaper_to_db(normalized):
    if normalized <= 0.0:
        return float("-inf")
    return 80.0 * (np.log10(normalized * _REAPER_FADER_MAX_AMP_ROOT))

REAPER_UNITY_FADER = _db_to_reaper(0.0)

TRACK_CURRENT_DB = {}

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
}

ACTIVE_STEM_TRACK_MAP = dict(DEFAULT_STEM_TRACK_MAP)

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

    sub_low = _unique([drums, bass])
    key_targets = _unique([keys] + keys_layers)
    vocal_targets = _unique([vocals] + backing_vocals)
    mid_targets = _unique(key_targets + [other, guitar] + vocal_targets)
    highmid_targets = _unique(key_targets + [guitar] + vocal_targets)
    air_targets = _unique([drums] + key_targets + vocal_targets)

    dynamic_map = {
        "drums": drums,
        "bass": bass,
        "piano": keys,
        "other": other,
        "vocals": vocals,
        "backing_vocals": vocal_targets,
        "sub": sub_low,
        "low": sub_low,
        "mid": mid_targets,
        "highmid": highmid_targets,
        "air": air_targets,
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
    global _config, MASTER_TRACK, TRACK_DB_LIMITS, ENABLED_TRACKS, ACTIVE_STEM_TRACK_MAP
    global LUFS_WARNING_THRESHOLD, ERROR_GAIN_UP, ERROR_GAIN_DOWN
    global MAX_STEP_UP_DB, MAX_STEP_DOWN_DB, ERROR_DEADBAND, MAX_TRACKS_RAISE_PER_CYCLE
    
    _config = load_config()
    configure_from_config(_config)
    ACTIVE_STEM_TRACK_MAP = _build_track_map_from_config(_config)
    MASTER_TRACK = get_master_track(_config)
    TRACK_DB_LIMITS = get_track_db_limits(_config)
    ENABLED_TRACKS = get_enabled_tracks(_config)
    
    settings = get_analysis_settings(_config)
    LUFS_WARNING_THRESHOLD = settings.get("lufs_warning_threshold", -14)
    ERROR_GAIN_UP = settings.get("error_gain_up", 1.2)
    ERROR_GAIN_DOWN = settings.get("error_gain_down", 2.2)
    MAX_STEP_UP_DB = settings.get("max_step_up_db", 0.10)
    MAX_STEP_DOWN_DB = settings.get("max_step_down_db", 0.35)
    ERROR_DEADBAND = settings.get("error_deadband", 0.18)
    MAX_TRACKS_RAISE_PER_CYCLE = settings.get("max_tracks_raise_per_cycle", 1)

def _error_to_desired_db(error):
    if abs(error) < ERROR_DEADBAND:
        return 0.0
    if error > 0:
        raw = error * ERROR_GAIN_UP
        max_db = max(v[1] for v in TRACK_DB_LIMITS.values())
        return _clamp(raw, 0.0, max_db)
    else:
        raw = error * ERROR_GAIN_DOWN
        min_db = min(v[0] for v in TRACK_DB_LIMITS.values())
        return _clamp(raw, min_db, 0.0)

def _command_track(track, desired_db, debug=False):
    # Check if track is enabled
    if track not in ENABLED_TRACKS:
        if debug:
            print(f"[process] Track {track} disabled - skipping")
        return None
    
    min_db, max_db = TRACK_DB_LIMITS.get(track, (-3.0, 1.5))
    desired_db = _clamp(desired_db, min_db, max_db)

    current_db = TRACK_CURRENT_DB.get(track, 0.0)
    delta = desired_db - current_db

    if delta > MAX_STEP_UP_DB:
        command_db = current_db + MAX_STEP_UP_DB
    elif delta < -MAX_STEP_DOWN_DB:
        command_db = current_db - MAX_STEP_DOWN_DB
    else:
        command_db = desired_db

    TRACK_CURRENT_DB[track] = command_db
    fader = _db_to_reaper(command_db)
    fader_min = _db_to_reaper(min_db)
    fader_max = _db_to_reaper(max_db)
    fader = _clamp(fader, fader_min, fader_max)

    if debug:
        print(
            f"[process] Track {track}: target={desired_db:+.2f}dB  "
            f"applied={command_db:+.2f}dB  fader_OSC={fader:.4f}  "
            f"(0dB={REAPER_UNITY_FADER:.4f})"
        )

    return fader

def _load_profiles(profiles_path="learning/profiles.json"):
    if not os.path.exists(profiles_path):
        return {}
    with open(profiles_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}

def process(audio, sample_rate=SAMPLE_RATE, profile_name=None,
            profiles_path="learning/profiles.json", stem_track_map=None,
            profiles=None, verbose=False):
    import os
    
    # Reload config from file (allows hot-reloading changes from GUI)
    _reload_config()
    
    debug = verbose or os.environ.get("MIX_ROBO_DEBUG", "0") != "0"

    if debug:
        print(f"[process] Starting audio analysis (shape={audio.shape}, duration={len(audio)/sample_rate:.2f}s)")

    try:
        lufs = get_lufs(audio)
        if lufs < LUFS_WARNING_THRESHOLD:
            print(f"[WARNING LUFS] Level below {LUFS_WARNING_THRESHOLD} LUFS ({lufs:.1f} LUFS). Check master level.")
        elif debug:
            print(f"[process] Integrated LUFS: {lufs:.1f}")
    except Exception:
        pass

    analysis_audio = audio
    if isinstance(audio, np.ndarray) and audio.ndim > 1:
        analysis_audio = audio.mean(axis=1)

    spectrum = analyze(analysis_audio)
    freqs = np.fft.rfftfreq(len(analysis_audio), 1.0 / sample_rate)
    band_values = bands(spectrum, freqs)

    if debug:
        print(f"[process] Band values: {band_values}")

    track_map = stem_track_map or ACTIVE_STEM_TRACK_MAP

    if profile_name is None:
        actions = decide(band_values)
        for band, error in actions:
            track = track_map.get(band)
            if track is None or track == MASTER_TRACK:
                continue
            desired_db = _error_to_desired_db(error)
            tracks = track if isinstance(track, (list, tuple)) else [track]
            for t in tracks:
                if t == MASTER_TRACK or t not in ENABLED_TRACKS:
                    continue
                fader = _command_track(t, desired_db, debug=debug)
                if fader is not None:
                    set_volume(t, fader, verbose=debug)
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
        print(f"[process] Actions to execute: {actions}")

    raised_tracks = 0
    for band, error in actions:
        track = track_map.get(band)
        if track is None or track == MASTER_TRACK:
            if debug and track == MASTER_TRACK:
                print(f"[process] Band '{band}' blocked - master track protected (track {MASTER_TRACK})")
            continue
        desired_db = _error_to_desired_db(error)

        if debug:
            print(f"[process] Band '{band}' -> track {track}: error={error:+.3f} target={desired_db:+.2f}dB")

        tracks = track if isinstance(track, (list, tuple)) else [track]
        for t in tracks:
            if t == MASTER_TRACK or t not in ENABLED_TRACKS:
                continue
            current_db = TRACK_CURRENT_DB.get(t, 0.0)
            if desired_db > current_db:
                if raised_tracks >= MAX_TRACKS_RAISE_PER_CYCLE:
                    if debug:
                        print(f"[process] Track {t} queued for next cycle (limit {MAX_TRACKS_RAISE_PER_CYCLE}/cycle)")
                    continue
                raised_tracks += 1
            fader = _command_track(t, desired_db, debug=debug)
            if fader is not None:
                set_volume(t, fader, verbose=debug)

def process_stems(stems, profile_name=None, profiles_path="learning/profiles.json", stem_track_map=None, verbose=False):
    """Process multiple separated stems and send OSC updates for each."""
    _reload_config()
    stem_track_map = stem_track_map or ACTIVE_STEM_TRACK_MAP

    if verbose:
        print(f"Loaded stems: {sorted(stems.keys())}")
        print(f"Using profile: {profile_name}")
        print(f"Using stem->track map: {stem_track_map}")

    profiles = _load_profiles(profiles_path)
    profile = profiles.get(profile_name, {}) if profile_name else {}

    for stem_name, audio in stems.items():
        max_samples = SAMPLE_RATE * 10
        audio = audio[:max_samples]

        spectrum = analyze(audio)
        freqs = np.fft.rfftfreq(len(audio), 1.0 / SAMPLE_RATE)
        band_values = bands(spectrum, freqs)

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
            target = _clamp(0.5 + 0.5 * error, 0.0, 1.0)
            tracks = track if isinstance(track, (list, tuple)) else [track]
            for t in tracks:
                if verbose:
                    print(f"{stem_name}:{band} -> track {t} = {target:.3f} (error {error:.3f})")
                set_volume(t, target)
