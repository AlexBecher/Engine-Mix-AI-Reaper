import math
from typing import Dict, Iterable, Optional

import requests

from config_manager import load_config


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
    _set_target(str(host).strip(), int(port), str(base), float(timeout), verbose=verbose)


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
    safe_db = clamp_db(db_value)
    volume = db_to_volume(safe_db)
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
