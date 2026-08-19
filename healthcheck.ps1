# healthcheck.ps1 — Verifies the Unstract platform is up and reports status.
#
# Usage:
#   .\healthcheck.ps1              # full check (frontend, backend, core service states)
#   .\healthcheck.ps1 -Wait 600    # poll every 15s until healthy or timeout (seconds)
#
# Pinned to the same release as bootstrap.ps1 (v0.186.1) — only used to locate
# the compose file for the `docker compose ps` fallback; VERSION does not need
# to match the actually-running image tag for that to work.
#
# NOTE on worker HTTP health: v0.186.1's docker-compose.yaml sets a generic
# `HEALTH_PORT=8090` env var on worker-api-deployment-v2, but the shipped
# worker entrypoint reads a per-type var (e.g. API_DEPLOYMENT_HEALTH_PORT)
# that is never set there, so no HTTP health server actually binds inside
# that container (verified: nothing listens on 8090; the worker itself is
# fully healthy per its logs — connected to RabbitMQ, task queue ready).
# That's a vendored upstream quirk we don't patch (unstract/ stays untouched).
# Worker liveness is therefore verified via `docker compose ps` state below,
# consistent with design D3's documented fallback path.

param(
    [int]$Wait = 0,
    [string]$Version = "v0.186.1"
)

$ErrorActionPreference = "Stop"

# Windows: the *.unstract.localhost names resolve via systemd-resolved on Linux
# but NOT on Windows, and our docker-compose.override.yaml remaps the host ports
# (frontend 3100, backend 8100). Probe the remapped host ports directly.
$frontendUrl = "http://localhost:3100"
$backendHealthUrl = "http://localhost:8100/health"

# Core services BOOT-2 requires to "report up" (spec scenario), checked by
# compose state rather than HTTP where no reachable HTTP endpoint exists.
$coreServices = @("backend", "frontend", "reverse-proxy", "runner", "x2text-service", "worker-api-deployment-v2")

$deadline = if ($Wait -gt 0) { (Get-Date).AddSeconds($Wait) } else { $null }

function Test-Http {
    param([string]$url, [int]$timeoutMs = 10000)
    try {
        $r = Invoke-WebRequest -Uri $url -TimeoutSec ($timeoutMs / 1000) -UseBasicParsing -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Get-ContainerStatus {
    $env:VERSION = $Version
    docker compose -f (Join-Path $PSScriptRoot "unstract/docker/docker-compose.yaml") ps --format "{{.Service}}\t{{.State}}" 2>$null
}

function Check-Once {
    $results = @()

    # 1. Frontend (login page).
    $frontend = Test-Http $frontendUrl
    $results += [pscustomobject]@{ Check = "frontend ($frontendUrl)"; Ok = $frontend }

    # 2. Backend /health.
    $backendHealth = Test-Http $backendHealthUrl
    $results += [pscustomobject]@{ Check = "backend /health ($backendHealthUrl)"; Ok = $backendHealth }

    $containerStatus = @(Get-ContainerStatus)

    # 3. Named core services (BOOT-2): each must be present and in state "running".
    foreach ($svc in $coreServices) {
        $line = $containerStatus | Where-Object { $_ -match "^$([regex]::Escape($svc))\t" }
        $up = [bool]($line -and ($line -match "`trunning$"))
        $results += [pscustomobject]@{ Check = "service '$svc' running"; Ok = $up }
    }

    # 4. Any other container in a bad state (extra safety net beyond the named list).
    $bad = @($containerStatus | Where-Object { $_ -match "\t(exited|restarting|dead)" })
    $results += [pscustomobject]@{ Check = "no unexpected exited/restarting/dead containers"; Ok = ($bad.Count -eq 0) -and ($containerStatus.Count -gt 0) }

    $results
}

function Write-FailedChecks {
    param($results)
    $failed = @($results | Where-Object { -not $_.Ok })
    if ($failed.Count -eq 0) { return }
    Write-Host "Unhealthy:" -ForegroundColor Red
    foreach ($f in $failed) {
        Write-Host "  - $($f.Check)" -ForegroundColor Red
    }
}

if ($deadline) {
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        $res = Check-Once
        if (@($res | Where-Object { -not $_.Ok }).Count -eq 0) { $ready = $true; break }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 15
    }
    if (-not $ready) {
        Write-Host ""
        Write-Host "Timed out waiting for platform to become healthy." -ForegroundColor Red
        $final = Check-Once
        $final | Format-Table -AutoSize
        Write-FailedChecks $final
        exit 1
    }
    Write-Host ""
    Write-Host "Platform is healthy." -ForegroundColor Green
    Check-Once | Format-Table -AutoSize
    exit 0
}

$res = Check-Once
$res | Format-Table -AutoSize
if (@($res | Where-Object { -not $_.Ok }).Count -gt 0) {
    Write-Host ""
    Write-FailedChecks $res
    Write-Host "See container states:" -ForegroundColor Yellow
    Get-ContainerStatus | Where-Object { $_ -match "\t(exited|restarting|dead)" }
    exit 1
} else {
    Write-Host ""
    Write-Host "All checks passed." -ForegroundColor Green
    exit 0
}
