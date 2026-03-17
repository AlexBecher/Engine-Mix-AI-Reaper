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

# Ensure PyInstaller can detect and bundle tkinter/Tcl-Tk when building from venv.
$pyBasePrefix = & $python -c "import sys; print(sys.base_prefix)"
$tclDir = Join-Path $pyBasePrefix "tcl\tcl8.6"
$tkDir = Join-Path $pyBasePrefix "tcl\tk8.6"

if ((Test-Path (Join-Path $tclDir "init.tcl")) -and (Test-Path (Join-Path $tkDir "tk.tcl"))) {
    $env:TCL_LIBRARY = $tclDir
    $env:TK_LIBRARY = $tkDir
    Write-Host "Using Tcl/Tk from: $tclDir and $tkDir"
}
else {
    Write-Warning "Tcl/Tk runtime files not found under $pyBasePrefix. Build may exclude tkinter."
}

$imgDir = Join-Path $root "img"
$iconPath = Join-Path $imgDir "icon.png"
$faderPath = Join-Path $imgDir "fader.png"
$faderBottomPath = Join-Path $imgDir "fader_buttom.png"
$backPngPath = Join-Path $imgDir "back.png"
$bassPngPath = Join-Path $imgDir "bass.png"
$drumPngPath = Join-Path $imgDir "drum.png"
$guitarsPngPath = Join-Path $imgDir "guitars.png"
$keysPngPath = Join-Path $imgDir "keys.png"
$leadPngPath = Join-Path $imgDir "lead.png"
$checkInPngPath = Join-Path $imgDir "checkin.png"
$checkOutPngPath = Join-Path $imgDir "checkout.png"
$deletePngPath = Join-Path $imgDir "del.png"
$startPngPath = Join-Path $imgDir "start.png"
$stopPngPath = Join-Path $imgDir "stop.png"
$savePngPath = Join-Path $imgDir "save.png"
$learnPngPath = Join-Path $imgDir "learn.png"
$dryPngPath = Join-Path $imgDir "dry.png"
$applyPngPath = Join-Path $imgDir "apply.png"
$buildAssetsDir = Join-Path $root "build"
$iconPngBuildPath = Join-Path $buildAssetsDir "icon_256.png"
$iconBuildPath = Join-Path $buildAssetsDir "icon_256.ico"

foreach ($asset in @($iconPath, $faderPath, $faderBottomPath, $backPngPath, $bassPngPath, $drumPngPath, $guitarsPngPath, $keysPngPath, $leadPngPath, $checkInPngPath, $checkOutPngPath, $deletePngPath, $startPngPath, $stopPngPath, $savePngPath, $learnPngPath, $dryPngPath, $applyPngPath)) {
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
    "--add-data", "$iconPath;img",
    "--add-data", "$faderPath;img",
    "--add-data", "$faderBottomPath;img",
    "--add-data", "$backPngPath;img",
    "--add-data", "$bassPngPath;img",
    "--add-data", "$drumPngPath;img",
    "--add-data", "$guitarsPngPath;img",
    "--add-data", "$keysPngPath;img",
    "--add-data", "$leadPngPath;img",
    "--add-data", "$checkInPngPath;img",
    "--add-data", "$checkOutPngPath;img",
    "--add-data", "$deletePngPath;img",
    "--add-data", "$startPngPath;img",
    "--add-data", "$stopPngPath;img",
    "--add-data", "$savePngPath;img",
    "--add-data", "$learnPngPath;img",
    "--add-data", "$dryPngPath;img",
    "--add-data", "$applyPngPath;img",
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

# Keep image assets inside dist/img for organized runtime loading.
$appImgDir = Join-Path $appDir "img"
if (!(Test-Path $appImgDir)) { New-Item -ItemType Directory -Path $appImgDir | Out-Null }
Copy-Item $iconPath "$appImgDir\icon.png" -Force
Copy-Item $faderPath "$appImgDir\fader.png" -Force
Copy-Item $faderBottomPath "$appImgDir\fader_buttom.png" -Force
Copy-Item $backPngPath "$appImgDir\back.png" -Force
Copy-Item $bassPngPath "$appImgDir\bass.png" -Force
Copy-Item $drumPngPath "$appImgDir\drum.png" -Force
Copy-Item $guitarsPngPath "$appImgDir\guitars.png" -Force
Copy-Item $keysPngPath "$appImgDir\keys.png" -Force
Copy-Item $leadPngPath "$appImgDir\lead.png" -Force
Copy-Item $checkInPngPath "$appImgDir\checkin.png" -Force
Copy-Item $checkOutPngPath "$appImgDir\checkout.png" -Force
Copy-Item $deletePngPath "$appImgDir\del.png" -Force
Copy-Item $startPngPath "$appImgDir\start.png" -Force
Copy-Item $stopPngPath "$appImgDir\stop.png" -Force
Copy-Item $savePngPath "$appImgDir\save.png" -Force
Copy-Item $learnPngPath "$appImgDir\learn.png" -Force
Copy-Item $dryPngPath "$appImgDir\dry.png" -Force
Copy-Item $applyPngPath "$appImgDir\apply.png" -Force

# Create logs folder for dry-run logs
if (!(Test-Path "$appDir\logs")) { New-Item -ItemType Directory -Path "$appDir\logs" | Out-Null }

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
