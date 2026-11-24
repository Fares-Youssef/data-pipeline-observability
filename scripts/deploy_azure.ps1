<#
.SYNOPSIS
  One-command, re-runnable deployment of DEPI Weather to Azure.

.DESCRIPTION
  Creates (or reuses) every resource, copies the local database, deploys the API and the scheduled
  jobs, then runs an end-to-end smoke test and one real collection batch.

  Resources (all in -ResourceGroup):
    ACR  depiweather...          container image
    Log Analytics depi-logs      job/app logs
    Container Apps env depi-env
    MySQL Flexible depi-mysql-*  Burstable B1ms, MySQL 8, TLS
    Storage depilake*            ADLS Gen2: containers weather-raw (Bronze) + lake (Silver/Gold)
    Container App depi-weather-api        dashboard + REST API (scales to zero)
    Job depi-forecast  hourly UTC, collects only at 09:00 Africa/Cairo (run-batch)
    Job depi-backfill  daily 14:00 UTC (backfill-actuals, 45 days)
    Job depi-ops       manual, used for init/validation/one-off commands
    [-WithSynapse] Synapse workspace (serverless SQL only, pay per query)

  Generated passwords and names are kept in .azure\deploy-state.json (git-ignored). Keep that file safe.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\deploy_azure.ps1 -WithSynapse -AlertEmail you@example.com
#>
[CmdletBinding()]
param(
    [string]$ResourceGroup = 'rg-depi-weather',
    [string]$Location = 'uaenorth',
    [string]$AcrName = 'depiweather56232c7d',
    [string]$Tag = '',
    [int]$MaxLocations = 1500,
    [string]$OpenMeteoApiKey = '',
    [string]$AlertEmail = '',
    [switch]$WithSynapse,
    [switch]$SkipDataCopy,
    [switch]$SkipFirstRun
)

$ErrorActionPreference = 'Stop'
$env:LOG_LEVEL = 'WARNING'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Invoke-Az {
    $output = & az @args
    # Never echo the full command: it can contain secrets (--secrets, passwords, connection strings).
    if ($LASTEXITCODE -ne 0) { throw "Azure CLI failed: az $(($args | Select-Object -First 3) -join ' ') ... (see the error above)" }
    return $output
}
function Exists([scriptblock]$Probe) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $null = & $Probe 2>$null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $old
    return $ok
}
function New-Secret([int]$Length = 24) {
    $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'.ToCharArray()
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes = New-Object byte[] $Length
    $rng.GetBytes($bytes)
    $body = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    return "Dp1-$body"   # upper + lower + digit + symbol (Azure complexity rules)
}

