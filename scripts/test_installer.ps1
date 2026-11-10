param([Parameter(Mandatory)][string]$Installer)
$ErrorActionPreference = 'Stop'
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$registry = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\FoodMicroCount.YawAgyeiGyasi_is1'
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'FoodMicro Count/FoodMicro Count.lnk'
$guideShortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'FoodMicro Count/FoodMicro Count Beginner Guide.lnk'
if ((Test-Path $registry) -or (Test-Path $shortcut) -or (Test-Path $guideShortcut)) {
    throw 'Run the install/uninstall check under a user account without an existing installation.'
}

$working = Join-Path (Split-Path -Parent $PSScriptRoot) 'build'
$target = Join-Path $working ('installed-smoke-' + [guid]::NewGuid().ToString('N'))
$env:TEMP = $working
$env:TMP = $working
$env:PYTHONHOME = $null
$env:PYTHONPATH = $null
$env:QT_QPA_PLATFORM = $null
$env:FOODMICRO_SMOKE_LOG = Join-Path $working 'installed-app-smoke.log'

function Invoke-CheckedProcess($File, $Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000)) {
        $process.Kill()
        throw "$File timed out"
    }
    if ($process.ExitCode -ne 0) { throw "$File exited with $($process.ExitCode)" }
}

$exe = Join-Path $target 'FoodMicroCount.exe'
$uninstaller = Join-Path $target 'unins000.exe'
try {
    Invoke-CheckedProcess $Installer "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=""$target"" /LOG=""$working\install-smoke.log"""
    if (-not ((Test-Path $exe) -and (Test-Path $uninstaller) -and (Test-Path $registry) -and (Test-Path $shortcut) -and (Test-Path $guideShortcut))) {
        throw 'Installation is missing the executable, uninstall entry or Start Menu shortcut'
    }
    Write-Host 'Installed application, uninstall entry and Start Menu shortcut present'
    try { Invoke-CheckedProcess $exe '--smoke-test' }
    finally { if (Test-Path $env:FOODMICRO_SMOKE_LOG) { Get-Content $env:FOODMICRO_SMOKE_LOG } }
    Write-Host 'Installed executable completed the Qt, count, database and export smoke test'
}
finally {
    if (Test-Path $uninstaller) {
        Invoke-CheckedProcess $uninstaller "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=""$working\uninstall-smoke.log"""
    }
}
if ((Test-Path $exe) -or (Test-Path $registry) -or (Test-Path $shortcut) -or (Test-Path $guideShortcut)) {
    throw 'Uninstallation left the executable, uninstall entry or shortcut'
}
Write-Host 'Uninstall removed the program and its registration'


