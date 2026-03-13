"""Run the mix profile logic on separated stems and send OSC commands.

This script assumes you already have separated stems (e.g., produced by Demucs) in a
folder structure like:

  separated/<track-name>/<stem>.wav

It will load each stem, compute tonal band values, compare to the profile, and then
send OSC volume updates via the existing `mix_profile.process_stems` logic.

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
        return None

    # --- Structured parse (standard ReaStream format) ---
    null_pos = packet.find(b"\x00")
    if null_pos >= 0:
        try:
            pkt_ident = packet[:null_pos].decode("ascii", errors="replace")
            meta_start = null_pos + 1
            if len(packet) >= meta_start + 8:
                sample_rate, num_ch, num_samples = struct.unpack_from("<fHH", packet, meta_start)
                audio_start = meta_start + 8
                expected = num_ch * num_samples * 4
                if (
                    num_ch > 0
                    and num_samples > 0
                    and 8000 <= sample_rate <= 192000
                    and len(packet) >= audio_start + expected
                ):
                    # Identifier filter
                    if identifier and identifier.lower() not in pkt_ident.lower():
                        if verbose:
                            print(f"[DECODE] Identifier mismatch: packet='{pkt_ident}' expected='{identifier}'")
                        return None

                    # Slice into a new bytes object so numpy offset=0 avoids alignment issues
                    audio_bytes = packet[audio_start:audio_start + expected]
                    audio_data = np.frombuffer(audio_bytes, dtype=np.float32)
                    if np.isfinite(audio_data).all():
                        peak = float(np.max(np.abs(audio_data)))
                        if peak >= 1e-7:
                            out_ch = min(num_ch, channels)
                            frames = audio_data.reshape(num_samples, num_ch)[:, :out_ch]
                            if verbose:
                                print(
                                    f"[DECODE STRUCT] ident='{pkt_ident}' sr={sample_rate:.0f} "
                                    f"ch={num_ch} samples={num_samples} peak={peak:.4f}"
                                )
                            return frames
        except Exception:
            pass

    # --- Fallback: brute-force scan (step=1 to catch any byte alignment) ---
    if identifier:
        ident_bytes = identifier.encode("utf-8", errors="ignore")
        if ident_bytes and ident_bytes not in packet:
            if verbose:
                print(f"[DECODE] Identifier bytes not found in packet, rejecting.")
            return None

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
        return samples[:usable].reshape(-1, channels)

    return None


def load_stems(stems_dir, stem_names=None, sample_rate=44100):
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
    parser.add_argument("--sr", type=int, default=44100, help="Sample rate to use")

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
        help="Print info about stems and OSC commands.",
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

    if args.live:
        use_reastream = args.reastream

        if use_reastream and args.loopback:
            raise SystemExit("--loopback nao pode ser usado junto com --reastream.")

        sd = None
        if not use_reastream:
            try:
                import sounddevice as sd
            except ImportError:
                raise SystemExit(
                    "sounddevice is required for live capture. Install it with: pip install sounddevice"
                )

        # If no explicit map is provided:
        # - In ReaStream mode, use None so mix_profile falls back to DEFAULT_STEM_TRACK_MAP
        #   (each band routed to its respective instrument track).
        # - In WASAPI/loopback mode, map all bands to the single live-track.
        if track_map is None:
            if use_reastream:
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
        if not use_reastream:
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
            else:
                print(f"Starting live capture (device={device!r}).")
            print(f"Using track map: {track_map}")
            if channel_map is not None:
                print(f"Using channel map: {channel_map}")

        profiles = None
        if args.profile:
            import json

            try:
                with open("learning/profiles.json", "r", encoding="utf-8") as f:
                    profiles = json.load(f)
            except FileNotFoundError:
                raise SystemExit("No profiles file found at learning/profiles.json")

        def _process_audio(audio, map_for_audio):
            if args.verbose:
                print(f"[_PROCESS_AUDIO] Chamando process() com audio shape {audio.shape}, profile '{args.profile}'")
            process(
                audio,
                sample_rate=args.sr,
                profile_name=args.profile,
                stem_track_map=map_for_audio,
                profiles=profiles,
                verbose=args.verbose,
            )
            if args.verbose:
                print(f"[_PROCESS_AUDIO] Retornou de process()")

        def _callback(indata, frames, time_info, status):
            if status and args.verbose:
                print("Audio status:", status)

            # indata shape: (frames, channels)
            if indata.ndim == 1:
                _process_audio(indata, track_map)
                return

            if channel_map:
                # process each mapped channel separately
                for ch_idx, track in channel_map.items():
                    if ch_idx < 0 or ch_idx >= indata.shape[1]:
                        continue
                    channel_audio = indata[:, ch_idx]
                    band_map = {
                        "sub": track,
                        "low": track,
                        "mid": track,
                        "vocal": track,
                        "highmid": track,
                        "air": track,
                    }
                    _process_audio(channel_audio, band_map)
            else:
                # Keep stereo mix when available (no forced mono downmix).
                _process_audio(indata, track_map)

        if use_reastream:
            ident = (args.reastream_identifier or "").strip()
            if ident.lower() == "any":
                ident = ""

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

            # On Windows, binding to 0.0.0.0 doesn't properly receive localhost traffic.
            # Try binding to the specified host first; if it fails on Windows with 0.0.0.0,
            # also try 127.0.0.1 for local broadcast compatibility.
            bind_host = args.reastream_host
            try:
                sock.bind((bind_host, args.reastream_port))
                if args.verbose:
                    print(f"[REASTREAM] Bindado em {bind_host}:{args.reastream_port}")
            except OSError as exc:
                if getattr(exc, "winerror", None) == 10048:
                    sock.close()
                    raise SystemExit(
                        "Nao foi possivel abrir a porta UDP 58710 do ReaStream porque ela ja esta em uso. "
                        "Feche/desative instancias de ReaStream em modo Receive no REAPER (ou outro app que esteja escutando nessa porta) "
                        "e execute novamente."
                    )
                # On Windows, if binding to 0.0.0.0 fails for other reasons, try localhost
                if bind_host == "0.0.0.0":
                    sock.close()
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    except OSError:
                        pass
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    except OSError:
                        pass
                    try:
                        sock.bind(("127.0.0.1", args.reastream_port))
                        if args.verbose:
                            print(f"[REASTREAM] Nao conseguiu bind em 0.0.0.0, bindado em 127.0.0.1:{args.reastream_port} para broadcast local")
                    except OSError as exc2:
                        sock.close()
                        raise exc2
                else:
                    sock.close()
                    raise

            try:
                print("Capturing ReaStream audio... press Ctrl+C to stop.")
                packet_count = 0
                min_samples_for_analysis = max(1024, int(args.sr * 0.5))
                mono_buffer = []
                buffered_samples = 0
                last_analysis_time = time.monotonic()
                if args.verbose:
                    print(
                        f"[ANALYSIS] Janela de {args.analysis_interval:.1f}s "
                        f"(min {min_samples_for_analysis} samples) para cada ajuste."
                    )
                while True:
                    try:
                        data, _ = sock.recvfrom(max(1024, args.reastream_buffer_size))
                    except TimeoutError:
                        continue
                    packet_count += 1
                    if args.verbose:
                        print(f"[UDP RECV #{packet_count}] Pacote de {len(data)} bytes recebido")

                    frames = _decode_reastream_frames(data, args.channels, identifier=ident or None, verbose=args.verbose)

                    if frames is None:
                        if args.verbose:
                            print(f"[DECODE #{packet_count}] Falha ao decodificar frames. Pacote rejeitado.")
                        continue

                    if args.verbose:
                        peak = np.max(np.abs(frames))
                        print(f"[DECODE OK #{packet_count}] {frames.shape[0]} samples, {frames.shape[1]} channels, peak: {peak:.4f}")

                    if channel_map:
                        # process each mapped channel separately
                        for ch_idx, track in channel_map.items():
                            if ch_idx < 0 or ch_idx >= frames.shape[1]:
                                continue
                            channel_audio = frames[:, ch_idx]
                            band_map = {
                                "sub": track,
                                "low": track,
                                "mid": track,
                                "vocal": track,
                                "highmid": track,
                                "air": track,
                            }
                            if args.verbose:
                                print(f"[CHANNEL] Processando canal {ch_idx} ? track {track}")
                            _process_audio(channel_audio, band_map)
                    else:
                        # Keep stereo and accumulate a time window before each decision.
                        mono_buffer.append(frames)
                        buffered_samples += frames.shape[0]

                        elapsed = time.monotonic() - last_analysis_time
                        if elapsed < args.analysis_interval or buffered_samples < min_samples_for_analysis:
                            continue

                        window_audio = np.concatenate(mono_buffer, axis=0)
                        mono_buffer.clear()
                        buffered_samples = 0
                        last_analysis_time = time.monotonic()

                        if args.verbose:
                            print(
                                f"[PROCESS] Janela pronta ({window_audio.shape[0]} samples). "
                                f"Processando profile '{args.profile}' em {'stereo' if window_audio.ndim > 1 else 'mono'}"
                            )
                        _process_audio(window_audio, track_map)
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
