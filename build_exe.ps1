param(
    [bool]$Clean = $true
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pythonCandidates = @(
    (Join-Path $root "venv\Scripts\python.exe"),
    (Join-Path $root ".venv\Scripts\python.exe")
)

$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
    $paths = $pythonCandidates -join " or "
    throw "Python venv not found. Expected $paths"
}

Write-Host "Using Python from: $python"

$iconPath = Join-Path $root "icon.png"
$faderPath = Join-Path $root "fader.png"
$faderBottomPath = Join-Path $root "fader_buttom.png"
$buildAssetsDir = Join-Path $root "build"
$iconPngBuildPath = Join-Path $buildAssetsDir "icon_256.png"
$iconBuildPath = Join-Path $buildAssetsDir "icon_256.ico"

foreach ($asset in @($iconPath, $faderPath, $faderBottomPath)) {
    if (!(Test-Path $asset)) {
        throw "Required asset not found: $asset"
    }
}

function Invoke-PyInstaller {
    param(
        [string[]]$Arguments
    )

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()

    try {
        $process = Start-Process `
            -FilePath $python `
            -ArgumentList (@("-m", "PyInstaller") + $Arguments) `
            -Wait `
            -NoNewWindow `
            -PassThru `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        if ($process.ExitCode -ne 0) {
            $stdout = Get-Content $stdoutPath -Raw -ErrorAction SilentlyContinue
            $stderr = Get-Content $stderrPath -Raw -ErrorAction SilentlyContinue
            throw "PyInstaller failed with exit code $($process.ExitCode)`n$stdout`n$stderr"
        }
    }
    finally {
        Remove-Item $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
    }
}

if ($Clean) {
    if (Test-Path "$root\build") { Remove-Item "$root\build" -Recurse -Force }
    if (Test-Path "$root\dist") { Remove-Item "$root\dist" -Recurse -Force }
}

if (!(Test-Path $buildAssetsDir)) {
    New-Item -ItemType Directory -Path $buildAssetsDir | Out-Null
}

Add-Type -AssemblyName System.Drawing
$srcImage = [System.Drawing.Image]::FromFile($iconPath)
try {
    $targetSize = 256
    $bitmap = New-Object System.Drawing.Bitmap($targetSize, $targetSize)
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.DrawImage($srcImage, 0, 0, $targetSize, $targetSize)
        }
        finally {
            $graphics.Dispose()
        }

        $bitmap.Save($iconPngBuildPath, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $bitmap.Dispose()
    }
}
finally {
    $srcImage.Dispose()
}

$pngBytes = [System.IO.File]::ReadAllBytes($iconPngBuildPath)
$stream = New-Object System.IO.FileStream($iconBuildPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
try {
    $writer = New-Object System.IO.BinaryWriter($stream)
    try {
        # ICONDIR header
        $writer.Write([UInt16]0)
        $writer.Write([UInt16]1)
        $writer.Write([UInt16]1)

        # ICONDIRENTRY for one 256x256 PNG image
        $writer.Write([Byte]0)  # width: 0 means 256
        $writer.Write([Byte]0)  # height: 0 means 256
        $writer.Write([Byte]0)  # color palette count
        $writer.Write([Byte]0)  # reserved
        $writer.Write([UInt16]1)   # color planes
        $writer.Write([UInt16]32)  # bits per pixel
        $writer.Write([UInt32]$pngBytes.Length)
        $writer.Write([UInt32]22)  # image data offset

        $writer.Write($pngBytes)
    }
    finally {
        $writer.Dispose()
    }
}
finally {
    $stream.Dispose()
}

# Build GUI executable (windowed)
Invoke-PyInstaller @(
    "--noconfirm",
    "--clean",
    "--name", "AlexStudioMix",
    "--windowed",
    "--icon", $iconBuildPath,
    "--add-data", "$iconPath;.",
    "--add-data", "$faderPath;.",
    "--add-data", "$faderBottomPath;.",
    "--hidden-import", "soundfile",
    "--hidden-import", "sounddevice",
    "--hidden-import", "pyloudnorm",
    "config_gui.py"
)

# Build worker executable as one-file so it can be copied standalone into the GUI folder.
Invoke-PyInstaller @(
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "run_profile_worker",
    "--console",
    "--hidden-import", "soundfile",
    "--hidden-import", "sounddevice",
    "--hidden-import", "pyloudnorm",
    "run_profile_worker.py"
)

$appDir = Join-Path $root "dist\AlexStudioMix"
$workerExe = Join-Path $root "dist\run_profile_worker.exe"

if (!(Test-Path $appDir)) {
    throw "GUI dist folder missing: $appDir"
}
if (!(Test-Path $workerExe)) {
    throw "Worker EXE missing: $workerExe"
}

# Copy worker exe into GUI app folder
Copy-Item $workerExe "$appDir\run_profile_worker.exe" -Force

# Keep image assets at dist root for straightforward runtime loading.
Copy-Item $iconPath "$appDir\icon.png" -Force
Copy-Item $faderPath "$appDir\fader.png" -Force
Copy-Item $faderBottomPath "$appDir\fader_buttom.png" -Force

# External editable config
Copy-Item "$root\config.json" "$appDir\config.json" -Force

# Profiles needed by run_profile.py
if (!(Test-Path "$appDir\learning")) { New-Item -ItemType Directory -Path "$appDir\learning" | Out-Null }
Copy-Item "$root\learning\profiles.json" "$appDir\learning\profiles.json" -Force

# Convenience launcher for final user
$launcher = @"
@echo off
cd /d "%~dp0"
start "" "AlexStudioMix.exe"
"@
Set-Content -Path "$appDir\Run_AlexStudioMix.bat" -Value $launcher -Encoding ASCII

Write-Host "Build complete!"
Write-Host "Distribution folder: $appDir"
Write-Host "Copy this folder to the target PC and run AlexStudioMix.exe"