# ---------------------------------------------------------------- state
$StateDir = Join-Path $ProjectRoot '.azure'
$StateFile = Join-Path $StateDir 'deploy-state.json'
New-Item -ItemType Directory -Force $StateDir | Out-Null
if (Test-Path $StateFile) {
    $State = Get-Content $StateFile -Raw | ConvertFrom-Json
} else {
    $suffix = -join ((48..57) + (97..122) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
    $State = [pscustomobject]@{
        Suffix = $suffix; DbAdminUser = 'depiadmin'; DbAdminPassword = (New-Secret); DbAppUser = 'depi_app'
        DbAppPassword = (New-Secret); SynapseSqlPassword = (New-Secret)
    }
    $State | ConvertTo-Json | Set-Content -Encoding utf8 $StateFile
}
$Suffix = $State.Suffix
$DbServer = "depi-mysql-$Suffix"
$DbHost = "$DbServer.mysql.database.azure.com"
$StorageAccount = "depilake$Suffix"
$EnvName = 'depi-env'
$Workspace = 'depi-logs'
if (-not $Tag) { $Tag = (git rev-parse --short=12 HEAD); if (-not $Tag) { $Tag = 'v1.1.0' } }
$Image = "$AcrName.azurecr.io/depi-weather:$Tag"

# ---------------------------------------------------------------- 0. local checks
Step 'Local checks (tests + local database)'
if (-not (Test-Path $Python)) { throw "Python not found at $Python" }
& $Python -c "import sys;sys.path.insert(0,'.deps');import pytest;raise SystemExit(pytest.main(['-q']))"
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed' }
& $Python weather_cli.py validate | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Local database validation failed. Start MySQL in XAMPP first.' }
if (-not (Exists { az extension show --name containerapp })) { Invoke-Az extension add --name containerapp --only-show-errors | Out-Null }
$Subscription = Invoke-Az account show --query name -o tsv
Write-Host "Subscription: $Subscription | Region: $Location | Image tag: $Tag"

# ---------------------------------------------------------------- 1. providers
Step 'Registering resource providers (one-time)'
foreach ($p in 'Microsoft.ContainerRegistry', 'Microsoft.App', 'Microsoft.OperationalInsights', 'Microsoft.DBforMySQL',
                'Microsoft.Storage', 'Microsoft.Insights', 'Microsoft.Synapse', 'Microsoft.Sql') {
    if ((az provider show -n $p --query registrationState -o tsv) -ne 'Registered') { Invoke-Az provider register -n $p --wait | Out-Null }
    Write-Host "  $p registered"
}
if (-not (Exists { az group show -n $ResourceGroup })) { Invoke-Az group create -n $ResourceGroup -l $Location | Out-Null }

# ---------------------------------------------------------------- 2. registry + image
Step "Container registry and image $Image"
if (-not (Exists { az acr show -n $AcrName })) { Invoke-Az acr create -g $ResourceGroup -n $AcrName --sku Basic -l $Location | Out-Null }
if (Exists { az acr repository show -n $AcrName --image "depi-weather:$Tag" }) {
    Write-Host '  image already built'
} else {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & az acr build --registry $AcrName --image "depi-weather:$Tag" $ProjectRoot
    $built = $LASTEXITCODE -eq 0
    $ErrorActionPreference = $old
    if (-not $built) {
        Write-Warning 'az acr build is not available on this subscription; falling back to local Docker Desktop.'
        & powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build_push_acr.ps1') -RegistryName $AcrName -Tag $Tag
        if ($LASTEXITCODE -ne 0) { throw 'Image build failed. Start Docker Desktop (docker info must work) and re-run this script.' }
    }
}

# ---------------------------------------------------------------- 3. storage (data lake)
Step "Data lake storage $StorageAccount (ADLS Gen2)"
if (-not (Exists { az storage account show -g $ResourceGroup -n $StorageAccount })) {
    Invoke-Az storage account create -g $ResourceGroup -n $StorageAccount -l $Location --sku Standard_LRS --kind StorageV2 `
        --hns true --min-tls-version TLS1_2 --allow-blob-public-access false | Out-Null
}
$StorageConnection = Invoke-Az storage account show-connection-string -g $ResourceGroup -n $StorageAccount --query connectionString -o tsv
foreach ($c in 'weather-raw', 'lake', 'synapse') {
    Invoke-Az storage container create -n $c --connection-string $StorageConnection --only-show-errors | Out-Null
}

# ---------------------------------------------------------------- 4. MySQL
Step "Azure Database for MySQL $DbServer"
$MyIp = (Invoke-RestMethod -Uri 'https://api.ipify.org')
if (-not (Exists { az mysql flexible-server show -g $ResourceGroup -n $DbServer })) {
    Invoke-Az mysql flexible-server create -g $ResourceGroup -n $DbServer -l $Location --tier Burstable --sku-name Standard_B1ms `
        --storage-size 20 --version 8.0.21 --admin-user $State.DbAdminUser --admin-password $State.DbAdminPassword `
        --public-access $MyIp --backup-retention 7 --yes | Out-Null
}
Invoke-Az mysql flexible-server firewall-rule create -g $ResourceGroup -n $DbServer --rule-name allow-azure-services `
    --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0 --only-show-errors | Out-Null
Invoke-Az mysql flexible-server firewall-rule create -g $ResourceGroup -n $DbServer --rule-name deploy-client `
    --start-ip-address $MyIp --end-ip-address $MyIp --only-show-errors | Out-Null
if (-not (Exists { az mysql flexible-server db show -g $ResourceGroup -s $DbServer -d depi })) {
    Invoke-Az mysql flexible-server db create -g $ResourceGroup -s $DbServer -d depi --charset utf8mb4 --collation utf8mb4_unicode_ci | Out-Null
}

# ---------------------------------------------------------------- 5. copy data
if (-not $SkipDataCopy) {
    Step 'Copying local database to Azure MySQL (schema + data + verification)'
    $env:TARGET_DB_HOST = $DbHost; $env:TARGET_DB_USER = $State.DbAdminUser; $env:TARGET_DB_PASSWORD = $State.DbAdminPassword
    $env:TARGET_DB_NAME = 'depi'; $env:TARGET_APP_USER = $State.DbAppUser; $env:TARGET_APP_PASSWORD = $State.DbAppPassword
    $env:LOG_LEVEL = 'WARNING'
    & $Python scripts\copy_database.py
    $copyCode = $LASTEXITCODE
    Remove-Item Env:TARGET_DB_PASSWORD, Env:TARGET_APP_PASSWORD -ErrorAction SilentlyContinue
    if ($copyCode -ne 0) { throw 'Database copy reported issues (see the table lines above).' }
}

# ---------------------------------------------------------------- 6. logs + environment
Step 'Log Analytics + Container Apps environment'
if (-not (Exists { az monitor log-analytics workspace show -g $ResourceGroup -n $Workspace })) {
    Invoke-Az monitor log-analytics workspace create -g $ResourceGroup -n $Workspace -l $Location --retention-time 30 | Out-Null
}
$WorkspaceId = Invoke-Az monitor log-analytics workspace show -g $ResourceGroup -n $Workspace --query customerId -o tsv
$WorkspaceKey = Invoke-Az monitor log-analytics workspace get-shared-keys -g $ResourceGroup -n $Workspace --query primarySharedKey -o tsv
$old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$existingMode = az containerapp env show -g $ResourceGroup -n $EnvName --query properties.environmentMode -o tsv --only-show-errors 2>$null
$ErrorActionPreference = $old
if ($existingMode -eq 'Express') { throw "Environment $EnvName is in Express mode; delete it (az containerapp env delete) and re-run." }
if (-not (Exists { az containerapp env show -g $ResourceGroup -n $EnvName })) {
    # WorkloadProfiles (Consumption profile): newer CLI versions default to "Express", which supports
    # neither managed identity for registry pulls nor all job features.
    Invoke-Az containerapp env create -g $ResourceGroup -n $EnvName -l $Location --environment-mode WorkloadProfiles `
        --logs-workspace-id $WorkspaceId --logs-workspace-key $WorkspaceKey | Out-Null
}

# ---------------------------------------------------------------- 7. app + jobs
$Secrets = @("db-password=$($State.DbAppPassword)", "storage-conn=$StorageConnection")
$Vars = @("DB_HOST=$DbHost", 'DB_PORT=3306', 'DB_NAME=depi', "DB_USER=$($State.DbAppUser)", 'DB_PASSWORD=secretref:db-password',
          'DB_SSL_CA=/etc/ssl/certs/ca-certificates.crt', 'DB_SSL_VERIFY_CERT=true', 'DB_CONNECT_TIMEOUT=20',
          'AZURE_STORAGE_CONNECTION_STRING=secretref:storage-conn', 'AZURE_STORAGE_CONTAINER=weather-raw', 'AZURE_LAKE_CONTAINER=lake',
          'WEATHER_BATCH_SIZE=25', 'WEATHER_REQUEST_INTERVAL=3', 'WEATHER_RATE_LIMIT_BACKOFF=30', 'WEATHER_ALIGNMENT_DISTANCE_KM=50',
          'LAKE_ROOT=/tmp/lake', 'LOG_LEVEL=INFO')
if ($OpenMeteoApiKey) {
    $Secrets += "openmeteo-key=$OpenMeteoApiKey"; $Vars += 'OPEN_METEO_API_KEY=secretref:openmeteo-key'; $Vars += 'WEATHER_MAX_LOCATIONS=0'
} else {
    $Vars += "WEATHER_MAX_LOCATIONS=$MaxLocations"   # free Open-Meteo tier: 3 models x this value must stay < 5,000/hour
}
$Registry = @('--registry-server', "$AcrName.azurecr.io", '--registry-identity', 'system')

Step 'API / dashboard: depi-weather-api'
if (Exists { az containerapp show -g $ResourceGroup -n depi-weather-api }) {
    Invoke-Az containerapp secret set -g $ResourceGroup -n depi-weather-api --secrets @Secrets | Out-Null
    Invoke-Az containerapp update -g $ResourceGroup -n depi-weather-api --image $Image --set-env-vars @Vars | Out-Null
} else {
    Invoke-Az containerapp create -g $ResourceGroup -n depi-weather-api --environment $EnvName --image $Image @Registry `
        --target-port 8000 --ingress external --cpu 0.5 --memory 1Gi --min-replicas 0 --max-replicas 2 `
        --secrets @Secrets --env-vars @Vars | Out-Null
}
$Fqdn = Invoke-Az containerapp show -g $ResourceGroup -n depi-weather-api --query properties.configuration.ingress.fqdn -o tsv

function Set-Job([string]$Name, [string]$Trigger, [string]$Cron, [string[]]$CliArgs, [int]$Timeout) {
    Write-Host "  job $Name"
    if (Exists { az containerapp job show -g $ResourceGroup -n $Name }) {
        Invoke-Az containerapp job secret set -g $ResourceGroup -n $Name --secrets @Secrets | Out-Null
        Invoke-Az containerapp job update -g $ResourceGroup -n $Name --image $Image --set-env-vars @Vars | Out-Null
        return
    }
    $schedule = @('--trigger-type', $Trigger)
    if ($Trigger -eq 'Schedule') { $schedule += @('--cron-expression', $Cron) }
    Invoke-Az containerapp job create -g $ResourceGroup -n $Name --environment $EnvName --image $Image @Registry @schedule `
        --replica-timeout $Timeout --replica-retry-limit 1 --parallelism 1 --replica-completion-count 1 `
        --cpu 1 --memory 2Gi --secrets @Secrets --env-vars @Vars --command python --args @CliArgs | Out-Null
}
Step 'Jobs (user-assigned identity with AcrPull, created before the jobs)'
# A job's system identity only exists after creation, but creation already validates the image pull.
# A user-assigned identity granted AcrPull up front avoids that chicken-and-egg failure.
if (-not (Exists { az identity show -g $ResourceGroup -n depi-pull-identity })) {
    Invoke-Az identity create -g $ResourceGroup -n depi-pull-identity -l $Location | Out-Null
}
$PullIdentityId = Invoke-Az identity show -g $ResourceGroup -n depi-pull-identity --query id -o tsv
$PullPrincipal = Invoke-Az identity show -g $ResourceGroup -n depi-pull-identity --query principalId -o tsv
$AcrId = Invoke-Az acr show -n $AcrName --query id -o tsv
$hasRole = Invoke-Az role assignment list --assignee $PullPrincipal --scope $AcrId --role AcrPull --query "[].id" -o tsv
if (-not $hasRole) {
    Invoke-Az role assignment create --assignee-object-id $PullPrincipal --assignee-principal-type ServicePrincipal `
        --role AcrPull --scope $AcrId | Out-Null
    Write-Host '  waiting 60s for the AcrPull role assignment to propagate'
    Start-Sleep -Seconds 60
}
$Registry = @('--registry-server', "$AcrName.azurecr.io", '--registry-identity', $PullIdentityId, '--mi-user-assigned', $PullIdentityId)
Set-Job 'depi-ops' 'Manual' '' @('weather_cli.py') 7200
Set-Job 'depi-forecast' 'Schedule' '0 * * * *' @('weather_cli.py', 'run-batch') 7200
Set-Job 'depi-backfill' 'Schedule' '0 14 * * *' @('weather_cli.py', 'backfill-actuals') 3600

function Invoke-Ops([string]$Command, [int]$TimeoutMinutes = 60) {
    Write-Host "  depi-ops> $Command" -ForegroundColor Yellow
    Invoke-Az containerapp job update -g $ResourceGroup -n depi-ops --set-env-vars "DEPI_COMMAND=$Command" | Out-Null
    $execution = Invoke-Az containerapp job start -g $ResourceGroup -n depi-ops --query name -o tsv
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    do {
        Start-Sleep -Seconds 20
        $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        $status = az containerapp job execution show -g $ResourceGroup -n depi-ops --job-execution-name $execution --query properties.status -o tsv --only-show-errors 2>$null
        $ErrorActionPreference = $old
        Write-Host "    $execution : $status"
    } while ($status -in @('Running', 'Processing', '') -and (Get-Date) -lt $deadline)
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & az containerapp job logs show -g $ResourceGroup -n depi-ops --execution $execution --container depi-ops --tail 30 --format text 2>$null | Out-Host
    $ErrorActionPreference = $old
    if ($status -ne 'Succeeded') { throw "depi-ops '$Command' finished with status '$status'. Check logs: monitoring\kql_queries.kql" }
}

# ---------------------------------------------------------------- 8. verification in the cloud
Step 'Cloud verification'
Invoke-Ops 'init-db'
Invoke-Ops 'validate'
Invoke-Ops 'collect-forecasts --limit 5'
Invoke-Ops 'collect-forecasts --limit 5'   # second run must insert 0 (idempotent)
& $Python scripts\verify_deployment.py "https://$Fqdn"
if ($LASTEXITCODE -ne 0) { Write-Warning 'API verification reported issues (the app may still be starting; re-run scripts\verify_deployment.py).' }

if (-not $SkipFirstRun) {
    Step 'First full collection now (instead of waiting for tomorrow 09:00 Cairo)'
    Invoke-Ops 'run-batch --force' 120
}

# ---------------------------------------------------------------- 9. Synapse (optional)
if ($WithSynapse) {
    Step 'Synapse workspace (serverless SQL over the lake)'
    $Synapse = "depi-syn-$Suffix"
    if (-not (Exists { az synapse workspace show -g $ResourceGroup -n $Synapse })) {
        Invoke-Az synapse workspace create -g $ResourceGroup -n $Synapse -l $Location --storage-account $StorageAccount `
            --file-system synapse --sql-admin-login-user sqladminuser --sql-admin-login-password $State.SynapseSqlPassword | Out-Null
    }
    Invoke-Az synapse workspace firewall-rule create -g $ResourceGroup --workspace-name $Synapse -n deploy-client `
        --start-ip-address $MyIp --end-ip-address $MyIp --only-show-errors | Out-Null
    $principal = Invoke-Az synapse workspace show -g $ResourceGroup -n $Synapse --query identity.principalId -o tsv
    $storageId = Invoke-Az storage account show -g $ResourceGroup -n $StorageAccount --query id -o tsv
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & az role assignment create --assignee-object-id $principal --assignee-principal-type ServicePrincipal `
        --role 'Storage Blob Data Reader' --scope $storageId --only-show-errors 2>$null | Out-Null
    $ErrorActionPreference = $old
    (Get-Content synapse\01_serverless_lake_views.sql -Raw).Replace('__STORAGE_ACCOUNT__', $StorageAccount) |
        Set-Content -Encoding utf8 (Join-Path $StateDir 'synapse_views_ready.sql')
    Write-Host "  Open https://web.azuresynapse.net (workspace $Synapse) > Develop > SQL script > Built-in,"
    Write-Host "  paste .azure\synapse_views_ready.sql then synapse\02_analysis_queries.sql and Run."
}

# ---------------------------------------------------------------- 10. alerts (optional)
if ($AlertEmail) {
    Step "Azure Monitor alert to $AlertEmail"
    & powershell -ExecutionPolicy Bypass -File monitoring\create_alerts.ps1 -Email $AlertEmail -ResourceGroup $ResourceGroup -Workspace $Workspace
}

Step 'Done'
[pscustomobject]@{
    Dashboard = "https://$Fqdn"
    Database = $DbHost
    DataLake = "https://$StorageAccount.dfs.core.windows.net/lake"
    Image = $Image
    Schedule = 'depi-forecast hourly (collects at 09:00 Africa/Cairo); depi-backfill daily 14:00 UTC'
    Secrets = '.azure\deploy-state.json (keep private)'
} | Format-List
