# Meter Guardian Gating Logic - Implementation Guide

## Problem Statement
Current implementation: Guardian (meter-based level control) acts **constantly** on faders whenever meter_peak_db is detected, causing excessive/constant adjustments.

**Desired behavior:** Guardian should have a "gate" - only act when tracks deviate **significantly** from their target levels. When tracks are within acceptable tolerance, let the spectral model (profile-based control) dominate.

## Architecture

### Dual-Mode Decision (Gating):
1. **Guardian Active Mode** (deviation is large):
   - Track's `meter_peak_db` deviates > `GUARDIAN_GATE_THRESHOLD_DB` from target
   - Guardian acts: applies `level_guard_db` correction
   - Spectral actions may be suppressed/reduced (cut-first logic applies)
   
2. **Guardian Dormant Mode** (deviation is small/acceptable):
   - Track's `meter_peak_db` is within `GUARDIAN_GATE_THRESHOLD_DB` of target
   - Guardian does NOT act (returns 0.0)
   - Spectral model (profile) assumes control
   - No meter-forced fader movement

### Key Parameters to Add/Modify:
- `GUARDIAN_GATE_THRESHOLD_DB`: activation threshold (e.g., 3.0 dB = gate opens only if |error| > 3dB)
  - Config path: `meter_fusion.guardian_gate_threshold_db`
  - Default: 3.0 (conservative; only big deviations trigger Guardian)
  
- `METER_PEAK_MIN_ACTIVITY_DB`: already implemented (-1300.0 = silence gate)

### Implementation Points:
1. **`_compute_level_delta_db()` function:** 
   - After calculating `level_error`, check if `abs(level_error) > GUARDIAN_GATE_THRESHOLD_DB`
   - If NO: return 0.0 immediately (Guardian gate closed → no action)
   - If YES: proceed with normal calculation (Guardian gate open → act)

2. **Logging improvement:**
   - Add diagnostic log for when Guardian gate is **open** vs **closed**
   - Example: `[DIAG] Track 11 Guardian gate=CLOSED (error=-0.5dB < threshold=3.0dB) → spectral takes control`
   - Example: `[DIAG] Track 11 Guardian gate=OPEN (error=-5.2dB > threshold=3.0dB) → Guardian active`

3. **Config entry (config.json):**
   ```json
   "meter_fusion": {
     "guardian_gate_threshold_db": 3.0,
     ...
   }
   ```

### Expected Outcome:
- Small deviations (e.g., ±1.5 dB from target): spectral profile controls → smooth, natural blend
- Large deviations (e.g., ±5 dB from target): Guardian activates → emergency level rescue
- Reduced fader chatter, more predictable behavior, hybrid control (spectral + level safety net)

### Related Code Files:
- `mix_profile.py`: `_compute_level_delta_db()`, `GUARDIAN_GATE_THRESHOLD_DB` constant, reload in `reload_config()`
- `config.json`: `meter_fusion.guardian_gate_threshold_db` setting
