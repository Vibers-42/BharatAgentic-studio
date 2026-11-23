<#
Checks a running Phase 1 stack (after `docker compose up -d --build`) and prints PASS / FAIL / SKIP
per check. Native Windows version of verify_phase1.sh; works in Windows PowerShell 5.1 and PowerShell 7.

Run from anywhere:
  powershell -ExecutionPolicy Bypass -File scripts\verify_phase1.ps1

PASS = verified. FAIL = checked and wrong. SKIP = could not be checked (for example the container
it needs is not running); a SKIP is never counted as a pass. Exit code is 0 only if all 22 checks pass.
#>

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)   # repo root, so `docker compose` finds the project

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0

function Write-Result([string]$Result, [string]$Name, [string]$Detail = '') {
    $colors = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow' }
    $line = '{0,-5} {1}' -f $Result, $Name
    if ($Detail) { $line += "  ($Detail)" }
    Write-Host $line -ForegroundColor $colors[$Result]
    switch ($Result) {
        'PASS' { $script:Pass++ }
        'FAIL' { $script:Fail++ }
        'SKIP' { $script:Skip++ }
    }
}

# Runs one check. $Test returns $true (PASS) or a string explaining the failure (FAIL).
# $Needs lists services that must be running; if one is not, the check is SKIP, never PASS.
function Test-Check([string]$Name, [scriptblock]$Test, [string[]]$Needs = @()) {
    foreach ($need in $Needs) {
        if (-not $script:Running[$need]) { Write-Result 'SKIP' $Name "$need is not running"; return }
    }
    try {
        $result = & $Test
        if ($result -is [bool] -and $result) { Write-Result 'PASS' $Name }
        elseif ($result) { Write-Result 'FAIL' $Name "$result" }
        else { Write-Result 'FAIL' $Name }
    } catch {
        Write-Result 'FAIL' $Name $_.Exception.Message
    }
}

# Runs a native command and returns its stdout as one string plus its exit code.
# stderr is discarded so Windows PowerShell 5.1 does not turn it into error records.
function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$StdIn) {
    if ($PSBoundParameters.ContainsKey('StdIn')) { $out = $StdIn | & $Exe @Arguments 2>$null }
    else { $out = & $Exe @Arguments 2>$null }
    [pscustomobject]@{ Output = (($out | Out-String).Trim()); Code = $LASTEXITCODE }
}

# Runs Python code inside a container. The code goes in through stdin, so no quoting problems.
function Invoke-ContainerPython([string]$Service, [string]$Code) {
    Invoke-Native 'docker' @('compose', 'exec', '-T', $Service, 'python', '-') $Code
}

function Get-Json([string]$Url) {
    $res = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
    $res.Content | ConvertFrom-Json
}

function Get-Text([string]$Url) {
    (Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10).Content
}

# ---------- Preflight: these stop the script, because nothing below could be trusted ----------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host 'docker was not found on PATH. Install or start Docker Desktop.' -ForegroundColor Red; exit 1
}
$info = Invoke-Native 'docker' @('info', '--format', '{{.ServerVersion}}')
if ($info.Code -ne 0 -or -not $info.Output) {
    Write-Host 'Docker Desktop is not running (docker info failed). Start it and try again.' -ForegroundColor Red; exit 1
}
if (-not (Test-Path '.env')) {
    Write-Host 'No .env found. Run: Copy-Item .env.example .env   (then set POSTGRES_PASSWORD)' -ForegroundColor Red; exit 1
}

$envValues = @{}
foreach ($line in Get-Content '.env') {
    if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $envValues[$Matches[1]] = $Matches[2].Trim() }
}
$DbUser = if ($envValues['POSTGRES_USER']) { $envValues['POSTGRES_USER'] } else { 'bharat' }
$DbName = if ($envValues['POSTGRES_DB']) { $envValues['POSTGRES_DB'] } else { 'bharatagent' }

Write-Host "Docker engine $($info.Output), project folder $(Get-Location)"

