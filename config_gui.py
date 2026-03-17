# -*- coding: utf-8 -*-
"""Single-screen dashboard: config + start/stop + live bargraph."""

import ast
import datetime as dt
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from config_manager import CONFIG_FILE, load_config, save_config
BAND_ORDER = ["p20", "p40", "p80", "p160", "p320", "p640", "p1200", "p2500", "p5000", "p10000", "p20000"]
TRACK_ACTION_RE = re.compile(r"\[process\]\s+Track\s+(\d+):")
TRACK_APPLIED_DB_RE = re.compile(r"\[process\]\s+Track\s+(\d+):.*?applied=([+-]?\d+(?:\.\d+)?)dB")
WEBAPI_TELEMETRY_RE = re.compile(r"\[WEBAPI (?:SET|READ)\]\s+track=(\d+)\s+db=([+-]?\d+(?:\.\d+)?)")
WEBAPI_STATUS_RE = re.compile(r"\[WEBAPI STATUS\]\s+(\w+)\s+(.+)")
REASTREAM_STATUS_RE = re.compile(r"\[REASTREAM STATUS\]\s+(\w+)\s+(.+)")
MASTER_METER_RE = re.compile(r"\[process\]\s+Master meters:\s+LUFS=([+-]?(?:\d+(?:\.\d+)?|nan))\s+RMS=([+-]?\d+(?:\.\d+)?)dB")
TRACK_DIAG_RE = re.compile(
    r"\[DIAG\]\s+Track\s+(\d+)\s+role=([^\s]+)\s+spec=([+-]?\d+(?:\.\d+)?)dB\s+"
    r"level=([+-]?\d+(?:\.\d+)?)dB\s+fused=([+-]?\d+(?:\.\d+)?)dB\s+"
    r"meter\(lufs=(--|[+-]?\d+(?:\.\d+)?),\s+rms=(--|[+-]?\d+(?:\.\d+)?)\)"
)
TRACK_DEADBAND_RE = re.compile(r"\[process\]\s+Track\s+(\d+):\s+fused delta inside deadband")
MAX_TRACK_ROWS = 9
METER_DB_FLOOR = -24.0
METER_DB_CEIL = 6.0
BAR_ATTACK = 0.35
BAR_RELEASE = 0.12
MIN_LAYOUT_WIDTH = 1280
LINEUP_ICON_SIZE = 45
LINEUP_ICON_COLUMNS = 2
TOGGLE_ICON_SIZE = 16
ACTION_ICON_SIZE = 60
ICON_LINEUP_SCENE_KEY = "icon_roles"
LINEUP_ROLE_ITEMS = [
    ("back", "backing_vocals", "back.png"),
    ("bass", "bass", "bass.png"),
    ("lead", "vocals", "lead.png"),
    ("guitar", "guitar", "guitars.png"),
    ("keys", "piano", "keys.png"),
    ("drum", "drums", "drum.png"),
]

# Full Reaper fader hardware range: 0% = -133 dB, 100% = +12 dB
REAPER_FADER_MIN_DB = -133.0
REAPER_FADER_MAX_DB = 12.0
_REAPER_FADER_MAX_AMP_ROOT = 10.0 ** (12.0 / 80.0)


def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _asset_path(filename):
    base = _app_dir()
    candidate = base / "img" / str(filename)
    if candidate.exists():
        return candidate
    return base / str(filename)


