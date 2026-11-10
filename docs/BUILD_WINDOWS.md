# Build and verify on Windows

## Build environment

Use x64 Windows, x64 Python 3.12, Git and Inno Setup 6.7.4. The reference build uses CPython 3.12.14. Use the official Inno Setup compiler. Its vendor requests a licence from commercial compiler users.

From a clean Git checkout of the release commit:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-windows-build.txt
$env:QT_QPA_PLATFORM = 'offscreen'
.\.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:QT_QPA_PLATFORM
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows.ps1 -Python .\.venv\Scripts\python.exe -InnoCompiler 'C:\Path\To\ISCC.exe'
```

A clean extracted release source ZIP can also be built. Run the last command with `-SourceArchive` pointing at the original release ZIP. The build checks that the archive carries a Git commit ID and that its files match the build directory byte for byte. Ignored output folders are permitted in a Git checkout.

The package build isolates the Windows DLL search path. PyInstaller produces a directory with an executable and replaceable Qt DLLs. Inno Setup packages that directory. The build omits unused video I/O and software OpenGL plugins. The frozen app must pass its startup, synthetic count, local save/reopen and export check before Setup is compiled. Artifacts and a build provenance manifest are written under `dist`.

Use `scripts/test_installer.ps1 -Installer <path>` from an account without an existing FoodMicro Count installation to check installation, startup, the guide shortcut and removal. Check installation on a clean Windows machine before wider deployment. When verifying the public release, compare its SHA-256 values and check that the GitHub release tag points to the recorded commit. GitHub generates ZIP and tar archives from that tag.
