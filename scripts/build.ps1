# Build dist\SwarLekhak\SwarLekhak.exe (PyInstaller one-folder) and, if Inno Setup 6 is installed,
# dist\installer\SwarLekhak-Setup.exe. Then runs the packaged self-test.
$ErrorActionPreference = "Stop"
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --distpath dist --workpath build installer\SwarLekhak.spec
if (-not $?) { throw "PyInstaller failed" }

# packaged smoke test (windowed exe writes %LOCALAPPDATA%\SwarLekhak\logs\selftest.json)
$p = Start-Process -FilePath dist\SwarLekhak\SwarLekhak.exe -ArgumentList "--selftest" -Wait -PassThru
Write-Host "Packaged self-test exit code: $($p.ExitCode)"
Get-Content "$env:LOCALAPPDATA\SwarLekhak\logs\selftest.json"

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc installer\SwarLekhak.iss
    Write-Host "Installer: dist\installer\SwarLekhak-Setup.exe (UNSIGNED)"
} else {
    Write-Warning "Inno Setup 6 not found - installer not built. Install it (https://jrsoftware.org/isinfo.php) and re-run."
}
