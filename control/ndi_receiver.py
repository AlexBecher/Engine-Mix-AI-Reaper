"""Minimal NDI receiver implementation using the NewTek NDI C API.

This is intentionally small and dependency-free: it uses ctypes to load the
NDI runtime DLL and exposes a simple receiver API.

Requirements:
- NewTek NDI Runtime installed (provides Processing.NDI.Lib.x64.dll / x86)
- On Windows, the DLL must be on the PATH (usually installed by the runtime)

Usage:
    from control.ndi_receiver import NDIReceiver

    recv = NDIReceiver(source_name="My NDI Source")
    recv.start()
    while True:
        audio_frame = recv.capture(timeout_ms=1000)
        if audio_frame is None:
            continue
        audio = audio_frame.to_mono()  # numpy array float32
        ...

    recv.close()
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


class NDILibraryLoadError(Exception):
    pass


def _load_ndi_library() -> ctypes.CDLL:
    # Try to load the NDI runtime library.
    #
    # NDI uses the Windows stdcall calling convention. On Windows we must use
    # ctypes.WinDLL instead of ctypes.CDLL to match that convention, otherwise
    # callbacks/return values can be corrupted (e.g. huge "count" values).
    loader = ctypes.WinDLL if os.name == "nt" else ctypes.CDLL

    # Supported NDI runtime/driver DLL names. OBS-ndi (obs-ndi.dll) can also provide the NDI API
    # and is useful when the NewTek runtime is not installed.
    candidates = [
    
        "obs-ndi.dll",
    ]

    # Allow overriding location via environment variable (useful when PATH isn't updated).
    # You can point this to either a directory containing the DLL, or the DLL file itself.
    ndi_path = "C:\\Program Files\\obs-studio\\obs-plugins\\64bit\\"
    if ndi_path:
        # If the env var is a file path, try loading it directly.
        if os.path.isfile(ndi_path):
            try:
                return loader(ndi_path)
            except Exception:
                pass

        for name in candidates:
            try:
                return loader(os.path.join(ndi_path, name))
            except Exception:
                continue

    for name in candidates:
        try:
            return loader(name)
        except Exception:
            continue

    # Try common Windows install locations for NewTek NDI Runtime.
    # https://www.ndi.tv/sdk/
    common_paths = [
     
        # NDI 6+ Tools runtime folder
       "C:\\Program Files\\obs-studio\\obs-plugins\\64bit"
    ]
    for base in common_paths:
        if not base:
            continue
        for name in candidates:
            path = os.path.join(base, name)
            try:
                return loader(path)
            except Exception:
                continue

    # Try to resolve via ctypes.util.find_library
    for name in ["Processing.NDI.Lib.x64", "Processing.NDI.Lib.x86", "NDI"]:
        path = ctypes.util.find_library(name)
        if path:
            try:
                return loader(path)
            except Exception:
                continue

    raise NDILibraryLoadError(
        "Could not load NDI runtime library. Make sure the NewTek NDI Runtime" \
        " is installed and that the DLL is on your PATH (e.g., Processing.NDI.Lib.x64.dll)."
    )


# C definitions pulled from NDI 5 SDK headers (minimal set for audio capture).

class NDIlib_find_create_t(ctypes.Structure):
    _fields_ = [
        ("p_groups", ctypes.c_char_p),
    ]


class NDIlib_source_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_url_address", ctypes.c_char_p),
    ]


class NDIlib_source_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_url_address", ctypes.c_char_p),
    ]


class NDIlib_recv_create_v3_t(ctypes.Structure):
    _fields_ = [
        ("color_format", ctypes.c_int),
        ("allow_video_fields", ctypes.c_bool),
        ("bandwidth", ctypes.c_int),
        ("p_ndi_name", ctypes.POINTER(NDIlib_source_t)),
        ("p_ndi_recv_name", ctypes.c_char_p),
    ]


class NDIlib_audio_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("sample_rate", ctypes.c_int),
        ("no_channels", ctypes.c_int),
        ("no_samples", ctypes.c_int),
        ("no_samples_per_channel", ctypes.c_int),
        ("no_bytes_per_sample", ctypes.c_int),
        ("p_data", ctypes.POINTER(ctypes.c_float)),
        ("timecode", ctypes.c_longlong),
    ]


class NDIlib_frame_type_e(ctypes.c_int):
    pass


# Frame type constants (from NDI SDK)
NDIlib_frame_type_video = 1
NDIlib_frame_type_audio = 2
NDIlib_frame_type_metadata = 3
NDIlib_frame_type_none = 0


@dataclass
class AudioFrame:
    data: np.ndarray
    sample_rate: int
    channels: int
    timecode: int

    def to_mono(self) -> np.ndarray:
        if self.channels == 1:
            return self.data
        return self.data.mean(axis=1)


class NDIReceiver:
    def __init__(self, source_name: str, timeout_ms: int = 5000, verbose: bool = False):
        """Create an NDI receiver for the given source name."""
        self.source_name = source_name
        self.timeout_ms = timeout_ms
        self.verbose = verbose
        self._lib = _load_ndi_library()

        # Initialize NDI.
        self._lib.NDIlib_initialize.restype = ctypes.c_bool
        if not self._lib.NDIlib_initialize():
            raise NDILibraryLoadError("NDI library initialization failed")

        # Configure function prototypes (support multiple SDK versions)
        def _find_func(names, argtypes, restype, required=True):
            for n in names:
                if hasattr(self._lib, n):
                    func = getattr(self._lib, n)
                    func.argtypes = argtypes
                    func.restype = restype
                    return func
            if required:
                raise NDILibraryLoadError(
                    f"NDI runtime is missing required function(s): {names}. "
                    f"Loaded library: {getattr(self._lib, '_name', '<unknown>')}"
                )

            # If optional, return a no-op function
            def _noop(*args, **kwargs):
                return None

            return _noop

        self._find_create = _find_func(
            ["NDIlib_find_create_v3", "NDIlib_find_create_v2", "NDIlib_find_create"],
            [ctypes.POINTER(NDIlib_find_create_t)],
            ctypes.c_void_p,
        )

        self._find_wait_for_sources = _find_func(
            ["NDIlib_find_wait_for_sources"],
            [ctypes.c_void_p, ctypes.c_uint32],
            ctypes.c_bool,
        )

        self._find_get_current_sources = _find_func(
            ["NDIlib_find_get_current_sources"],
            [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)],
            ctypes.POINTER(NDIlib_source_t),
        )

        self._find_destroy = _find_func(
            ["NDIlib_find_destroy"],
            [ctypes.c_void_p],
            None,
        )

        self._recv_create = _find_func(
            ["NDIlib_recv_create_v3", "NDIlib_recv_create_v2", "NDIlib_recv_create"],
            [ctypes.POINTER(NDIlib_recv_create_v3_t)],
            ctypes.c_void_p,
        )

        self._recv_destroy = _find_func(
            ["NDIlib_recv_destroy"],
            [ctypes.c_void_p],
            None,
        )

        self._lib.NDIlib_destroy.argtypes = []
        self._lib.NDIlib_destroy.restype = None

        self._recv_connect = _find_func(
            ["NDIlib_recv_connect"],
            [ctypes.c_void_p, ctypes.POINTER(NDIlib_source_t)],
            ctypes.c_bool,
        )

        self._recv_disconnect = _find_func(
            ["NDIlib_recv_disconnect"],
            [ctypes.c_void_p],
            None,
            required=False,
        )

        self._recv_capture = _find_func(
            ["NDIlib_recv_capture_v2", "NDIlib_recv_capture"],
            [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(NDIlib_audio_frame_v2_t),
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_uint32,
            ],
            ctypes.c_int,
        )

        self._recv_free_audio = _find_func(
            ["NDIlib_recv_free_audio"],
            [ctypes.c_void_p, ctypes.POINTER(NDIlib_audio_frame_v2_t)],
            None,
        )

        self._find = None
        self._recv = None
        self._source = None
        # Note: existing code references self._lib.NDIlib_find_create_v3 and friends.
        # We now use wrapper methods that support multiple SDK function versions.

    def _find_source(self) -> Optional[NDIlib_source_t]:
        # Create the finder
        find_settings = NDIlib_find_create_t(p_groups=None)
        self._find = self._find_create(ctypes.byref(find_settings))
        deadline = time.time() + (self.timeout_ms / 1000.0)

        while time.time() < deadline:
            self._find_wait_for_sources(self._find, 500)
            count = ctypes.c_uint32()
            src_ptr = self._find_get_current_sources(self._find, ctypes.byref(count))

            # Some runtimes return count=0 and/or a NULL pointer; guard against it.
            # Also protect against invalid pointer/count values coming from broken runtimes.
            ptr_val = ctypes.cast(src_ptr, ctypes.c_void_p).value if src_ptr else None
            count_val = int(count.value or 0)
            if count_val <= 0 or not ptr_val or ptr_val < 0x1000 or count_val > 1024:
                if self.verbose:
                    print(
                        f"[NDIReceiver] found 0 sources (count={count_val}, src_ptr={ptr_val})"
                    )
                time.sleep(0.1)
                continue

            sources = []
            for idx in range(count_val):
                src = src_ptr[idx]
                name = src.p_ndi_name.decode("utf-8") if src.p_ndi_name else ""
                sources.append(name)
                if not self.source_name or self.source_name in name:
                    if self.verbose:
                        print(f"[NDIReceiver] matched source: {name}")
                    return src

            if self.verbose:
                print(f"[NDIReceiver] available sources: {sources}")
            time.sleep(0.1)

        return None

    def list_sources(self, timeout_ms: Optional[int] = None) -> List[str]:
        """Return a list of available NDI source names."""
        timeout_ms = timeout_ms or self.timeout_ms
        find_settings = NDIlib_find_create_t(p_groups=None)
        find = self._find_create(ctypes.byref(find_settings))
        deadline = time.time() + (timeout_ms / 1000.0)
        sources: List[str] = []

        while time.time() < deadline:
            result = self._find_wait_for_sources(find, 500)
            count = ctypes.c_uint32()
            src_ptr = self._find_get_current_sources(find, ctypes.byref(count))

            ptr_val = ctypes.cast(src_ptr, ctypes.c_void_p).value if src_ptr else None
            count_val = int(count.value or 0)
            if self.verbose:
                print(
                    f"[NDIReceiver] wait_for_sources returned {result}, count={count_val}, src_ptr={ptr_val}"
                )

            # Protect against broken runtimes returning invalid pointer/count values.
            if count_val <= 0 or not ptr_val or ptr_val < 0x1000 or count_val > 1024:
                if self.verbose:
                    print(
                        f"[NDIReceiver] ignoring invalid source list (count={count_val}, src_ptr={ptr_val})"
                    )
                time.sleep(0.1)
                continue

            for idx in range(count_val):
                try:
                    src = src_ptr[idx]
                except ValueError:
                    # Bad pointer/index; stop trying
                    if self.verbose:
                        print(f"[NDIReceiver] invalid src_ptr index {idx}")
                    break
                if src.p_ndi_name:
                    name = src.p_ndi_name.decode("utf-8")
                    if name not in sources:
                        sources.append(name)
            if sources:
                break
            time.sleep(0.1)

        self._find_destroy(find)
        return sources

    def start(self):
        if self._recv is not None:
            return

        source = self._find_source()
        if not source:
            if self.source_name:
                # Some runtimes may not return sources via the finder; try using the
                # provided name directly to create a source descriptor.
                if self.verbose:
                    print(f"[NDIReceiver] falling back to direct source creation for '{self.source_name}'")
                src = NDIlib_source_t()
                src.p_ndi_name = self.source_name.encode("utf-8")
                src.p_url_address = None
                source = src
            else:
                raise RuntimeError(f"NDI source '{self.source_name}' not found")

        self._source = source

        create_settings = NDIlib_recv_create_v3_t()
        create_settings.p_ndi_name = ctypes.pointer(source)
        create_settings.color_format = 0
        create_settings.allow_video_fields = False
        create_settings.bandwidth = 0
        create_settings.p_ndi_recv_name = None

        self._recv = self._recv_create(ctypes.byref(create_settings))
        if not self._recv:
            raise RuntimeError("Failed to create NDI receiver")

    def capture(self, timeout_ms: int = 1000) -> Optional[AudioFrame]:
        if not self._recv:
            raise RuntimeError("NDI receiver not started")

        audio_frame = NDIlib_audio_frame_v2_t()
        video_ptr = ctypes.c_void_p()
        metadata_ptr = ctypes.c_void_p()

        frame_type = self._recv_capture(
            self._recv,
            ctypes.byref(video_ptr),
            ctypes.byref(audio_frame),
            ctypes.byref(metadata_ptr),
            int(timeout_ms),
        )

        if frame_type != NDIlib_frame_type_audio:
            return None

        if not audio_frame.p_data or audio_frame.no_samples <= 0 or audio_frame.no_channels <= 0:
            self._recv_free_audio(self._recv, ctypes.byref(audio_frame))
            return None

        total_samples = audio_frame.no_samples * audio_frame.no_channels
        array_type = ctypes.c_float * total_samples
        buffer = ctypes.cast(audio_frame.p_data, ctypes.POINTER(array_type)).contents
        np_data = np.ctypeslib.as_array(buffer)
        np_data = np_data.reshape((audio_frame.no_samples, audio_frame.no_channels))

        result = AudioFrame(
            data=np_data,
            sample_rate=audio_frame.sample_rate,
            channels=audio_frame.no_channels,
            timecode=audio_frame.timecode,
        )

        self._recv_free_audio(self._recv, ctypes.byref(audio_frame))
        return result

    def close(self):
        if self._recv:
            self._recv_destroy(self._recv)
            self._recv = None
        if self._find:
            self._find_destroy(self._find)
            self._find = None
        try:
            self._lib.NDIlib_destroy()
        except Exception:
            pass
