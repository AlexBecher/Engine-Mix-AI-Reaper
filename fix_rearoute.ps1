$path = "HKLM:\SOFTWARE\ASIO\ReaRoute ASIO (x64)"

Write-Host "Fixing ReaRoute registry..."

New-ItemProperty -Path $path -Name "Dll" -Value "C:\Program Files\REAPER (x64)\Plugins\rearoute.dll" -PropertyType String -Force

New-ItemProperty -Path $path -Name "NumInputs" -Value 16 -PropertyType DWord -Force
New-ItemProperty -Path $path -Name "NumOutputs" -Value 16 -PropertyType DWord -Force

Write-Host "Done."