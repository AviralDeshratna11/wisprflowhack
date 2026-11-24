# Automated tests + benchmark. Writes docs\test_report.txt and docs\benchmark_results.json
$ErrorActionPreference = "Continue"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe -m pytest -v -p no:cacheprovider 2>&1 | Tee-Object -FilePath docs\test_report.txt
.\.venv\Scripts\python.exe scripts\benchmark.py
.\.venv\Scripts\python.exe scripts\gate_b_capture.py --turns 10 --seconds 2
