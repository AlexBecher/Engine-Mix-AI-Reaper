# -*- coding: utf-8 -*-
"""Single-screen dashboard: config + start/stop + live bargraph."""

import ast
import json
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
OSC_TRACK_RE = re.compile(r"/track/(\d+)/volume")


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
        self.tracks_box = None
        self._track_row_counter = 0
        self.analysis_vars = {}
        self.run_vars = {}
        self.band_values = {band: 0.0 for band in BAND_ORDER}
        self.band_peak = {band: 1e-6 for band in BAND_ORDER}
        self.band_ui = {}

        self.process_handle = None
        self.output_queue = queue.Queue()
        self.profile_combo = None

        self._ensure_defaults()
        self._build_ui()
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
        run.setdefault("osc_host", "127.0.0.1")
        run.setdefault("osc_port", 8000)
        run.setdefault("channels", 2)
        run.setdefault("analysis_interval", 3.0)
        run.setdefault("verbose", True)

    def _build_ui(self):
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=2)
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
        headers = ["LED", "Active", "Track ID", "Name", "Min dB", "Max dB"]
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
            )

        self.track_controls = ttk.Frame(tracks_box)
        self.track_controls.grid(row=len(self.track_rows) + 1, column=0, columnspan=6, sticky="w", pady=(8, 0))
        ttk.Button(self.track_controls, text="+ Add Track", command=self._on_add_track).grid(row=0, column=0, sticky="w")
        ttk.Button(self.track_controls, text="Detail", command=self._show_track_map_details).grid(row=0, column=1, sticky="w", padx=(8, 0))

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
        self.run_vars["osc_host"] = tk.StringVar(value=str(run.get("osc_host", "127.0.0.1")))
        self.run_vars["osc_port"] = tk.IntVar(value=int(run.get("osc_port", 8000)))
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

        ttk.Label(run_box, text="OSC IP").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["osc_host"], width=14).grid(row=2, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(run_box, text="OSC Port").grid(row=2, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["osc_port"], width=14).grid(row=2, column=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Label(run_box, text="ReaStream IP").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["reastream_host"], width=14).grid(row=3, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(run_box, text="ReaStream Port").grid(row=3, column=2, sticky="w", pady=(6, 0))
        ttk.Entry(run_box, textvariable=self.run_vars["reastream_port"], width=14).grid(row=3, column=3, sticky="w", padx=8, pady=(6, 0))

        ttk.Checkbutton(run_box, text="Use ReaStream", variable=self.run_vars["reastream"]).grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(run_box, text="Verbose (required for telemetry)", variable=self.run_vars["verbose"]).grid(row=4, column=2, columnspan=2, sticky="w", pady=(8, 0))

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

    def _add_track_row(self, track_id, name="", enabled=True, min_db=-6.0, max_db=0.0):
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

        led_canvas = tk.Canvas(self.tracks_box, width=14, height=14, highlightthickness=0, bd=0)
        led_item = led_canvas.create_oval(2, 2, 12, 12, fill="#595959", outline="#3c3c3c")

        led_canvas.grid(row=row, column=0, padx=4, sticky="w")
        ttk.Checkbutton(self.tracks_box, variable=enabled_var).grid(row=row, column=1, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=id_var, width=8).grid(row=row, column=2, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=name_var, width=14).grid(row=row, column=3, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=min_var, width=8).grid(row=row, column=4, padx=4, sticky="w")
        ttk.Entry(self.tracks_box, textvariable=max_var, width=8).grid(row=row, column=5, padx=4, sticky="w")

        row_data = {
            "enabled": enabled_var,
            "track_id": id_var,
            "name": name_var,
            "min_db": min_var,
            "max_db": max_var,
            "led_canvas": led_canvas,
            "led_item": led_item,
            "led_reset_job": None,
        }
        self.track_rows.append(row_data)
        self.track_vars[row_key] = row_data

        if self.track_controls is not None:
            self.track_controls.grid_configure(row=len(self.track_rows) + 1)

    def _next_track_id(self):
        existing = []
        for row_data in self.track_rows:
            try:
                existing.append(int(row_data["track_id"].get()))
            except Exception:
                continue
        return max(existing, default=0) + 1

    def _on_add_track(self):
        self._add_track_row(track_id=self._next_track_id(), name="new_track", enabled=True, min_db=-6.0, max_db=0.0)

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
            "osc_host": str(self.run_vars["osc_host"].get()).strip(),
            "osc_port": int(self.run_vars["osc_port"].get()),
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

            match = TRACK_ACTION_RE.search(line)
            if match:
                self._flash_track_led(int(match.group(1)))
                continue

            osc_match = OSC_TRACK_RE.search(line)
            if osc_match:
                self._flash_track_led(int(osc_match.group(1)))

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
