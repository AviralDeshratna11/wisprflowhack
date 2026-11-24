# Download + SHA-256-verify the local speech model listed in models\manifest.json
$ErrorActionPreference = "Stop"
.\.venv\Scripts\python.exe scripts\download_models.py
if (-not $?) { exit 1 }
