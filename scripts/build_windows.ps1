param(
    [string]$Python = "python",
    [string]$InnoCompiler = "ISCC.exe",
    [string]$SourceArchive,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
if ($SourceArchive) { $SourceArchive = (Resolve-Path -LiteralPath $SourceArchive).Path }
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not $SkipInstaller) {
    $InnoCompiler = (Get-Command $InnoCompiler -ErrorAction Stop).Source
}
$version = & $Python -c "from foodmicro import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw "Could not read the application version" }
$displayVersion = $version -replace '\.0$', ''
$stage = Join-Path (Get-Location).Path 'build'
New-Item -ItemType Directory -Force $stage | Out-Null
$env:TEMP = $stage
$env:TMP = $stage
$parts = $version.Split('.') -join ', '
@"
VSVersionInfo(
    ffi=FixedFileInfo(filevers=($parts, 0), prodvers=($parts, 0),
                      mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'Yaw Agyei Gyasi'),
            StringStruct('FileDescription', 'FoodMicro Count'),
            StringStruct('FileVersion', '$version'),
            StringStruct('OriginalFilename', 'FoodMicroCount.exe'),
            StringStruct('ProductName', 'FoodMicro Count'),
            StringStruct('ProductVersion', '$version')
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])
    ]
)
"@ | Set-Content -Encoding ascii "$stage/windows-version.txt"

$pythonExecutable = (Get-Command $Python -ErrorAction Stop).Source
$originalPath = $env:PATH
$env:PYINSTALLER_CONFIG_DIR = Join-Path $stage 'pyinstaller-cache'
try {
    # Prevent unrelated DLLs from installed tools entering the frozen bundle.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    & $pythonExecutable -m PyInstaller --clean --noconfirm --onedir --windowed --name FoodMicroCount --icon foodmicro/assets/icon.ico --version-file "$stage/windows-version.txt" --collect-data foodmicro --collect-all skimage run_foodmicro.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
}
finally {
    $env:PATH = $originalPath
}

$bundle = Join-Path (Get-Location).Path 'dist/FoodMicroCount'
$prepareArgs = @('scripts/prepare_distribution.py', $bundle)
if ($SourceArchive) { $prepareArgs += @('--source-archive', $SourceArchive) }
& $Python @prepareArgs
if ($LASTEXITCODE -ne 0) { throw "Distribution preparation failed" }

$env:FOODMICRO_SMOKE_LOG = Join-Path $stage 'frozen-smoke.log'
$smoke = Start-Process -FilePath (Join-Path $bundle 'FoodMicroCount.exe') -ArgumentList '--smoke-test' -WindowStyle Hidden -PassThru
if (-not $smoke.WaitForExit(120000)) {
    $smoke.Kill()
    throw "Frozen smoke test timed out"
}
if (Test-Path $env:FOODMICRO_SMOKE_LOG) { Get-Content $env:FOODMICRO_SMOKE_LOG }
if ($smoke.ExitCode -ne 0) { throw "Frozen smoke test failed with exit code $($smoke.ExitCode)" }
Write-Host "Frozen application smoke test passed"

if (-not $SkipInstaller) {
    & $InnoCompiler "/DAppVersion=$version" "/DDisplayVersion=$displayVersion" scripts/FoodMicroCount.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed with exit code $LASTEXITCODE" }
    Get-ChildItem "dist/FoodMicroCount-$version-*" -File | Where-Object Extension -In '.exe','.zip' | ForEach-Object {
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $($_.Name)" | Set-Content -Path ($_.FullName + '.sha256') -Encoding ascii
    }
}
