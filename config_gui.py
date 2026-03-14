# -*- coding: utf-8 -*-
"""Single-screen dashboard: config + start/stop + live bargraph."""

import ast
import json
import math
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from config_manager import CONFIG_FILE, load_config, save_config
BAND_ORDER = ["sub", "low", "mid", "highmid", "air"]
TRACK_ACTION_RE = re.compile(r"\[process\]\s+Track\s+(\d+):")
TRACK_APPLIED_DB_RE = re.compile(r"\[process\]\s+Track\s+(\d+):.*?applied=([+-]?\d+(?:\.\d+)?)dB")
WEBAPI_TELEMETRY_RE = re.compile(r"\[WEBAPI (?:SET|READ)\]\s+track=(\d+)\s+db=([+-]?\d+(?:\.\d+)?)")
MAX_TRACK_ROWS = 9

# Full Reaper fader hardware range: 0% = -133 dB, 100% = +12 dB
REAPER_FADER_MIN_DB = -133.0
REAPER_FADER_MAX_DB = 12.0
_REAPER_FADER_MAX_AMP_ROOT = 10.0 ** (12.0 / 80.0)


def _app_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Alex Studio MIX  - AI")
        self.root.geometry("1240x760")

        self.config = load_config(CONFIG_FILE)
        self.track_vars = {}
        self.track_rows = []
        self.track_controls = None
        self.add_track_btn = None
        self.tracks_box = None
        self._track_row_counter = 0
        self.analysis_vars = {}
        self.run_vars = {}
        self.band_values = {band: 0.0 for band in BAND_ORDER}
        self.band_peak = {band: 1e-6 for band in BAND_ORDER}
        self.band_ui = {}
        self.mixer_canvas = None
        self.mixer_slot_items = {}
        self.mixer_anim_jobs = {}
        self.mixer_anim_duration_ms = 140
        self.fader_strip_img = None
        self.fader_button_img = None

        self.process_handle = None
        self.output_queue = queue.Queue()
        self.profile_combo = None

        self._ensure_defaults()
        self._build_ui()
        self._rebuild_mixer_view()
        self._schedule_ui_updates()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _ensure_defaults(self):
        self.config.setdefault("master_track", 153)
        self.config.setdefault("tracks", {})
        self.config.setdefault("analysis_settings", {})
        self.config.setdefault("run_settings", {})

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
        run.setdefault("analysis_interval", 3.0)
        run.setdefault("verbose", True)
        run.pop("osc_host", None)
        run.pop("osc_port", None)

    def _build_ui(self):
        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        left = ttk.Frame(self.root, padding=10)
        right = ttk.Frame(self.root, padding=10)
        left.grid(row=0, column=0, sticky="nsew")
        right.grid(row=0, column=1, sticky="nsew")

        self._build_config_panel(left)
        self._build_runtime_panel(right)

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
        headers = ["LED", "Active", "Track ID", "Name", "Min dB", "Max dB", "Faders"]
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
            )

        self.track_controls = ttk.Frame(tracks_box)
        self.track_controls.grid(row=len(self.track_rows) + 1, column=0, columnspan=7, sticky="w", pady=(8, 0))
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
        }
        labels = [
            ("Error gain up", "error_gain_up", tk.DoubleVar),
            ("Error gain down", "error_gain_down", tk.DoubleVar),
            ("Max step up (dB)", "max_step_up_db", tk.DoubleVar),
            ("Max step down (dB)", "max_step_down_db", tk.DoubleVar),
            ("Error deadband", "error_deadband", tk.DoubleVar),
            ("Max tracks raise/cycle", "max_tracks_raise_per_cycle", tk.IntVar),
            ("LUFS warning threshold", "lufs_warning_threshold", tk.DoubleVar),
        ]

        for row_idx, (label, key, var_type) in enumerate(labels):
            current = self.config["analysis_settings"].get(key, analysis_defaults[key])
            var = var_type(value=current)
            self.analysis_vars[key] = var
            ttk.Label(analysis_box, text=label).grid(row=row_idx, column=0, sticky="w", pady=2)
            ttk.Entry(analysis_box, textvariable=var, width=12).grid(row=row_idx, column=1, sticky="w", padx=8)

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
        self.run_vars["analysis_interval"] = tk.DoubleVar(value=float(run.get("analysis_interval", 3.0)))
        self.run_vars["verbose"] = tk.BooleanVar(value=bool(run.get("verbose", True)))
        self.run_vars["reastream"] = tk.BooleanVar(value=bool(run.get("reastream", True)))

        ttk.Label(run_box, text="Profile").grid(row=0, column=0, sticky="w")
        self.profile_combo = ttk.Combobox(
            run_box,
            textvariable=self.run_vars["profile"],
            values=self._load_profile_names(),
            width=18,
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

        ttk.Checkbutton(run_box, text="Use ReaStream", variable=self.run_vars["reastream"]).grid(row=5, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(run_box, text="Verbose (required for telemetry)", variable=self.run_vars["verbose"]).grid(row=5, column=2, columnspan=2, sticky="w", pady=(8, 0))

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

    def _add_track_row(self, track_id, name="", enabled=True, min_db=-6.0, max_db=0.0, fader_db=0.0):
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

        led_canvas = tk.Canvas(self.tracks_box, width=14, height=14, highlightthickness=0, bd=0)
        led_item = led_canvas.create_oval(2, 2, 12, 12, fill="#595959", outline="#3c3c3c")

        led_canvas.grid(row=row, column=0, padx=4, sticky="w")
        ttk.Checkbutton(self.tracks_box, variable=enabled_var).grid(row=row, column=1, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=id_var, width=8).grid(row=row, column=2, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=name_var, width=14).grid(row=row, column=3, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=min_var, width=8).grid(row=row, column=4, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=max_var, width=8).grid(row=row, column=5, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=fader_var, width=8).grid(row=row, column=6, padx=4, sticky="w")

        row_data = {
            "enabled": enabled_var,
            "track_id": id_var,
            "name": name_var,
            "min_db": min_var,
            "max_db": max_var,
            "fader_db": fader_var,
            "led_canvas": led_canvas,
            "led_item": led_item,
            "led_reset_job": None,
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
        )

    def _on_track_row_changed(self, _row_key):
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

    def _flash_track_led(self, track_id):
        for row_data in self.track_rows:
            try:
                current_id = int(row_data["track_id"].get())
            except Exception:
                continue
            if current_id != track_id:
                continue

            row_data["led_canvas"].itemconfig(row_data["led_item"], fill="#30d158", outline="#2aa64a")
            pending_job = row_data.get("led_reset_job")
            if pending_job is not None:
                try:
                    self.root.after_cancel(pending_job)
                except Exception:
                    pass
            row_data["led_reset_job"] = self.root.after(
                220,
                lambda item=row_data["led_item"], canvas=row_data["led_canvas"], data=row_data: (
                    canvas.itemconfig(item, fill="#595959", outline="#3c3c3c"),
                    data.__setitem__("led_reset_job", None),
                ),
            )

    def _build_runtime_panel(self, parent):
        parent.columnconfigure(0, weight=1)

        control_box = ttk.LabelFrame(parent, text="Runtime", padding=10)
        control_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self.start_stop_btn = ttk.Button(control_box, text="START", command=self._toggle_start_stop)
        self.start_stop_btn.grid(row=0, column=0, padx=(0, 8), sticky="w")
        ttk.Button(control_box, text="SAVE CONFIG", command=self._save_config).grid(row=0, column=1, sticky="w")

        self.runtime_label = ttk.Label(control_box, text="Stopped", foreground="red")
        self.runtime_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        self.status_label = ttk.Label(control_box, text="Ready", foreground="blue")
        self.status_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        graph_box = ttk.LabelFrame(parent, text="Audio bands (live bargraph)", padding=10)
        graph_box.grid(row=1, column=0, sticky="nsew")
        parent.rowconfigure(1, weight=1)

        for row, band in enumerate(BAND_ORDER):
            ttk.Label(graph_box, text=band.upper(), width=10).grid(row=row, column=0, sticky="w", pady=5)
            pb = ttk.Progressbar(graph_box, orient="horizontal", mode="determinate", maximum=100, length=260)
            pb.grid(row=row, column=1, sticky="ew", padx=(6, 10), pady=5)
            value_lbl = ttk.Label(graph_box, text="0.000000", width=10)
            value_lbl.grid(row=row, column=2, sticky="e", pady=5)
            self.band_ui[band] = {"bar": pb, "label": value_lbl}

        graph_box.columnconfigure(1, weight=1)

        mixer_box = ttk.LabelFrame(parent, text="Track faders", padding=10)
        mixer_box.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        parent.rowconfigure(2, weight=1)

        self.mixer_canvas = tk.Canvas(mixer_box, height=300, highlightthickness=0, bd=0, bg="#242628")
        self.mixer_canvas.grid(row=0, column=0, sticky="nsew")
        self.mixer_canvas.bind("<Configure>", self._on_mixer_canvas_resize)
        mixer_box.columnconfigure(0, weight=1)
        mixer_box.rowconfigure(0, weight=1)
        self._load_mixer_images()

    def _on_mixer_canvas_resize(self, _event):
        self._rebuild_mixer_view()

    def _load_mixer_images(self):
        base = _app_dir()
        strip_path = base / "fader.png"
        button_path = base / "fader_buttom.png"
        if not button_path.exists():
            alt = base / "fader_button.png"
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
        min_slot_w = 80
        available_w = max(1, int(canvas.winfo_width()))
        slot_w = max(min_slot_w, (available_w - left_pad - right_pad) / float(len(visible_rows)))
        strip_pitch = (self.fader_strip_img.width() + 20) if self.fader_strip_img is not None else 58
        top_label_y = 18
        top_id_y = 36
        track_top_y = 50
        track_bottom_y = 248

        content_width = left_pad + right_pad + max(slot_w * len(visible_rows), strip_pitch * len(visible_rows))
        canvas.config(scrollregion=(0, 0, content_width, 300))

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
                    outline="#a7a7a7",
                    fill="#3a3d40",
                )

            # Draw labels after center_x is finalized from strip geometry.
            canvas.create_text(center_x, top_label_y, text=name, fill="#f0f0f0", font=("Segoe UI", 9, "bold"))
            canvas.create_text(center_x, top_id_y, text=str(track_id), fill="#d0d0d0", font=("Segoe UI", 9))

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
                min(292, strip_bottom + 30),
                text=f"{float(row_data['fader_db'].get()):+.2f} dB",
                fill="#8fb9ff",
                font=("Consolas", 8),
            )

            self.mixer_slot_items[row_key] = {
                "track_id": track_id,
                "strip_item": strip_item,
                "knob_item": knob_item,
                "value_item": value_item,
                "top_y": top_y,
                "bottom_y": bottom_y,
                "center_x": center_x,
                "strip_left": strip_left,
                "uses_image_knob": self.fader_button_img is not None,
            }

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

        tracks = {}
        for _, vars_dict in self.track_vars.items():
            track_id = int(vars_dict["track_id"].get())
            tracks[str(track_id)] = {
                "name": str(vars_dict["name"].get()),
                "enabled": bool(vars_dict["enabled"].get()),
                "min_db": float(vars_dict["min_db"].get()),
                "max_db": float(vars_dict["max_db"].get()),
                "fader_db": float(vars_dict["fader_db"].get()),
            }
        cfg["tracks"] = tracks

        cfg["analysis_settings"] = {
            "error_gain_up": float(self.analysis_vars["error_gain_up"].get()),
            "error_gain_down": float(self.analysis_vars["error_gain_down"].get()),
            "max_step_up_db": float(self.analysis_vars["max_step_up_db"].get()),
            "max_step_down_db": float(self.analysis_vars["max_step_down_db"].get()),
            "error_deadband": float(self.analysis_vars["error_deadband"].get()),
            "max_tracks_raise_per_cycle": int(self.analysis_vars["max_tracks_raise_per_cycle"].get()),
            "lufs_warning_threshold": float(self.analysis_vars["lufs_warning_threshold"].get()),
        }

        cfg["run_settings"] = {
            "profile": str(self.run_vars["profile"].get()).strip(),
            "reastream": bool(self.run_vars["reastream"].get()),
            "reastream_identifier": str(self.run_vars["reastream_identifier"].get()).strip(),
            "reastream_host": str(self.run_vars["reastream_host"].get()).strip(),
            "reastream_port": int(self.run_vars["reastream_port"].get()),
            "webapi_host": str(self.run_vars["webapi_host"].get()).strip(),
            "webapi_port": int(self.run_vars["webapi_port"].get()),
            "webapi_base": str(self.run_vars["webapi_base"].get()).strip() or "/_",
            "webapi_timeout": float(self.run_vars["webapi_timeout"].get()),
            "channels": int(self.run_vars["channels"].get()),
            "analysis_interval": float(self.run_vars["analysis_interval"].get()),
            "verbose": bool(self.run_vars["verbose"].get()),
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
                "run_profile.py",
                "--profile",
                run["profile"],
                "--channels",
                str(run["channels"]),
                "--analysis-interval",
                str(run["analysis_interval"]),
            ]

        if run["reastream"]:
            cmd.extend(
                [
                    "--reastream",
                    "--reastream-identifier",
                    run["reastream_identifier"],
                    "--reastream-host",
                    run["reastream_host"],
                    "--reastream-port",
                    str(run["reastream_port"]),
                ]
            )
        if run["verbose"]:
            cmd.append("--verbose")
        return cmd

    def _toggle_start_stop(self):
        if self.process_handle is None:
            self._start_process()
        else:
            self._stop_process(manual=True)

    def _start_process(self):
        try:
            self._save_config()
            cmd = self._build_run_command()
            self.process_handle = subprocess.Popen(
                cmd,
                cwd=str(_app_dir()),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            threading.Thread(target=self._read_process_output, daemon=True).start()
            self.start_stop_btn.config(text="STOP")
            self.runtime_label.config(text="Running", foreground="green")
            self.status_label.config(text="[OK] Script started", foreground="green")
        except Exception as exc:
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
            self.start_stop_btn.config(text="START")
            self.runtime_label.config(text="Stopped", foreground="red")
            if manual:
                self.status_label.config(text="[OK] Script stopped", foreground="blue")

    def _read_process_output(self):
        if self.process_handle is None or self.process_handle.stdout is None:
            return
        try:
            for line in self.process_handle.stdout:
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
                self.status_label.config(text="[INFO] Script finished", foreground="blue")
                continue

            if "[process] Band values:" in line:
                payload = line.split("[process] Band values:", 1)[1].strip()
                try:
                    parsed = ast.literal_eval(payload)
                    if isinstance(parsed, dict):
                        for band in BAND_ORDER:
                            if band in parsed:
                                value = float(parsed[band])
                                self.band_values[band] = value
                                self.band_peak[band] = max(self.band_peak[band] * 0.995, abs(value), 1e-6)
                except Exception:
                    pass

            if line.startswith("[ERROR") or "Traceback" in line:
                self.status_label.config(text="[ERROR] Runtime error (see terminal)", foreground="red")

            applied_match = TRACK_APPLIED_DB_RE.search(line)
            if applied_match:
                track_id = int(applied_match.group(1))
                applied_db = float(applied_match.group(2))
                self._set_track_fader_value(track_id, applied_db)
                self._update_mixer_knob_for_track(track_id, animate=True)

            match = TRACK_ACTION_RE.search(line)
            if match:
                self._flash_track_led(int(match.group(1)))
                continue

            webapi_match = WEBAPI_TELEMETRY_RE.search(line)
            if webapi_match:
                track_id = int(webapi_match.group(1))
                live_db = float(webapi_match.group(2))
                self._set_track_fader_value(track_id, live_db)
                self._update_mixer_knob_for_track(track_id, animate=False)

    def _refresh_bars(self):
        for band in BAND_ORDER:
            raw = float(self.band_values[band])
            peak = max(self.band_peak[band], 1e-6)
            normalized = min(100.0, max(0.0, (abs(raw) / peak) * 100.0))
            self.band_ui[band]["bar"]["value"] = normalized
            self.band_ui[band]["label"].config(text=f"{raw:.6f}")

    def _on_close(self):
        self._stop_process(manual=False)
        self.root.destroy()


def main():
    root = tk.Tk()
    ConfigGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
