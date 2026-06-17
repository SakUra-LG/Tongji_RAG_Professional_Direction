$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example." -ForegroundColor Yellow
    Write-Host "Fill DASHSCOPE_API_KEY and replace all change_me values, then run this script again."
    exit 1
}

Write-Host "Starting SynapseQ with idempotent database initialization..." -ForegroundColor Cyan
docker compose up -d --build

Write-Host "Waiting for services..." -ForegroundColor Cyan
docker compose wait rag-init
docker compose ps

Write-Host ""
Write-Host "Milvus counts:" -ForegroundColor Cyan
docker compose exec -T rag-backend python scripts/check_milvus_counts.py

Write-Host ""
Write-Host "SynapseQ is ready." -ForegroundColor Green

function Get-EnvValue {
    param([string] $Name, [string] $Default)
    $match = Get-Content ".env" | Select-String "^$Name="
    if ($match) {
        return $match.Line.Split("=", 2)[1]
    }
    return $Default
}

$frontendPort = Get-EnvValue "FRONTEND_PORT" "80"
$backendPort = Get-EnvValue "BACKEND_PORT" "8000"
Write-Host "Frontend: http://localhost:$frontendPort"
Write-Host "Backend docs: http://localhost:$backendPort/docs"
