Write-Host "==============================="
Write-Host " REAROUTE DIAGNOSTIC TOOL"
Write-Host "==============================="

Write-Host ""
Write-Host "1) Checking ASIO drivers in registry..."
$asio = "HKLM:\SOFTWARE\ASIO"
if (Test-Path $asio) {
    Get-ChildItem $asio | ForEach-Object {
        Write-Host "ASIO Driver Found:" $_.PSChildName
        Get-ItemProperty $_.PSPath
    }
} else {
    Write-Host "No ASIO registry entries found."
}

Write-Host ""
Write-Host "2) Searching for ReaRoute DLL..."
$paths = @(
"C:\Program Files",
"C:\Program Files (x86)",
"C:\Windows\System32",
"C:\Windows\SysWOW64"
)

foreach ($p in $paths) {
    Get-ChildItem -Path $p -Filter "*rearoute*.dll" -Recurse -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "3) Checking installed audio devices..."
Get-CimInstance Win32_SoundDevice | Select Name, Manufacturer, Status

Write-Host ""
Write-Host "4) Checking Windows audio services..."
Get-Service | Where-Object {
    $_.Name -match "Audio"
} | Select Name, Status

Write-Host ""
Write-Host "5) Checking if REAPER is installed..."
$reaper = Get-ChildItem "C:\Program Files" -Recurse -Filter "reaper.exe" -ErrorAction SilentlyContinue
if ($reaper) {
    Write-Host "Reaper found at:"
    $reaper.FullName
} else {
    Write-Host "Reaper not found in Program Files."
}

Write-Host ""
Write-Host "6) Checking sample rate configuration..."
Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "==============================="
Write-Host "Diagnostic finished."
Write-Host "==============================="