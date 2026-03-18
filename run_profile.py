"""Run the mix profile logic on separated stems and send Web API commands.

This script assumes you already have separated stems (e.g., produced by Demucs) in a
folder structure like:

  separated/<track-name>/<stem>.wav

It will load each stem, compute tonal band values, compare to the profile, and then
send Web API volume updates via the existing `mix_profile.process_stems` logic.

Example:
    python run_profile.py --profile mymix --stems-dir learning/separated/audio

If you want to create the stems first, use `python learning/train_profile.py audio.wav --name mymix`
which will also write the profile into learning/profiles.json.
"""

import argparse
import os
import socket
import time

import numpy as np
import soundfile as sf

from mix_profile import process, process_stems


DEFAULT_WASAPI_DEVICE_NAME = "Speakers (USB AUDIO DEVICE)"
DEFAULT_LIVE_BLOCKSIZE = 4096
DEFAULT_REASTREAM_HOST = "0.0.0.0"
DEFAULT_REASTREAM_PORT = 58710
DEFAULT_REASTREAM_IDENTIFIER = "master"
DEFAULT_REASTREAM_BUFFER_SIZE = 65536
MIN_ANALYSIS_INTERVAL = 0.5


def _estimate_input_peak_frequency(audio, sample_rate):
    """Estimate the dominant raw FFT peak frequency from the incoming audio window."""
    audio_arr = np.asarray(audio, dtype=np.float64)
    if audio_arr.size == 0 or sample_rate is None or float(sample_rate) <= 0.0:
        return None, None

    if audio_arr.ndim <= 1:
        channels = [audio_arr.reshape(-1)]
    else:
        channels = [
            np.asarray(audio_arr[:, ch_idx], dtype=np.float64).reshape(-1)
            for ch_idx in range(audio_arr.shape[1])
        ]

    channels = [channel for channel in channels if channel.size >= 256]
    if not channels:
        return None, None

    min_len = min(channel.size for channel in channels)
    channels = [channel[:min_len] for channel in channels]
    window = np.hanning(min_len)
    spectra = [np.abs(np.fft.rfft(channel * window)) for channel in channels]
    spectrum = np.mean(np.asarray(spectra, dtype=np.float64), axis=0)
    freqs = np.fft.rfftfreq(min_len, d=1.0 / float(sample_rate))

    valid = np.where(freqs >= 20.0)[0]
    if valid.size == 0:
        return None, None

    valid_spectrum = spectrum[valid]
    if valid_spectrum.size == 0:
        return None, None

    peak_local_idx = int(np.argmax(valid_spectrum))
    peak_idx = int(valid[peak_local_idx])
    peak_mag = float(spectrum[peak_idx])
    noise_floor = float(np.median(valid_spectrum))
    peak_hz = float(freqs[peak_idx])
    prominence = peak_mag / max(1e-12, noise_floor)

    if not np.isfinite(peak_hz) or not np.isfinite(prominence) or peak_mag <= 0.0:
        return None, None
    return peak_hz, prominence


def _summarize_stereo_pair_peaks(audio, sample_rate):
    """Return dominant FFT peaks for each stereo pair in a multichannel buffer."""
    audio_arr = np.asarray(audio, dtype=np.float64)
    if audio_arr.ndim != 2 or audio_arr.shape[0] < 256 or audio_arr.shape[1] < 2:
        return []

    total_ch = int(audio_arr.shape[1])
    pair_starts = list(range(0, total_ch - 1, 2))
    summaries = []
    for start in pair_starts:
        pair = audio_arr[:, start:start + 2]
        if pair.shape[1] != 2:
            continue
        peak_hz, prominence = _estimate_input_peak_frequency(pair, sample_rate)
        energy = float(np.sqrt(np.mean(np.square(pair))))
        if peak_hz is None:
            continue
        summaries.append(
            {
                "start": int(start),
                "peak_hz": float(peak_hz),
                "prominence": float(prominence),
                "energy": float(energy),
            }
        )
    return summaries


def _normalize_host(host_value, fallback=DEFAULT_REASTREAM_HOST):
    host = str(host_value or "").strip()
    return host or fallback


def _candidate_bind_hosts(host_value):
    host = _normalize_host(host_value)
    candidates = []
    for value in (host, "0.0.0.0", "127.0.0.1"):
        if value not in candidates:
            candidates.append(value)
    return candidates


