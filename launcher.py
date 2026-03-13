#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mix Robo Launcher - Simple menu to choose between Config GUI and Processing
"""
import sys
import os

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
        from config_gui import main as gui_main
        gui_main()
    elif choice == "2":
        print("\n[*] Starting ReaStream processing...")
        print("    Use: python run_profile.py --profile worship --reastream --reastream-identifier master --channels 2 --verbose\n")
        os.system("python run_profile.py --profile worship --reastream --reastream-identifier master --channels 2 --verbose")
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
