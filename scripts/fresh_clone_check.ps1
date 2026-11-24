# Week 11-12: "can a stranger follow the README and get a working system?" (Windows version)
# Clones the repo fresh into a temp folder, builds everything, runs unit + e2e tests
# with the real gateway, then cleans up.
#
#   powershell -ExecutionPolicy Bypass -File scripts\fresh_clone_check.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\fresh_clone_check.ps1 -Branch person1-week10-12
#
# WARNING: stops any running containers named app1..3, controller, gateway, prometheus, grafana
#          (the names are fixed in docker-compose.yml, so two stacks cannot run at once).
param(
    [string]$Repo = "https://github.com/AndrewOh077/Software-project.git",
    [string]$Branch = "main"
)

# "Continue", not "Stop": git and docker print progress on stderr, which PowerShell 5.1
# would otherwise treat as an error. Every command's exit code is checked explicitly instead.
$ErrorActionPreference = "Continue"
$start = Get-Date
$work = Join-Path $env:TEMP ("shp-check-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
$failed = $false

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" } }
function Up([string]$url) {
    try { Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 2 | Out-Null; return $true } catch { return $false }
}

try {
    Step "check tools"
    foreach ($t in "git", "docker", "python") {
        if (-not (Get-Command $t -ErrorAction SilentlyContinue)) { throw "missing: $t" }
    }
    docker compose version; Check "docker compose"

    Step "clone $Repo ($Branch)"
    git clone --quiet --depth 1 --branch $Branch $Repo "$work\repo"; Check "git clone"
    Set-Location "$work\repo"

    Step "unit tests (no Docker needed)"
    python -m unittest discover -s tests/unit; Check "unit tests"

    Step "stop containers with the same names"
    docker rm -f app1 app2 app3 controller gateway gateway-mock prometheus grafana 2>$null | Out-Null

    Step "build and start (first time: several minutes)"
    docker compose up -d --build; Check "docker compose up"

    Step "wait for controller and gateway"
    $ready = $false
    for ($i = 0; $i -lt 90; $i++) {
        if ((Up "http://localhost:9100/healthz") -and (Up "http://localhost:8080/health")) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw "controller or gateway did not come up (see: docker compose logs)" }

    Step "e2e scenario tests with the real gateway (about 5 minutes)"
    $env:REAL_GATEWAY = "1"
    $env:GATEWAY_ADMIN = "http://localhost:8080"
    python -m unittest discover -s tests/e2e -v; Check "e2e tests"

    $secs = [int]((Get-Date) - $start).TotalSeconds
    Write-Host "`nOK: fresh clone of $Branch builds and passes all tests in ${secs}s" -ForegroundColor Green
}
catch {
    $failed = $true
    Write-Host "`nFAILED: $_" -ForegroundColor Red
}
finally {
    if (Test-Path "$work\repo") {
        Push-Location "$work\repo"
        docker compose down -v --remove-orphans 2>$null | Out-Null
        Pop-Location
    }
    Set-Location $env:USERPROFILE
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
    Remove-Item Env:\REAL_GATEWAY, Env:\GATEWAY_ADMIN -ErrorAction SilentlyContinue
}
if ($failed) { exit 1 }
