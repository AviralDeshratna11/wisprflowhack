# SwarLekhak developer setup (Windows PowerShell). Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$py = "py"
& $py -3.11 --version
if (-not $?) { throw "Python 3.11 is required (py -3.11)." }
if (-not (Test-Path .venv)) { & $py -3.11 -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe scripts\download_models.py
if (-not $?) { throw "Model download/verification failed." }
.\.venv\Scripts\python.exe scripts\make_fixtures.py
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe scripts\migrate.py
.\.venv\Scripts\python.exe -m swarlekhak --demo-setup
Write-Host ""
Write-Host "Setup complete. Launch:  `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m swarlekhak"
Write-Host "Demo fixture mode:       `$env:PYTHONPATH='src'; .\.venv\Scripts\python.exe -m swarlekhak --fixture"
