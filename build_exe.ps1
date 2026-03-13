param(
    [bool]$Clean = $true
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (!(Test-Path $python)) {
    throw "Python venv not found at $python"
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

# Build GUI executable (windowed)
Invoke-PyInstaller @(
    "--noconfirm",
    "--clean",
    "--name", "AlexStudioMix",
    "--windowed",
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
