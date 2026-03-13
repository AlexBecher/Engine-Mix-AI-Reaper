"""Train and save a tonal profile from an audio file.

This script takes a reference audio file (e.g., "audio.wav"), separates it into stems
(using Spleeter), computes tonal band averages for each stem, and saves a profile
that can later be used by `mix_profile.py`.

Example:
    python learning\train_profile.py audio.wav --output learning\profiles.json --name mymix

The resulting JSON will contain:
    {
        "mymix": {
            "vocals": {"sub": ..., "low": ..., ...},
            "drums": {...},
            "bass": {...},
            "piano": {...},
            "other": {...}
        }
    }

NOTE: Spleeter's 5-stem model provides stems: vocals, drums, bass, piano, other.
"""

import argparse
import json
import os
import subprocess
import sys

import librosa
import numpy as np

# Allow execution both as a module (python -m learning.train_profile) and as a script
# (python learning\train_profile.py).
try:
    from learning.reference_loader import load_reference
except ImportError:
    from reference_loader import load_reference

def _separate_with_demucs(input_file, output_dir, model_name="htdemucs", sample_rate=44100, device="cpu"):
    """Separate an audio mix into stems using Demucs (without torchaudio/ffmpeg)."""

    try:
        import torch
        import soundfile as sf
        from demucs.apply import apply_model
        from demucs.pretrained import get_model
    except ImportError as exc:
        raise RuntimeError(
            "Demucs separation requires optional dependencies. Install with: "
            "pip install torch demucs soundfile"
        ) from exc

    os.makedirs(output_dir, exist_ok=True)

    # Load audio with librosa (works without torchcodec/ffmpeg)
    mix, sr = librosa.load(input_file, sr=sample_rate, mono=True)

    # Demucs expects 2-channel audio; duplicate mono if needed.
    mix = np.stack([mix, mix], axis=0)

    # Prepare tensor: (batch, channels, length)
    mix_t = torch.from_numpy(mix).float().unsqueeze(0)

    model = get_model(model_name)
    model.to(device)
    model.eval()

    with torch.no_grad():
        separated = apply_model(
            model,
            mix_t,
            device=device,
            progress=False,
            split=True,
            overlap=0.25,
        )

    # separated: (batch=1, sources, channels, length)
    separated = separated[0].cpu().numpy()
    sources = model.sources

    for idx, source in enumerate(sources):
        out = separated[idx]  # (channels, length)
        out = out.T  # (length, channels)
        sf.write(os.path.join(output_dir, f"{source}.wav"), out, sample_rate)

    return {s: os.path.join(output_dir, f"{s}.wav") for s in sources}


def train_from_audio(input_file, output_file, profile_name, sample_rate=44100, separate=False, demucs_model="htdemucs"):
    """Generate a tonal profile from a reference audio file.

    By default, it creates a single "master" profile from the full mix.
    If `separate=True`, it also runs Demucs to create per-stem WAVs.
    """

    # 1) Compute tonal profile for the full file
    master_profile = load_reference(input_file, sample_rate=sample_rate)

    # 2) If requested, separate stems and compute individual profiles
    stem_profiles = {}
    if separate:
        stem_dir = os.path.join(os.path.dirname(output_file), "separated", profile_name)
        stem_paths = _separate_with_demucs(input_file, stem_dir, model_name=demucs_model, sample_rate=sample_rate)

        for stem_name, stem_path in stem_paths.items():
            if os.path.exists(stem_path):
                stem_profiles[stem_name] = load_reference(stem_path, sample_rate=sample_rate)

    # 3) Save to profiles JSON
    profiles = {}
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            try:
                profiles = json.load(f)
            except Exception:
                profiles = {}

    profiles[profile_name] = {"master": master_profile, **stem_profiles}

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)

    return profiles[profile_name]

    # 3) Save to profiles JSON
    profiles = {}
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            try:
                profiles = json.load(f)
            except Exception:
                profiles = {}

    profiles[profile_name] = stems

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2)

    return stems


def main():
    parser = argparse.ArgumentParser(description="Train a tonal profile from an audio file.")
    parser.add_argument("input", help="Path to the reference audio file (e.g., audio.wav)")
    parser.add_argument("--output", default="learning/profiles.json", help="Path to write profiles JSON")
    parser.add_argument("--name", default="default", help="Profile name to store")
    parser.add_argument("--sr", type=int, default=44100, help="Sample rate to use for analysis")
    parser.add_argument("--separate", action="store_true", help="Run stem separation (Demucs) and save stems under learning/separated/<name>")
    parser.add_argument("--demucs-model", default="htdemucs", help="Demucs model to use for separation (e.g., htdemucs)")

    args = parser.parse_args()

    stems = train_from_audio(
        args.input,
        args.output,
        args.name,
        sample_rate=args.sr,
        separate=args.separate,
        demucs_model=args.demucs_model,
    )
    print(f"Saved profile '{args.name}' to {args.output}")
    if args.separate:
        print(f"Separated stems saved under learning/separated/{args.name}")
    for stem, profile in stems.items():
        print(f"  {stem}: {profile}")


if __name__ == "__main__":
    main()
