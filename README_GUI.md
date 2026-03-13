## Mix Robo - GUI Configuration System

Your Mix Robo system now has a **graphical interface** for easy configuration!

### Quick Start

Double-click **`run_gui.bat`** to open the configuration interface.

Or run from PowerShell:
```powershell
.\.venv\Scripts\python config_gui.py
```

### What You Can Do

#### In the GUI:

1. **Master Track** - Change the master track ID (default: 153)
2. **Tracks** - For each instrument:
   - ? **Checkbox** - Enable (checked) / Disable (unchecked)
   - ID - OSC track address
   - Name - Label (for your reference)
   - Min dB - Minimum dB limit
   - Max dB - Maximum dB limit
3. **Analysis Settings** - Fine-tune control behavior
4. **Buttons:**
   - **[SAVE]** - Save changes to config.json (MUST CLICK!)
   - **[RELOAD]** - Refresh from file
   - **[RESET]** - Restore defaults

### Example: Disable a Track

1. Open GUI
2. Find track (e.g., Vocal)
3. Uncheck the checkbox
4. Click **[SAVE]**
5. Script will skip that track (no more OSC commands sent to it)
6. Changes apply in next analysis cycle (~5 seconds)

### Files

- **config.json** - All your settings (editable manually if needed)
- **config_gui.py** - The GUI application
- **config_manager.py** - Behind-the-scenes config loading
- **run_gui.bat** - Quick launcher for Windows

### During Processing

The script **auto-loads** config.json every cycle, so:

- Start script: `python run_profile.py --profile worship --reastream ...`
- In another terminal: Open GUI and make changes
- After saving: Script picks up changes within 5 seconds
- No restart needed!

### For Deployment

When moving to another machine:
1. Copy all files (including config.json)
2. User runs `python config_gui.py` to configure
3. User runs `python run_profile.py ...` to process
4. Done - no compilation needed!

### Help

- **CONFIG_GUIDE.md** - Detailed guide with examples
- **GUI_TUTORIAL.md** - Full GUI documentation
- **IMPLEMENTATION_SUMMARY.md** - Technical details

---

**All features working! Enjoy the GUI! ??**
