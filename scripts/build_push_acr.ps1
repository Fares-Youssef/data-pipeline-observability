[CmdletBinding()]
param(
    [string]$RegistryName = 'depiweather56232c7d',
    [string]$ImageName = 'depi-weather',
    [string]$Tag = 'v1.0.0',
    [ValidateRange(1, 10)]
    [int]$PushAttempts = 4
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host 'Checking Docker Engine...'
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'Docker Engine is not running. Start the updated Docker Desktop and wait until it reports that the engine is running.'
}

Write-Host 'Reading Azure Container Registry...'
$LoginServer = az acr show --name $RegistryName --query loginServer --output tsv
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($LoginServer)) {
    throw "Azure Container Registry '$RegistryName' was not found or is not accessible."
}

$FullImage = "${LoginServer}/${ImageName}:${Tag}"

Write-Host "Signing in to $LoginServer with the current Azure identity..."
az acr login --name $RegistryName
if ($LASTEXITCODE -ne 0) {
    throw 'Azure Container Registry sign-in failed.'
}

Write-Host "Building $FullImage..."
docker build --pull --platform linux/amd64 --tag $FullImage $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Docker image build failed.'
}

$PushSucceeded = $false
for ($Attempt = 1; $Attempt -le $PushAttempts; $Attempt++) {
    Write-Host "Pushing $FullImage (attempt $Attempt of $PushAttempts)..."
    docker push $FullImage
    if ($LASTEXITCODE -eq 0) {
        $PushSucceeded = $true
        break
    }

    if ($Attempt -lt $PushAttempts) {
        $DelaySeconds = [Math]::Min(60, 15 * $Attempt)
        Write-Warning "Image push was interrupted. Retrying in $DelaySeconds seconds; completed layers will be reused."
        Start-Sleep -Seconds $DelaySeconds
    }
}

if (-not $PushSucceeded) {
    throw "Docker image push failed after $PushAttempts attempts."
}

Write-Host 'Verifying the pushed image...'
az acr repository show --name $RegistryName --image "${ImageName}:${Tag}" --query '{image:name,digest:digest,lastUpdated:lastUpdateTime}' --output table
if ($LASTEXITCODE -ne 0) {
    throw 'The image was pushed, but registry verification failed.'
}

Write-Host "Image ready: $FullImage"
