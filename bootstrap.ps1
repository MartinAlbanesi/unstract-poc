# bootstrap.ps1 — Reproduces run-platform.sh for the Unstract OSS platform on Windows.
#
# Pins Unstract release v0.186.1, replicates the env-file setup run-platform.sh does,
# and brings the platform up with `docker compose`. Idempotent: safe to re-run.
#
# Usage:
#   .\bootstrap.ps1            # full setup + compose up
#   .\bootstrap.ps1 -OnlyEnv   # only create/merge .env files
#   .\bootstrap.ps1 -Version v0.186.1

param(
    [string]$Version = "v0.186.1",
    [switch]$OnlyEnv,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = $PSScriptRoot
$upstreamDir = Join-Path $scriptDir "unstract"
$upstreamUrl = "https://github.com/Zipstack/unstract.git"

function Log { param([string]$msg, [string]$color = "White") Write-Host $msg -ForegroundColor $color }
function Warn { param([string]$msg) Write-Host ("WARN: " + $msg) -ForegroundColor Yellow }
function Fail { param([string]$msg) Write-Host ("ERROR: " + $msg) -ForegroundColor Red; exit 1 }
function DebugLog { param([string]$msg) if ($Verbose) { Write-Host ("DEBUG: " + $msg) -ForegroundColor DarkGray } }

function Get-FernetKey {
    # Same generation as run-platform.sh: base64 url-safe of 32 random bytes.
    python -c "import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
}

function Test-Dependency {
    param([string]$cmd, [string]$hint)
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Fail "Dependency '$cmd' not found. $hint"
    }
}

function Test-DockerDaemon {
    try {
        $info = docker info 2>&1
        if ($LASTEXITCODE -ne 0) { Fail "Docker daemon is not reachable. Ensure Docker Desktop is running." }
    } catch {
        Fail "Docker daemon is not reachable. Ensure Docker Desktop is running."
    }
}

function Get-OccupiedPorts {
    param([int[]]$Ports)
    $occupied = @()
    foreach ($p in $Ports) {
        $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
        if ($conn) { $occupied += $p }
    }
    , @($occupied)
}

function Test-OwnStackRunning {
    # The compose project name defaults to "docker" (the dir docker-compose.yaml
    # lives in) unless COMPOSE_PROJECT_NAME overrides it. If it's already up,
    # the platform's own containers legitimately hold the platform ports and
    # the preflight below must not treat that as a conflict (BOOT-1 idempotency).
    try {
        $projects = docker compose ls --format json 2>$null | ConvertFrom-Json
    } catch {
        return $false
    }
    if (-not $projects) { return $false }
    $projectName = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { "docker" }
    $ours = $projects | Where-Object { $_.Name -eq $projectName -and $_.Status -match "^running" }
    return [bool]$ours
}

function Test-PortPreflight {
    if (Test-OwnStackRunning) {
        Log "Unstract platform is already running (compose project up) - skipping port preflight." -color Green
        return
    }
    # Host ports bound by the platform (post-override), from docker-compose.yaml +
    # docker-compose-dev-essentials.yaml + our docker-compose.override.yaml.
    $platformPorts = @(80, 8080, 3100, 8100, 5433, 3001, 3004, 5002, 8185, 8086, 8087, 8088, 8089, 8090, 6379, 6333, 9000, 9001, 8082, 8083, 5555)
    $occupied = Get-OccupiedPorts -Ports $platformPorts
    if ($occupied.Count -gt 0) {
        $list = ($occupied | Sort-Object | ForEach-Object { "  port $_" }) -join "`n"
        Fail "Port preflight FAILED. The following ports are already in use:`n$list`n`nFree them (or stop the occupying services) and re-run."
    }
    Log "Port preflight OK (all $($platformPorts.Count) platform ports free)." -color Green
}

