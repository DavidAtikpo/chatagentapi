Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creation du venv..."
    py -m venv venv
    .\venv\Scripts\pip install -r requirements.txt
}

Write-Host "Demarrage API sur http://localhost:8000"
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
