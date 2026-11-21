# Run Flashbang Shield in the background without locking up the PowerShell console
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) {
    $pythonw = "pythonw.exe"
}

Start-Process -FilePath $pythonw -ArgumentList ("-m flashbang_shield.main " + ($args -join " ")) -WindowStyle Hidden
Write-Host "Flashbang Shield launched in background. PowerShell terminal is free to use." -ForegroundColor Green