function Get-ComposeServices {
    # Same list run-platform.sh derives from docker-compose.build.yaml, minus ignored services.
    $buildCompose = Join-Path $upstreamDir "docker/docker-compose.build.yaml"
    $env:VERSION = $Version
    $services = docker compose -f $buildCompose config --services 2>$null
    if ($LASTEXITCODE -ne 0) { Fail "Could not parse docker-compose.build.yaml. Is 'docker compose' available?" }
    $ignore = @("tool-sidecar", "tool-classifier", "tool-text_extractor", "worker-unified")
    $result = @($services | Where-Object { $ignore -notcontains $_ })
    $result += "workers"
    $result
}

function Set-EnvValue {
    param([string]$file, [string]$key, [string]$value)
    $lines = [System.IO.File]::ReadAllLines($file)
    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$([regex]::Escape($key))\s*=") {
            $lines[$i] = "$key=`"$value`""
            $found = $true
            break
        }
    }
    if (-not $found) { $lines += "$key=`"$value`"" }
    [System.IO.File]::WriteAllLines($file, $lines)
}

function Get-InternalServiceApiKey {
    # Shared bearer key for worker->backend internal auth. Not a Fernet key —
    # just a random opaque token, generated the same way run-platform.sh
    # generates ENCRYPTION_KEY so we don't depend on backend/sample.env and
    # workers/sample.env happening to ship matching hardcoded defaults.
    python -c "import secrets; print(secrets.token_hex(32))"
}

