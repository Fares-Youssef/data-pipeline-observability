[CmdletBinding()]
param(
    [switch]$SkipTests,
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

function Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found at $Python"
}

Step "Checking required local files"
$required = @(".env", "Dockerfile", "weather_cli.py", "migrations\004_pipeline_resilience.sql")
foreach ($file in $required) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing required file: $file" }
}

Step "Compiling Python"
& $Python -m compileall -q app.py weather weather_cli.py scripts
if ($LASTEXITCODE -ne 0) { throw "Python compilation failed" }

if (-not $SkipTests) {
    Step "Running deterministic tests"
    & $Python -c "import sys;sys.path.insert(0,'.deps');import pytest;raise SystemExit(pytest.main(['-q']))"
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
}

Step "Applying idempotent migrations"
& $Python weather_cli.py init-db
if ($LASTEXITCODE -ne 0) { throw "Migration failed" }

Step "Validating database and pipeline state"
& $Python weather_cli.py validate
if ($LASTEXITCODE -ne 0) { throw "Validation reported blocking issues" }

Step "Checking deployment tools"
$docker = Get-Command docker -ErrorAction SilentlyContinue
$azure = Get-Command az -ErrorAction SilentlyContinue
$dump = Get-Command mysqldump -ErrorAction SilentlyContinue
if (-not $dump -and (Test-Path -LiteralPath "C:\xampp\mysql\bin\mysqldump.exe")) {
    $dump = Get-Item "C:\xampp\mysql\bin\mysqldump.exe"
}
$dockerEngine = $false
if ($docker) {
    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $dockerVersion = & docker info --format '{{.ServerVersion}}' 2>$null
    $dockerEngine = $LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($dockerVersion)
    $ErrorActionPreference = $oldPreference
}
[pscustomobject]@{
    DockerCli = [bool]$docker
    DockerEngine = $dockerEngine
    AzureCli = [bool]$azure
    MySqlDump = [bool]$dump
} | Format-List

Write-Host "`nPreflight completed." -ForegroundColor Green
