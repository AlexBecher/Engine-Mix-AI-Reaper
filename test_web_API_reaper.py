import argparse
import math
import re
import time
from typing import Dict, List, Optional, Tuple

import requests


REAPER_VOL_MIN = 0.0
REAPER_VOL_MAX = 3.981072
REAPER_DB_MIN = -133.0
REAPER_DB_MAX = 12.0

_FLOAT_PATTERN = r"([+-]?(?:\d+(?:\.\d+)?|\.\d+))"
_LUFS_PATTERN = re.compile(rf"\blufs\b\s*[:=]\s*{_FLOAT_PATTERN}", re.IGNORECASE)
_RMS_DB_PATTERN = re.compile(rf"\brms\b\s*[:=]\s*{_FLOAT_PATTERN}\s*dB?", re.IGNORECASE)
_RMS_GENERIC_PATTERN = re.compile(rf"\brms\b\s*[:=]\s*{_FLOAT_PATTERN}", re.IGNORECASE)

METER_COMMAND_TEMPLATES = [
    "TRACK/{track}/METER",
    "GET/TRACK/{track}/METER",
    "TRACK/{track}",
    "GET/TRACK/{track}",
    "TRACK/{track}/VU",
    "GET/TRACK/{track}/VU",
    "TRACK/{track}/PEAK",
    "GET/TRACK/{track}/PEAK",
    "TRACK/{track}/RMS",
    "GET/TRACK/{track}/RMS",
    "TRACK/{track}/LOUDNESS",
    "GET/TRACK/{track}/LOUDNESS",
    "TRACK/{track}/LUFS",
    "GET/TRACK/{track}/LUFS",
    "TRACK/{track}/STATS",
    "GET/TRACK/{track}/STATS",
    "TRACK/{track}/STATUS",
    "GET/TRACK/{track}/STATUS",
    "TRACK/{track}/INFO",
    "GET/TRACK/{track}/INFO",
]


def clamp_volume(value: float) -> float:
    return max(REAPER_VOL_MIN, min(REAPER_VOL_MAX, float(value)))


def clamp_db(value: float) -> float:
    return max(REAPER_DB_MIN, min(REAPER_DB_MAX, float(value)))


def volume_to_db(value: float) -> float:
    vol = clamp_volume(value)
    if vol <= 0.0:
        return REAPER_DB_MIN
    # Web API reports linear gain (amplitude), where 1.0 == 0 dB.
    db = 20.0 * math.log10(vol)
    return clamp_db(db)


def db_to_volume(db: float) -> float:
    safe_db = clamp_db(db)
    vol = 10.0 ** (safe_db / 20.0)
    return clamp_volume(vol)


def parse_track_meter_payload(payload: str) -> Dict[str, float]:
    text = str(payload or "").strip()
    if not text:
        return {}

    out: Dict[str, float] = {}

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