# Container id and state for each service, used by the checks and by the SKIP rule.
$Services = @('db', 'ollama', 'mcp-runner', 'api', 'web')
$script:Ids = @{}
$script:States = @{}
$script:Running = @{}
foreach ($svc in $Services) {
    $id = (Invoke-Native 'docker' @('compose', 'ps', '-a', '-q', $svc)).Output
    $script:Ids[$svc] = $id
    $state = if ($id) { (Invoke-Native 'docker' @('inspect', '-f', '{{.State.Status}}', $id)).Output } else { 'missing' }
    $script:States[$svc] = $state
    $script:Running[$svc] = ($state -eq 'running')
}

function Get-Health([string]$Service) {
    $id = $script:Ids[$Service]
    if (-not $id) { return "no container for $Service" }
    if (-not $script:Running[$Service]) { return "container is $($script:States[$Service])" }
    $h = (Invoke-Native 'docker' @('inspect', '-f', '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}', $id)).Output
    if ($h -eq 'healthy') { return $true }
    "health is '$h'"
}

# ---------- Containers ----------
Write-Host "`n== Containers"
foreach ($svc in @('db', 'ollama', 'mcp-runner', 'api')) {
    Test-Check "$svc is healthy" { Get-Health $svc }
}
Test-Check 'web is running' {
    if ($script:Running['web']) { $true } else { "container is $($script:States['web'])" }
}

# ---------- API ----------
Write-Host "`n== API"
Test-Check 'GET :8000/health returns ok' {
    (Get-Json 'http://127.0.0.1:8000/health').ok -eq $true
} -Needs api
$status = $null
try { $status = Get-Json 'http://127.0.0.1:8000/status' } catch { }
foreach ($field in @('db', 'ollama', 'mcp_runner')) {
    Test-Check "GET :8000/status shows $field up" {
        if ($null -eq $status) { return 'GET /status failed' }
        if ($status.$field -eq $true) { $true } else { "$field is '$($status.$field)'" }
    } -Needs api
}

# ---------- Web ----------
Write-Host "`n== Web"
Test-Check 'web serves the React page on :3000' {
    (Get-Text 'http://127.0.0.1:3000/') -match 'id="root"'
} -Needs web
Test-Check 'web proxies /api/status to the api' {
    (Get-Json 'http://127.0.0.1:3000/api/status').api -eq $true
} -Needs web, api

# ---------- Database ----------
Write-Host "`n== Database"
$psqlArgs = @('compose', 'exec', '-T', 'db', 'psql', '-U', $DbUser, '-d', $DbName, '-tA', '-v', 'ON_ERROR_STOP=1')
Test-Check 'pgvector extension installed' {
    $r = Invoke-Native 'docker' $psqlArgs "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
    if ($r.Code -ne 0) { return "psql exited with $($r.Code)" }
    if ($r.Output -match '^\d+\.\d+') { $true } else { "no vector extension (got '$($r.Output)')" }
} -Needs db
Test-Check 'tables users, graphs, chunks, usage exist' {
    $sql = "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name IN ('users', 'graphs', 'chunks', 'usage');"
    $r = Invoke-Native 'docker' $psqlArgs $sql
    if ($r.Code -ne 0) { return "psql exited with $($r.Code)" }
    if ($r.Output -eq '4') { $true } else { "found $($r.Output) of 4 tables" }
} -Needs db
Test-Check 'db publishes no ports to the host' {
    # Must get a real, successful answer from docker; an error here is a FAIL, not a pass.
    $r = Invoke-Native 'docker' @('port', $script:Ids['db'])
    if ($r.Code -ne 0) { return "docker port exited with $($r.Code)" }
    if (-not $r.Output) { $true } else { "published: $($r.Output)" }
} -Needs db

# ---------- Network isolation ----------
Write-Host "`n== Network isolation"
$probe = @'
import socket
try:
    socket.create_connection(("{HOST}", {PORT}), 3).close()
    print("REACHABLE")
except OSError as e:
    print("BLOCKED", type(e).__name__)