def _decode_reastream_frames(packet, channels, identifier=None, verbose=False):
    """Decode ReaStream UDP packet into float32 frames.

    Tries the standard ReaStream wire format first:
      [identifier: null-terminated ASCII string]
      [sample_rate: float32 LE]
      [num_channels: uint16 LE]
      [num_samples: uint16 LE]
      [audio: float32 * num_channels * num_samples, interleaved]

    Falls back to a brute-force offset scan if the structured parse fails.
    """
    import struct

    if channels <= 0:
        return None, None

    def _select_output_channels(frame_matrix, requested_channels, sample_rate=None, decode_label=""):
        """Select output channels robustly from decoded multi-channel packets.

        For requested stereo (2ch) on packets with >2 channels, pick the stereo
        pair with highest RMS energy. This avoids wrong routing when ReaStream
        is configured as 8ch and the active signal is not on channels 1/2.
        """
        arr = np.asarray(frame_matrix)
        if arr.ndim != 2 or arr.size == 0:
            return arr

        total_ch = int(arr.shape[1])
        req_ch = int(max(1, requested_channels))
        if total_ch < req_ch:
            return arr[:, :req_ch]

        if req_ch == 1:
            channel_rms = np.sqrt(np.mean(np.square(arr.astype(np.float64)), axis=0))
            best_idx = int(np.argmax(channel_rms))
            if verbose and total_ch > 1:
                print(
                    f"[DECODE CHANNEL SELECT]{decode_label} mono from ch={best_idx} "
                    f"(total_ch={total_ch})"
                )
            return arr[:, best_idx:best_idx + 1]

        if req_ch == 2 and total_ch >= 2:
            # Prefer standard bus pairs: (0,1), (2,3), (4,5), ...
            pair_starts = list(range(0, total_ch - 1, 2))
            if (total_ch % 2) == 1 and (total_ch - 2) not in pair_starts:
                pair_starts.append(total_ch - 2)

            best_start = 0
            best_energy = -1.0
            arr64 = arr.astype(np.float64, copy=False)
            pair_summaries = []
            for start in pair_starts:
                pair = arr64[:, start:start + 2]
                if pair.shape[1] != 2:
                    continue
                energy = float(np.sqrt(np.mean(np.square(pair))))
                peak_hz, prominence = _estimate_input_peak_frequency(pair, sample_rate)
                if peak_hz is not None:
                    pair_summaries.append(
                        f"{start}/{start + 1}@{peak_hz:.1f}Hz(e={energy:.4f},p={prominence:.1f})"
                    )
                if energy > best_energy:
                    best_energy = energy
                    best_start = int(start)

            if verbose:
                print(
                    f"[DECODE CHANNEL SELECT]{decode_label} stereo pair={best_start}/{best_start + 1} "
                    f"(total_ch={total_ch}, requested=2, energy={best_energy:.6f})"
                )
            if pair_summaries:
                print(
                    f"[DIAG] REASTREAM pairs sr={int(sample_rate or 0)} total_ch={total_ch} "
                    f"selected={best_start}/{best_start + 1} :: {' | '.join(pair_summaries)}"
                )
            return arr[:, best_start:best_start + 2]

        return arr[:, :req_ch]

    def _decode_candidate(meta_start, pkt_ident_label=""):
        """Try decoding a <float sr, uint16 ch, uint16 samples> block at meta_start."""
        if meta_start < 0 or len(packet) < (meta_start + 8):
            return None, None
        try:
            sample_rate, num_ch, num_samples = struct.unpack_from("<fHH", packet, meta_start)
        except Exception:
            return None, None

        expected = int(num_ch) * int(num_samples) * 4
        audio_start = int(meta_start) + 8
        if not (
            num_ch > 0
            and num_samples > 0
            and 8000 <= sample_rate <= 192000
            and expected > 0
            and len(packet) >= (audio_start + expected)
        ):
            return None, None

        audio_bytes = packet[audio_start:audio_start + expected]
        audio_data = np.frombuffer(audio_bytes, dtype=np.float32)
        if audio_data.size != (int(num_ch) * int(num_samples)) or not np.isfinite(audio_data).all():
            return None, None

        peak = float(np.max(np.abs(audio_data))) if audio_data.size else 0.0
        if peak < 1e-7 or peak > 16.0:
            return None, None

        frame_matrix = audio_data.reshape(int(num_samples), int(num_ch))
        frames = _select_output_channels(
            frame_matrix,
            requested_channels=int(channels),
            sample_rate=float(sample_rate),
            decode_label=f" ident='{pkt_ident_label}'" if pkt_ident_label else "",
        )
        if verbose:
            label = f" ident='{pkt_ident_label}'" if pkt_ident_label else ""
            print(
                f"[DECODE STRUCT]{label} sr={float(sample_rate):.0f} "
                f"ch={int(num_ch)} -> out_ch={int(frames.shape[1])} "
                f"samples={int(num_samples)} peak={peak:.4f}"
            )
        return frames, float(sample_rate)

    # --- Structured parse by explicit identifier location (most reliable) ---
    ident = str(identifier or "").strip()
    if ident:
        ident_bytes = ident.encode("ascii", errors="ignore") + b"\x00"
        if ident_bytes:
            ident_candidates = []
            search_start = 0
            while True:
                ident_pos = packet.find(ident_bytes, search_start)
                if ident_pos < 0:
                    break
                frames, packet_sr = _decode_candidate(ident_pos + len(ident_bytes), pkt_ident_label=ident)
                if frames is not None:
                    payload_bytes = int(frames.shape[0]) * int(frames.shape[1]) * 4
                    ident_candidates.append((payload_bytes, ident_pos, frames, packet_sr))
                search_start = ident_pos + 1

            if ident_candidates:
                # Prefer the candidate with largest coherent payload; if tied,
                # prefer the one that appears later in the packet.
                ident_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
                _payload_bytes, _ident_pos, frames, packet_sr = ident_candidates[0]
                return frames, packet_sr

    # --- Structured parse (standard ReaStream format) ---
    null_pos = packet.find(b"\x00")
    if null_pos >= 0:
        try:
            pkt_ident = packet[:null_pos].decode("ascii", errors="replace")
            if ident and ident.lower() not in pkt_ident.lower():
                if verbose:
                    print(f"[DECODE] Identifier mismatch on first-null parse: packet='{pkt_ident}' expected='{ident}'")
            else:
                frames, packet_sr = _decode_candidate(null_pos + 1, pkt_ident_label=pkt_ident)
                if frames is not None:
                    return frames, packet_sr
        except Exception:
            pass

    # --- Heuristic metadata scan ---
    # Some packet variants prepend extra bytes before the identifier/meta block.
    # Scan for plausible <float sample_rate, uint16 channels, uint16 samples>
    # and validate by expected payload size.
    if identifier:
        ident_bytes = identifier.encode("utf-8", errors="ignore")
        if ident_bytes and ident_bytes not in packet:
            if verbose:
                print("[DECODE] Identifier bytes not found in packet, rejecting.")
            return None, None

    best_candidate = None
    max_meta_probe = max(0, len(packet) - 8)
    for meta_start in range(0, min(512, max_meta_probe + 1)):
        try:
            sample_rate, num_ch, num_samples = struct.unpack_from("<fHH", packet, meta_start)
        except Exception:
            continue

        if not (8000 <= sample_rate <= 192000):
            continue
        if num_ch <= 0 or num_ch > 16 or num_samples <= 0:
            continue

        audio_start = meta_start + 8
        expected = int(num_ch) * int(num_samples) * 4
        if expected <= 0 or (audio_start + expected) > len(packet):
            continue

        # Prefer candidates with larger coherent frame payloads.
        if best_candidate is None or expected > best_candidate[0]:
            best_candidate = (expected, meta_start, float(sample_rate), int(num_ch), int(num_samples), audio_start)

    if best_candidate is not None:
        _expected, _meta_start, sample_rate, num_ch, num_samples, audio_start = best_candidate
        audio_bytes = packet[audio_start:audio_start + (num_ch * num_samples * 4)]
        audio_data = np.frombuffer(audio_bytes, dtype=np.float32)
        if audio_data.size == (num_ch * num_samples) and np.isfinite(audio_data).all():
            peak = float(np.max(np.abs(audio_data)))
            if 1e-7 <= peak <= 16.0:
                frame_matrix = audio_data.reshape(num_samples, num_ch)
                frames = _select_output_channels(
                    frame_matrix,
                    requested_channels=int(channels),
                    sample_rate=float(sample_rate),
                    decode_label=" meta-scan",
                )
                if verbose:
                    print(
                        f"[DECODE META-SCAN] sr={sample_rate:.0f} ch={num_ch} "
                        f"-> out_ch={int(frames.shape[1])} samples={num_samples} peak={peak:.4f}"
                    )
                return frames, float(sample_rate)

    # --- Fallback: brute-force scan (step=1 to catch any byte alignment) ---
    max_probe = min(256, max(0, len(packet) - 4))
    for offset in range(0, max_probe + 1):  # step=1 to hit any valid alignment
        remaining = len(packet) - offset
        if remaining <= 0 or remaining % 4 != 0:
            continue

        samples = np.frombuffer(packet, dtype=np.float32, offset=offset)
        if samples.size < channels * 32:
            continue
        if not np.isfinite(samples).all():
            continue

        peak = float(np.max(np.abs(samples)))
        if peak < 1e-7 or peak > 16.0:
            continue

        usable = (samples.size // channels) * channels
        if usable <= 0:
            continue

        if verbose:
            print(f"[DECODE FALLBACK] Found audio at offset={offset} peak={peak:.4f}")
        return samples[:usable].reshape(-1, channels), None

    return None, None


def load_stems(stems_dir, stem_names=None, sample_rate=48000):
    if stem_names is None:
        stem_names = ["vocals", "drums", "bass", "piano", "other"]

    stems = {}
    for stem in stem_names:
        path = os.path.join(stems_dir, f"{stem}.wav")
        if not os.path.exists(path):
            continue
        audio, sr = sf.read(path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != sample_rate:
            factor = sample_rate / sr
            indices = (np.arange(int(len(audio) * factor)) / factor).astype(int)
            audio = audio[indices]
        stems[stem] = audio

    return stems


def load_test_audio(path, sample_rate=48000, channels=2):
    audio, sr = sf.read(path, dtype="float32")

    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        audio = audio.reshape(-1, 1)

    if sr != sample_rate:
        factor = sample_rate / sr
        indices = (np.arange(int(audio.shape[0] * factor)) / factor).astype(int)
        indices = np.clip(indices, 0, max(0, audio.shape[0] - 1))
        audio = audio[indices]

    if channels <= 0:
        channels = 1

    if audio.shape[1] > channels:
        audio = audio[:, :channels]
    elif audio.shape[1] < channels:
        if audio.shape[1] == 1:
            audio = np.repeat(audio, channels, axis=1)
        else:
            pad_count = channels - audio.shape[1]
            pad = np.repeat(audio[:, -1:], pad_count, axis=1)
            audio = np.concatenate([audio, pad], axis=1)

    return np.array(audio, dtype=np.float32, copy=False)


def main():
    parser = argparse.ArgumentParser(description="Run mix profile on separated stems.")
    parser.add_argument("--profile", required=True, help="Profile name (from learning/profiles.json)")
    parser.add_argument(
        "--stems-dir",
        default=None,
        help=(
            "Directory containing separated stems (.wav files). "
            "If omitted, defaults to learning/separated/<profile>."
        ),
    )
    parser.add_argument("--sr", type=int, default=48000, help="Sample rate to use")
    parser.add_argument(
        "--calibrate-freq",
        type=float,
        default=0.0,
        dest="calibrate_freq",
        help=(
            "Reference frequency in Hz for automatic SR calibration. "
            "Play a pure tone at this frequency (e.g. 320) while the robot is running "
            "and it will measure the FFT peak, compute the correction factor, and "
            "apply it to all subsequent analysis. Octave errors (x0.5 / x2) are "
            "snapped automatically if the correction is within 15%% of a power of 2."
        ),
    )

    # Track mapping
    parser.add_argument(
        "--track-map",
        help=(
            "Optional JSON mapping of stem/band -> track (e.g. '{\"vocals\":8,\"drums\":2}'). "
            "If omitted, the default Reaper mapping is used."
        ),
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Capture audio from the system (Reaper output via loopback/ReaRoute) and apply the profile live. "
            "If enabled, --stems-dir is ignored and audio is captured from a sound device."
        ),
    )
    parser.add_argument(
        "--reastream",
        action="store_true",
        help=(
            "Use UDP ReaStream capture instead of sounddevice input stream. "
            "This mode listens on --reastream-host/--reastream-port and filters by --reastream-identifier."
        ),
    )
    parser.add_argument(
        "--test-audio",
        help=(
            "Path to a WAV file used as a simulated live input. "
            "This bypasses ReaStream and feeds the file through the same analysis pipeline for testing."
        ),
    )
    parser.add_argument(
        "--test-speed",
        type=float,
        default=0.0,
        help=(
            "Playback speed for --test-audio. "
            "0 runs as fast as possible, 1.0 emulates real time, 2.0 runs 2x faster."
        ),
    )
    parser.add_argument(
        "--reastream-host",
        default=DEFAULT_REASTREAM_HOST,
        help="Host/IP to bind for ReaStream UDP packets.",
    )
    parser.add_argument(
        "--reastream-port",
        type=int,
        default=DEFAULT_REASTREAM_PORT,
        help="UDP port for ReaStream packets.",
    )
    parser.add_argument(
        "--reastream-identifier",
        default=DEFAULT_REASTREAM_IDENTIFIER,
        help=(
            "ReaStream identifier to accept (default: master). "
            "Use empty string to accept any stream identifier."
        ),
    )
    parser.add_argument(
        "--reastream-buffer-size",
        type=int,
        default=DEFAULT_REASTREAM_BUFFER_SIZE,
        help="Maximum UDP packet size for ReaStream receive.",
    )
    parser.add_argument(
        "--webapi-host",
        default=None,
        help="Optional Web API host override for this process.",
    )
    parser.add_argument(
        "--webapi-port",
        type=int,
        default=None,
        help="Optional Web API port override for this process.",
    )
    parser.add_argument(
        "--webapi-base",
        default=None,
        help="Optional Web API base path override for this process.",
    )
    parser.add_argument(
        "--webapi-timeout",
        type=float,
        default=None,
        help="Optional Web API timeout override for this process.",
    )
    parser.add_argument(
        "--device",
        help=(
            "Optional sound device name or index for live capture. "
            "If omitted, the script prioritizes Speakers (USB AUDIO DEVICE) on Windows WASAPI."
        ),
    )
    parser.add_argument(
        "--loopback",
        action="store_true",
        help=(
            "Use WASAPI loopback capture (Windows) from an output device. "
            "This is the recommended way to capture Reaper output on Windows."
        ),
    )
    parser.add_argument(
        "--ndi",
        action="store_true",
        help=(
            "(Deprecated) Capture audio using NDI. "
            "This mode now uses local sounddevice live capture instead. "
            "Prefer using --live / --loopback and --device to select the capture source."
        ),
    )
    parser.add_argument(
        "--blocksize",
        type=int,
        default=DEFAULT_LIVE_BLOCKSIZE,
        help="Block size (in samples) for live capture buffers.",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=2,
        help="Number of channels to capture for live mode (usually 2).",
    )
    parser.add_argument(
        "--channel-map",
        help=(
            "Optional JSON mapping of captured channel index -> track (e.g. '{\"0\":14,\"1\":27}'). "
            "When provided, each channel is treated as a separate bus and the analysis is applied to its corresponding track."
        ),
    )
    parser.add_argument(
        "--live-track",
        type=int,
        default=153,
        help=(
            "When running in live mode without --channel-map, map the analysis bands to this track. "
            "Useful when capturing the master output and controlling master volume."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print info about stems and Web API read/write telemetry.",
    )
    parser.add_argument(
        "--analysis-interval",
        type=float,
        default=5.0,
        help=(
            "Interval in seconds between live analysis/actuation cycles. "
            "Higher values make the automation react more slowly."
        ),
    )
    args = parser.parse_args()

    if args.webapi_host is not None:
        os.environ["MIX_ROBO_WEBAPI_HOST"] = str(args.webapi_host).strip()
    if args.webapi_port is not None:
        os.environ["MIX_ROBO_WEBAPI_PORT"] = str(int(args.webapi_port))
    if args.webapi_base is not None:
        os.environ["MIX_ROBO_WEBAPI_BASE"] = str(args.webapi_base).strip()
    if args.webapi_timeout is not None:
        os.environ["MIX_ROBO_WEBAPI_TIMEOUT"] = str(float(args.webapi_timeout))

    args.reastream_host = _normalize_host(args.reastream_host)
    if args.analysis_interval < MIN_ANALYSIS_INTERVAL:
        if args.verbose:
            print(
                f"[ANALYSIS] analysis_interval {args.analysis_interval:.2f}s muito curto; "
                f"usando {MIN_ANALYSIS_INTERVAL:.1f}s para estabilidade."
            )
        args.analysis_interval = MIN_ANALYSIS_INTERVAL

    track_map = None
    if args.track_map:
        import json

        track_map = json.loads(args.track_map)

    # Treat --ndi as an alias for --live/--loopback in the new workflow.
    # NDI capture has been deprecated and replaced by local sounddevice capture.
    if args.ndi:
        if args.verbose:
            print("NOTE: --ndi is deprecated; switching to live loopback capture.")
        args.live = True
        args.loopback = True

    if args.reastream:
        args.live = True

    if args.test_audio:
        args.live = True

    if args.live:
        use_reastream = args.reastream
        use_test_audio = bool(args.test_audio)

        if use_test_audio and "MIX_ROBO_CUT_FIRST" not in os.environ:
            os.environ["MIX_ROBO_CUT_FIRST"] = "0"
        if use_test_audio and "MIX_ROBO_VOCAL_FOCUS" not in os.environ:
            os.environ["MIX_ROBO_VOCAL_FOCUS"] = "1"

        if use_reastream:
            print(
                "[REASTREAM CONFIG] "
                f"host={args.reastream_host} port={args.reastream_port} "
                f"identifier={args.reastream_identifier!r}"
            )

        if use_test_audio and use_reastream:
            raise SystemExit("--test-audio nao pode ser usado junto com --reastream.")

        if use_reastream and args.loopback:
            raise SystemExit("--loopback nao pode ser usado junto com --reastream.")

        if use_test_audio and args.loopback:
            raise SystemExit("--test-audio nao pode ser usado junto com --loopback.")

        sd = None
        if not use_reastream and not use_test_audio:
            try:
                import sounddevice as sd
            except ImportError:
                raise SystemExit(
                    "sounddevice is required for live capture. Install it with: pip install sounddevice"
                )

        # If no explicit map is provided:
        # - In ReaStream mode, use None so mix_profile falls back to DEFAULT_STEM_TRACK_MAP
        #   (each band routed to its respective instrument track).
        # - In test-audio mode, do the same to emulate the default live ReaStream routing.
        # - In WASAPI/loopback mode, map all bands to the single live-track.
        if track_map is None:
            if use_reastream or use_test_audio:
                track_map = None  # let mix_profile.process() use DEFAULT_STEM_TRACK_MAP
            else:
                track_map = {
                    "sub": args.live_track,
                    "low": args.live_track,
                    "mid": args.live_track,
                    "vocal": args.live_track,
                    "highmid": args.live_track,
                    "air": args.live_track,
                }
 
        # Channel mapping allows treating each input channel as a separate bus.
        channel_map = None
        if args.channel_map:
            import ast
            import json

            raw = args.channel_map
            try:
                raw = json.loads(raw)
            except Exception:
                # Allow Python dict syntax like {0:14, 1:27} or single quotes for keys.
                raw = ast.literal_eval(raw)

            channel_map = {int(k): int(v) for k, v in raw.items()}

        def _select_wasapi_output_device():
            hostapis = sd.query_hostapis()
            for idx, info in enumerate(sd.query_devices()):
                host_name = hostapis[info["hostapi"]]["name"]
                if (
                    info["name"] == DEFAULT_WASAPI_DEVICE_NAME
                    and host_name == "Windows WASAPI"
                    and info["max_output_channels"] > 0
                ):
                    return idx
            return None

        device = None
        if not use_reastream and not use_test_audio:
            if args.device is not None:
                # allow numeric device index or name
                try:
                    device = int(args.device)
                except ValueError:
                    device = args.device
            elif args.loopback:
                device = _select_wasapi_output_device()

        if args.verbose:
            if use_reastream:
                print(
                    "Starting ReaStream UDP capture "
                    f"({args.reastream_host}:{args.reastream_port}, "
                    f"identifier={args.reastream_identifier!r})."
                )
            elif use_test_audio:
                print(f"Starting simulated live input from WAV: {args.test_audio}")
                print("[TEST MODE] CUT-FIRST disabled for calibration")
                print("[TEST MODE] VOCAL-FOCUS enabled for calibration")
            else:
                print(f"Starting live capture (device={device!r}).")
            print(f"Using track map: {track_map}")
            if channel_map is not None:
                print(f"Using channel map: {channel_map}")

        def _process_audio(audio, map_for_audio, sample_rate_override=None):
            effective_sr_base = int(sample_rate_override) if sample_rate_override else int(args.sr)
            correction = float(analysis_state.get("sr_correction_factor", 1.0))
            effective_sr = max(8000, min(192000, round(effective_sr_base * correction)))

            input_peak_hz, input_prominence = _estimate_input_peak_frequency(audio, effective_sr)
            if input_peak_hz is not None:
                channel_count = 1 if np.asarray(audio).ndim <= 1 else int(np.asarray(audio).shape[1])
                print(
                    f"[DIAG] INPUT FFT peak_hz={input_peak_hz:.1f} "
                    f"prominence={input_prominence:.1f} sr={effective_sr} channels={channel_count}"
                )

                # ── SR auto-calibration ─────────────────────────────────────────────
                cal_freq = float(getattr(args, "calibrate_freq", 0.0) or 0.0)
                if cal_freq > 0.0 and input_peak_hz is not None and float(input_prominence or 0.0) >= 3.0:
                    cal = analysis_state["_calibration"]
                    if not cal["done"]:
                        cal["peaks"].append(input_peak_hz)
                        n = len(cal["peaks"])
                        print(f"[SR-CAL] CALIBRATING ref_hz={cal_freq:.1f} n={n}/3")
                        if n >= 3:
                            measured = float(np.median(cal["peaks"]))
                            raw_factor = cal_freq / measured
                            # Octave-snap: pick closest power-of-2 if within ±15 %
                            snap_factor = raw_factor
                            snap_label = "raw"
                            for candidate in [0.25, 0.5, 1.0, 2.0, 4.0]:
                                if abs(raw_factor / candidate - 1.0) < 0.15:
                                    snap_factor = candidate
                                    snap_label = f"{candidate}x"
                                    break
                            analysis_state["sr_correction_factor"] = snap_factor
                            correction = snap_factor
                            cal["done"] = True
                            effective_sr = max(8000, min(192000, round(effective_sr_base * snap_factor)))
                            print(
                                f"[SR-CAL] DONE measured_hz={measured:.1f} ref_hz={cal_freq:.1f} "
                                f"raw={raw_factor:.4f} factor={snap_factor:.4f} snap={snap_label} "
                                f"new_sr={effective_sr}"
                            )

                # ── Emit ongoing SR-CAL status when a correction is active ──────────
                if correction != 1.0:
                    snap_label = "raw"
                    for candidate in [0.25, 0.5, 1.0, 2.0, 4.0]:
                        if abs(correction / candidate - 1.0) < 0.01:
                            snap_label = f"{candidate}x"
                            break
                    print(
                        f"[SR-CAL] factor={correction:.4f} base_sr={effective_sr_base} "
                        f"eff_sr={effective_sr} snap={snap_label}"
                    )
                # ───────────────────────────────────────────────────────────────────

            if args.verbose:
                print(
                    f"[_PROCESS_AUDIO] Chamando process() com audio shape {audio.shape}, "
                    f"sr={effective_sr}, profile '{args.profile}'"
                )
            # Pass profiles=None so mix_profile._load_profiles() rereads profiles.json
            # on every cycle, picking up any changes made while the script is running.
            process(
                audio,
                sample_rate=effective_sr,
                profile_name=args.profile,
                stem_track_map=map_for_audio,
                profiles=None,
                verbose=args.verbose,
            )
            if args.verbose:
                print(f"[_PROCESS_AUDIO] Retornou de process()")

        min_samples_for_analysis = max(1024, int(args.sr * 0.5))
        analysis_state = {
            "stereo_chunks": [],
            "stereo_samples": 0,
            "stereo_last": time.monotonic(),
            "channel_chunks": {},
            "channel_samples": {},
            "channel_last": {},
            "stream_sr": float(args.sr),
            "sr_correction_factor": 1.0,
            "_calibration": {"peaks": [], "done": False},
        }

        if args.verbose:
            print(
                f"[ANALYSIS] Janela de {args.analysis_interval:.1f}s "
                f"(min {min_samples_for_analysis} samples) para cada ajuste."
            )

        def _process_pending_windows(now, force=False):
            active_sr = float(analysis_state.get("stream_sr") or args.sr)
            min_samples_required = max(1024, int(active_sr * 0.5))
            if channel_map:
                for ch_idx, track in channel_map.items():
                    analysis_state["channel_last"].setdefault(ch_idx, now)

                    elapsed = now - analysis_state["channel_last"][ch_idx]
                    if not force and elapsed < args.analysis_interval:
                        continue
                    if analysis_state["channel_samples"].get(ch_idx, 0) < min_samples_required:
                        continue

                    window_audio = np.concatenate(analysis_state["channel_chunks"][ch_idx], axis=0)
                    analysis_state["channel_chunks"][ch_idx] = []
                    analysis_state["channel_samples"][ch_idx] = 0
                    analysis_state["channel_last"][ch_idx] = now

                    band_map = {
                        "sub": track,
                        "low": track,
                        "mid": track,
                        "vocal": track,
                        "highmid": track,
                        "air": track,
                    }
                    if args.verbose:
                        print(
                            f"[PROCESS] Janela canal {ch_idx} pronta "
                            f"({window_audio.shape[0]} samples) -> track {track}"
                        )
                    _process_audio(window_audio, band_map, sample_rate_override=active_sr)
                return

            elapsed = now - analysis_state["stereo_last"]
            if not force and elapsed < args.analysis_interval:
                return
            if analysis_state["stereo_samples"] < min_samples_required:
                return

            window_audio = np.concatenate(analysis_state["stereo_chunks"], axis=0)
            analysis_state["stereo_chunks"] = []
            analysis_state["stereo_samples"] = 0
            analysis_state["stereo_last"] = now

            if args.verbose:
                print(
                    f"[PROCESS] Janela pronta ({window_audio.shape[0]} samples). "
                    f"Processando profile '{args.profile}' em {'stereo' if window_audio.ndim > 1 else 'mono'}"
                )
            _process_audio(window_audio, track_map, sample_rate_override=active_sr)

        def _buffer_and_maybe_process(frames, now=None, force=False, stream_sr=None):
            current_time = time.monotonic() if now is None else float(now)

            if stream_sr is not None:
                try:
                    parsed_sr = float(stream_sr)
                except (TypeError, ValueError):
                    parsed_sr = 0.0
                if 8000.0 <= parsed_sr <= 192000.0:
                    analysis_state["stream_sr"] = parsed_sr

            if frames is not None:
                frame_array = np.asarray(frames)
                if frame_array.ndim == 1:
                    frame_array = frame_array.reshape(-1, 1)
                if frame_array.size > 0 and frame_array.shape[0] > 0:
                    # Copy to detach from callback/file buffer lifecycle.
                    frame_array = np.array(frame_array, dtype=np.float32, copy=True)

                    if channel_map:
                        for ch_idx in channel_map.keys():
                            if ch_idx < 0 or ch_idx >= frame_array.shape[1]:
                                continue
                            channel_audio = frame_array[:, ch_idx]
                            analysis_state["channel_chunks"].setdefault(ch_idx, []).append(channel_audio)
                            analysis_state["channel_samples"][ch_idx] = (
                                analysis_state["channel_samples"].get(ch_idx, 0) + channel_audio.shape[0]
                            )
                    else:
                        analysis_state["stereo_chunks"].append(frame_array)
                        analysis_state["stereo_samples"] += frame_array.shape[0]

            _process_pending_windows(current_time, force=force)

        def _callback(indata, frames, time_info, status):
            if status and args.verbose:
                print("Audio status:", status)

            _buffer_and_maybe_process(indata)

        if use_test_audio:
            test_audio = load_test_audio(args.test_audio, sample_rate=args.sr, channels=args.channels)
            total_samples = int(test_audio.shape[0])
            duration_seconds = (total_samples / float(args.sr)) if args.sr > 0 else 0.0

            if total_samples <= 0:
                raise SystemExit(f"Arquivo de teste sem audio utilizavel: {args.test_audio}")

            try:
                print(
                    f"Simulating live audio from {args.test_audio} "
                    f"({duration_seconds:.2f}s, {test_audio.shape[1]} ch)... press Ctrl+C to stop."
                )
                simulated_now = time.monotonic()
                for start in range(0, total_samples, max(1, args.blocksize)):
                    stop = min(total_samples, start + max(1, args.blocksize))
                    chunk = test_audio[start:stop]
                    _buffer_and_maybe_process(chunk, now=simulated_now)

                    chunk_duration = chunk.shape[0] / float(args.sr)
                    simulated_now += chunk_duration

                    if args.test_speed and args.test_speed > 0.0:
                        time.sleep(chunk_duration / args.test_speed)

                _buffer_and_maybe_process(None, now=simulated_now + args.analysis_interval, force=True)
                print("Simulated live audio finished.")
            except KeyboardInterrupt:
                print("Simulated live capture stopped.")
        elif use_reastream:
            ident = (args.reastream_identifier or "").strip()
            if ident.lower() == "any":
                ident = ""

            def _emit_reastream_status(state, detail):
                print(f"[REASTREAM STATUS] {state} {detail}")

            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # Try to allow coexistence with other listeners when the OS supports it.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except OSError:
                pass
            
            try:
                # Enable broadcast for both unicast and broadcast packets
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass

            # Set timeout so recvfrom() doesn't block forever, allowing Ctrl+C to work on Windows
            sock.settimeout(0.5)

            bind_errors = []
            bound_host = None
            for bind_host in _candidate_bind_hosts(args.reastream_host):
                try:
                    sock.bind((bind_host, args.reastream_port))
                    bound_host = bind_host
                    _emit_reastream_status("BOUND", f"{bind_host}:{args.reastream_port}")
                    if args.verbose:
                        print(f"[REASTREAM] Bindado em {bind_host}:{args.reastream_port}")
                        if bind_host != args.reastream_host:
                            print(
                                f"[REASTREAM] Host solicitado {args.reastream_host} indisponivel; "
                                f"usando fallback {bind_host}."
                            )
                    break
                except OSError as exc:
                    if getattr(exc, "winerror", None) == 10048:
                        sock.close()
                        raise SystemExit(
                            "Nao foi possivel abrir a porta UDP 58710 do ReaStream porque ela ja esta em uso. "
                            "Feche/desative instancias de ReaStream em modo Receive no REAPER (ou outro app que esteja escutando nessa porta) "
                            "e execute novamente."
                        )
                    bind_errors.append((bind_host, exc))

            if bound_host is None:
                sock.close()
                details = "; ".join(
                    f"{host}: [WinError {getattr(exc, 'winerror', 'n/a')}] {exc}"
                    for host, exc in bind_errors
                )
                raise SystemExit(f"Nao foi possivel bindar o ReaStream em nenhum host local: {details}")

            try:
                print("Capturing ReaStream audio... press Ctrl+C to stop.")
                packet_count = 0
                last_packet_time = None
                stream_state = "waiting"
                while True:
                    try:
                        data, _ = sock.recvfrom(max(1024, args.reastream_buffer_size))
                    except TimeoutError:
                        if last_packet_time is None and stream_state != "waiting":
                            stream_state = "waiting"
                            _emit_reastream_status("WAITING", "No packets yet")
                        elif last_packet_time is not None and (time.monotonic() - last_packet_time) > 2.0 and stream_state != "stalled":
                            stream_state = "stalled"
                            _emit_reastream_status("STALL", "No packets for >2s")
                        continue
                    packet_count += 1
                    last_packet_time = time.monotonic()
                    if stream_state != "streaming":
                        stream_state = "streaming"
                        _emit_reastream_status("STREAMING", f"packets={packet_count}")
                    if args.verbose:
                        print(f"[UDP RECV #{packet_count}] Pacote de {len(data)} bytes recebido")

                    frames, packet_sr = _decode_reastream_frames(
                        data,
                        args.channels,
                        identifier=ident or None,
                        verbose=args.verbose,
                    )

                    if frames is None:
                        if args.verbose:
                            print(f"[DECODE #{packet_count}] Falha ao decodificar frames. Pacote rejeitado.")
                        continue

                    if args.verbose:
                        peak = np.max(np.abs(frames))
                        sr_text = f"{packet_sr:.0f}" if packet_sr is not None else "unknown"
                        print(
                            f"[DECODE OK #{packet_count}] {frames.shape[0]} samples, {frames.shape[1]} channels, "
                            f"sr={sr_text}, peak: {peak:.4f}"
                        )

                    if packet_sr is not None and abs(float(packet_sr) - float(args.sr)) > 1.0:
                        print(
                            f"[REASTREAM SR] packet_sr={packet_sr:.1f}Hz "
                            f"configured_sr={float(args.sr):.1f}Hz"
                        )

                    _buffer_and_maybe_process(frames, stream_sr=packet_sr)
            except KeyboardInterrupt:
                print("Live capture stopped.")
            finally:
                sock.close()
        else:
            # If loopback is requested, we capture from an output device via WASAPI.
            stream_kwargs = {
                "channels": args.channels,
                "samplerate": args.sr,
                "blocksize": args.blocksize,
                "callback": _callback,
            }

            if args.loopback:
                if not hasattr(sd, "WasapiSettings"):
                    raise SystemExit(
                        "Loopback capture requires WASAPI support (Windows)."
                    )

                if device is None:
                    raise SystemExit(
                        "Nenhum dispositivo Windows WASAPI compativel foi encontrado para loopback. "
                        f"Esperado: {DEFAULT_WASAPI_DEVICE_NAME}."
                    )

                device_info = sd.query_devices(device)
                if device_info["max_output_channels"] <= 0:
                    raise SystemExit(
                        "Loopback WASAPI precisa de um dispositivo de saida. "
                        f"Dispositivo atual: {device_info['name']}"
                    )

                wasapi_settings = sd.WasapiSettings(auto_convert=True)

                stream_kwargs["dtype"] = "float32"
                stream_kwargs["extra_settings"] = wasapi_settings

                # This sounddevice build does not expose explicit loopback mode.
                # Keep a clear failure instead of a cryptic PortAudio channel error.
                if device_info["max_input_channels"] <= 0:
                    raise SystemExit(
                        "O dispositivo WASAPI selecionado nao oferece captura loopback nesta versao do sounddevice. "
                        "Use um device de entrada exposto pelo ReaRoute/ReaStream ou instale um backend com suporte a loopback WASAPI."
                    )

            stream_kwargs["device"] = device

            try:
                with sd.InputStream(**stream_kwargs):
                    print("Capturing live audio... press Ctrl+C to stop.")
                    while True:
                        time.sleep(1)
            except KeyboardInterrupt:
                print("Live capture stopped.")
        return

    stems_dir = args.stems_dir or os.path.join("learning", "separated", args.profile)
    stems = load_stems(stems_dir, sample_rate=args.sr)

    # If user passed a directory but it turned out empty, try the default folder.
    if not stems and args.stems_dir:
        fallback = os.path.join("learning", "separated", args.profile)
        if fallback != stems_dir:
            stems = load_stems(fallback, sample_rate=args.sr)
            if stems:
                print(f"No stems found in {stems_dir}; using {fallback} instead.")
                stems_dir = fallback

    if not stems:
        raise SystemExit(f"No stems found in {stems_dir}")

    process_stems(
        stems,
        profile_name=args.profile,
        stem_track_map=track_map,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
