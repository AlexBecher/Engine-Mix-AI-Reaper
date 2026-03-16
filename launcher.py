#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mix Robo Launcher - Simple menu to choose between Config GUI and Processing
"""
import sys
import os
import subprocess


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
        print("\n[*] Starting ReaStream processing...")
        print("    Use: python run_profile.py --profile worship --reastream --reastream-identifier master --channels 2 --verbose\n")
        subprocess.run([
            sys.executable,
            "run_profile.py",
            "--profile",
            "worship",
            "--reastream",
            "--reastream-identifier",
            "master",
            "--channels",
            "2",
            "--verbose",
        ], check=False)
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
