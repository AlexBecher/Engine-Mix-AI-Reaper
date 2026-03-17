# DRY-RUN Mode Guide

## Overview

DRY-RUN (Dry Run without affecting Reaper) enables calibration and testing of the mix automation without writing to the REAPER Web API. When enabled, all volume commands are logged but not executed.

## Feature Summary

### Toggle & Control
- **Button**: "DRY-RUN: OFF" (gray) / "DRY-RUN: ON" (cyan) in Runtime panel
- **Expanded Controls**: Audio source selector (appears only when DRY-RUN = ON)
- **State**: Persists in config.json across sessions

### Audio Sources (When DRY-RUN = ON)
1. **ReaStream** (default): Normal UDP capture from REAPER
2. **File** (placeholder): Load a local WAV/FLAC and play with loop control
3. **Device**: Capture from system microphone or loopback device

### Telemetry & Logging
- **Web API Block**: `[WEBAPI SET] [DRY-RUN] track=5 db=+0.8dB (blocked)`
- **Mix Profile Intent**: All actions logged with [DRY-RUN] prefix
- **UI Status Badge**: "DRY-RUN ON" displayed in Web API status line (amber color)

### Behavior

#### DRY-RUN = ON
- Web API **writes blocked** (no volume commands sent)
- Web API **reads still work** (optional: LUFS/RMS metrics for validation)
- Audio source can be ReaStream, file, or device
- Band chart, error calculations, and fader intent arrows work normally
- Scene/routing changes update as expected

#### DRY-RUN = OFF
- Normal operation (writes enabled if Web API connected)
- ReaStream or configured source runs
- No restrictions

### Configuration Example

```json
{
  "dry_run_settings": {
    "enabled": true,
    "audio_source": "file",
    "file_path": "learning/separated/test_mix.wav",
    "loop_count": 2,
    "device_id": null,
    "device_name": "",
    "sample_rate": 44100,
    "blocksize": 4096,
    "channels": 2
  }
}
```

## Testing Checklist

### T1: DRY-RUN=ON + File Source
- [ ] Enable DRY-RUN toggle
- [ ] Select "file" from audio source dropdown
- [ ] Load a WAV file (when file picker is implemented)
- [ ] Verify band chart updates
- [ ] Verify no Web API writes occur (logs show [DRY-RUN] tags)
- [ ] Verify fader intent arrows show simulated adjustments

### T2: DRY-RUN=ON + Device Source
- [ ] Enable DRY-RUN toggle
- [ ] Select "device" from audio source dropdown
- [ ] Choose a device from the device list
- [ ] Speak/play audio into device
- [ ] Verify band chart responds to audio
- [ ] Verify intent calculations work
- [ ] Verify no Web API writes occur

### T3: DRY-RUN=OFF + ReaStream
- [ ] Disable DRY-RUN toggle
- [ ] Start script with ReaStream enabled
- [ ] Launch REAPER with ReaStream sender
- [ ] Verify fader values change in REAPER (Web API writes enabled)
- [ ] Check logs show `[WEBAPI SET]` without [DRY-RUN] tag

### T4: Runtime Toggle
- [ ] Start with DRY-RUN=OFF
- [ ] Toggle DRY-RUN=ON (running process)
- [ ] Verify UI doesn't freeze
- [ ] Verify audio capture pipeline adapts
- [ ] Toggle back to OFF
- [ ] Verify normal operation resumes

### T5: Config Persistence
- [ ] Enable DRY-RUN with device source + custom sample_rate
- [ ] Save config
- [ ] Restart application
- [ ] Verify DRY-RUN still ON
- [ ] Verify device and sample_rate restored
- [ ] Verify last used file path persists

## Known Limitations

- **File source**: UI placeholder only; actual file picker/playback not yet implemented
- **Device source**: Depends on sounddevice library; fallback to ReaStream if not installed
- **LUFS per-track**: Currently omitted in DRY-RUN; consider "Read-Only Web API" mode for future

## Environment Variables (Override Config)

- `MIX_ROBO_DRY_RUN=1`: Force DRY-RUN enabled (env var takes precedence over config)
- `MIX_ROBO_CUT_FIRST=0`: Disable cutfirst (useful for calibration + DRY-RUN)
- `MIX_ROBO_VOCAL_FOCUS=1`: Enable vocal-only high-freq routing (useful for testing vocals)

## Notes for Developers

1. **Guard location**: `web_api_client.set_track_db()` checks `_dry_run_enabled` before calling `_request()`
2. **Config reload**: `mix_profile._reload_config()` calls `auto_configure_dry_run()` to sync DRY-RUN state from file
3. **Runtime update**: Toggle in UI auto-saves config; process picks up changes on next cycle
4. **Future enhancement**: File picker and true file playback control (play/pause/loop) pending implementation