def _normalize_runtime_host(value, fallback):
    host = str(value or "").strip()
    if not host:
        return str(fallback)
    if host == "127.0.0.0":
        return "127.0.0.1"
    return host


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Alex Studio MIX  - AI")
        self.root.geometry("1240x760")
        self.root.configure(bg="#06080b")
        self.window_icon_img = None
        self._apply_window_icon()

        self.config = load_config(CONFIG_FILE)
        self.track_vars = {}
        self.track_rows = []
        self.track_controls = None
        self.add_track_btn = None
        self.tracks_box = None
        self._track_row_counter = 0
        self.analysis_vars = {}
        self.run_vars = {}
        self.dry_run_var = tk.BooleanVar(value=False)
        self.audio_source_var = tk.StringVar(value="reastream")
        self.audio_source_combo = None
        self.device_combo = None
        self.file_picker_label = None
        self.dry_run_btn = None
        self.dry_run_frame = None
        self.dry_run_status_label = None
        self.learn_in_progress = False
        self.learn_samples = []
        self.learn_preview_db = None
        self.learn_preview_source_name = ""
        self.learn_preview_actions_frame = None
        self.learn_apply_btn = None
        self.learn_merge_btn = None
        self.band_values = {band: METER_DB_FLOOR for band in BAND_ORDER}
        self.band_targets = {band: METER_DB_FLOOR for band in BAND_ORDER}
        self.band_errors = {band: 0.0 for band in BAND_ORDER}
        self.band_ui = {}
        self.band_graph_canvas = None
        self.mixer_canvas = None
        self.mixer_slot_items = {}
        self.mixer_anim_jobs = {}
        self.mixer_anim_duration_ms = 140
        self.track_diag_state = {}
        self.top_band_errors = []
        self.pending_actions = []
        self.dominant_bands = set()
        self.fader_strip_img = None
        self.fader_button_img = None
        self.scroll_canvas = None
        self.scroll_content = None
        self.scroll_window_id = None

        self.process_handle = None
        self.output_queue = queue.Queue()
        self.runtime_log_handle = None
        self.runtime_log_path = None
        self.last_runtime_log_path = None
        self.profile_combo = None
        self.scene_combo = None
        self.webapi_status_label = None
        self.reastream_status_label = None
        self.master_meter_label = None
        self.profile_status_label = None
        self.scene_status_label = None
        self.mix_stability_label = None
        self.scene_banner_label = None
        self.lineup_role_vars = {}
        self.lineup_icon_buttons = {}
        self.lineup_icon_images = {}
        self.toggle_on_img = None
        self.toggle_off_img = None
        self.delete_profile_img = None
        self.action_button_images = {}
        self._lineup_role_syncing = False

        self._ensure_defaults()
        self._apply_dark_theme()
        self._build_ui()
        self._sync_dry_run_ui()
        self._rebuild_mixer_view()
        self._schedule_ui_updates()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _apply_window_icon(self):
        icon_path = _asset_path("icon.png")
        if not icon_path.exists():
            return

        try:
            self.window_icon_img = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self.window_icon_img)
        except Exception:
            pass

    def _apply_dark_theme(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        bg = "#06080b"
        card = "#0d1117"
        field = "#111827"
        edge = "#173A5A"
        text = "#f5f8ff"
        accent = "#4cc9ff"

        # Family names with spaces must be wrapped for Tk option database parsing.
        self.root.option_add("*Font", "{Segoe UI} 9")
        self.root.option_add("*TCombobox*Listbox.background", field)
        self.root.option_add("*TCombobox*Listbox.foreground", text)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#213d88")
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#e6f7ff")

        style.configure(".", background=bg, foreground=text)
        style.configure("TFrame", background=bg)
        style.configure(
            "TLabelframe",
            background=card,
            borderwidth=1,
            relief="solid",
            bordercolor=edge,
            lightcolor=edge,
            darkcolor=edge,
        )
        style.configure("TLabelframe.Label", background=card, foreground=accent, font=("Segoe UI", 9, "bold"))
        style.configure("TLabel", background=card, foreground=text)
        style.configure(
            "TEntry",
            fieldbackground=field,
            foreground=text,
            bordercolor=edge,
            lightcolor=edge,
            darkcolor=edge,
            insertcolor="#ffffff",
        )
        style.configure(
            "TButton",
            background="#172033",
            foreground="#e6f7ff",
            bordercolor=edge,
            lightcolor=edge,
            darkcolor=edge,
            focusthickness=1,
            focuscolor="#4cc9ff",
            padding=(8, 4),
        )
        style.map(
            "TButton",
            background=[("active", "#1f2f4d"), ("pressed", "#0f172a")],
            foreground=[("disabled", "#64748b")],
        )
        style.configure("TCheckbutton", background=card, foreground=text)
        style.map("TCheckbutton", foreground=[("active", "#d7ecff")])
        style.configure(
            "TCombobox",
            fieldbackground=field,
            background=field,
            foreground=text,
            arrowcolor=accent,
            bordercolor=edge,
            lightcolor=edge,
            darkcolor=edge,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", field), ("disabled", "#0b1220")],
            foreground=[("readonly", text), ("disabled", "#64748b")],
            selectbackground=[("readonly", "#1e3a8a")],
            selectforeground=[("readonly", "#e6f7ff")],
        )
        style.configure("Vertical.TScrollbar", background="#111827", troughcolor="#0b1220", bordercolor="#1f2937")

    def _reset_band_graph(self):
        for band in BAND_ORDER:
            self.band_values[band] = METER_DB_FLOOR
            self.band_targets[band] = METER_DB_FLOOR
            self.band_errors[band] = 0.0
        if self.master_meter_label is not None:
            self.master_meter_label.config(text="Master: LUFS -- | RMS -- dB")

    def _update_runtime_scene_label(self):
        active_scene = str(self.run_vars.get("active_scene", tk.StringVar(value="")).get()).strip()
        if active_scene == ICON_LINEUP_SCENE_KEY and self.lineup_role_vars:
            selected_labels = [
                label
                for (label, role_key, _icon_file) in LINEUP_ROLE_ITEMS
                if role_key in self.lineup_role_vars and bool(self.lineup_role_vars[role_key].get())
            ]
            if selected_labels:
                scene_text = f"Scene:{'+'.join(selected_labels)}"
                scene_color = "#7de8ff"
            else:
                scene_text = "Scene:icon_roles"
                scene_color = "#6b7280"
        elif active_scene:
            scene_text = f"Scene: {active_scene}"
            scene_color = "#7de8ff"
        else:
            scene_text = "Scene: default"
            scene_color = "#6b7280"
        if self.scene_status_label is not None:
            self.scene_status_label.config(text=scene_text, foreground=scene_color)
        if self.scene_banner_label is not None:
            self.scene_banner_label.config(text=scene_text, foreground=scene_color)

    def _get_profile_runtime_alias(self, profile_name):
        name = str(profile_name or "").strip()
        if not name:
            return ""
        if name.lower().startswith("profile_learn_"):
            return name

        profiles_path = _app_dir() / "learning" / "profiles.json"
        if not profiles_path.exists():
            return name
        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return name
        if not isinstance(data, dict):
            return name

        learn_like = []
        for key in data.keys():
            key_str = str(key)
            if key_str.lower().startswith("profile_learn_") or key_str.lower().startswith("worship-pa-"):
                learn_like.append(key_str)

        if name in learn_like:
            idx = learn_like.index(name) + 1
            return f"Profile_learn_{idx:03d}"

        return name

    def _update_runtime_profile_label(self):
        if self.profile_status_label is None:
            return

        current = str(self.run_vars.get("profile", tk.StringVar(value="")).get()).strip()
        if not current:
            self.profile_status_label.config(text="Profile: --", foreground="#6b7280")
            return

        alias = self._get_profile_runtime_alias(current)
        self.profile_status_label.config(text=f"Profile: {alias}", foreground="#7de8ff")

    def _format_stability_bar(self, score):
        filled = max(0, min(5, int(round((float(score) / 100.0) * 5.0))))
        return ("#" * filled) + ("-" * (5 - filled))

    def _band_delta_style(self, delta_value):
        try:
            delta = float(delta_value)
        except Exception:
            delta = 0.0

        deadband = 0.0
        try:
            deadband = abs(float(self.analysis_vars.get("error_deadband", tk.DoubleVar(value=0.0)).get()))
        except Exception:
            deadband = 0.0

        if delta > deadband:
            return "#39ff88"
        if delta < -deadband:
            return "#ff5a36"
        return "#9ca3af"

    def _update_mix_stability_label(self):
        if self.mix_stability_label is None:
            return

        max_step = max(
            1e-6,
            abs(float(self.analysis_vars.get("max_step_up_db", tk.DoubleVar(value=1.0)).get())),
            abs(float(self.analysis_vars.get("max_step_down_db", tk.DoubleVar(value=1.0)).get())),
        )

        active_track_ids = []
        for row_data in self.track_rows:
            try:
                if not bool(row_data.get("enabled", tk.BooleanVar(value=True)).get()):
                    continue
                active_track_ids.append(int(row_data["track_id"].get()))
            except Exception:
                continue

        if not active_track_ids:
            self.mix_stability_label.config(text="Mix stability 0-100%: --", foreground="#7de8ff")
            return

        avg_abs_delta = sum(abs(float(self.track_diag_state.get(tid, {}).get("fused_db", 0.0) or 0.0)) for tid in active_track_ids)
        avg_abs_delta /= float(len(active_track_ids))
        score = max(0.0, min(100.0, (1.0 - (avg_abs_delta / max_step)) * 100.0))
        stability_bar = self._format_stability_bar(score)

        if score >= 90.0:
            state = "Stable"
            color = "#39ff88"
        elif score >= 75.0:
            state = "Holding"
            color = "#fbbf24"
        elif score >= 50.0:
            state = "Active"
            color = "#fb923c"
        else:
            state = "Correcting"
            color = "#ff5a36"

        self.mix_stability_label.config(
            text=f"Mix stability 0-100%: [{stability_bar}] {score:.0f}% ({state})",
            foreground=color,
        )

    def _is_track_frozen(self, track_id):
        try:
            target = int(track_id)
        except Exception:
            return False

        for row_data in self.track_rows:
            try:
                if int(row_data["track_id"].get()) != target:
                    continue
            except Exception:
                continue
            frozen_var = row_data.get("frozen")
            return bool(frozen_var.get()) if frozen_var is not None else False
        return False

    def _toggle_track_freeze(self, row_key):
        row_data = self.track_vars.get(row_key)
        if row_data is None:
            return

        frozen_var = row_data.get("frozen")
        if frozen_var is None:
            return

        new_state = not bool(frozen_var.get())
        frozen_var.set(new_state)

        try:
            track_id = int(row_data["track_id"].get())
        except Exception:
            track_id = None

        self._refresh_mixer_metadata(track_id)
        self._update_mix_stability_label()

        try:
            self.config = self._collect_config_from_ui()
            save_config(self.config, CONFIG_FILE)
            if track_id is not None:
                self.status_label.config(
                    text=f"[OK] Track {track_id} {'frozen' if new_state else 'unfrozen'}",
                    foreground="#4cc9ff",
                )
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Failed to persist freeze: {exc}", foreground="red")

    def _reset_runtime_telemetry(self):
        self.track_diag_state = {}
        self.top_band_errors = []
        self.pending_actions = []
        self.dominant_bands = set()
        self.band_errors = {band: 0.0 for band in BAND_ORDER}
        self._update_mix_stability_label()
        self._refresh_bars()
        if self.mixer_canvas is not None:
            self._refresh_mixer_metadata()

    def _safe_parse_optional_float(self, value):
        text = str(value).strip()
        if not text or text == "--":
            return None
        try:
            return float(text)
        except Exception:
            return None

    def _intent_style_for_track(self, track_id):
        if self._is_track_frozen(track_id):
            return "L", "#7de8ff", "frozen"

        state = self.track_diag_state.get(int(track_id), {}) if track_id is not None else {}
        intent = state.get("intent", "hold")
        fused_db = float(state.get("fused_db", 0.0) or 0.0)

        if intent == "boost":
            return "↑", "#30d158", f"Δ {fused_db:+.2f} dB"
        if intent == "cut":
            return "↓", "#ff6b6b", f"Δ {fused_db:+.2f} dB"
        return "=", "#9ca3af", "deadband"

    def _format_track_meter_text(self, track_id):
        state = self.track_diag_state.get(int(track_id), {}) if track_id is not None else {}
        lufs = state.get("lufs")
        rms = state.get("rms")
        if lufs is None and rms is None:
            return "LUFS -- | RMS --"
        lufs_text = "--" if lufs is None else f"{float(lufs):+.1f}"
        rms_text = "--" if rms is None else f"{float(rms):+.1f}"
        return f"LUFS {lufs_text} | RMS {rms_text}"

    def _refresh_mixer_metadata(self, track_id=None):
        if self.mixer_canvas is None:
            return

        target_id = None if track_id is None else int(track_id)
        for row_key, slot in list(self.mixer_slot_items.items()):
            slot_track_id = int(slot.get("track_id", 0))
            if target_id is not None and slot_track_id != target_id:
                continue

            arrow_text, arrow_color, detail_text = self._intent_style_for_track(slot_track_id)
            meter_text = self._format_track_meter_text(slot_track_id)
            lock_text = "LOCK" if self._is_track_frozen(slot_track_id) else "FREE"
            lock_color = "#7de8ff" if self._is_track_frozen(slot_track_id) else "#6b7280"
            try:
                self.mixer_canvas.itemconfig(slot["intent_item"], text=arrow_text, fill=arrow_color)
                self.mixer_canvas.itemconfig(slot["intent_detail_item"], text=detail_text, fill=arrow_color)
                self.mixer_canvas.itemconfig(slot["meter_item"], text=meter_text)
                self.mixer_canvas.itemconfig(slot["lock_item"], text=lock_text, fill=lock_color)
            except Exception:
                continue

    def _ensure_defaults(self):
        self.config.setdefault("master_track", 153)
        self.config.setdefault("tracks", {})
        self.config.setdefault("analysis_settings", {})
        self.config.setdefault("run_settings", {})
        self.config.setdefault("lineup", {})
        self.config.setdefault("dry_run_settings", {})

        tracks_cfg = self.config.get("tracks", {}) if isinstance(self.config.get("tracks", {}), dict) else {}
        for track_data in tracks_cfg.values():
            if isinstance(track_data, dict):
                track_data.setdefault("frozen", False)

        lineup = self.config["lineup"]
        lineup.setdefault("active_scene", "")
        lineup.setdefault("scenes", {})

        run = self.config["run_settings"]
        run.setdefault("profile", "worship")
        run.setdefault("reastream", True)
        run.setdefault("reastream_identifier", "master")
        run.setdefault("reastream_host", "0.0.0.0")
        run.setdefault("reastream_port", 58710)
        run.setdefault("webapi_host", "127.0.0.1")
        run.setdefault("webapi_port", 8080)
        run.setdefault("webapi_base", "/_")
        run.setdefault("webapi_timeout", 2.5)
        run.setdefault("channels", 2)
        run.setdefault("analysis_interval", 5.0)
        run.setdefault("verbose", True)
        run.pop("osc_host", None)
        run.pop("osc_port", None)

        dry_run = self.config["dry_run_settings"]
        dry_run.setdefault("enabled", False)
        dry_run.setdefault("audio_source", "reastream")
        dry_run.setdefault("file_path", "")
        dry_run.setdefault("loop_count", 1)
        dry_run.setdefault("device_id", None)
        dry_run.setdefault("device_name", "")
        dry_run.setdefault("sample_rate", 44100)
        dry_run.setdefault("blocksize", 4096)
        dry_run.setdefault("channels", 2)

    def _get_active_scene_roles(self):
        lineup_cfg = self.config.get("lineup", {}) if isinstance(self.config.get("lineup", {}), dict) else {}
        scenes_cfg = lineup_cfg.get("scenes", {}) if isinstance(lineup_cfg.get("scenes", {}), dict) else {}

        active_scene = str(lineup_cfg.get("active_scene", "")).strip()
        candidates = [active_scene, ICON_LINEUP_SCENE_KEY]
        for scene_name in candidates:
            if not scene_name:
                continue
            scene_cfg = scenes_cfg.get(scene_name, {})
            if not isinstance(scene_cfg, dict):
                continue
            raw_roles = scene_cfg.get("present_roles", [])
            if isinstance(raw_roles, (list, tuple, set)):
                return {str(role).strip().lower() for role in raw_roles if str(role).strip()}
        return set()

    def _selected_lineup_roles(self):
        selected = []
        for _label, role_key, _icon_file in LINEUP_ROLE_ITEMS:
            role_var = self.lineup_role_vars.get(role_key)
            if role_var is not None and bool(role_var.get()):
                selected.append(role_key)
        return selected

    def _build_lineup_config_from_roles(self, cfg):
        lineup_cfg = cfg.get("lineup", {}) if isinstance(cfg.get("lineup", {}), dict) else {}
        scenes_cfg = lineup_cfg.get("scenes", {}) if isinstance(lineup_cfg.get("scenes", {}), dict) else {}

        scenes_copy = {}
        for name, scene in scenes_cfg.items():
            if isinstance(scene, dict):
                scenes_copy[str(name)] = dict(scene)

        current_scene = scenes_copy.get(ICON_LINEUP_SCENE_KEY, {}) if isinstance(scenes_copy.get(ICON_LINEUP_SCENE_KEY, {}), dict) else {}
        band_targets = current_scene.get("band_targets", {}) if isinstance(current_scene.get("band_targets", {}), dict) else {}
        icon_scene = {"present_roles": self._selected_lineup_roles()}
        if band_targets:
            icon_scene["band_targets"] = dict(band_targets)

        scenes_copy[ICON_LINEUP_SCENE_KEY] = icon_scene
        return {
            "active_scene": ICON_LINEUP_SCENE_KEY,
            "scenes": scenes_copy,
        }

    def _refresh_lineup_icon_styles(self):
        for role_key, icon_canvas in self.lineup_icon_buttons.items():
            role_var = self.lineup_role_vars.get(role_key)
            is_selected = bool(role_var.get()) if role_var is not None else False
            if is_selected:
                icon_canvas.config(bg="#0f3b4a", highlightbackground="#57e8ff", highlightthickness=2)
            else:
                icon_canvas.config(bg="#0b1220", highlightbackground="#1f3a5f", highlightthickness=1)

    def _toggle_lineup_role(self, role_key):
        role_var = self.lineup_role_vars.get(role_key)
        if role_var is None:
            return
        role_var.set(not bool(role_var.get()))

    def _on_lineup_roles_changed(self, *_args):
        if self._lineup_role_syncing:
            return

        self._refresh_lineup_icon_styles()
        self.run_vars["active_scene"].set(ICON_LINEUP_SCENE_KEY)
        self._update_runtime_scene_label()

        try:
            cfg = self._collect_config_from_ui()
            self.config = cfg
            save_config(self.config, CONFIG_FILE)
            self.status_label.config(text="[OK] Lineup roles updated", foreground="#4cc9ff")
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Failed to update lineup: {exc}", foreground="red")

    def _fit_icon_image(self, image):
        if image is None:
            return None

        width = int(image.width())
        height = int(image.height())
        if width <= 0 or height <= 0:
            return image

        scale = max(
            1,
            int(
                math.ceil(
                    max(
                        float(width) / float(LINEUP_ICON_SIZE),
                        float(height) / float(LINEUP_ICON_SIZE),
                    )
                )
            ),
        )
        if scale > 1:
            return image.subsample(scale, scale)
        return image

    def _fit_toggle_image(self, image):
        if image is None:
            return None

        width = int(image.width())
        height = int(image.height())
        if width <= 0 or height <= 0:
            return image

        scale = max(1, int(math.ceil(max(float(width) / float(TOGGLE_ICON_SIZE), float(height) / float(TOGGLE_ICON_SIZE)))))
        if scale > 1:
            return image.subsample(scale, scale)
        return image

    def _ensure_toggle_images(self):
        if self.toggle_on_img is not None and self.toggle_off_img is not None:
            return

        on_path = _asset_path("checkin.png")
        off_path = _asset_path("checkout.png")

        if on_path.exists():
            try:
                self.toggle_on_img = self._fit_toggle_image(tk.PhotoImage(file=str(on_path)))
            except Exception:
                self.toggle_on_img = None
        if off_path.exists():
            try:
                self.toggle_off_img = self._fit_toggle_image(tk.PhotoImage(file=str(off_path)))
            except Exception:
                self.toggle_off_img = None

    def _create_image_toggle(self, parent, variable, text=""):
        self._ensure_toggle_images()

        container = ttk.Frame(parent)
        icon_btn = tk.Button(
            container,
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg="#0d1117",
            activebackground="#0d1117",
            cursor="hand2",
            padx=0,
            pady=0,
        )
        icon_btn.grid(row=0, column=0, sticky="w")

        text_label = None
        if text:
            text_label = ttk.Label(container, text=text)
            text_label.grid(row=0, column=1, sticky="w", padx=(6, 0))

        def _sync_icon(*_args):
            is_on = bool(variable.get())
            if self.toggle_on_img is not None and self.toggle_off_img is not None:
                icon_btn.config(image=self.toggle_on_img if is_on else self.toggle_off_img, text="")
            else:
                icon_btn.config(
                    image="",
                    text="ON" if is_on else "OFF",
                    fg="#57e8ff" if is_on else "#9ca3af",
                    font=("Consolas", 8, "bold"),
                    width=4,
                )

        def _toggle(_event=None):
            variable.set(not bool(variable.get()))

        icon_btn.config(command=_toggle)
        if text_label is not None:
            text_label.bind("<Button-1>", _toggle)

        variable.trace_add("write", _sync_icon)
        _sync_icon()
        return container

    def _create_delete_profile_button(self, parent):
        if self.delete_profile_img is None:
            icon_path = _asset_path("del.png")
            if icon_path.exists():
                try:
                    self.delete_profile_img = self._fit_toggle_image(tk.PhotoImage(file=str(icon_path)))
                except Exception:
                    self.delete_profile_img = None

        btn = tk.Button(
            parent,
            command=self._delete_selected_profile,
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg="#0d1117",
            activebackground="#0d1117",
            cursor="hand2",
            padx=2,
            pady=0,
        )
        if self.delete_profile_img is not None:
            btn.config(image=self.delete_profile_img)
        else:
            btn.config(text="X", fg="#ff6b6b", font=("Segoe UI", 9, "bold"), width=2)
        return btn

    def _fit_action_image(self, image):
        if image is None:
            return None
        # Keep native asset dimensions for action buttons.
        return image

    def _load_action_button_images(self):
        image_files = {
            "start": "start.png",
            "stop": "stop.png",
            "save": "save.png",
            "learn": "learn.png",
            "dry": "dry.png",
            "apply": "apply.png",
            "merge": "merge.png",
        }
        for key, filename in image_files.items():
            if key in self.action_button_images:
                continue
            path = _asset_path(filename)
            if not path.exists():
                continue
            try:
                self.action_button_images[key] = self._fit_action_image(tk.PhotoImage(file=str(path)))
            except Exception:
                pass

    def _set_start_stop_button_visual(self, running):
        if self.start_stop_btn is None:
            return
        if running and "stop" in self.action_button_images:
            self.start_stop_btn.config(image=self.action_button_images["stop"], text="")
            return
        if (not running) and "start" in self.action_button_images:
            self.start_stop_btn.config(image=self.action_button_images["start"], text="")
            return
        self.start_stop_btn.config(text="STOP" if running else "START")

    def _build_lineup_role_selector(self, parent):
        selected_roles = self._get_active_scene_roles()
        self.lineup_role_vars = {}
        self.lineup_icon_buttons = {}
        self.lineup_icon_images = {}

        for idx, (label, role_key, icon_file) in enumerate(LINEUP_ROLE_ITEMS):
            col = idx % LINEUP_ICON_COLUMNS
            row = idx // LINEUP_ICON_COLUMNS
            role_frame = ttk.Frame(parent)
            right_pad = 10 if col < (LINEUP_ICON_COLUMNS - 1) else 0
            role_frame.grid(row=row, column=col, padx=(0, right_pad), pady=(0, 8), sticky="n")

            role_var = tk.BooleanVar(value=(role_key in selected_roles))
            self.lineup_role_vars[role_key] = role_var
            role_var.trace_add("write", self._on_lineup_roles_changed)

            icon_path = _asset_path(icon_file)
            icon_image = None
            if icon_path.exists():
                try:
                    icon_image = self._fit_icon_image(tk.PhotoImage(file=str(icon_path)))
                except Exception:
                    icon_image = None

            if icon_image is not None:
                self.lineup_icon_images[role_key] = icon_image

            icon_canvas = tk.Canvas(
                role_frame,
                width=LINEUP_ICON_SIZE,
                height=LINEUP_ICON_SIZE,
                bg="#0b1220",
                bd=0,
                highlightbackground="#1f3a5f",
                highlightthickness=1,
                cursor="hand2",
            )
            icon_canvas.grid(row=0, column=0, sticky="n")
            if icon_image is not None:
                icon_canvas.create_image(LINEUP_ICON_SIZE // 2, LINEUP_ICON_SIZE // 2, image=icon_image)
            else:
                icon_canvas.create_text(
                    LINEUP_ICON_SIZE // 2,
                    LINEUP_ICON_SIZE // 2,
                    text=label.upper(),
                    fill="#9ca3af",
                    font=("Segoe UI", 9, "bold"),
                )

            icon_canvas.bind("<Button-1>", lambda _event, rk=role_key: self._toggle_lineup_role(rk))
            self.lineup_icon_buttons[role_key] = icon_canvas

            ttk.Label(role_frame, text=label, foreground="#7de8ff").grid(row=1, column=0, sticky="n", pady=(3, 0))

        self._refresh_lineup_icon_styles()

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.root)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        self.scroll_canvas = tk.Canvas(shell, highlightthickness=0, bd=0, bg="#06080b")
        v_scroll = ttk.Scrollbar(shell, orient="vertical", command=self.scroll_canvas.yview)
        h_scroll = ttk.Scrollbar(shell, orient="horizontal", command=self.scroll_canvas.xview)
        self.scroll_canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.scroll_canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")

        self.scroll_content = ttk.Frame(self.scroll_canvas, padding=10)
        self.scroll_window_id = self.scroll_canvas.create_window((0, 0), window=self.scroll_content, anchor="nw")

        self.scroll_content.bind("<Configure>", self._on_scroll_content_configure)
        self.scroll_canvas.bind("<Configure>", self._on_scroll_canvas_configure)
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

        self.scroll_content.columnconfigure(0, weight=0)
        self.scroll_content.columnconfigure(1, weight=1)
        self.scroll_content.rowconfigure(1, weight=1)

        top_bar = ttk.Frame(self.scroll_content, padding=(10, 0, 10, 8))
        top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        top_bar.columnconfigure(0, weight=1)
        top_bar.columnconfigure(1, weight=0)
        ttk.Label(top_bar, text="Alex Studio MIX Runtime", foreground="#95d5ff").grid(row=0, column=0, sticky="w")
        self.scene_banner_label = ttk.Label(top_bar, text="Scene: default", foreground="#6b7280", font=("Segoe UI", 10, "bold"))
        self.scene_banner_label.grid(row=0, column=1, sticky="e")

        left = ttk.Frame(self.scroll_content, padding=10)
        right = ttk.Frame(self.scroll_content, padding=10)
        left.grid(row=1, column=0, sticky="nsew")
        right.grid(row=1, column=1, sticky="nsew")

        self._build_config_panel(left)
        self._build_runtime_panel(right)

    def _on_scroll_content_configure(self, _event):
        if self.scroll_canvas is None:
            return
        self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

    def _on_scroll_canvas_configure(self, event):
        if self.scroll_canvas is None or self.scroll_window_id is None:
            return
        self.scroll_canvas.itemconfigure(self.scroll_window_id, width=max(int(event.width), MIN_LAYOUT_WIDTH))

    def _on_mousewheel(self, event):
        if self.scroll_canvas is None:
            return
        step = int(-1 * (event.delta / 120))
        if step:
            self.scroll_canvas.yview_scroll(step, "units")

    def _build_config_panel(self, parent):
        parent.columnconfigure(0, weight=1)

        master_box = ttk.LabelFrame(parent, text="Master", padding=10)
        master_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.master_var = tk.IntVar(value=int(self.config.get("master_track", 153)))
        ttk.Label(master_box, text="Master track ID").grid(row=0, column=0, sticky="w")
        ttk.Entry(master_box, textvariable=self.master_var, width=12).grid(row=0, column=1, padx=8, sticky="w")

        tracks_box = ttk.LabelFrame(parent, text="Tracks (mute + IDs + limits)", padding=10)
        tracks_box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.tracks_box = tracks_box
        headers = ["Active", "Track ID", "Name", "Min dB", "Max dB", "Band"]
        for idx, text in enumerate(headers):
            ttk.Label(tracks_box, text=text).grid(row=0, column=idx, padx=4, pady=(0, 4), sticky="w")

        for track_id_str, track_data in sorted(self.config.get("tracks", {}).items(), key=lambda x: int(x[0])):
            track_id = int(track_id_str)
            self._add_track_row(
                track_id=track_id,
                name=str(track_data.get("name", "")),
                enabled=bool(track_data.get("enabled", True)),
                min_db=float(track_data.get("min_db", -6.0)),
                max_db=float(track_data.get("max_db", 0.0)),
                fader_db=float(track_data.get("fader_db", track_data.get("max_db", 0.0))),
                frozen=bool(track_data.get("frozen", False)),
            )

        self.track_controls = ttk.Frame(tracks_box)
        self.track_controls.grid(row=len(self.track_rows) + 1, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.add_track_btn = ttk.Button(self.track_controls, text="+ Add Track", command=self._on_add_track)
        self.add_track_btn.grid(row=0, column=0, sticky="w")
        ttk.Button(self.track_controls, text="Detail", command=self._show_track_map_details).grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._update_add_track_button_state()

        analysis_box = ttk.LabelFrame(parent, text="Analysis settings", padding=10)
        analysis_box.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        analysis_defaults = {
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
        }
        labels = [
            ("Error gain up", "error_gain_up", tk.DoubleVar),
            ("Error gain down", "error_gain_down", tk.DoubleVar),
            ("Max step up (dB)", "max_step_up_db", tk.DoubleVar),
            ("Max step down (dB)", "max_step_down_db", tk.DoubleVar),
            ("Error deadband", "error_deadband", tk.DoubleVar),
            ("Max tracks raise/cycle", "max_tracks_raise_per_cycle", tk.IntVar),
            ("LUFS warning threshold", "lufs_warning_threshold", tk.DoubleVar),
            ("Silence floor RMS", "silence_floor_rms", tk.DoubleVar),
            ("Blend spectral", "control_blend_spec", tk.DoubleVar),
            ("Blend level meter", "control_blend_lufs", tk.DoubleVar),
            ("Level gain", "level_gain", tk.DoubleVar),
            ("Level error clip (dB)", "level_error_clip_db", tk.DoubleVar),
        ]

        for row_idx, (label, key, var_type) in enumerate(labels):
            current = self.config["analysis_settings"].get(key, analysis_defaults[key])
            var = var_type(value=current)
            self.analysis_vars[key] = var
            ttk.Label(analysis_box, text=label).grid(row=row_idx, column=0, sticky="w", pady=2)
            ttk.Entry(analysis_box, textvariable=var, width=12).grid(row=row_idx, column=1, sticky="w", padx=8)

        level_source_row = len(labels)
        level_source_current = str(self.config["analysis_settings"].get("level_source", analysis_defaults["level_source"]))
        self.analysis_vars["level_source"] = tk.StringVar(value=level_source_current)
        ttk.Label(analysis_box, text="Level source").grid(row=level_source_row, column=0, sticky="w", pady=2)
        ttk.Combobox(
            analysis_box,
            textvariable=self.analysis_vars["level_source"],
            values=["lufs", "rms_db", "rms"],
            state="readonly",
            width=10,
        ).grid(row=level_source_row, column=1, sticky="w", padx=8)

        run_box = ttk.LabelFrame(parent, text="Run settings", padding=10)
        run_box.grid(row=3, column=0, sticky="ew")
        run = self.config["run_settings"]
        initial_profile = str(run.get("profile", "worship"))
        self.run_vars["profile"] = tk.StringVar(value=initial_profile)
        self.run_vars["reastream_identifier"] = tk.StringVar(value=str(run.get("reastream_identifier", "master")))
        self.run_vars["reastream_host"] = tk.StringVar(value=str(run.get("reastream_host", "0.0.0.0")))
        self.run_vars["reastream_port"] = tk.IntVar(value=int(run.get("reastream_port", 58710)))
        self.run_vars["webapi_host"] = tk.StringVar(value=str(run.get("webapi_host", "127.0.0.1")))
        self.run_vars["webapi_port"] = tk.IntVar(value=int(run.get("webapi_port", 8080)))
        self.run_vars["webapi_base"] = tk.StringVar(value=str(run.get("webapi_base", "/_")))
        self.run_vars["webapi_timeout"] = tk.DoubleVar(value=float(run.get("webapi_timeout", 2.5)))
        self.run_vars["channels"] = tk.IntVar(value=int(run.get("channels", 2)))
        self.run_vars["analysis_interval"] = tk.DoubleVar(value=float(run.get("analysis_interval", 5.0)))
        self.run_vars["verbose"] = tk.BooleanVar(value=bool(run.get("verbose", True)))
        self.run_vars["reastream"] = tk.BooleanVar(value=bool(run.get("reastream", True)))
        lineup_cfg = self.config.get("lineup", {}) if isinstance(self.config.get("lineup", {}), dict) else {}
        initial_scene = str(lineup_cfg.get("active_scene", "")).strip() or ICON_LINEUP_SCENE_KEY
        self.run_vars["active_scene"] = tk.StringVar(value=initial_scene)
        self.run_vars["active_scene"].trace_add("write", lambda *_args: self._update_runtime_scene_label())
        self.run_vars["profile"].trace_add("write", lambda *_args: self._update_runtime_profile_label())

        profile_header = ttk.Frame(run_box)
        profile_header.grid(row=0, column=0, sticky="w")
        ttk.Label(profile_header, text="Profile").grid(row=0, column=0, sticky="w")
        self._create_delete_profile_button(profile_header).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.profile_combo = ttk.Combobox(
            run_box,
            textvariable=self.run_vars["profile"],
            values=self._load_profile_names(),
            width=30,
            state="normal",
        )
        self.profile_combo.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(run_box, text="Refresh", command=self._refresh_profile_options).grid(row=0, column=4, sticky="w")
        ttk.Label(run_box, text="Identifier").grid(row=0, column=2, sticky="w")
        ttk.Entry(run_box, textvariable=self.run_vars["reastream_identifier"], width=14).grid(row=0, column=3, sticky="w", padx=8)

        ttk.Label(run_box, text="Channels").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["channels"], width=14).grid(row=1, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(run_box, text="Analysis interval (s)").grid(row=1, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["analysis_interval"], width=14).grid(row=1, column=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Label(run_box, text="Web API IP").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["webapi_host"], width=14).grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(run_box, text="Web API Port").grid(row=2, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["webapi_port"], width=14).grid(row=2, column=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Label(run_box, text="Web API Base").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["webapi_base"], width=14).grid(row=3, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(run_box, text="Web API Timeout").grid(row=3, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["webapi_timeout"], width=14).grid(row=3, column=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Label(run_box, text="ReaStream IP").grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["reastream_host"], width=14).grid(row=4, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(run_box, text="ReaStream Port").grid(row=4, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["reastream_port"], width=14).grid(row=4, column=3, sticky="w", padx=8, pady=(6, 0))

        reastream_toggle = self._create_image_toggle(run_box, self.run_vars["reastream"], "Use ReaStream")
        reastream_toggle.grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        verbose_toggle = self._create_image_toggle(run_box, self.run_vars["verbose"], "Verbose (required for telemetry)")
        verbose_toggle.grid(row=5, column=2, columnspan=2, sticky="w", pady=(8, 0))

    def _load_profile_names(self):
        profiles_path = _app_dir() / "learning" / "profiles.json"
        if not profiles_path.exists():
            current = str(self.run_vars.get("profile", tk.StringVar(value="worship")).get()).strip()
            return [current] if current else ["worship"]

        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            current = str(self.run_vars.get("profile", tk.StringVar(value="worship")).get()).strip()
            return [current] if current else ["worship"]

        if not isinstance(data, dict) or not data:
            current = str(self.run_vars.get("profile", tk.StringVar(value="worship")).get()).strip()
            return [current] if current else ["worship"]

        names = sorted(str(name) for name in data.keys())
        current = str(self.run_vars.get("profile", tk.StringVar(value="")).get()).strip()
        if current and current not in names:
            names.insert(0, current)
        return names

    def _refresh_profile_options(self):
        if self.profile_combo is None:
            return
        values = self._load_profile_names()
        self.profile_combo["values"] = values
        current = str(self.run_vars["profile"].get()).strip()
        if not current and values:
            self.run_vars["profile"].set(values[0])
        self._update_runtime_profile_label()

    def _delete_selected_profile(self):
        selected = str(self.run_vars.get("profile", tk.StringVar(value="")).get()).strip()
        if not selected:
            self.status_label.config(text="[INFO] No profile selected", foreground="#fbbf24")
            return

        profiles_path = _app_dir() / "learning" / "profiles.json"
        if not profiles_path.exists():
            self.status_label.config(text="[INFO] profiles.json not found", foreground="#fbbf24")
            return

        confirm = messagebox.askyesno("Excluir perfil", f"Excluir perfil '{selected}'?")
        if not confirm:
            return

        try:
            with open(profiles_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
            if selected not in data:
                self.status_label.config(text=f"[INFO] Profile '{selected}' not found", foreground="#fbbf24")
                return

            data.pop(selected, None)
            with open(profiles_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            values = sorted(str(name) for name in data.keys())
            fallback = values[0] if values else ""
            self.run_vars["profile"].set(fallback)
            self._refresh_profile_options()
            self.status_label.config(text=f"[OK] Profile '{selected}' removido", foreground="#9ae6b4")
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Delete profile failed: {exc}", foreground="red")

    def _load_scene_names(self):
        lineup_cfg = self.config.get("lineup", {}) if isinstance(self.config.get("lineup", {}), dict) else {}
        scenes_cfg = lineup_cfg.get("scenes", {}) if isinstance(lineup_cfg.get("scenes", {}), dict) else {}
        names = sorted(str(name) for name in scenes_cfg.keys())
        current = str(self.run_vars.get("active_scene", tk.StringVar(value="")).get()).strip()
        if current and current not in names:
            names.insert(0, current)
        if not names:
            return [""]
        return names

    def _refresh_scene_options(self):
        if self.scene_combo is None:
            return
        values = self._load_scene_names()
        self.scene_combo["values"] = values
        current = str(self.run_vars["active_scene"].get()).strip()
        if not current and values:
            self.run_vars["active_scene"].set(values[0])

    def _infer_band_label_for_track(self, track_name):
        name = str(track_name or "").strip().lower()
        if not name:
            return "unmapped"

        if any(alias in name for alias in ("back", "backing", "choir", "coro", "bv")):
            return "p640-p1200 (back vocal body)"
        if any(alias in name for alias in ("vocal", "vox", "voz", "lead")):
            return "p1200-p2500 (lead presence)"
        if any(alias in name for alias in ("drum", "bateria")):
            return "p80-p160 + p5000 (impact/attack)"
        if any(alias in name for alias in ("bass", "baixo", "sub")):
            return "p20-p80 (sub/low end)"
        if any(alias in name for alias in ("keys", "key", "piano", "teclado", "synth", "pad", "kbd")):
            return "p320-p1200 (mid body)"
        if any(alias in name for alias in ("violao", "guitar", "gtr", "guitarra")):
            return "p320-p2500 (harmonics/presence)"
        return "p320-p640 (support bus)"

    def _update_track_band_label(self, row_data):
        if row_data is None:
            return
        band_var = row_data.get("band_label")
        name_var = row_data.get("name")
        if band_var is None or name_var is None:
            return
        band_var.set(self._infer_band_label_for_track(name_var.get()))

    def _add_track_row(self, track_id, name="", enabled=True, min_db=-6.0, max_db=0.0, fader_db=0.0, frozen=False):
        if self.tracks_box is None:
            return

        row = len(self.track_rows) + 1
        row_key = self._track_row_counter
        self._track_row_counter += 1

        enabled_var = tk.BooleanVar(value=bool(enabled))
        id_var = tk.IntVar(value=int(track_id))
        name_var = tk.StringVar(value=str(name))
        min_var = tk.DoubleVar(value=float(min_db))
        max_var = tk.DoubleVar(value=float(max_db))
        fader_var = tk.DoubleVar(value=float(fader_db))
        frozen_var = tk.BooleanVar(value=bool(frozen))
        band_label_var = tk.StringVar(value=self._infer_band_label_for_track(name_var.get()))

        enabled_toggle = self._create_image_toggle(self.tracks_box, enabled_var)
        enabled_toggle.grid(row=row, column=0, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=id_var, width=8).grid(row=row, column=1, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=name_var, width=14).grid(row=row, column=2, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=min_var, width=8).grid(row=row, column=3, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=max_var, width=8).grid(row=row, column=4, padx=4, sticky="w")
        ttk.Label(self.tracks_box, textvariable=band_label_var, width=30).grid(row=row, column=5, padx=4, sticky="w")

        row_data = {
            "enabled": enabled_var,
            "track_id": id_var,
            "name": name_var,
            "min_db": min_var,
            "max_db": max_var,
            "fader_db": fader_var,
            "frozen": frozen_var,
            "band_label": band_label_var,
        }

        for var_key in ("track_id", "name", "min_db", "max_db"):
            row_data[var_key].trace_add("write", lambda *_args, rk=row_key: self._on_track_row_changed(rk))
        row_data["fader_db"].trace_add("write", lambda *_args, rk=row_key: self._on_track_fader_changed(rk))

        self.track_rows.append(row_data)
        self.track_vars[row_key] = row_data

        if self.track_controls is not None:
            self.track_controls.grid_configure(row=len(self.track_rows) + 1)
        self._update_add_track_button_state()
        self._rebuild_mixer_view()

    def _next_track_id(self):
        existing = []
        for row_data in self.track_rows:
            try:
                existing.append(int(row_data["track_id"].get()))
            except Exception:
                continue
        return max(existing, default=0) + 1

    def _on_add_track(self):
        if len(self.track_rows) >= MAX_TRACK_ROWS:
            messagebox.showwarning("Track limit", f"Maximum of {MAX_TRACK_ROWS} tracks reached.")
            self._update_add_track_button_state()
            return
        self._add_track_row(
            track_id=self._next_track_id(),
            name="new_track",
            enabled=True,
            min_db=-6.0,
            max_db=0.0,
            fader_db=0.0,
            frozen=False,
        )

    def _on_track_row_changed(self, row_key):
        row_data = self.track_vars.get(row_key)
        self._update_track_band_label(row_data)
        self._rebuild_mixer_view()

    def _on_track_fader_changed(self, row_key):
        # Guard against partial input (empty string, lone "-", etc.)
        row_data = self.track_vars.get(row_key)
        if row_data is not None:
            try:
                float(row_data["fader_db"].get())
            except (ValueError, Exception):
                return  # field is still being typed ? ignore until valid
        if row_key not in self.mixer_slot_items:
            self._rebuild_mixer_view()
        self._animate_mixer_knob_for_row(row_key)

    def _update_add_track_button_state(self):
        if self.add_track_btn is None:
            return
        state = "normal" if len(self.track_rows) < MAX_TRACK_ROWS else "disabled"
        self.add_track_btn.config(state=state)

    def _show_track_map_details(self):
        lines = [
            "Map labels used by _build_track_map_from_config:",
            "",
            "drums: drum kit / bateria",
            "bass: bass / baixo",
            "piano: keys, piano, synth, pad",
            "other: guitar/fx fallback bus",
            "vocals: lead vocal",
            "backing_vocals: choir/BV/coro layers",
            "sub, low: drums + bass targets",
            "mid: keys + vocals + support layers",
            "highmid: keys + guitars + vocals",
            "air: drums + keys + vocals",
        ]
        messagebox.showinfo("Track Map Detail", "\n".join(lines))

    def _build_runtime_panel(self, parent):
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=0)

        control_box = ttk.LabelFrame(parent, text="Runtime", padding=10)
        control_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        scene_box = ttk.LabelFrame(parent, text="Scene", padding=10)
        scene_box.grid(row=0, column=1, sticky="nsew", pady=(0, 8), padx=(8, 0))
        scene_box.columnconfigure(0, weight=1)
        lineup_roles_box = ttk.Frame(scene_box)
        lineup_roles_box.grid(row=0, column=0, sticky="nw")
        self._lineup_role_syncing = True
        self._build_lineup_role_selector(lineup_roles_box)
        self._lineup_role_syncing = False

        self._load_action_button_images()

        self.start_stop_btn = tk.Button(
            control_box,
            command=self._toggle_start_stop,
            bg="#1f2937",
            fg="#9ca3af",
            activebackground="#374151",
            activeforeground="#e5e7eb",
            relief="solid",
            borderwidth=1,
            padx=0,
            pady=0,
            height=60,
        )
        self._set_start_stop_button_visual(False)
        self.start_stop_btn.grid(row=0, column=0, padx=(0, 6), sticky="w")

        save_btn = tk.Button(
            control_box,
            command=self._save_config,
            bg="#1f2937",
            fg="#9ca3af",
            activebackground="#374151",
            activeforeground="#e5e7eb",
            relief="solid",
            borderwidth=1,
            padx=0,
            pady=0,
            height=60,
        )
        if "save" in self.action_button_images:
            save_btn.config(image=self.action_button_images["save"], text="")
        else:
            save_btn.config(text="SAVE CONFIG")
        save_btn.grid(row=0, column=1, padx=(0, 6), sticky="w")

        learn_btn = tk.Button(
            control_box,
            command=self._learn_and_save_suggested,
            bg="#1f2937",
            fg="#9ca3af",
            activebackground="#374151",
            activeforeground="#e5e7eb",
            relief="solid",
            borderwidth=1,
            padx=0,
            pady=0,
            height=60,
        )
        if "learn" in self.action_button_images:
            learn_btn.config(image=self.action_button_images["learn"], text="")
        else:
            learn_btn.config(text="LEARN (10 s) + SAVE como")
        learn_btn.grid(row=0, column=2, padx=(0, 6), sticky="w")

        self.runtime_label = ttk.Label(control_box, text="Stopped", foreground="#ff6b6b")
        self.runtime_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.status_label = ttk.Label(control_box, text="Ready", foreground="#4cc9ff")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.reastream_status_label = ttk.Label(control_box, text="ReaStream: unknown", foreground="#6b7280")
        self.reastream_status_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.webapi_status_label = ttk.Label(control_box, text="Web API: unknown", foreground="#6b7280")
        self.webapi_status_label.grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self.master_meter_label = ttk.Label(control_box, text="Master: LUFS -- | RMS -- dB", foreground="#95d5ff")
        self.master_meter_label.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self.profile_status_label = ttk.Label(control_box, text="Profile: --", foreground="#6b7280")
        self.profile_status_label.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.scene_status_label = ttk.Label(control_box, text="Scene: default", foreground="#6b7280")
        self.scene_status_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(2, 0))

        self.mix_stability_label = ttk.Label(control_box, text="Mix stability: --", foreground="#6b7280")
        self.mix_stability_label.grid(row=8, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self._update_runtime_profile_label()
        self._update_runtime_scene_label()

        self.learn_preview_actions_frame = None
        self.learn_apply_btn = tk.Button(
            control_box,
            command=self._apply_learned_profile,
            state="disabled",
            bg="#1f2937",
            fg="#9ca3af",
            activebackground="#374151",
            activeforeground="#e5e7eb",
            relief="solid",
            borderwidth=1,
            padx=0,
            pady=0,
            height=60,
        )
        if "apply" in self.action_button_images:
            self.learn_apply_btn.config(image=self.action_button_images["apply"], text="")
        else:
            self.learn_apply_btn.config(text="APLICAR COMO PERFIL")
        self.learn_apply_btn.grid(row=0, column=4, padx=(0, 6), sticky="w")
        self.learn_merge_btn = tk.Button(
            control_box,
            command=self._merge_learned_profile_70_30,
            state="disabled",
            bg="#1f2937",
            fg="#9ca3af",
            activebackground="#374151",
            activeforeground="#e5e7eb",
            relief="solid",
            borderwidth=1,
            padx=0,
            pady=0,
            height=60,
        )
        if "merge" in self.action_button_images:
            self.learn_merge_btn.config(image=self.action_button_images["merge"], text="")
        else:
            self.learn_merge_btn.config(text="MESCLAR 70/30")
        self.learn_merge_btn.grid(row=0, column=5, sticky="w", padx=(0, 0))

        self.dry_run_btn = tk.Button(
            control_box,
            command=self._toggle_dry_run,
            bg="#1f2937",
            fg="#9ca3af",
            activebackground="#374151",
            activeforeground="#e5e7eb",
            relief="solid",
            borderwidth=1,
            padx=0,
            pady=0,
            height=60,
        )
        if "dry" in self.action_button_images:
            self.dry_run_btn.config(image=self.action_button_images["dry"], text="")
        else:
            self.dry_run_btn.config(text="DRY-RUN: OFF")
        self.dry_run_btn.grid(row=0, column=3, padx=(0, 6), sticky="w")

        self.dry_run_frame = ttk.LabelFrame(control_box, text="DRY-RUN Source", padding=8)
        self.dry_run_frame.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.dry_run_frame.columnconfigure(1, weight=1)

        ttk.Label(self.dry_run_frame, text="Source").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.audio_source_combo = ttk.Combobox(
            self.dry_run_frame,
            textvariable=self.audio_source_var,
            values=["reastream", "device", "file"],
            state="readonly",
            width=16,
        )
        self.audio_source_combo.grid(row=0, column=1, sticky="ew")
        self.audio_source_combo.bind("<<ComboboxSelected>>", self._on_audio_source_changed)

        ttk.Label(self.dry_run_frame, text="Device").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(6, 0))
        self.device_combo = ttk.Combobox(self.dry_run_frame, state="readonly", width=56)
        self.device_combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_selected)

        self.file_picker_label = ttk.Label(self.dry_run_frame, text="File: none", foreground="#9ca3af")
        self.file_picker_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(self.dry_run_frame, text="Browse", command=self._on_file_picker_click).grid(
            row=2,
            column=2,
            sticky="e",
            pady=(6, 0),
        )

        self.dry_run_status_label = ttk.Label(self.dry_run_frame, text="Status: OFF", foreground="#9ca3af")
        self.dry_run_status_label.grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))

        graph_box = ttk.LabelFrame(parent, text="Audio bands (live RMS dB meter)", padding=10)
        graph_box.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(6, 4))
        parent.rowconfigure(1, weight=1)

        self.band_graph_canvas = tk.Canvas(graph_box, height=250, highlightthickness=0, bd=0, bg="#031425")
        self.band_graph_canvas.grid(row=0, column=0, sticky="nsew")
        self.band_graph_canvas.bind("<Configure>", self._on_band_graph_resize)
        graph_box.columnconfigure(0, weight=1)
        graph_box.rowconfigure(0, weight=1)
        self._rebuild_band_graph()

        mixer_box = ttk.LabelFrame(parent, text="Track faders", padding=10)
        mixer_box.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(4, 0))
        parent.rowconfigure(2, weight=1)

        self.mixer_canvas = tk.Canvas(mixer_box, height=304, highlightthickness=0, bd=0, bg="#111317")
        self.mixer_canvas.grid(row=0, column=0, sticky="nsew")
        self.mixer_canvas.bind("<Configure>", self._on_mixer_canvas_resize)
        mixer_box.columnconfigure(0, weight=1)
        mixer_box.rowconfigure(0, weight=1)
        self._load_mixer_images()

    def _on_band_graph_resize(self, _event):
        self._rebuild_band_graph()

    def _rebuild_band_graph(self):
        if self.band_graph_canvas is None:
            return

        canvas = self.band_graph_canvas
        canvas.delete("all")
        self.band_ui = {}

        w = max(1, int(canvas.winfo_width()))
        h = max(1, int(canvas.winfo_height()))
        left = 14
        right = 14
        top = 38
        bottom = 36
        plot_w = max(1, w - left - right)
        plot_h = max(1, h - top - bottom)
        slot = plot_w / float(len(BAND_ORDER))

        for i in range(6):
            y = top + (plot_h * i / 5.0)
            canvas.create_line(left, y, w - right, y, fill="#0b4f80")

        for idx, band in enumerate(BAND_ORDER):
            x_left = left + (idx * slot) + (slot * 0.18)
            x_right = left + ((idx + 1) * slot) - (slot * 0.18)
            x_mid = (x_left + x_right) * 0.5
            floor_y = top + plot_h

            slot_item = canvas.create_rectangle(x_left, top, x_right, floor_y, fill="#06243b", outline="#1177b8")
            bar_item = canvas.create_rectangle(x_left, floor_y, x_right, floor_y, fill="#16d4ff", outline="#5ce5ff")
            value_item = canvas.create_text(
                x_mid,
                24,
                text=f"{METER_DB_FLOOR:+.1f} dB",
                fill="#7de8ff",
                font=("Consolas", 8, "bold"),
            )
            delta_item = canvas.create_text(
                x_mid,
                12,
                text=f"{0.0:+.2f}",
                fill="#b9f2ff",
                font=("Consolas", 8, "bold"),
            )
            label_item = canvas.create_text(
                x_mid,
                h - 16,
                text=band.upper(),
                fill="#79cfff",
                font=("Segoe UI", 8, "bold"),
            )
            self.band_ui[band] = {
                "slot": slot_item,
                "bar": bar_item,
                "learn_bar": canvas.create_rectangle(x_left, floor_y, x_right, floor_y, fill="#f59e0b", outline="#fbbf24"),
                "delta": delta_item,
                "value": value_item,
                "label": label_item,
                "x0": x_left,
                "x1": x_right,
                "top": top,
                "bottom": floor_y,
            }

        self._refresh_bars()

    def _learn_10_seconds(self):
        self._start_learn_capture(save_to_profiles=False)

    def _learn_and_save_suggested(self):
        self._start_learn_capture(save_to_profiles=True)

    def _suggest_learn_profile_name(self):
        profiles_path = _app_dir() / "learning" / "profiles.json"
        next_idx = 1
        if profiles_path.exists():
            try:
                with open(profiles_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
            if isinstance(data, dict):
                seen = 0
                for key in data.keys():
                    key_str = str(key).strip().lower()
                    if key_str.startswith("profile_learn_") or key_str.startswith("worship-pa-"):
                        seen += 1
                next_idx = seen + 1
        return f"Profile_learn_{next_idx:03d}"

    def _learn_db_to_profile_values(self, learned_db):
        out = {}
        denom = float(METER_DB_CEIL - METER_DB_FLOOR)
        if abs(denom) < 1e-9:
            denom = 1.0
        for band in BAND_ORDER:
            value_db = float(learned_db.get(band, METER_DB_FLOOR))
            norm = (value_db - METER_DB_FLOOR) / denom
            out[band] = max(0.0, min(1.0, float(norm)))
        return out

    def _upsert_profile(self, profile_name, master_values):
        profile_name = str(profile_name or "").strip()
        if not profile_name:
            raise ValueError("Profile name is empty")

        profiles_path = _app_dir() / "learning" / "profiles.json"
        if profiles_path.exists():
            try:
                with open(profiles_path, "r", encoding="utf-8") as f:
                    profiles = json.load(f)
            except Exception:
                profiles = {}
        else:
            profiles = {}

        if not isinstance(profiles, dict):
            profiles = {}

        profiles[profile_name] = dict(master_values)
        with open(profiles_path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)

    def _apply_learned_profile(self):
        if not isinstance(self.learn_preview_db, dict):
            self.status_label.config(text="[INFO] No learned curve to apply", foreground="#fbbf24")
            return

        profile_name = str(self.run_vars.get("profile", tk.StringVar(value="")).get()).strip() or self._suggest_learn_profile_name()
        profile_values = self._learn_db_to_profile_values(self.learn_preview_db)
        try:
            self._upsert_profile(profile_name, profile_values)
            self.run_vars["profile"].set(profile_name)
            self._refresh_profile_options()
            self.status_label.config(text=f"[OK] Learned curve applied to profile '{profile_name}'", foreground="#9ae6b4")
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Apply profile failed: {exc}", foreground="red")

    def _merge_learned_profile_70_30(self):
        if not isinstance(self.learn_preview_db, dict):
            self.status_label.config(text="[INFO] No learned curve to merge", foreground="#fbbf24")
            return

        profile_name = str(self.run_vars.get("profile", tk.StringVar(value="")).get()).strip() or self._suggest_learn_profile_name()
        profiles_path = _app_dir() / "learning" / "profiles.json"
        existing = {}
        if profiles_path.exists():
            try:
                with open(profiles_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = {}
        if not isinstance(existing, dict):
            existing = {}

        learned_norm = self._learn_db_to_profile_values(self.learn_preview_db)
        current = existing.get(profile_name, {}) if isinstance(existing.get(profile_name, {}), dict) else {}
        merged = {}
        for band in BAND_ORDER:
            current_value = float(current.get(band, learned_norm.get(band, 0.0)))
            learned_value = float(learned_norm.get(band, 0.0))
            merged[band] = (0.70 * current_value) + (0.30 * learned_value)

        try:
            self._upsert_profile(profile_name, merged)
            self.run_vars["profile"].set(profile_name)
            self._refresh_profile_options()
            self.status_label.config(text=f"[OK] Profile '{profile_name}' merged (70/30 current/learned)", foreground="#9ae6b4")
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Merge failed: {exc}", foreground="red")

    def _start_learn_capture(self, save_to_profiles=False):
        if self.learn_in_progress:
            self.status_label.config(text="[INFO] Learn already running", foreground="#fbbf24")
            return
        if self.process_handle is None:
            self.status_label.config(text="[INFO] Start runtime before learning", foreground="#fbbf24")
            return

        self.learn_in_progress = True
        self.learn_samples = []
        self.learn_preview_db = None
        self.learn_preview_source_name = ""
        if self.learn_apply_btn is not None:
            self.learn_apply_btn.config(state="disabled")
        if self.learn_merge_btn is not None:
            self.learn_merge_btn.config(state="disabled")
        self.status_label.config(text="[INFO] Learning... 10", foreground="#7de8ff")

        start_at = dt.datetime.now()

        def _collect(step=0):
            if step < 20:
                self.learn_samples.append(dict(self.band_targets))
                remaining = max(0, 10 - (step // 2))
                self.status_label.config(text=f"[INFO] Learning... {remaining}", foreground="#7de8ff")
                self.root.after(500, lambda: _collect(step + 1))
                return

            learned = {}
            for band in BAND_ORDER:
                values = [float(s.get(band, METER_DB_FLOOR)) for s in self.learn_samples]
                learned[band] = float(sum(values) / len(values)) if values else float(METER_DB_FLOOR)

            self.learn_preview_db = learned
            self._refresh_bars()
            if self.learn_apply_btn is not None:
                self.learn_apply_btn.config(state="normal")
            if self.learn_merge_btn is not None:
                self.learn_merge_btn.config(state="normal")

            preview_text = "[OK] Learn complete: preview ready"

            payload = {
                "created_at": start_at.replace(microsecond=0).isoformat(),
                "source": "live_segment",
                "duration_seconds": 10,
                "master": learned,
            }

            out_name = f"mymix_profile_{start_at.date().isoformat()}.json"
            out_path = _app_dir() / "learning" / out_name
            try:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                if save_to_profiles:
                    suggested_name = self._suggest_learn_profile_name()
                    self.learn_preview_source_name = suggested_name
                    profile_values = self._learn_db_to_profile_values(learned)
                    self._upsert_profile(suggested_name, profile_values)
                    self.run_vars["profile"].set(suggested_name)
                    self._refresh_profile_options()
                    self.status_label.config(
                        text=f"[OK] Learn saved as profile '{suggested_name}' + preview ready",
                        foreground="#9ae6b4",
                    )
                else:
                    self.status_label.config(text=preview_text, foreground="#9ae6b4")
            except Exception as exc:
                self.status_label.config(text=f"[ERROR] Learn save failed: {exc}", foreground="red")
            finally:
                self.learn_in_progress = False

        _collect(0)

    def _on_mixer_canvas_resize(self, _event):
        self._rebuild_mixer_view()

    def _load_mixer_images(self):
        strip_path = _asset_path("fader.png")
        button_path = _asset_path("fader_buttom.png")
        if not button_path.exists():
            alt = _asset_path("fader_button.png")
            if alt.exists():
                button_path = alt

        try:
            if strip_path.exists():
                self.fader_strip_img = tk.PhotoImage(file=str(strip_path))
        except Exception:
            self.fader_strip_img = None

        try:
            if button_path.exists():
                self.fader_button_img = tk.PhotoImage(file=str(button_path))
        except Exception:
            self.fader_button_img = None

    def _db_to_mixer_y(self, value_db, top_y, bottom_y):
        try:
            value = float(value_db)
        except Exception:
            value = 0.0

        # Reaper fader travel is logarithmic in dB. Map dB -> amplitude domain
        # and normalize between configured endpoints for visual parity.
        clamped = max(REAPER_FADER_MIN_DB, min(REAPER_FADER_MAX_DB, value))
        min_amp = 10.0 ** (REAPER_FADER_MIN_DB / 80.0)
        max_amp = 10.0 ** (REAPER_FADER_MAX_DB / 80.0)
        amp = 10.0 ** (clamped / 80.0)

        denom = (max_amp - min_amp)
        if denom <= 1e-12:
            ratio = 0.0
        else:
            ratio = (amp - min_amp) / denom

        ratio = max(0.0, min(1.0, ratio))
        return bottom_y - ratio * (bottom_y - top_y)

    def _rebuild_mixer_view(self):
        if self.mixer_canvas is None:
            return

        for row_key in list(self.mixer_anim_jobs.keys()):
            self._cancel_mixer_knob_animation(row_key)

        canvas = self.mixer_canvas
        canvas.delete("all")
        self.mixer_slot_items = {}

        visible_rows = self.track_rows[:MAX_TRACK_ROWS]
        if not visible_rows:
            canvas.create_text(14, 24, text="No tracks configured", anchor="w", fill="#f0f0f0")
            return

        left_pad = 6
        right_pad = 6
        min_slot_w = 90
        available_w = max(1, int(canvas.winfo_width()))
        slot_w = max(min_slot_w, (available_w - left_pad - right_pad) / float(len(visible_rows)))
        strip_pitch = (self.fader_strip_img.width() + 36) if self.fader_strip_img is not None else 72
        top_label_y = 12
        top_id_y = 26
        track_top_y = 36
        track_bottom_y = 230

        content_width = left_pad + right_pad + max(slot_w * len(visible_rows), strip_pitch * len(visible_rows))
        canvas.config(scrollregion=(0, 0, content_width, 304))

        for idx, row_data in enumerate(visible_rows):
            x0 = left_pad + idx * strip_pitch
            center_x = x0 + slot_w / 2
            row_key = next((k for k, v in self.track_vars.items() if v is row_data), None)
            if row_key is None:
                continue

            try:
                track_id = int(row_data["track_id"].get())
            except Exception:
                track_id = 0
            name = str(row_data["name"].get()).strip() or f"Track {track_id}"

            if self.fader_strip_img is not None:
                strip_w = self.fader_strip_img.width()
                strip_h = self.fader_strip_img.height()
                center_x = x0 + (strip_w / 2)
                strip_item = canvas.create_image(center_x, track_top_y, image=self.fader_strip_img, anchor="n")
                strip_left = center_x - strip_w / 2
                strip_top = track_top_y
                strip_bottom = strip_top + strip_h
            else:
                center_x = x0 + 24
                strip_left = center_x - 24
                strip_top = track_top_y
                strip_bottom = track_bottom_y
                strip_item = canvas.create_rectangle(
                    strip_left,
                    strip_top,
                    strip_left + 48,
                    strip_bottom,
                    outline="#6b7280",
                    fill="#2b3038",
                )

            # Draw labels after center_x is finalized from strip geometry.
            canvas.create_text(center_x, top_label_y, text=name, fill="#eaf5ff", font=("Segoe UI", 9, "bold"))
            canvas.create_text(center_x, top_id_y, text=str(track_id), fill="#6fd3ff", font=("Segoe UI", 9))
            lock_text = "LOCK" if bool(row_data.get("frozen", tk.BooleanVar(value=False)).get()) else "FREE"
            lock_color = "#7de8ff" if lock_text == "LOCK" else "#6b7280"
            lock_item = canvas.create_text(
                center_x - 24,
                top_id_y,
                text=lock_text,
                fill=lock_color,
                font=("Consolas", 7, "bold"),
            )
            canvas.tag_bind(lock_item, "<Button-1>", lambda _event, rk=row_key: self._toggle_track_freeze(rk))

            top_y = strip_top + 10
            bottom_y = min(strip_bottom - 10, track_bottom_y)
            knob_center_y = self._db_to_mixer_y(
                row_data["fader_db"].get(),
                top_y,
                bottom_y,
            )

            if self.fader_button_img is not None:
                knob_item = canvas.create_image(center_x, knob_center_y, image=self.fader_button_img, anchor="center")
            else:
                knob_item = canvas.create_rectangle(
                    strip_left + 4,
                    knob_center_y - 8,
                    strip_left + 44,
                    knob_center_y + 8,
                    fill="#d7d7d7",
                    outline="#505050",
                )

            value_item = canvas.create_text(
                center_x,
                min(268, strip_bottom + 22),
                text=f"{float(row_data['fader_db'].get()):+.2f} dB",
                fill="#8fb9ff",
                font=("Consolas", 8),
            )
            intent_text, intent_color, intent_detail = self._intent_style_for_track(track_id)
            intent_item = canvas.create_text(
                center_x + 22,
                top_id_y,
                text=intent_text,
                fill=intent_color,
                font=("Segoe UI", 11, "bold"),
            )
            intent_detail_item = canvas.create_text(
                center_x,
                min(284, strip_bottom + 36),
                text=intent_detail,
                fill=intent_color,
                font=("Consolas", 8, "bold"),
            )
            meter_item = canvas.create_text(
                center_x,
                min(298, strip_bottom + 50),
                text=self._format_track_meter_text(track_id),
                fill="#228537",
                font=("Consolas", 6),
            )

            self.mixer_slot_items[row_key] = {
                "track_id": track_id,
                "strip_item": strip_item,
                "knob_item": knob_item,
                "value_item": value_item,
                "intent_item": intent_item,
                "intent_detail_item": intent_detail_item,
                "meter_item": meter_item,
                "lock_item": lock_item,
                "top_y": top_y,
                "bottom_y": bottom_y,
                "center_x": center_x,
                "strip_left": strip_left,
                "uses_image_knob": self.fader_button_img is not None,
            }

        self._refresh_mixer_metadata()

    def _set_track_fader_value(self, track_id, value_db):
        for row_data in self.track_rows:
            try:
                current_id = int(row_data["track_id"].get())
            except Exception:
                continue
            if current_id != track_id:
                continue
            try:
                current_val = float(row_data["fader_db"].get())
            except Exception:
                current_val = 0.0
            if abs(current_val - float(value_db)) > 0.0001:
                row_data["fader_db"].set(round(float(value_db), 3))

    def _cancel_mixer_knob_animation(self, row_key):
        pending_job = self.mixer_anim_jobs.pop(row_key, None)
        if pending_job is None:
            return
        try:
            self.root.after_cancel(pending_job)
        except Exception:
            pass

    def _knob_center_y(self, slot):
        coords = self.mixer_canvas.coords(slot["knob_item"])
        if slot["uses_image_knob"] and len(coords) >= 2:
            return float(coords[1])
        if len(coords) >= 4:
            return float((coords[1] + coords[3]) * 0.5)
        return None

    def _set_knob_center_y(self, row_key, knob_center_y):
        slot = self.mixer_slot_items.get(row_key)
        row_data = self.track_vars.get(row_key)
        if slot is None or row_data is None:
            return

        try:
            if slot["uses_image_knob"]:
                self.mixer_canvas.coords(slot["knob_item"], slot["center_x"], knob_center_y)
            else:
                self.mixer_canvas.coords(
                    slot["knob_item"],
                    slot["strip_left"] + 4,
                    knob_center_y - 8,
                    slot["strip_left"] + 44,
                    knob_center_y + 8,
                )

            try:
                label_val = float(row_data["fader_db"].get())
            except Exception:
                label_val = knob_center_y  # fallback ? won't happen normally
            self.mixer_canvas.itemconfig(
                slot["value_item"],
                text=f"{label_val:+.2f} dB",
            )
        except Exception:
            pass  # canvas item IDs went stale after a rebuild ? next tick will have fresh ones

    def _animate_mixer_knob_for_row(self, row_key):
        if self.mixer_canvas is None:
            return

        slot = self.mixer_slot_items.get(row_key)
        row_data = self.track_vars.get(row_key)
        if slot is None or row_data is None:
            return

        try:
            fader_db_val = float(row_data["fader_db"].get())
        except (ValueError, Exception):
            return  # skip animation while field is partially edited

        target_y = self._db_to_mixer_y(
            fader_db_val,
            slot["top_y"],
            slot["bottom_y"],
        )
        current_y = self._knob_center_y(slot)
        if current_y is None:
            current_y = target_y

        self._cancel_mixer_knob_animation(row_key)

        if abs(target_y - current_y) <= 0.4:
            self._set_knob_center_y(row_key, target_y)
            return

        steps = max(1, int(self.mixer_anim_duration_ms / 16))
        animation_state = {"step": 0}

        def _tick():
            animation_state["step"] += 1
            t = animation_state["step"] / float(steps)
            t = max(0.0, min(1.0, t))
            eased = 1.0 - ((1.0 - t) * (1.0 - t))
            y = current_y + ((target_y - current_y) * eased)
            self._set_knob_center_y(row_key, y)

            if animation_state["step"] < steps:
                self.mixer_anim_jobs[row_key] = self.root.after(16, _tick)
            else:
                self.mixer_anim_jobs.pop(row_key, None)

        _tick()

    def _update_mixer_knob_for_track(self, track_id, animate=False):
        if self.mixer_canvas is None:
            return
        for row_key, slot in list(self.mixer_slot_items.items()):
            if slot.get("track_id") != track_id:
                continue
            row_data = self.track_vars.get(row_key)
            if row_data is None:
                continue

            try:
                fader_val = float(row_data["fader_db"].get())
            except Exception:
                continue

            if animate:
                self._animate_mixer_knob_for_row(row_key)
                continue

            knob_center_y = self._db_to_mixer_y(
                fader_val,
                slot["top_y"], slot["bottom_y"],
            )
            self._set_knob_center_y(row_key, knob_center_y)

    def _collect_config_from_ui(self):
        cfg = dict(self.config)
        cfg["master_track"] = int(self.master_var.get())

        silence_floor = float(self.analysis_vars["silence_floor_rms"].get())
        if not math.isfinite(silence_floor) or silence_floor < 0.0 or silence_floor > 1e-2:
            silence_floor = 1e-6

        tracks = {}
        for _, vars_dict in self.track_vars.items():
            track_id = int(vars_dict["track_id"].get())
            tracks[str(track_id)] = {
                "name": str(vars_dict["name"].get()),
                "enabled": bool(vars_dict["enabled"].get()),
                "frozen": bool(vars_dict.get("frozen", tk.BooleanVar(value=False)).get()),
                "min_db": float(vars_dict["min_db"].get()),
                "max_db": float(vars_dict["max_db"].get()),
                "fader_db": float(vars_dict["fader_db"].get()),
            }
        cfg["tracks"] = tracks

        analysis_settings = dict(cfg.get("analysis_settings", {})) if isinstance(cfg.get("analysis_settings"), dict) else {}
        analysis_settings.update({
            "error_gain_up": float(self.analysis_vars["error_gain_up"].get()),
            "error_gain_down": float(self.analysis_vars["error_gain_down"].get()),
            "max_step_up_db": float(self.analysis_vars["max_step_up_db"].get()),
            "max_step_down_db": float(self.analysis_vars["max_step_down_db"].get()),
            "error_deadband": float(self.analysis_vars["error_deadband"].get()),
            "max_tracks_raise_per_cycle": int(self.analysis_vars["max_tracks_raise_per_cycle"].get()),
            "lufs_warning_threshold": float(self.analysis_vars["lufs_warning_threshold"].get()),
            "silence_floor_rms": silence_floor,
            "control_blend_spec": float(self.analysis_vars["control_blend_spec"].get()),
            "control_blend_lufs": float(self.analysis_vars["control_blend_lufs"].get()),
            "level_gain": float(self.analysis_vars["level_gain"].get()),
            "level_error_clip_db": float(self.analysis_vars["level_error_clip_db"].get()),
            "level_source": str(self.analysis_vars["level_source"].get()).strip().lower() or "lufs",
        })
        cfg["analysis_settings"] = analysis_settings

        reastream_host = _normalize_runtime_host(self.run_vars["reastream_host"].get(), "0.0.0.0")
        webapi_host = _normalize_runtime_host(self.run_vars["webapi_host"].get(), "127.0.0.1")

        cfg["run_settings"] = {
            "profile": str(self.run_vars["profile"].get()).strip(),
            "reastream": bool(self.run_vars["reastream"].get()),
            "reastream_identifier": str(self.run_vars["reastream_identifier"].get()).strip(),
            "reastream_host": reastream_host,
            "reastream_port": int(self.run_vars["reastream_port"].get()),
            "webapi_host": webapi_host,
            "webapi_port": int(self.run_vars["webapi_port"].get()),
            "webapi_base": str(self.run_vars["webapi_base"].get()).strip() or "/_",
            "webapi_timeout": float(self.run_vars["webapi_timeout"].get()),
            "channels": int(self.run_vars["channels"].get()),
            "analysis_interval": float(self.run_vars["analysis_interval"].get()),
            "verbose": bool(self.run_vars["verbose"].get()),
        }

        cfg["lineup"] = self._build_lineup_config_from_roles(cfg)

        current_dry = cfg.get("dry_run_settings", {}) if isinstance(cfg.get("dry_run_settings"), dict) else {}
        selected_device = str(self.device_combo.get()).strip() if self.device_combo is not None else ""
        cfg["dry_run_settings"] = {
            "enabled": bool(self.dry_run_var.get()),
            "audio_source": str(self.audio_source_var.get()).strip().lower() or "reastream",
            "file_path": str(current_dry.get("file_path", "")).strip(),
            "loop_count": int(current_dry.get("loop_count", 1) or 1),
            "device_id": current_dry.get("device_id", None),
            "device_name": selected_device,
            "sample_rate": int(current_dry.get("sample_rate", 44100) or 44100),
            "blocksize": int(current_dry.get("blocksize", 4096) or 4096),
            "channels": int(current_dry.get("channels", 2) or 2),
        }
        return cfg

    def _save_config(self):
        try:
            self.config = self._collect_config_from_ui()
            save_config(self.config, CONFIG_FILE)
            self._refresh_profile_options()
            self.status_label.config(text="[OK] Config saved", foreground="green")
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] {exc}", foreground="red")
            messagebox.showerror("Save error", str(exc))

    def _build_run_command(self):
        cfg = self._collect_config_from_ui()
        run = cfg["run_settings"]

        if getattr(sys, "frozen", False):
            worker = _app_dir() / "run_profile_worker.exe"
            if not worker.exists():
                raise FileNotFoundError(
                    f"Worker executable not found: {worker}. Rebuild distribution with run_profile_worker.exe."
                )
            cmd = [
                str(worker),
                "--profile",
                run["profile"],
                "--channels",
                str(run["channels"]),
                "--analysis-interval",
                str(run["analysis_interval"]),
            ]
        else:
            cmd = [
                sys.executable,
                "-u",
                "run_profile.py",
                "--profile",
                run["profile"],
                "--channels",
                str(run["channels"]),
                "--analysis-interval",
                str(run["analysis_interval"]),
            ]

        cmd.extend(
            [
                "--webapi-host",
                run["webapi_host"],
                "--webapi-port",
                str(run["webapi_port"]),
                "--webapi-base",
                run["webapi_base"],
                "--webapi-timeout",
                str(run["webapi_timeout"]),
                "--reastream-identifier",
                run["reastream_identifier"],
                "--reastream-host",
                run["reastream_host"],
                "--reastream-port",
                str(run["reastream_port"]),
            ]
        )

        dry_run_cfg = cfg.get("dry_run_settings", {})
        is_dry_run = bool(dry_run_cfg.get("enabled", False))
        audio_source = str(dry_run_cfg.get("audio_source", "reastream")).strip().lower() or "reastream"
        force_verbose = bool(run["verbose"]) or is_dry_run

        if is_dry_run:
            if audio_source == "device":
                device_name = str(dry_run_cfg.get("device_name", "")).strip()
                if not device_name:
                    raise ValueError("DRY-RUN device mode: no device selected")
                device_value = device_name.split(":", 1)[0].strip() if ":" in device_name else device_name
                cmd.extend(
                    [
                        "--live",
                        "--device",
                        device_value,
                        "--blocksize",
                        str(int(dry_run_cfg.get("blocksize", 4096) or 4096)),
                        "--channels",
                        str(int(dry_run_cfg.get("channels", 2) or 2)),
                    ]
                )
            elif audio_source == "file":
                file_path = str(dry_run_cfg.get("file_path", "")).strip()
                if not file_path:
                    raise ValueError("DRY-RUN file mode: no file selected")
                if not os.path.exists(file_path):
                    raise FileNotFoundError(f"Test audio file not found: {file_path}")
                cmd.extend(["--test-audio", file_path, "--test-speed", "1.0"])
            else:
                cmd.extend(
                    [
                        "--reastream",
                    ]
                )
        elif run["reastream"]:
            cmd.extend(
                [
                    "--reastream",
                ]
            )
        if force_verbose:
            cmd.append("--verbose")
        return cmd

    def _toggle_start_stop(self):
        if self.process_handle is None:
            self._start_process()
        else:
            self._stop_process(manual=True)

    def _toggle_dry_run(self):
        new_state = not bool(self.dry_run_var.get())
        self.dry_run_var.set(new_state)

        if new_state:
            self.dry_run_btn.config(bg="#0f3b4a", fg="#4cc9ff")
            if "dry" not in self.action_button_images:
                self.dry_run_btn.config(text="DRY-RUN: ON")
            self.dry_run_frame.grid()
            self.status_label.config(text="[INFO] DRY-RUN enabled - Web API writes blocked", foreground="#fbbf24")
        else:
            self.dry_run_btn.config(bg="#1f2937", fg="#9ca3af")
            if "dry" not in self.action_button_images:
                self.dry_run_btn.config(text="DRY-RUN: OFF")
            self.dry_run_frame.grid_remove()
            self.status_label.config(text="[INFO] DRY-RUN disabled", foreground="#4cc9ff")

        self._refresh_audio_sources()
        self._save_config()

    def _on_audio_source_changed(self, _event=None):
        self._refresh_audio_sources()
        self._save_config()

        source = str(self.audio_source_var.get()).strip().lower() or "reastream"
        color = "#9ca3af"
        if source == "device":
            color = "#9ae6b4"
        elif source == "file":
            color = "#fbbf24"
        if self.dry_run_status_label is not None:
            self.dry_run_status_label.config(text=f"Status: source={source}", foreground=color)

    def _get_audio_devices(self):
        try:
            import sounddevice as sd
        except Exception:
            return []

        devices = []
        try:
            for idx, info in enumerate(sd.query_devices()):
                if int(info.get("max_input_channels", 0)) > 0:
                    name = str(info.get("name", "Unknown")).strip()
                    devices.append(f"{idx}: {name}")
        except Exception:
            return []
        return devices

    def _refresh_audio_sources(self):
        if self.dry_run_frame is None:
            return

        source = str(self.audio_source_var.get()).strip().lower() or "reastream"
        if source == "device":
            devices = self._get_audio_devices()
            self.device_combo.config(values=devices)
            if not devices:
                self.device_combo.set("")
                self.file_picker_label.config(text="Device: none found", foreground="#f87171")
            elif not str(self.device_combo.get()).strip():
                self.device_combo.set(devices[0])
                self.file_picker_label.config(text=f"Device: {devices[0]}", foreground="#9ae6b4")
            else:
                self.file_picker_label.config(text=f"Device: {self.device_combo.get()}", foreground="#9ae6b4")
            self.device_combo.grid()
        elif source == "file":
            self.device_combo.grid_remove()
            current = self.config.get("dry_run_settings", {}).get("file_path", "")
            if current:
                self.file_picker_label.config(text=f"File: {os.path.basename(current)}", foreground="#fbbf24")
            else:
                self.file_picker_label.config(text="File: none", foreground="#9ca3af")
        else:
            self.device_combo.grid_remove()
            self.file_picker_label.config(text="Source: ReaStream", foreground="#9ca3af")

    def _on_device_selected(self, _event=None):
        selected = str(self.device_combo.get()).strip()
        self.config.setdefault("dry_run_settings", {})
        self.config["dry_run_settings"]["device_name"] = selected
        try:
            save_config(self.config, CONFIG_FILE)
            if self.file_picker_label is not None:
                self.file_picker_label.config(text=f"Device: {selected}", foreground="#9ae6b4")
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Failed to save device: {exc}", foreground="red")

    def _on_file_picker_click(self):
        file_path = filedialog.askopenfilename(
            parent=self.root,
            title="Select audio file",
            initialdir=str(_app_dir()),
            filetypes=[("Audio", "*.wav *.wave *.flac *.aiff *.aif *.mp3"), ("All files", "*.*")],
        )
        if not file_path:
            return

        self.config.setdefault("dry_run_settings", {})
        self.config["dry_run_settings"]["file_path"] = file_path
        try:
            save_config(self.config, CONFIG_FILE)
            if self.file_picker_label is not None:
                self.file_picker_label.config(text=f"File: {os.path.basename(file_path)}", foreground="#fbbf24")
            if self.dry_run_status_label is not None:
                self.dry_run_status_label.config(
                    text=f"Status: file={os.path.basename(file_path)}",
                    foreground="#fbbf24",
                )
        except Exception as exc:
            self.status_label.config(text=f"[ERROR] Failed to save file: {exc}", foreground="red")

    def _sync_dry_run_ui(self):
        dry = self.config.get("dry_run_settings", {}) if isinstance(self.config.get("dry_run_settings"), dict) else {}
        enabled = bool(dry.get("enabled", False))
        source = str(dry.get("audio_source", "reastream")).strip().lower() or "reastream"

        self.dry_run_var.set(enabled)
        self.audio_source_var.set(source)

        device_name = str(dry.get("device_name", "")).strip()
        if self.device_combo is not None and device_name:
            self.device_combo.set(device_name)

        if enabled:
            self.dry_run_btn.config(bg="#0f3b4a", fg="#4cc9ff")
            if "dry" not in self.action_button_images:
                self.dry_run_btn.config(text="DRY-RUN: ON")
            self.dry_run_frame.grid()
        else:
            self.dry_run_btn.config(bg="#1f2937", fg="#9ca3af")
            if "dry" not in self.action_button_images:
                self.dry_run_btn.config(text="DRY-RUN: OFF")
            self.dry_run_frame.grid_remove()

        self._refresh_audio_sources()

    def _open_runtime_log(self, cmd):
        self._close_runtime_log()
        self.last_runtime_log_path = None

        dry = self.config.get("dry_run_settings", {}) if isinstance(self.config.get("dry_run_settings"), dict) else {}
        if not bool(dry.get("enabled", False)):
            return

        source = str(dry.get("audio_source", "reastream")).strip().lower() or "reastream"
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = _app_dir() / "logs"
        logs_dir.mkdir(exist_ok=True)
        log_path = logs_dir / f"dry_run_{source}_{stamp}.txt"
        cmd_text = " ".join(f'"{part}"' if " " in str(part) else str(part) for part in cmd)

        handle = open(log_path, "w", encoding="utf-8", buffering=1)
        handle.write(f"[DRY-RUN LOG] started={dt.datetime.now().isoformat()}\n")
        handle.write(f"[DRY-RUN LOG] cwd={_app_dir()}\n")
        handle.write(f"[DRY-RUN LOG] command={cmd_text}\n")
        handle.write("\n")
        self.runtime_log_handle = handle
        self.runtime_log_path = log_path

    def _write_runtime_log(self, line):
        if self.runtime_log_handle is None:
            return
        try:
            self.runtime_log_handle.write(f"{line}\n")
        except Exception:
            pass

    def _close_runtime_log(self):
        handle = self.runtime_log_handle
        if handle is None:
            self.runtime_log_path = None
            return

        try:
            handle.write(f"\n[DRY-RUN LOG] ended={dt.datetime.now().isoformat()}\n")
        except Exception:
            pass

        try:
            handle.close()
        except Exception:
            pass

        self.last_runtime_log_path = self.runtime_log_path
        self.runtime_log_handle = None
        self.runtime_log_path = None

    def _start_process(self):
        try:
            self._save_config()
            cmd = self._build_run_command()
            self._open_runtime_log(cmd)
            self.process_handle = subprocess.Popen(
                cmd,
                cwd=str(_app_dir()),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            threading.Thread(target=self._read_process_output, daemon=True).start()
            self._set_start_stop_button_visual(True)
            self.runtime_label.config(text="Running", foreground="green")
            if self.runtime_log_path is not None:
                self.status_label.config(
                    text=f"[OK] Script started | log: {self.runtime_log_path.name}",
                    foreground="green",
                )
            else:
                self.status_label.config(text="[OK] Script started", foreground="green")
            self._reset_runtime_telemetry()
            self._update_runtime_scene_label()
            if self.reastream_status_label is not None:
                self.reastream_status_label.config(text="ReaStream: starting...", foreground="#1d4ed8")
            if self.webapi_status_label is not None:
                self.webapi_status_label.config(text="Web API: waiting...", foreground="#1d4ed8")
            if self.master_meter_label is not None:
                self.master_meter_label.config(text="Master: LUFS -- | RMS -- dB", foreground="#95d5ff")
        except Exception as exc:
            self._close_runtime_log()
            self.process_handle = None
            self.status_label.config(text=f"[ERROR] Start failed: {exc}", foreground="red")
            messagebox.showerror("Start error", str(exc))

    def _stop_process(self, manual=False):
        if self.process_handle is None:
            return
        try:
            self.process_handle.terminate()
            self.process_handle.wait(timeout=2.0)
        except Exception:
            try:
                self.process_handle.kill()
            except Exception:
                pass
        finally:
            self.process_handle = None
            self._close_runtime_log()
            self._set_start_stop_button_visual(False)
            self.runtime_label.config(text="Stopped", foreground="red")
            if self.reastream_status_label is not None:
                self.reastream_status_label.config(text="ReaStream: stopped", foreground="#6b7280")
            if self.webapi_status_label is not None:
                self.webapi_status_label.config(text="Web API: stopped", foreground="#6b7280")
            self._reset_band_graph()
            self._reset_runtime_telemetry()
            if manual:
                if self.last_runtime_log_path is not None:
                    self.status_label.config(
                        text=f"[OK] Script stopped | log saved: {self.last_runtime_log_path.name}",
                        foreground="blue",
                    )
                else:
                    self.status_label.config(text="[OK] Script stopped", foreground="blue")

    def _read_process_output(self):
        if self.process_handle is None or self.process_handle.stdout is None:
            return
        try:
            for line in self.process_handle.stdout:
                self._write_runtime_log(line.rstrip("\n"))
                self.output_queue.put(line.rstrip("\n"))
        finally:
            self.output_queue.put("__PROCESS_ENDED__")

    def _schedule_ui_updates(self):
        self._drain_output_queue()
        self._refresh_bars()
        self.root.after(120, self._schedule_ui_updates)

    def _drain_output_queue(self):
        while True:
            try:
                line = self.output_queue.get_nowait()
            except queue.Empty:
                break

            if line == "__PROCESS_ENDED__":
                self._stop_process(manual=False)
                if self.last_runtime_log_path is not None:
                    self.status_label.config(
                        text=f"[INFO] Script finished | log saved: {self.last_runtime_log_path.name}",
                        foreground="blue",
                    )
                else:
                    self.status_label.config(text="[INFO] Script finished", foreground="blue")
                continue

            if "[process] Band values:" in line:
                payload = line.split("[process] Band values:", 1)[1].strip()
                try:
                    parsed = ast.literal_eval(payload)
                    if isinstance(parsed, dict):
                        for band in BAND_ORDER:
                            if band in parsed:
                                value = max(0.0, min(1.0, float(parsed[band])))
                                meter_db = METER_DB_FLOOR + (value * (METER_DB_CEIL - METER_DB_FLOOR))
                                self.band_targets[band] = meter_db
                except Exception:
                    pass

            if "[process] Band meter dB:" in line:
                payload = line.split("[process] Band meter dB:", 1)[1].strip()
                try:
                    parsed = ast.literal_eval(payload)
                    if isinstance(parsed, dict):
                        for band in BAND_ORDER:
                            if band in parsed:
                                meter_db = float(parsed[band])
                                meter_db = max(METER_DB_FLOOR, min(METER_DB_CEIL, meter_db))
                                self.band_targets[band] = meter_db
                except Exception:
                    pass

            if line.startswith("[ERROR") or "Traceback" in line:
                self.status_label.config(text="[ERROR] Runtime error (see terminal)", foreground="red")

            track_diag_match = TRACK_DIAG_RE.search(line)
            if track_diag_match:
                track_id = int(track_diag_match.group(1))
                role = track_diag_match.group(2)
                spec_db = float(track_diag_match.group(3))
                level_db = float(track_diag_match.group(4))
                fused_db = float(track_diag_match.group(5))
                lufs = self._safe_parse_optional_float(track_diag_match.group(6))
                rms = self._safe_parse_optional_float(track_diag_match.group(7))
                intent = "hold"
                if fused_db > 1e-6:
                    intent = "boost"
                elif fused_db < -1e-6:
                    intent = "cut"
                self.track_diag_state[track_id] = {
                    "role": role,
                    "spec_db": spec_db,
                    "level_db": level_db,
                    "fused_db": fused_db,
                    "lufs": lufs,
                    "rms": rms,
                    "intent": intent,
                }
                self._refresh_mixer_metadata(track_id)
                continue

            track_deadband_match = TRACK_DEADBAND_RE.search(line)
            if track_deadband_match:
                track_id = int(track_deadband_match.group(1))
                state = dict(self.track_diag_state.get(track_id, {}))
                state.update({"fused_db": 0.0, "intent": "hold"})
                self.track_diag_state[track_id] = state
                self._refresh_mixer_metadata(track_id)
                continue

            applied_match = TRACK_APPLIED_DB_RE.search(line)
            if applied_match:
                track_id = int(applied_match.group(1))
                applied_db = float(applied_match.group(2))
                self._set_track_fader_value(track_id, applied_db)
                self._update_mixer_knob_for_track(track_id, animate=True)

            match = TRACK_ACTION_RE.search(line)
            if match:
                continue

            if "[DIAG] Top band errors:" in line:
                payload = line.split("[DIAG] Top band errors:", 1)[1].strip()
                try:
                    parsed = ast.literal_eval(payload)
                    if isinstance(parsed, (list, tuple)):
                        top_errors = []
                        dominant = set()
                        for item in parsed:
                            if not isinstance(item, (list, tuple)) or len(item) < 2:
                                continue
                            band = str(item[0]).strip().lower()
                            if band not in BAND_ORDER:
                                continue
                            err = float(item[1])
                            top_errors.append((band, err))
                            dominant.add(band)
                        self.top_band_errors = top_errors
                        self.dominant_bands = dominant
                        self._update_mix_stability_label()
                except Exception:
                    pass

            if "[process] Actions to execute:" in line:
                payload = line.split("[process] Actions to execute:", 1)[1].strip()
                try:
                    parsed = ast.literal_eval(payload)
                    if isinstance(parsed, (list, tuple)):
                        self.pending_actions = list(parsed)
                        next_band_errors = {band: 0.0 for band in BAND_ORDER}
                        for item in parsed:
                            if not isinstance(item, (list, tuple)) or len(item) < 2:
                                continue
                            band = str(item[0]).strip().lower()
                            if band not in next_band_errors:
                                continue
                            next_band_errors[band] = float(item[1])
                        self.band_errors = next_band_errors
                        self._update_mix_stability_label()
                except Exception:
                    pass

            webapi_match = WEBAPI_TELEMETRY_RE.search(line)
            if webapi_match:
                track_id = int(webapi_match.group(1))
                live_db = float(webapi_match.group(2))
                self._set_track_fader_value(track_id, live_db)
                self._update_mixer_knob_for_track(track_id, animate=False)

            webapi_status_match = WEBAPI_STATUS_RE.search(line)
            if webapi_status_match and self.webapi_status_label is not None:
                state = webapi_status_match.group(1).upper()
                detail = webapi_status_match.group(2).strip()
                color = "#1d4ed8"
                if state == "OK":
                    color = "#15803d"
                elif state == "ERROR":
                    color = "#b91c1c"
                elif state == "DRY-RUN":
                    color = "#fbbf24"
                self.webapi_status_label.config(text=f"Web API: {state} ({detail})", foreground=color)

            reastream_status_match = REASTREAM_STATUS_RE.search(line)
            if reastream_status_match and self.reastream_status_label is not None:
                state = reastream_status_match.group(1).upper()
                detail = reastream_status_match.group(2).strip()
                color = "#1d4ed8"
                if state in {"STREAMING", "BOUND"}:
                    color = "#15803d"
                elif state in {"STALL", "ERROR"}:
                    color = "#b91c1c"
                self.reastream_status_label.config(text=f"ReaStream: {state} ({detail})", foreground=color)
                if state in {"WAITING", "STALL", "ERROR", "STOPPED"}:
                    self._reset_band_graph()
                    self._reset_runtime_telemetry()

            master_meter_match = MASTER_METER_RE.search(line)
            if master_meter_match and self.master_meter_label is not None:
                lufs_raw = master_meter_match.group(1)
                rms_db = float(master_meter_match.group(2))
                if lufs_raw.lower() == "nan":
                    text = f"Master: LUFS -- | RMS {rms_db:+.1f} dB"
                else:
                    text = f"Master: LUFS {float(lufs_raw):+.1f} | RMS {rms_db:+.1f} dB"
                self.master_meter_label.config(text=text, foreground="#95d5ff")

    def _refresh_bars(self):
        if self.band_graph_canvas is None:
            return

        for band in BAND_ORDER:
            if band not in self.band_ui:
                continue
            current = float(self.band_values[band])
            target = float(self.band_targets[band])
            alpha = BAR_ATTACK if target >= current else BAR_RELEASE
            current += (target - current) * alpha
            self.band_values[band] = current
            normalized = ((current - METER_DB_FLOOR) / (METER_DB_CEIL - METER_DB_FLOOR)) * 100.0
            normalized = min(100.0, max(0.0, normalized))
            slot = self.band_ui[band]
            bar_height = (normalized / 100.0) * (slot["bottom"] - slot["top"])
            y_top = slot["bottom"] - bar_height
            delta_value = float(self.band_errors.get(band, 0.0))
            delta_color = self._band_delta_style(delta_value)
            self.band_graph_canvas.coords(slot["bar"], slot["x0"], y_top, slot["x1"], slot["bottom"])
            self.band_graph_canvas.itemconfig(slot["value"], text=f"{current:+.1f} dB")
            self.band_graph_canvas.itemconfig(slot["delta"], text=f"{delta_value:+.2f}", fill=delta_color)

            learned_value = None
            if isinstance(self.learn_preview_db, dict):
                try:
                    learned_value = float(self.learn_preview_db.get(band, METER_DB_FLOOR))
                except Exception:
                    learned_value = None

            if learned_value is not None:
                learned_norm = ((learned_value - METER_DB_FLOOR) / (METER_DB_CEIL - METER_DB_FLOOR)) * 100.0
                learned_norm = min(100.0, max(0.0, learned_norm))
                learned_h = (learned_norm / 100.0) * (slot["bottom"] - slot["top"])
                learned_y_top = slot["bottom"] - learned_h
                lx0 = slot["x0"] + ((slot["x1"] - slot["x0"]) * 0.22)
                lx1 = slot["x1"] - ((slot["x1"] - slot["x0"]) * 0.22)
                self.band_graph_canvas.coords(slot["learn_bar"], lx0, learned_y_top, lx1, slot["bottom"])
                self.band_graph_canvas.itemconfig(slot["learn_bar"], state="normal")
            else:
                self.band_graph_canvas.itemconfig(slot["learn_bar"], state="hidden")

            if band in self.dominant_bands:
                self.band_graph_canvas.itemconfig(slot["slot"], fill="#0b3154", outline=delta_color)
                self.band_graph_canvas.itemconfig(slot["label"], fill=delta_color)
                self.band_graph_canvas.itemconfig(slot["bar"], fill="#21d4fd", outline="#8be9ff")
            else:
                self.band_graph_canvas.itemconfig(slot["slot"], fill="#06243b", outline="#1177b8")
                self.band_graph_canvas.itemconfig(slot["label"], fill=delta_color)
                self.band_graph_canvas.itemconfig(slot["bar"], fill="#16d4ff", outline="#5ce5ff")

    def _on_close(self):
        try:
            self.root.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self._stop_process(manual=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    ConfigGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
