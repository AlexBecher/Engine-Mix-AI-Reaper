# Mix Robo - Configuration Guide

## Overview

Mix Robo now features a graphical configuration interface for easy management of tracks, limits, and analysis settings without editing code.

## Configuration File

All settings are stored in **config.json** which contains:

### Master Track
- `master_track`: The Reaper master track ID (default: 153) - never actuated by the script

### Tracks
Each track has:
- **Track ID**: OSC address in Reaper (e.g., 154 for Bateria)
- **Name**: Human-readable label
- **Enabled**: Toggle to enable/disable the track (mute checkbox)
  - When **disabled**, the script will NOT send fader commands to this track
- **Min dB**: Minimum dB limit for this track
- **Max dB**: Maximum dB limit for this track

### Analysis Settings
Fine-tune the control behavior:
- **Error Gain Up**: Multiplier for positive errors (boosting tracks) - higher = slower boost
- **Error Gain Down**: Multiplier for negative errors (cutting tracks) - higher = faster cuts  
- **Max Step Up (dB)**: Maximum dB increase per cycle
- **Max Step Down (dB)**: Maximum dB decrease per cycle
- **Error Deadband**: Errors smaller than this are ignored (prevents overshooting)
- **Max Tracks Raise/Cycle**: Maximum tracks that can be boosted per analysis cycle
- **LUFS Warning Threshold**: Alert threshold for loudness metering

## Using the Configuration GUI

### Start the GUI

```powershell
.\.venv\Scripts\python config_gui.py
```

Or use the launcher:
```powershell
.\.venv\Scripts\python launcher.py
```

Then select option **1) Configuration GUI**

### GUI Layout

#### Master Track Section
- Single field to set the master track ID

#### Tracks Section
Each row represents one track with columns:
- **Mute**: Checkbox (? = enabled, empty = muted/disabled)
- **Track ID**: OSC address in Reaper
- **Name**: Track name for reference
- **Min dB**: Minimum limit
- **Max dB**: Maximum limit

#### Analysis Settings Section
All global analysis parameters in one place

#### Buttons
- **?? Save Config**: Writes changes to config.json (must do this to persist!)
- **? Reload**: Reload config.json and restart GUI
- **Reset to Defaults**: Restore original factory settings

### Example Workflow

1. Open the GUI
2. Want to disable Vocal (track 160) temporarily?
   - Find row for track 160
   - Uncheck the Mute checkbox
   - Click **Save Config**
3. Restart the processing script - it will auto-load and skip that track

## How It Works

### Config Loading
- `mix_profile.py` now loads `config.json` at startup via `config_manager.py`
- The main process loop calls `_reload_config()` every analysis cycle
- This allows **hot-reloading**: change config in GUI, save, and the script picks it up on next cycle

### Track Enable/Disable
When a track is disabled (checkbox unchecked):
- The script logs `[process] Track {ID} está desativado - ignorando`
- No OSC command is sent
- The track's current state in Reaper is preserved

## For Distribution

When compiling/packaging for another machine:

1. **Include these files:**
   - `config.json` (user-editable configuration)
   - `config_manager.py` (configuration loader)
   - `config_gui.py` (GUI application)
   - All existing scripts (mix_profile.py, run_profile.py, etc.)

2. **The user can:**
   - Run `python launcher.py` to open the GUI and configure
   - Run `python run_profile.py --profile worship --reastream --reastream-identifier master --channels 2`
   - Changes made in the GUI take effect immediately in the next processing cycle

3. **No code compilation needed** - pure Python (requires `tkinter` which is built-in)

## Troubleshooting

### "Config file not found"
- Ensure `config.json` is in the same directory as `config_manager.py`
- Check file permissions - must be readable/writable

### Changes not taking effect
- Click **Save Config** after making changes (must see status "? Config saved successfully!")
- Wait for the next analysis cycle (5 seconds by default, or use `--analysis-interval` flag)
- Check console logs for `[process] Reloading config...`

### GUI is slow or freezes
- GUI uses tkinter which is single-threaded
- Config changes apply after GUI is closed, so this is normal
- No config changes are lost - they're ready when you click Save

## Advanced: Hot-Reload During Processing

The script continuously reloads the config file every analysis cycle. This means:

1. Start the script: `python run_profile.py --profile worship --reastream ...`
2. Without stopping it, open the GUI in another terminal
3. Adjust settings and click **Save Config**
4. The running script picks up changes within 1-5 seconds (next analysis cycle)

Example: Disable a track's actuator mid-stream:
```
# Terminal 1: Script running (quiet)
$ python run_profile.py --profile worship --reastream ...
[OSC SEND] /track/154/volume 0.700
[OSC SEND] /track/155/volume 0.650
...

# Terminal 2: Disable track 155 (Baixo)
$ python config_gui.py
(uncheck Baixo, save)

# Back to Terminal 1: Script now skips 155
[OSC SEND] /track/154/volume 0.701
[track 155 está desativado - ignorando]
```

This is useful for:
- Disabling problem tracks on-the-fly
- Tuning parameters during a live mix session
- Testing different configurations without restarting
