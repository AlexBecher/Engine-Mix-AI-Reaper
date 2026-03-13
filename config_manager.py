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
        "lufs_warning_threshold": -14
    }
    return config.get("analysis_settings", defaults)
