Set-Location $PSScriptRoot
if (-not (Test-Path .\.venv\Scripts\python.exe)) { python -m venv .venv }
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install -q -r requirements.txt
& $py app.py