class ReaperWebAPI:
    def __init__(self, host: str = "192.168.15.48", port: int = 8080, base: str = "/_", timeout: float = 2.5):
        base = "/" + base.strip("/")
        self.base_url = f"http://{host}:{port}{base}"
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, command: str) -> str:
        url = f"{self.base_url}/{command.lstrip('/')}"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text.strip()

    def try_get(self, command: str) -> Tuple[bool, str]:
        try:
            return True, self._get(command)
        except Exception as exc:
            return False, f"ERROR: {exc}"

    def _first_int(self, text: str) -> int:
        match = re.search(r"-?\d+", text)
        if not match:
            raise ValueError(f"No integer found in response: {text}")
        return int(match.group(0))

    def _first_float(self, text: str) -> float:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            raise ValueError(f"No float found in response: {text}")
        return float(match.group(0))

    def get_track_count(self) -> int:
        return self._first_int(self._get("NTRACK"))

    def get_track_raw(self, track_index: int) -> str:
        return self._get(f"TRACK/{track_index}")

    def _parse_track_columns(self, track_raw: str) -> Optional[List[str]]:
        # REAPER commonly returns TRACK rows in tab-separated format.
        if "\t" not in track_raw:
            return None
        cols = [c.strip() for c in track_raw.split("\t")]
        if len(cols) < 5:
            return None
        if cols[0].upper() != "TRACK":
            return None
        return cols

    def parse_track_name(self, track_raw: str) -> str:
        cols = self._parse_track_columns(track_raw)
        if cols is not None:
            return cols[2] or "Unknown"

        quoted = re.search(r'"([^"]*)"', track_raw)
        if quoted:
            return quoted.group(1)
        return "Unknown"

    def parse_track_numbers(self, track_raw: str) -> List[float]:
        return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", track_raw)]

    def get_volume(self, track_index: int) -> float:
        track_raw = self.get_track_raw(track_index)

        cols = self._parse_track_columns(track_raw)
        if cols is not None:
            # TRACK\t<id>\t<name>\t<flags>\t<vol>...
            try:
                return float(cols[4])
            except (ValueError, TypeError):
                pass

        # Some configurations may expose alternate volume endpoints.
        direct_commands = [
            f"TRACK/{track_index}/VOL",
            f"GET/TRACK/{track_index}/VOL",
        ]
        for cmd in direct_commands:
            try:
                return self._first_float(self._get(cmd))
            except Exception:
                pass

        numbers = self.parse_track_numbers(track_raw)
        if not numbers:
            raise ValueError(f"Could not parse volume from TRACK response: {track_raw}")

        # Heuristic: first non-index non-negative small value is usually linear volume.
        filtered = [n for n in numbers if n >= 0 and n <= 4.0]
        if filtered:
            return filtered[0]
        return numbers[0]

    def set_volume(self, track_index: int, volume_linear: float) -> str:
        safe_volume = clamp_volume(volume_linear)
        commands = [
            f"SET/TRACK/{track_index}/VOL/{safe_volume}",
            f"TRACK/{track_index}/VOL/{safe_volume}",
        ]
        last_error: Optional[Exception] = None
        for cmd in commands:
            try:
                return self._get(cmd)
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Failed to set volume for track {track_index}: {last_error}")


def print_track_overview(api: ReaperWebAPI, track_ids: List[int]) -> None:
    for track_id in track_ids:
        try:
            raw = api.get_track_raw(track_id)
            name = api.parse_track_name(raw)
            vol = api.get_volume(track_id)
            db = volume_to_db(vol)
            print(f"Track {track_id:>3} | name='{name}' | db={db:+7.2f} dB | raw={vol:.6f}")
        except Exception as exc:
            print(f"Track {track_id:>3} | ERROR: {exc}")


def probe_track(api: ReaperWebAPI, track_id: int) -> None:
    raw = api.get_track_raw(track_id)
    name = api.parse_track_name(raw)
    nums = api.parse_track_numbers(raw)
    print(f"TRACK/{track_id} raw: {raw}")
    print(f"Parsed name: {name}")
    print(f"Parsed numbers: {nums}")
    try:
        guessed = api.get_volume(track_id)
        guessed_db = volume_to_db(guessed)
        print(f"Guessed volume: {guessed:.6f} ({guessed_db:+.2f} dB)")
    except Exception as exc:
        print(f"Guessed volume: ERROR: {exc}")


def scan_track_meter_commands(api: ReaperWebAPI, track_id: int, commands: Optional[List[str]] = None) -> None:
    templates = commands if commands else METER_COMMAND_TEMPLATES
    print(f"\n=== Meter scan for track {track_id} ===")
    for template in templates:
        command = template.format(track=track_id)
        ok, payload = api.try_get(command)
        parsed = parse_track_meter_payload(payload) if ok else {}
        status = "OK" if ok else "ERR"
        parsed_text = "-"
        if parsed:
            parts = []
            if "lufs" in parsed:
                parts.append(f"LUFS={parsed['lufs']:+.2f}")
            if "rms_db" in parsed:
                parts.append(f"RMS={parsed['rms_db']:+.2f}dB")
            parsed_text = ", ".join(parts)

        print(f"[{status}] {command}")
        if parsed:
            print(f"  Parsed: {parsed_text}")
        preview = payload.replace("\r", " ").replace("\n", " ")
        if len(preview) > 220:
            preview = preview[:217] + "..."
        print(f"  Payload: {preview}")


