# Configure le push Firebase pour l'API ChatAgent
# Usage : depuis api/ → .\scripts\setup_push.ps1 [chemin-vers-cle-firebase.json]

param(
    [string]$KeyFile = ""
)

$ErrorActionPreference = "Stop"
$apiRoot = Split-Path $PSScriptRoot -Parent

if (-not $KeyFile) {
    $KeyFile = Join-Path $apiRoot "firebase-service-account.json"
}

Write-Host "=== Configuration push ChatAgent ===" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-Path $KeyFile)) {
    Write-Host "Fichier cle introuvable : $KeyFile" -ForegroundColor Red
    Write-Host ""
    Write-Host "Etapes :" -ForegroundColor Yellow
    Write-Host "  1. https://console.firebase.google.com/ → projet ebonservices-75030"
    Write-Host "  2. Project settings → Service accounts → Generate new private key"
    Write-Host "  3. Enregistrez le fichier sous :"
    Write-Host "     $KeyFile"
    Write-Host "  4. Relancez : .\scripts\setup_push.ps1"
    exit 1
}

$dest = Join-Path $apiRoot "firebase-service-account.json"
if ($KeyFile -ne $dest) {
    Copy-Item $KeyFile $dest -Force
    Write-Host "Copie -> firebase-service-account.json" -ForegroundColor Green
}

# Test local
Push-Location $apiRoot
try {
    $result = py scripts/test_push.py 2>&1
    Write-Host $result
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Test local echoue — verifiez la cle et redemarrez uvicorn." -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "=== Pour Render (production) ===" -ForegroundColor Cyan
$json = Get-Content $dest -Raw | ConvertFrom-Json
$oneLine = (Get-Content $dest -Raw) -replace "`r`n", "" -replace "`n", ""
Write-Host "Variable : FIREBASE_SERVICE_ACCOUNT_JSON"
Write-Host "Projet   : $($json.project_id)"
Write-Host ""
Write-Host "Collez la ligne suivante dans Render → Environment :" -ForegroundColor Yellow
Write-Host $oneLine.Substring(0, [Math]::Min(120, $oneLine.Length)) "..." -ForegroundColor DarkGray
Write-Host ""
Write-Host "Puis Manual Deploy sur Render." -ForegroundColor Green
Write-Host "Verifiez : https://chatagentapi.onrender.com/health → push_enabled: true"
