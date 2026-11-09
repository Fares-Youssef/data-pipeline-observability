# Creates an e-mail action group and a log alert that fires when any DEPI job fails or logs errors.
# Usage: powershell -ExecutionPolicy Bypass -File .\monitoring\create_alerts.ps1 -Email you@example.com
param(
    [Parameter(Mandatory = $true)][string]$Email,
    [string]$ResourceGroup = 'rg-depi-weather',
    [string]$Workspace = 'depi-logs'
)
$ErrorActionPreference = 'Stop'
$workspaceId = az monitor log-analytics workspace show -g $ResourceGroup -n $Workspace --query id -o tsv
az monitor action-group create -g $ResourceGroup -n depi-alerts --short-name depialert --action email owner $Email | Out-Null
$actionGroupId = az monitor action-group show -g $ResourceGroup -n depi-alerts --query id -o tsv
$query = 'ContainerAppConsoleLogs_CL | where ContainerJobName_s startswith "depi-" and Log_s has_any ("ERROR","Traceback","rate_limited")'
az monitor scheduled-query create -g $ResourceGroup -n depi-job-errors --scopes $workspaceId `
    --condition "count 'Errors' > 0" --condition-query Errors="$query" `
    --evaluation-frequency 1h --window-size 1h --severity 2 --action-groups $actionGroupId `
    --description "DEPI weather job logged errors in the last hour"
Write-Host 'Alert created: depi-job-errors'