'@
function Get-Probe([string]$Service, [string]$TargetHost, [int]$Port) {
    $code = $probe.Replace('{HOST}', $TargetHost).Replace('{PORT}', "$Port")
    Invoke-ContainerPython $Service $code
}
# The probe must run and print BLOCKED inside mcp-runner. A failed exec prints nothing, which is a FAIL.
Test-Check 'mcp-runner CANNOT reach db:5432' {
    $r = Get-Probe 'mcp-runner' 'db' 5432
    if ($r.Output -match '^BLOCKED') { $true }
    elseif ($r.Output -match '^REACHABLE') { 'SECURITY: mcp-runner can connect to the database' }
    else { "probe did not run (exit $($r.Code))" }
} -Needs mcp-runner, db
Test-Check 'api CAN reach db:5432' {
    $r = Get-Probe 'api' 'db' 5432
    if ($r.Output -match '^REACHABLE') { $true } else { "got '$($r.Output)' (exit $($r.Code))" }
} -Needs api, db
Test-Check 'api CAN reach mcp-runner:8100' {
    $r = Get-Probe 'api' 'mcp-runner' 8100
    if ($r.Output -match '^REACHABLE') { $true } else { "got '$($r.Output)' (exit $($r.Code))" }
} -Needs api, mcp-runner

# ---------- MCP ----------
Write-Host "`n== MCP"
Test-Check 'ping tool returns pong' {
    $code = @'
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client("http://localhost:8100/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("ping", {})
            print("RESULT", res.content[0].text)

asyncio.run(main())
'@
    $r = Invoke-ContainerPython 'mcp-runner' $code
    if ($r.Output -match 'RESULT pong') { $true } else { "got '$($r.Output)' (exit $($r.Code))" }
} -Needs mcp-runner

# ---------- Ollama ----------
Write-Host "`n== Ollama"
Test-Check 'ollama CLI responds' {
    $r = Invoke-Native 'docker' @('compose', 'exec', '-T', 'ollama', 'ollama', '--version')
    if ($r.Code -eq 0 -and $r.Output -match 'version') { $true } else { "exit $($r.Code), output '$($r.Output)'" }
} -Needs ollama
Test-Check 'ollama API answers on :11434' {
    (Get-Text 'http://127.0.0.1:11434/') -match 'Ollama is running'
} -Needs ollama

# ---------- Secrets ----------
Write-Host "`n== Secrets"
$gitOk = [bool](Get-Command git -ErrorAction SilentlyContinue) -and
         ((Invoke-Native 'git' @('rev-parse', '--is-inside-work-tree')).Output -eq 'true')
foreach ($check in @(
    @{ Name = '.env is ignored by git'; Test = {
        $r = Invoke-Native 'git' @('check-ignore', '-q', '.env')
        if ($r.Code -eq 0) { $true } else { '.env is NOT ignored' } } },
    @{ Name = '.env is not tracked by git'; Test = {
        # exit 1 = not tracked (good); exit 0 = tracked (bad); anything else = git error (fail).
        $r = Invoke-Native 'git' @('ls-files', '--error-unmatch', '.env')
        if ($r.Code -eq 1) { $true } elseif ($r.Code -eq 0) { 'SECURITY: .env is committed' } else { "git exited with $($r.Code)" } } }
)) {
    if ($gitOk) { Test-Check $check.Name $check.Test } else { Write-Result 'SKIP' $check.Name 'not a git repository or git missing' }
}

# ---------- Summary ----------
$total = $script:Pass + $script:Fail + $script:Skip
Write-Host ''
Write-Host "Result: $($script:Pass) passed, $($script:Fail) failed, $($script:Skip) skipped (of $total)"
if ($script:Fail -eq 0 -and $script:Skip -eq 0) {
    Write-Host 'Phase 1 stack verified.' -ForegroundColor Green; exit 0
}
Write-Host 'Phase 1 stack NOT fully verified.' -ForegroundColor Red; exit 1
