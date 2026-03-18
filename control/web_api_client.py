import math
import os
import re
from typing import Dict, Iterable, Optional

import requests

from config_manager import load_config, is_dry_run_enabled


DEFAULT_WEBAPI_HOST = "127.0.0.1"
DEFAULT_WEBAPI_PORT = 8080
DEFAULT_WEBAPI_BASE = "/_"
DEFAULT_TIMEOUT = 2.5

REAPER_VOL_MIN = 0.0
REAPER_VOL_MAX = 3.981072
REAPER_DB_MIN = -133.0
REAPER_DB_MAX = 12.0

DEBUG = False

_session: Optional[requests.Session] = None
_target: Optional[tuple] = None
_timeout = DEFAULT_TIMEOUT
_last_status: Optional[tuple] = None
_dry_run_enabled: bool = False


def _emit_status(state: str, detail: str) -> None:
    """Emit Web API status only when state/detail changes to avoid log spam."""
    global _last_status
    payload = (state, detail)
    if _last_status == payload:
        return
    _last_status = payload
    print(f"[WEBAPI STATUS] {state} {detail}")


def clamp_db(value: float) -> float:
    return max(REAPER_DB_MIN, min(REAPER_DB_MAX, float(value)))


def clamp_volume(value: float) -> float:
    return max(REAPER_VOL_MIN, min(REAPER_VOL_MAX, float(value)))


def db_to_volume(db_value: float) -> float:
    safe_db = clamp_db(db_value)
    return clamp_volume(10.0 ** (safe_db / 20.0))


def volume_to_db(volume: float) -> float:
    safe_volume = clamp_volume(volume)
    if safe_volume <= 0.0:
        return REAPER_DB_MIN
    db = 20.0 * math.log10(safe_volume)
    return clamp_db(db)


def _build_base_url(host: str, port: int, base: str) -> str:
    base = "/" + str(base).strip("/")
    return f"http://{host}:{int(port)}{base}"


def _normalize_host(host: str, fallback: str = DEFAULT_WEBAPI_HOST) -> str:
    value = str(host or "").strip()
    if not value:
        return fallback
    if value == "127.0.0.0":
        return "127.0.0.1"
    return value


def _set_target(host: str, port: int, base: str, timeout: float, verbose: bool = False) -> None:
    global _session, _target, _timeout

    target = (_build_base_url(host, port, base), float(timeout))
    if _session is not None and _target == target:
        return

    _session = requests.Session()
    _target = target
    _timeout = float(timeout)
    _emit_status("CONFIG", target[0])
    if DEBUG or verbose:
        print(f"[WEBAPI CONFIG] url={target[0]} timeout={target[1]:.2f}s")


def configure_from_config(config=None, verbose: bool = False) -> None:
    if config is None:
        config = load_config()

    run = config.get("run_settings", {})
    host = run.get("webapi_host", DEFAULT_WEBAPI_HOST)
    port = run.get("webapi_port", DEFAULT_WEBAPI_PORT)
    base = run.get("webapi_base", DEFAULT_WEBAPI_BASE)
    timeout = run.get("webapi_timeout", DEFAULT_TIMEOUT)

    env_host = str(os.environ.get("MIX_ROBO_WEBAPI_HOST", "")).strip()
    env_port = str(os.environ.get("MIX_ROBO_WEBAPI_PORT", "")).strip()
    env_base = str(os.environ.get("MIX_ROBO_WEBAPI_BASE", "")).strip()
    env_timeout = str(os.environ.get("MIX_ROBO_WEBAPI_TIMEOUT", "")).strip()

    if env_host:
        host = env_host
    if env_port:
        try:
            port = int(env_port)
        except (TypeError, ValueError):
            pass
    if env_base:
        base = env_base
    if env_timeout:
        try:
            timeout = float(env_timeout)
        except (TypeError, ValueError):
            pass

    _set_target(_normalize_host(host), int(port), str(base), float(timeout), verbose=verbose)


def set_dry_run(enabled: bool) -> None:
    """Enable or disable DRY-RUN mode globally."""
    global _dry_run_enabled
    _dry_run_enabled = bool(enabled)
    if _dry_run_enabled:
        _emit_status("DRY-RUN", "ENABLED - Web API writes blocked")
    else:
        _emit_status("DRY-RUN", "DISABLED")


def get_dry_run() -> bool:
    """Get current DRY-RUN status."""
    global _dry_run_enabled
    return _dry_run_enabled


def auto_configure_dry_run(config=None) -> None:
    """Auto-configure DRY-RUN from config file."""
    if config is None:
        config = load_config()
    
    enabled = is_dry_run_enabled(config)
    set_dry_run(enabled)


def _request(command: str) -> str:
    if _session is None or _target is None:
        configure_from_config()

    assert _session is not None
    assert _target is not None

    base_url = _target[0]
    url = f"{base_url}/{command.lstrip('/')}"
    try:
        response = _session.get(url, timeout=_timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        _emit_status("ERROR", f"{base_url} ({exc.__class__.__name__})")
        raise

    _emit_status("OK", base_url)
    return response.text.strip()


def _parse_track_volume(track_raw: str) -> float:
    # Expected format: TRACK\t<id>\t<name>\t<flags>\t<vol>...
    if "\t" in track_raw:
        cols = [c.strip() for c in track_raw.split("\t")]
        if len(cols) >= 5 and cols[0].upper() == "TRACK":
            return float(cols[4])

    raise ValueError(f"Unable to parse TRACK volume from response: {track_raw}")


def _parse_track_name(track_raw: str) -> str:
    if "\t" in track_raw:
        cols = [c.strip() for c in track_raw.split("\t")]
        if len(cols) >= 3 and cols[0].upper() == "TRACK":
            return cols[2] or "Unknown"
    return "Unknown"


_FLOAT_PATTERN = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))"
_LUFS_PATTERN = re.compile(rf"\blufs\b\s*[:=]\s*{_FLOAT_PATTERN}", re.IGNORECASE)
_RMS_DB_PATTERN = re.compile(rf"\brms\b\s*[:=]\s*{_FLOAT_PATTERN}\s*dB?", re.IGNORECASE)
_RMS_GENERIC_PATTERN = re.compile(rf"\brms\b\s*[:=]\s*{_FLOAT_PATTERN}", re.IGNORECASE)


