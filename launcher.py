#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mix Robo Launcher - Simple menu to choose between Config GUI and Processing
"""
import sys
import os
import subprocess
from pathlib import Path

from config_manager import CONFIG_FILE, load_config


def _configure_tcl_tk_paths():
    """Configura TCL_LIBRARY/TK_LIBRARY para evitar erro de init.tcl no Windows."""
    if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
        return

    candidates = [getattr(sys, "base_prefix", ""), sys.prefix]
    for base in candidates:
        if not base:
            continue
        tcl_dir = os.path.join(base, "tcl", "tcl8.6")
        tk_dir = os.path.join(base, "tcl", "tk8.6")
        init_tcl = os.path.join(tcl_dir, "init.tcl")
        tk_tcl = os.path.join(tk_dir, "tk.tcl")
        if os.path.exists(init_tcl) and "TCL_LIBRARY" not in os.environ:
            os.environ["TCL_LIBRARY"] = tcl_dir
        if os.path.exists(tk_tcl) and "TK_LIBRARY" not in os.environ:
            os.environ["TK_LIBRARY"] = tk_dir

        if os.environ.get("TCL_LIBRARY") and os.environ.get("TK_LIBRARY"):
            return


def _load_run_settings():
    cfg = load_config(CONFIG_FILE)
    run = cfg.get("run_settings", {}) if isinstance(cfg, dict) else {}
    return {
        "profile": str(run.get("profile", "worship")).strip() or "worship",
        "reastream": bool(run.get("reastream", True)),
        "reastream_identifier": str(run.get("reastream_identifier", "master")).strip() or "master",
        "reastream_host": str(run.get("reastream_host", "0.0.0.0")).strip() or "0.0.0.0",
        "reastream_port": int(run.get("reastream_port", 58710)),
        "webapi_host": str(run.get("webapi_host", "127.0.0.1")).strip() or "127.0.0.1",
        "webapi_port": int(run.get("webapi_port", 8080)),
        "webapi_base": str(run.get("webapi_base", "/_")).strip() or "/_",
        "webapi_timeout": float(run.get("webapi_timeout", 2.5)),
        "channels": int(run.get("channels", 2)),
        "analysis_interval": float(run.get("analysis_interval", 5.0)),
        "calibrate_freq": float(run.get("calibrate_freq", 0.0) or 0.0),
        "verbose": bool(run.get("verbose", False)),
    }


def _build_processing_command():
    run = _load_run_settings()

    if getattr(sys, "frozen", False):
        worker = Path(sys.executable).resolve().parent / "run_profile_worker.exe"
        if not worker.exists():
            raise FileNotFoundError(f"Worker executable not found: {worker}")
        cmd = [str(worker)]
    else:
        cmd = [sys.executable, "run_profile.py"]

    cmd.extend(
        [
            "--profile",
            run["profile"],
            "--channels",
            str(run["channels"]),
            "--analysis-interval",
            str(run["analysis_interval"]),
            "--reastream-identifier",
            run["reastream_identifier"],
            "--reastream-host",
            run["reastream_host"],
            "--reastream-port",
            str(run["reastream_port"]),
            "--webapi-host",
            run["webapi_host"],
            "--webapi-port",
            str(run["webapi_port"]),
            "--webapi-base",
            run["webapi_base"],
            "--webapi-timeout",
            str(run["webapi_timeout"]),
        ]
    )

    if run["reastream"]:
        cmd.append("--reastream")
    if run["calibrate_freq"] > 0.0:
        cmd.extend(["--calibrate-freq", str(run["calibrate_freq"])])
    if run["verbose"]:
        cmd.append("--verbose")
    return cmd

def main():
    print("\n??????????????????????????????????????????")
    print("?        Mix Robo - Main Menu            ?")
    print("??????????????????????????????????????????\n")
    
    print("Choose an option:")
    print("  1) Configuration GUI (edit tracks, limits, etc)")
    print("  2) Start Processing (ReaStream analysis)")
    print("  3) Exit\n")
    
    choice = input("Enter choice (1-3): ").strip()
    
    if choice == "1":
        print("\n[*] Opening Configuration GUI...\n")
        _configure_tcl_tk_paths()
        from config_gui import main as gui_main
        gui_main()
    elif choice == "2":
        cmd = _build_processing_command()
        print("\n[*] Starting ReaStream processing...")
        print(f"    Command: {' '.join(cmd)}\n")
        subprocess.run(cmd, check=False)
    elif choice == "3":
        print("Goodbye!")
        sys.exit(0)
    else:
        print("Invalid choice. Please try again.")
        main()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"\n[!] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