def maybe_scan_track(api: ReaperWebAPI, track_id: Optional[int], commands: Optional[List[str]] = None) -> None:
    if track_id is None:
        return
    scan_track_meter_commands(api, track_id, commands=commands)


def maybe_set_volume(api: ReaperWebAPI, set_data: Optional[Tuple[int, float]]) -> None:
    if not set_data:
        return
    track_id, requested_db = set_data
    requested_db = float(requested_db)
    clamped_db = clamp_db(requested_db)
    target_volume = db_to_volume(clamped_db)

    before = api.get_volume(track_id)
    print(f"Before set: track={track_id}, db={volume_to_db(before):+.2f} dB | raw={before:.6f}")
    if abs(requested_db - clamped_db) > 1e-12:
        print(
            f"Requested dB {requested_db:+.2f} out of range; clamped to {clamped_db:+.2f} "
            f"(valid: {REAPER_DB_MIN:+.2f}..{REAPER_DB_MAX:+.2f} dB)"
        )
    api.set_volume(track_id, target_volume)
    time.sleep(0.2)
    after = api.get_volume(track_id)
    print(f"After  set: track={track_id}, db={volume_to_db(after):+.2f} dB | raw={after:.6f}")


def default_tracks(api: ReaperWebAPI, limit: int = 8) -> List[int]:
    count = api.get_track_count()
    return list(range(1, min(count, limit) + 1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Teste da API Web do REAPER para leitura/escrita de volume por track.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host do REAPER Web Remote")
    parser.add_argument("--port", type=int, default=8080, help="Porta do REAPER Web Remote")
    parser.add_argument("--base", default="/_", help="Path base da API web (default: /_)")
    parser.add_argument("--timeout", type=float, default=2.5, help="Timeout HTTP em segundos")
    parser.add_argument(
        "--tracks",
        type=int,
        nargs="+",
        help="IDs de track para consultar (ex: --tracks 1 3 7)",
    )
    parser.add_argument(
        "--set",
        nargs=2,
        metavar=("TRACK_ID", "DB"),
        help=(
            "Escreve em dB usando curva logaritmica do REAPER "
            f"(range: {REAPER_DB_MIN:+.2f}..{REAPER_DB_MAX:+.2f} dB)"
        ),
    )
    parser.add_argument(
        "--probe",
        type=int,
        help="Mostra resposta crua da track e parsing (bom para calibrar volume)",
    )
    parser.add_argument(
        "--scan-meter",
        type=int,
        metavar="TRACK_ID",
        help="Varre comandos candidatos de meter/LUFS para uma track e mostra payload + parsing",
    )
    parser.add_argument(
        "--meter-command",
        action="append",
        dest="meter_commands",
        help=(
            "Template extra de comando para scan de meter. Use {track} como placeholder, "
            "ex: --meter-command TRACK/{track}/LOUDNESS"
        ),
    )
    parser.add_argument(
        "--only-meter-commands",
        action="store_true",
        help="Usa somente os --meter-command informados (sem comandos padrao).",
    )
    parser.add_argument(
        "--only-scan",
        action="store_true",
        help="Executa apenas a varredura de meter, sem overview das tracks",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    api = ReaperWebAPI(host=args.host, port=args.port, base=args.base, timeout=args.timeout)

    try:
        ntracks = api.get_track_count()
        print(f"REAPER online | NTRACK={ntracks}")
    except Exception as exc:
        print(f"Falha ao conectar na API Web do REAPER: {exc}")
        return

    if args.probe is not None:
        probe_track(api, args.probe)

    scan_commands = list(METER_COMMAND_TEMPLATES)
    if args.only_meter_commands:
        scan_commands = list(args.meter_commands or [])
    elif args.meter_commands:
        scan_commands.extend(args.meter_commands)

    maybe_scan_track(api, args.scan_meter, commands=scan_commands)

    if args.only_scan:
        return

    track_ids = args.tracks if args.tracks else default_tracks(api)
    print_track_overview(api, track_ids)

    set_data: Optional[Tuple[int, float]] = None
    if args.set:
        set_data = (int(args.set[0]), float(args.set[1]))
    maybe_set_volume(api, set_data)


if __name__ == "__main__":
    main()