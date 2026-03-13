# GUI Configuration System - Tutorial

## What's New

You now have a **graphical interface** to configure Mix Robo without editing code!

## ?? New Files

1. **config.json** - Configuration file (all settings stored here)
2. **config_manager.py** - Python module to load/save config
3. **config_gui.py** - GUI application (tkinter-based)
4. **run_gui.bat** - Windows batch script to launch GUI easily
5. **launcher.py** - Menu-driven launcher (alternative)

## ?? Quick Start

### Open the GUI (Easiest)

**On Windows:** Double-click `run_gui.bat`

**Or from PowerShell:**
```powershell
.\.venv\Scripts\python config_gui.py
```

## ?? GUI Features

### Master Track
- Single field to change the master track ID (default: 153)
- This track is never actuated by the script

### Tracks Section
Each row represents one instrument track:
- **Active checkbox** - Check = track enabled, Uncheck = muted (script skips it)
- **Track ID** - OSC address in Reaper (154, 155, etc)
- **Name** - Human-readable label (shown only in config, not visible to script - purely for your reference)
- **Min dB** - Minimum dB limit (negative: cut, positive: boost)
- **Max dB** - Maximum dB limit

**Example:**
```
Active  Track ID  Name      Min dB   Max dB
  [?]    155      Bass      -8.0     -6.0    <- Bass track, can cut up to -8dB or boost to -6dB
  [ ]    160      Vocals    -20.0    -6.0    <- Vocal track disabled - script will skip it
```

### Analysis Settings
Fine-tune controller behavior:
- **Error Gain Up** - Multiplier for boosts (1.2 = conservative boost)
- **Error Gain Down** - Multiplier for cuts (2.2 = aggressive cutting)
- **Max Step Up/Down** - Max dB change per cycle (0.10 dB / 0.35 dB)
- **Error Deadband** - Errors below this are ignored (0.18)
- **Max Tracks Raise/Cycle** - Max tracks boosted per cycle (1)
- **LUFS Warning Threshold** - Loudness alert level (-14 LUFS)

## ?? Saving Changes

**IMPORTANT:** After editing, click **[SAVE] Config** button

Status shows:
- `[OK] Config saved successfully!` ? Changes persisted
- `[ERROR] Error saving: ...` ? Something failed

Without saving, changes are **lost when you close the GUI**.

## ?? Hot-Reload During Processing

The script **automatically reloads config.json every analysis cycle**.

This allows:
1. Start processing script
2. Open GUI in another terminal
3. Make changes and click Save
4. Script picks up changes within ~5 seconds (next analysis window)

**Example: Disable a track mid-stream**
```
# Terminal 1: Script running
$ python run_profile.py --profile worship --reastream --reastream-identifier master --channels 2
[OSC SEND] /track/154/volume 0.700
[OSC SEND] /track/155/volume 0.651

# Terminal 2: Run GUI, uncheck Vocal (160), Save
$ python config_gui.py
(uncheck, save)

# Back to Terminal 1: Skips track 160 now
[OSC SEND] /track/154/volume 0.701
[process] Track 160 disabled - skipping
```

## ?? Configuration File Structure

Format: JSON (human-editable, but use the GUI)

```json
{
  "master_track": 153,
  "tracks": {
    "154": {
      "name": "Drums",
      "enabled": true,
      "min_db": -3.0,
      "max_db": 3.0
    },
    ...
  },
  "analysis_settings": {
    "error_gain_up": 1.2,
    "error_gain_down": 2.2,
    ...
  }
}
```

## ??? For Deployment

When distributing to another machine:

1. **Copy these files:**
   - config.json (configuration)
   - config_manager.py (loader)
   - config_gui.py (GUI)
   - run_gui.bat (launcher)
   - All existing files (mix_profile.py, run_profile.py, etc)

2. **No compilation needed** - Pure Python, uses built-in tkinter

3. **User can:**
   - Double-click `run_gui.bat` to configure
   - Or run: `python run_profile.py --profile worship --reastream ...`
   - Changes take effect immediately in next cycle

## ?? Troubleshooting

### GUI won't open
- Check Python path: `.\.venv\Scripts\python config_gui.py`
- Verify tkinter is installed: `.\.venv\Scripts\python -m tkinter` (should show test window)

### Changes not saved
- **Must click [SAVE] Config** - status should show `[OK]`
- Check file permissions on config.json

### Script not picking up changes
- Wait for next analysis cycle (5 seconds default)
- Check logs: `$env:MIX_ROBO_DEBUG="1"` to see config reload

### Corrupt config.json
- Click **[RESET] to Defaults** in GUI
- Or manually delete config.json and restart (recreates defaults)

## ?? Example Workflows

### Disable problematic track
1. Open GUI: `run_gui.bat`
2. Uncheck the track's "Active" checkbox
3. Click [SAVE] Config
4. Script skips that track in next cycle

### Adjust dB limits for more aggressiveness
1. Open GUI
2. Increase Max dB (e.g., -6.0 ? -3.0)
3. Decrease Min dB (e.g., -8.0 ? -10.0)
4. Click [SAVE] Config
5. Script allows larger adjustments next cycle

### Fine-tune slew rates
1. Open GUI
2. Increase **Error Gain Up** for slower boosts (1.2 ? 1.5)
3. Decrease **Max Step Down** for slower cuts (0.35 ? 0.20)
4. Click [SAVE] Config
5. Next cycle runs smoother

## ? FAQ

**Q: Can I edit config.json directly?**
A: Yes, it's valid JSON. But GUI is easier and safer.

**Q: What if I mess up the config?**
A: Click **[RESET] to Defaults** in the GUI to restore original settings.

**Q: Do I need to restart the script after saving?**
A: No! Script auto-loads config every cycle (~5 seconds).

**Q: Can I run GUI and script simultaneously?**
A: Yes! GUI and script can both access config.json. Changes applied next cycle.

**Q: What happens if a track is disabled?**
A: Script logs `[process] Track XXX disabled - skipping` and sends no OSC commands to that track.
