# -*- coding: utf-8 -*-
"""
Config Manager - Loads and saves configuration from/to config.json
"""
import json
import os
import sys
from pathlib import Path


def _app_base_dir():
    """Return runtime base dir (project dir in dev, executable dir in frozen app)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


CONFIG_FILE = _app_base_dir() / "config.json"

def load_config(config_path=CONFIG_FILE):
    """Load configuration from JSON file."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config, config_path=CONFIG_FILE):
    """Save configuration to JSON file."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def get_master_track(config=None):
    """Get master track number."""
    if config is None:
        config = load_config()
    return config.get("master_track", 153)

def get_track_db_limits(config=None):
    """Get track DB limits as dict {track_id: (min_db, max_db)}."""
    if config is None:
        config = load_config()
    
    limits = {}
    for track_id_str, track_data in config.get("tracks", {}).items():
        track_id = int(track_id_str)
        min_db = track_data.get("min_db", -3.0)
        max_db = track_data.get("max_db", 1.5)
        limits[track_id] = (min_db, max_db)
    return limits

def get_track_fader_db(config=None):
    """Get current track fader dB values as dict {track_id: current_db}."""
    if config is None:
        config = load_config()

    faders = {}
    for track_id_str, track_data in config.get("tracks", {}).items():
        track_id = int(track_id_str)
        current_db = track_data.get("fader_db", track_data.get("max_db", 0.0))
        faders[track_id] = float(current_db)
    return faders

def get_enabled_tracks(config=None):
    """Get set of enabled track IDs."""
    if config is None:
        config = load_config()
    
    enabled = set()
    for track_id_str, track_data in config.get("tracks", {}).items():
        if track_data.get("enabled", True):
            enabled.add(int(track_id_str))
    return enabled

def get_analysis_settings(config=None):
    """Get analysis settings (error_gain, slew rates, etc)."""
    if config is None:
        config = load_config()
    
    defaults = {
        "error_gain_up": 1.2,
        "error_gain_down": 2.2,
        "max_step_up_db": 0.10,
        "max_step_down_db": 0.35,
        "error_deadband": 0.18,
        "max_tracks_raise_per_cycle": 1,
        "lufs_warning_threshold": -14,
        "silence_floor_rms": 1e-6,
        "control_blend_spec": 0.78,
        "control_blend_lufs": 0.22,
        "level_gain": 0.45,
        "level_error_clip_db": 6.0,
        "level_source": "lufs",
        "level_role_targets_lufs": {
            "vocals": -18.0,
            "backing_vocals": -22.0,
            "piano": -23.0,
            "bass": -20.0,
            "drums": -20.0,
            "other": -23.0,
        },
        "level_role_targets_rms": {
            "vocals": -18.0,
            "backing_vocals": -22.0,
            "piano": -23.0,
            "bass": -20.0,
            "drums": -20.0,
            "other": -23.0,
        },
    }
    raw_settings = config.get("analysis_settings", {})
    if not isinstance(raw_settings, dict):
        raw_settings = {}

    merged = dict(defaults)
    merged.update(raw_settings)
    return merged


def get_dry_run_settings(config=None):
    """Get DRY-RUN settings."""
    if config is None:
        config = load_config()
    
    dry_run_cfg = config.get("dry_run_settings", {})
    return {
        "enabled": dry_run_cfg.get("enabled", False),
        "audio_source": dry_run_cfg.get("audio_source", "reastream"),
        "file_path": dry_run_cfg.get("file_path", ""),
        "loop_count": dry_run_cfg.get("loop_count", 1),
        "device_id": dry_run_cfg.get("device_id", None),
        "device_name": dry_run_cfg.get("device_name", ""),
        "sample_rate": dry_run_cfg.get("sample_rate", 44100),
        "blocksize": dry_run_cfg.get("blocksize", 4096),
        "channels": dry_run_cfg.get("channels", 2),
    }


def set_dry_run_settings(dry_run_settings, config=None, config_path=CONFIG_FILE):
    """Update DRY-RUN settings in config."""
    if config is None:
        config = load_config(config_path)
    
    if "dry_run_settings" not in config:
        config["dry_run_settings"] = {}
    
    config["dry_run_settings"].update(dry_run_settings)
    save_config(config, config_path)


def is_dry_run_enabled(config=None):
    """Check if DRY-RUN is enabled."""
    if config is None:
        config = load_config()
    
    return config.get("dry_run_settings", {}).get("enabled", False)