function Setup-Env {
    $services = Get-ComposeServices
    $firstSetup = $false
    $encryptionKey = Get-FernetKey
    $internalApiKey = Get-InternalServiceApiKey

    foreach ($svc in $services) {
        $sample = Join-Path $upstreamDir "$svc/sample.env"
        $envPath = Join-Path $upstreamDir "$svc/.env"
        if (-not (Test-Path $sample)) { continue }
        if (-not (Test-Path $envPath)) {
            $firstSetup = $true
            Copy-Item -LiteralPath $sample -Destination $envPath
            Log "Created .env for $svc"
            if ($svc -eq "backend" -or $svc -eq "platform-service") {
                Set-EnvValue -file $envPath -key "ENCRYPTION_KEY" -value $encryptionKey
                Log "  -> injected ENCRYPTION_KEY"
            }
            if ($svc -eq "backend") {
                Set-EnvValue -file $envPath -key "DEFAULT_AUTH_USERNAME" -value "unstract"
                Set-EnvValue -file $envPath -key "DEFAULT_AUTH_PASSWORD" -value "unstract"
                Log "  -> injected default auth (unstract/unstract)"
            }
            if ($svc -eq "backend" -or $svc -eq "workers") {
                Set-EnvValue -file $envPath -key "INTERNAL_SERVICE_API_KEY" -value $internalApiKey
                Log "  -> injected shared INTERNAL_SERVICE_API_KEY"
            }
        } else {
            DebugLog ".env already exists for $svc — skipping (idempotent)."
        }
    }

    $essentialsSample = Join-Path $upstreamDir "docker/sample.essentials.env"
    $essentialsEnv = Join-Path $upstreamDir "docker/essentials.env"
    if (-not (Test-Path $essentialsEnv)) {
        Copy-Item -LiteralPath $essentialsSample -Destination $essentialsEnv
        Log "Created docker/essentials.env"
    }

    $dockerSample = Join-Path $upstreamDir "docker/sample.env"
    $dockerEnv = Join-Path $upstreamDir "docker/.env"
    if (-not (Test-Path $dockerEnv)) {
        Copy-Item -LiteralPath $dockerSample -Destination $dockerEnv
        Log "Created docker/.env"
    }

    # Windows fix: replace the ${PWD} relative path with an absolute one so the
    # tool registry mount resolves (compose does not expand ${PWD} on Windows hosts).
    $toolRegistry = Join-Path $upstreamDir "unstract/tool-registry/tool_registry_config"
    if (Test-Path -LiteralPath $toolRegistry) {
        $absolute = $toolRegistry.Replace("\", "/")
        Set-EnvValue -file $dockerEnv -key "TOOL_REGISTRY_CONFIG_SRC_PATH" -value $absolute
        Log "Absolute TOOL_REGISTRY_CONFIG_SRC_PATH set: $absolute"
    } else {
        Warn "tool_registry_config path not found: $toolRegistry"
    }

    # traefik file provider: the file MUST exist, otherwise Docker creates a directory.
    $proxyOverrides = Join-Path $upstreamDir "docker/proxy_overrides.yaml"
    if (-not (Test-Path -LiteralPath $proxyOverrides)) {
        Set-Content -LiteralPath $proxyOverrides -Value "# Minimal override file - empty on purpose." -NoNewline
        Log "Created docker/proxy_overrides.yaml (minimal)."
    }

    # Port remap override (host port collisions). Always copy: it is the source of truth.
    $overrideSrc = Join-Path $scriptDir "docker-compose.override.yaml"
    $overrideDst = Join-Path $upstreamDir "docker/docker-compose.override.yaml"
    if (Test-Path -LiteralPath $overrideSrc) {
        Copy-Item -LiteralPath $overrideSrc -Destination $overrideDst -Force
        Log "Applied docker-compose.override.yaml (port remap)."
    }

    if ($firstSetup) {
        Log ""
        Log "###################### BACKUP ENCRYPTION KEY ######################" -color Yellow
        Log "Copy the value of ENCRYPTION_KEY from either of these files to a secure location:" -color Yellow
        Log "  - $(Join-Path $upstreamDir 'backend\.env')" -color Yellow
        Log "  - $(Join-Path $upstreamDir 'platform-service\.env')" -color Yellow
        Log "Store it somewhere safe. Losing it makes existing adapter credentials unreadable." -color Yellow
        Log "###################################################################" -color Yellow
    }
}

function Update-Clone {
    if (Test-Path -LiteralPath $upstreamDir) {
        Push-Location $upstreamDir
        try {
            $checkedOutTag = git describe --tags --exact-match 2>$null
        } finally {
            Pop-Location
        }
        if ($checkedOutTag -and $checkedOutTag -ne $Version) {
            Warn "Vendored clone at $upstreamDir is pinned to '$checkedOutTag', not requested '$Version'. Delete $upstreamDir and re-run to reclone at the requested tag."
        }
        Log "Upstream clone already present at $upstreamDir - skipping clone."
        return
    }
    Log "Cloning Unstract $Version (shallow, single-branch)..."
    git clone --depth 1 --branch $Version $upstreamUrl $upstreamDir
    if ($LASTEXITCODE -ne 0) { Fail "git clone failed. Check network / that tag '$Version' exists." }
}

function Run-Compose {
    Log "Starting Unstract platform containers (VERSION=$Version, detached)..."
    Push-Location (Join-Path $upstreamDir "docker")
    try {
        $env:VERSION = $Version
        docker compose up -d
        if ($LASTEXITCODE -ne 0) { Fail "docker compose up failed." }
    } finally {
        Pop-Location
        Remove-Item Env:VERSION -ErrorAction SilentlyContinue
    }
    Log ""
    # *.unstract.localhost hostnames don't resolve via plain DNS on Windows
    # (only via systemd-resolved on Linux hosts) - our docker-compose.override.yaml
    # remaps frontend to host port 3100, so that's the address that actually works here.
    Log "Services are starting. Visit http://localhost:3100 once ready." -color Green
    Log "Login: unstract / unstract"
    Log "Monitor: .\healthcheck.ps1"
}

Log "=== Unstract POC bootstrap ($Version) ==="
Log ""

Test-Dependency "git" "Install Git for Windows."
Test-Dependency "python" "Install Python 3 (python on PATH)."
Test-Dependency "docker" "Install Docker Desktop."
Test-DockerDaemon
Test-PortPreflight

Update-Clone
Setup-Env

if ($OnlyEnv) {
    Log "Done (env setup only)." -color Green
    exit 0
}

Run-Compose
