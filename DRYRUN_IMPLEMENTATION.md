# DRY-RUN Implementation Summary

## What Was Implemented

### 1. **config_manager.py** ✓
Added three new functions for DRY-RUN configuration management:
- `get_dry_run_settings()` - Read DRY-RUN settings from config
- `set_dry_run_settings()` - Update DRY-RUN settings in config  
- `is_dry_run_enabled()` - Check if DRY-RUN is currently enabled

All settings persist in `config.json` under `"dry_run_settings"` key.

### 2. **control/web_api_client.py** ✓
Added global DRY-RUN guard mechanism:
- `set_dry_run(enabled)` - Global enable/disable
- `get_dry_run()` - Query current state
- `auto_configure_dry_run()` - Load from config file
- **Guard in `set_track_db()`**: Blocks Web API writes when DRY-RUN=ON, logs with `[DRY-RUN]` tag

```python
if _dry_run_enabled:
    print(f"[WEBAPI SET] [DRY-RUN] track=5 db=+0.8dB (blocked)")
else:
    _request(f"SET/TRACK/{track}/VOL/{volume}")
```

### 3. **mix_profile.py** ✓
Updated DRY-RUN detection to check config first, then environment:
- `_is_dry_run_enabled()` now checks config file before env var
- `_reload_config()` calls `auto_configure_dry_run()` to sync state
- Existing `_apply_actions()` logic already logs with [DIAG] DRY-RUN tag

### 4. **config_gui.py** ✓

#### UI Components Added:
- **DRY-RUN Toggle Button**: "DRY-RUN: OFF" (gray) ↔ "DRY-RUN: ON" (cyan)
- **Expanded Controls Frame**: Audio source selector (hidden by default)
  - Combo: ReaStream | File | Device
  - Device combo: Lists available audio devices
  - File label: Placeholder for future file picker
  - Status label: Shows current DRY-RUN state

#### Methods Added:
- `_toggle_dry_run()` - Toggle state, save config, update UI
- `_on_audio_source_changed()` - Handle source selection
- `_refresh_audio_sources()` - Populate device list and update UI
- `_get_audio_devices()` - Query sounddevice for available inputs
- `_sync_dry_run_ui()` - Initialize UI from config on startup

#### Telemetry Parser:
- Added `DRY_RUN_STATUS_RE` regex to detect `[WEBAPI STATUS] DRY-RUN ...` messages
- Displays DRY-RUN status in amber color (#fbbf24) in Web API label

#### Config Persistence:
- All DRY-RUN settings auto-saved to config.json when changed
- State restored on app startup (via `_sync_dry_run_ui()`)

---

## Configuration Structure

```json
{
  "dry_run_settings": {
    "enabled": false,
    "audio_source": "reastream",
    "file_path": "",
    "loop_count": 1,
    "device_id": null,
    "device_name": "",
    "sample_rate": 44100,
    "blocksize": 4096,
    "channels": 2
  }
}
```

---

## Guard Guarantee

**Central Guard Location**: `web_api_client.set_track_db()`

When DRY-RUN = ON:
```
✗ No Web API writes executed
✓ Intent logged with [DRY-RUN] tag
✓ UI updated with fader arrows
✓ Analysis and band calcs normal
```

---

## Key Design Decisions

1. **Config-first DRY-RUN**: Loaded from config.json; env var MIX_ROBO_DRY_RUN still takes precedence for testing
2. **Stateless toggles**: Each DRY-RUN toggle immediately saves config; no intermediate state
3. **Audio source flexibility**: Placeholder for file picker; device support via sounddevice
4. **Non-intrusive**: DRY-RUN wraps with guards; rest of pipeline unchanged
5. **Telemetry clarity**: [DRY-RUN] and [WEBAPI SET] tags make intent explicit in logs

---

## Acceptance Criteria ✓

- [x] Toggling DRY-RUN ON blocks all Web API set_track_db calls
- [x] DRY-RUN state + audio source persist in config.json
- [x] UI shows DRY-RUN button with ON/OFF states
- [x] Audio source selector visible only when DRY-RUN=ON
- [x] Device list auto-populated from sounddevice
- [x] No regressions in ReaStream mode (DRY-RUN=OFF)
- [x] Config loads/restores correctly
- [x] Telemetry shows [DRY-RUN] tags in logs
- [x] No errors or warnings in static analysis

---

## Future Enhancements

- [ ] File picker dialog (select WAV/FLAC file)
- [ ] File playback controls (Play/Pause/Loop)
- [ ] Read-only Web API mode (read LUFS but no writes)
- [ ] Scene + Device profile binding (remember device per scene)
- [ ] Device sample rate auto-detection
