[CmdletBinding()]
param(
    [string]$ComposeFile = "",
    [string]$EnvFile = "",
    [ValidateRange(1, 600)]
    [int]$ReadinessTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $ComposeFile) {
    $ComposeFile = Join-Path $repoRoot "docker-compose.yml"
}
if (-not $EnvFile) {
    $EnvFile = Join-Path $repoRoot ".env"
}

$compose = @("compose", "--env-file", $EnvFile, "-f", $ComposeFile)
$started = $false

function Invoke-Compose {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$Capture
    )

    $output = & docker @compose @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose command failed"
    }
    if ($Capture) {
        return ($output -join [Environment]::NewLine)
    }
}

function Assert-OnlyHttpsPublished {
    param([Parameter(Mandatory = $true)][string]$Json)

    $model = $Json | ConvertFrom-Json
    $published = @()
    foreach ($service in $model.services.PSObject.Properties) {
        foreach ($port in @($service.Value.ports)) {
            if ($null -ne $port -and $null -ne $port.published) {
                $published += [PSCustomObject]@{
                    Service = $service.Name
                    Port = [int]$port.published
                }
            }
        }
    }
    if ($published.Count -ne 1) {
        throw "production compose must publish exactly one port"
    }
    if ($published[0].Service -ne "nginx" -or $published[0].Port -ne 443) {
        throw "production compose may publish only nginx port 443"
    }
}

try {
    Invoke-Compose -Arguments @("config", "--quiet")
    Invoke-Compose -Arguments @("build")
    $started = $true
    Invoke-Compose -Arguments @("up", "-d")

    Invoke-Compose -Arguments @("exec", "-T", "api", "alembic", "upgrade", "head")

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($ReadinessTimeoutSeconds)
    $ready = $false
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        & docker @compose exec -T nginx wget -q -O /dev/null `
            http://api:8000/api/v1/health/ready 2>$null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        throw "readiness did not become healthy within the bounded window"
    }

    $configJson = Invoke-Compose -Arguments @("config", "--format", "json") -Capture
    Assert-OnlyHttpsPublished -Json $configJson
    Write-Output "M1_COMPOSE_SMOKE_PASSED"
}
finally {
    if ($started) {
        & docker @compose down
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "docker compose down failed"
        }
    }
}
