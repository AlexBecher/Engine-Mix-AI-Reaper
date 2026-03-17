# DRY-RUN Quick Start

## Enabling DRY-RUN in the GUI

### Step 1: Launch Application
```bash
python config_gui.py
# or run_exe AlexStudioMix
```

### Step 2: Toggle DRY-RUN
- Click the **"DRY-RUN: OFF"** button (bottom-left of Runtime panel)
- Button changes to **"DRY-RUN: ON"** (cyan color)
- Expanded controls appear below with audio source selector

### Step 3: Select Audio Source
1. **ReaStream** (default when DRY-RUN=ON):
   - Still captures from REAPER via UDP
   - No Web API writes sent

2. **File** (placeholder):
   - Select a local WAV/FLAC file (not yet clickable in UI)
   - Would loop N times

3. **Device**:
   - Dropdown auto-populated with your system's input devices
   - Captures from mic, loopback, or USB interface
   - Default: 44.1 kHz, 2 channels, 4096 blocksize

### Step 4: Start Processing
- Click **START** button
- Band chart updates in real-time
- Fader arrows show simulated adjustments
- **No volume commands sent to REAPER**

### Step 5: Check Logs
Look for messages like:
```
[WEBAPI STATUS] DRY-RUN ENABLED - Web API writes blocked
[WEBAPI SET] [DRY-RUN] track=5 db=+0.8 raw=0.872890 (blocked)
```

---

## Configuration File

Location: `config.json` in project root

Example after DRY-RUN setup:
```json
{
  "dry_run_settings": {
    "enabled": true,
    "audio_source": "device",
    "device_name": "0: Microphone",
    "sample_rate": 44100,
    "blocksize": 4096,
    "channels": 2
  },
  ...
}
```

---

## Next Steps

1. **Disable DRY-RUN**: Click "DRY-RUN: ON" button again → writes enabled
2. **Switch Audio Source**: Select another option from combo → captures live data
3. **Save Config**: Click "SAVE CONFIG" → settings persist across restarts
4. **Environment Override**: `set MIX_ROBO_DRY_RUN=1` in terminal to force DRY-RUN regardless of config

---

## Troubleshooting

### "No devices found"
- Install sounddevice: `pip install sounddevice`
- Check audio system is working (test with another app)
- Try selecting ReaStream source instead

### Process hangs on startup
- Check logs window for error messages
- If logging DRY-RUN status, that's normal (sync happening)
- Press Ctrl+C if truly stuck, try again

### Faders not moving (but DRY-RUN=ON)
- This is expected! Simulated writes don't affect REAPER
- Check logs for [DRY-RUN] tags to confirm commands being "sent"
- Band chart should still show analysis

### Can't turn off DRY-RUN
- Click button again to toggle OFF
- Config should save immediately
- If stuck, manually edit `config.json` and set `"enabled": false`

---

## Using DRY-RUN for Calibration

### Workflow
1. Enable DRY-RUN + Device source
2. Play reference track through your system
3. Adjust mix profile, error gains, level targets in config_gui UI
4. Watch band behavior, fader intents, LUFS levels
5. **Once happy, disable DRY-RUN → enables real writes**
6. Deploy to live REAPER session

### Tips
- Use `MIX_ROBO_CUT_FIRST=0` environment var to test boost-only scenarios
- Use `MIX_ROBO_VOCAL_FOCUS=1` to test vocal-only eq bands
- Keep scene lineup consistent with live mix for validation