def _parse_track_meter_payload(payload: str) -> Dict[str, float]:
    text = str(payload or "").strip()
    if not text:
        return {}

    out: Dict[str, float] = {}

    # Try to extract meter_peak from TRACK tab-separated columns (col 7: last_meter_peak in dB*10).
    if text.startswith("TRACK"):
        try:
            cols = text.split("\t")
            if len(cols) > 6:
                meter_peak_raw = cols[6].strip()
                if meter_peak_raw and meter_peak_raw != "-":
                    meter_peak_db10 = float(meter_peak_raw)
                    # Convert dB*10 to dB (e.g., -330 -> -33.0 dB)
                    meter_peak_db = meter_peak_db10 / 10.0
                    out["meter_peak_db"] = clamp_db(meter_peak_db)
        except (ValueError, IndexError, TypeError):
            pass

    lufs_match = _LUFS_PATTERN.search(text)
    if lufs_match:
        try:
            out["lufs"] = float(lufs_match.group(1))
        except (TypeError, ValueError):
            pass

    rms_db_match = _RMS_DB_PATTERN.search(text)
    if rms_db_match:
        try:
            out["rms_db"] = float(rms_db_match.group(1))
        except (TypeError, ValueError):
            pass

    if "rms_db" not in out:
        rms_match = _RMS_GENERIC_PATTERN.search(text)
        if rms_match:
            try:
                rms_value = float(rms_match.group(1))
                if -120.0 <= rms_value <= 24.0:
                    out["rms_db"] = rms_value
                elif rms_value > 0.0:
                    out["rms_db"] = clamp_db(20.0 * math.log10(rms_value))
            except (TypeError, ValueError):
                pass

    return out


def get_track_volume(track: int) -> float:
    track_raw = _request(f"TRACK/{int(track)}")
    return clamp_volume(_parse_track_volume(track_raw))


def get_track_db(track: int, verbose: bool = False) -> float:
    volume = get_track_volume(track)
    db = volume_to_db(volume)
    if DEBUG or verbose:
        print(f"[WEBAPI READ] track={int(track)} db={db:+.2f} raw={volume:.6f}")
    return db


def get_track_snapshot(track: int) -> Dict[str, float | str]:
    track_raw = _request(f"TRACK/{int(track)}")
    volume = clamp_volume(_parse_track_volume(track_raw))
    return {
        "track": int(track),
        "name": _parse_track_name(track_raw),
        "raw": volume,
        "db": volume_to_db(volume),
    }


def set_track_db(track: int, db_value: float, verbose: bool = False) -> float:
    global _dry_run_enabled
    safe_db = clamp_db(db_value)
    volume = db_to_volume(safe_db)
    
    if _dry_run_enabled:
        if DEBUG or verbose:
            print(f"[WEBAPI SET] [DRY-RUN] track={int(track)} db={safe_db:+.2f} raw={volume:.6f} (blocked)")
    else:
        _request(f"SET/TRACK/{int(track)}/VOL/{volume}")
        if DEBUG or verbose:
            print(f"[WEBAPI SET] track={int(track)} db={safe_db:+.2f} raw={volume:.6f}")
    
    return safe_db


def get_tracks_db(track_ids: Iterable[int], verbose: bool = False) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for tid in track_ids:
        try:
            out[int(tid)] = get_track_db(int(tid), verbose=verbose)
        except Exception:
            continue
    return out


def get_tracks_lufs_rms(track_ids: Iterable[int], verbose: bool = False) -> Dict[int, Dict[str, float]]:
    """Read per-track LUFS/RMS/meter_peak meters from REAPER Web API when available.

    The exact command support depends on the REAPER Web API setup. This helper
    tries a few known patterns and falls back to parsing TRACK payload fields (col 7: last_meter_peak).
    """
    out: Dict[int, Dict[str, float]] = {}
    for tid in track_ids:
        track_id = int(tid)
        meter_payload: Optional[str] = None
        for command in (
            f"TRACK/{track_id}/METER",
            f"GET/TRACK/{track_id}/METER",
            f"TRACK/{track_id}",
        ):
            try:
                response = _request(command)
            except Exception:
                continue
            parsed = _parse_track_meter_payload(response)
            if parsed:
                meter_payload = response
                out[track_id] = parsed
                break

        if verbose and track_id in out:
            parsed = out[track_id]
            meter_peak_db = parsed.get("meter_peak_db")
            lufs = parsed.get("lufs")
            rms_db = parsed.get("rms_db")
            log_parts = [f"track={track_id}"]
            if meter_peak_db is not None:
                log_parts.append(f"meter_peak={(f'{meter_peak_db:+.2f}' if meter_peak_db is not None else '--')}")
            if lufs is not None:
                log_parts.append(f"lufs={(f'{lufs:+.2f}' if lufs is not None else '--')}")
            if rms_db is not None:
                log_parts.append(f"rms_db={(f'{rms_db:+.2f}' if rms_db is not None else '--')}")
            print(f"[WEBAPI METER] {' '.join(log_parts)}")
        elif verbose and meter_payload is not None:
            print(f"[WEBAPI METER] track={track_id} meter payload not parsed: {meter_payload}")
    return out
