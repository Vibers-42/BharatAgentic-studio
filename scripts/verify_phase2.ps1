<#
Checks the Phase 2 pipeline engine on the running Docker stack and prints PASS / FAIL / SKIP per check.
Works in Windows PowerShell 5.1 and PowerShell 7. Run verify_phase1.ps1 too: Phase 2 must not break Phase 1.

Run from anywhere:
  powershell -ExecutionPolicy Bypass -File scripts\verify_phase2.ps1

HTTP goes through .NET HttpClient and is read as raw UTF-8, so Telugu and Hindi text is never
re-encoded by PowerShell. This file stays ASCII; Indic questions are written as JSON \u escapes.
Exit code is 0 only if every check passes.
#>

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0
$Api = 'http://127.0.0.1:8000'
$Web = 'http://127.0.0.1:3000/api'

function Write-Result([string]$Result, [string]$Name, [string]$Detail = '') {
    $colors = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow' }
    $line = '{0,-5} {1}' -f $Result, $Name
    if ($Detail) { $line += "  ($Detail)" }
    Write-Host $line -ForegroundColor $colors[$Result]
    switch ($Result) { 'PASS' { $script:Pass++ } 'FAIL' { $script:Fail++ } 'SKIP' { $script:Skip++ } }
}

# $Test returns $true for PASS, or a string saying what was wrong. Throwing is a FAIL.
function Test-Check([string]$Name, [scriptblock]$Test) {
    if (-not $script:ApiUp) { Write-Result 'SKIP' $Name 'api is not reachable'; return }
    try {
        $result = & $Test
        if ($result -is [bool] -and $result) { Write-Result 'PASS' $Name }
        elseif ($result) { Write-Result 'FAIL' $Name "$result" }
        else { Write-Result 'FAIL' $Name }
    } catch {
        Write-Result 'FAIL' $Name $_.Exception.Message
    }
}

$script:Http = New-Object System.Net.Http.HttpClient
# Generous: a direct answer can fall back to the local qwen3:14b, whose first load takes about a minute.
$script:Http.Timeout = [TimeSpan]::FromSeconds(600)

# Sends a request and returns status code, content type, the body as UTF-8 text, and parsed JSON.
function Invoke-Api([string]$Method, [string]$Url, [string]$Body = $null) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)
    if ($Body) {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
        $req.Content = [System.Net.Http.ByteArrayContent]::new($bytes)
        $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    }
    $res = $script:Http.SendAsync($req).GetAwaiter().GetResult()
    $text = [System.Text.Encoding]::UTF8.GetString($res.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
    $json = $null
    try { $json = $text | ConvertFrom-Json } catch { }
    [pscustomobject]@{
        Status      = [int]$res.StatusCode
        ContentType = if ($res.Content.Headers.ContentType) { $res.Content.Headers.ContentType.MediaType } else { '' }
        Text        = $text
        Json        = $json
    }
}

# Splits a Server-Sent Events body into objects, checking each "event:" name matches its data type.
function ConvertFrom-Sse([string]$Text) {
    $events = @()
    foreach ($block in ($Text -replace "`r", '').Trim() -split "`n`n") {
        $name = $null; $data = $null
        foreach ($line in $block -split "`n") {
            if ($line.StartsWith('event: ')) { $name = $line.Substring(7) }
            if ($line.StartsWith('data: ')) { $data = $line.Substring(6) | ConvertFrom-Json }
        }
        if ($null -eq $data -or $name -ne $data.type) { throw "bad SSE block: $block" }
        $events += $data
    }
    , $events
}

# Since Phase 8 a graph runs inside a project and its source nodes point at the project's sources:
# the example graphs' source nodes are linked to the PDF this script uploads.
function Get-Graph([string]$Name) {
    $g = Get-Content "api\app\pipeline\examples\$Name.json" -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($n in $g.nodes) {
        if ($n.type -like 'source_*') { $n.config | Add-Member -NotePropertyName source_ref -NotePropertyValue $script:SrcId -Force }
    }
    $g
}

function ConvertTo-Body($Graph, [string]$QuestionJson, [string]$Language) {
    # $QuestionJson is already a JSON string literal, e.g. '"hi"' (see Get-JsonString below).
    $g = $Graph | ConvertTo-Json -Depth 10 -Compress
    if (-not $QuestionJson) { return $g }
    $lang = if ($Language) { ",`"language`":`"$Language`"" } else { '' }
    "{`"question`":$QuestionJson$lang,`"project_id`":$($script:Who.Project),`"graph`":$g}"
}

# One sorted line per node and edge, so two graphs compare equal even if key order differs
# (Postgres JSONB stores object keys in its own order).
function Get-Canonical($Graph) {
    $lines = foreach ($n in $Graph.nodes) {
        $cfg = @($n.config.PSObject.Properties | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value -join '+')" }) -join ';'
        "node $($n.id) $($n.type) {$cfg}"
    }
    $lines += foreach ($e in $Graph.edges) { "edge $($e.from)>$($e.to)" }
    ($lines | Sort-Object) -join "`n"
}

function Get-Codes($Response) { @($Response.Json.errors | ForEach-Object { $_.code }) }

# Runs a question in JSON mode and returns the final result event plus all events.
function Invoke-Run($Graph, [string]$QuestionJson) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Body $Graph $QuestionJson)
    if ($r.Status -ne 200) { throw "HTTP $($r.Status): $($r.Text)" }
    $events = @($r.Json.events)
    [pscustomobject]@{ Result = $events[-1]; Events = $events }
}

# Builds a JSON string literal from Unicode code points, using JSON escapes, so this file stays ASCII
# (Windows PowerShell 5.1 misreads non-ASCII characters in a script saved without a BOM).
function Get-JsonString([int[]]$CodePoints) {
    $backslash = [char]92
    '"' + (($CodePoints | ForEach-Object { if ($_ -lt 128) { [string][char]$_ } else { "${backslash}u{0:x4}" -f $_ } }) -join '') + '"'
}
# "refund policy what is?" in Telugu and in Hindi
$Telugu = Get-JsonString @(0x0C30, 0x0C3F, 0x0C2B, 0x0C02, 0x0C21, 0x0C4D, 32, 0x0C2A, 0x0C3E, 0x0C32, 0x0C38, 0x0C40, 32,
                           0x0C0F, 0x0C2E, 0x0C3F, 0x0C1F, 0x0C3F, 63)
$Hindi = Get-JsonString @(0x0930, 0x093F, 0x092B, 0x0902, 0x0921, 32, 0x0928, 0x0940, 0x0924, 0x093F, 32,
                          0x0915, 0x094D, 0x092F, 0x093E, 32, 0x0939, 0x0948, 63)
$ExpectedOrder = 'input:running input:done guardrails:running guardrails:done router:running router:done agent:running agent:done output:running output:done'

# ---------- Preflight ----------
$script:ApiUp = $false
try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
if ($script:ApiUp) {
    # Phase 8: sign in as a fresh test account (real registration, own session and CSRF token) and give its
    # project one PDF source, which the example graphs' source nodes use.
    . "$PSScriptRoot\_session.ps1"
    $script:Who = New-TestSession 'p2'
    $script:Http = $script:Who.Http
    $script:SrcId = Add-ProjectFile $script:Who 'samples\refund-policy.pdf'
}
if (-not $script:ApiUp) { Write-Host 'The api is not reachable on :8000. Run: docker compose up -d --build' -ForegroundColor Red }

# ---------- Graph format ----------
Write-Host "`n== Graph format and validation"
Test-Check 'GET /pipeline/default returns the five main blocks' {
    $types = (Invoke-Api 'GET' "$Api/pipeline/default").Json.nodes | ForEach-Object { $_.type }
    if (($types -join ',') -eq 'input,guardrails,router,agent,output') { $true } else { "got $($types -join ',')" }
}
Test-Check 'default graph validates' {
    $r = Invoke-Api 'POST' "$Api/pipeline/validate?project_id=$($script:Who.Project)" (ConvertTo-Body (Get-Graph 'default'))
    if ($r.Json.valid -eq $true) { $true } else { $r.Text }
}
Test-Check 'handoff example graph (2 sources, 2 tools) validates' {
    $r = Invoke-Api 'POST' "$Api/pipeline/validate?project_id=$($script:Who.Project)" (ConvertTo-Body (Get-Graph 'full'))
    if ($r.Json.valid -eq $true) { $true } else { $r.Text }
}

# Each broken graph must be rejected with its own error code.
$broken = @(
    @{ Name = 'missing agent is rejected (agent_count)'; Code = 'agent_count'; Break = {
        param($g) $g.nodes = @($g.nodes | Where-Object { $_.type -ne 'agent' }); $g.edges = @($g.edges | Where-Object { $_.from -ne 'n4' -and $_.to -ne 'n4' }) } },
    @{ Name = 'two input nodes are rejected (input_count)'; Code = 'input_count'; Break = {
        param($g) $g.nodes = @($g.nodes) + [pscustomobject]@{ id = 'n9'; type = 'input'; config = @{} } } },
    @{ Name = 'gap in the main line is rejected (main_line_gap)'; Code = 'main_line_gap'; Break = {
        param($g) $g.edges = @($g.edges | Where-Object { -not ($_.from -eq 'n2' -and $_.to -eq 'n3') }) } },
    @{ Name = 'shortcut input -> agent is rejected (main_line_shortcut)'; Code = 'main_line_shortcut'; Break = {
        param($g) $g.edges = @($g.edges) + [pscustomobject]@{ from = 'n1'; to = 'n4' } } },
    @{ Name = 'unconnected source is rejected (not_connected)'; Code = 'not_connected'; Break = {
        param($g) $g.edges = @($g.edges | Where-Object { $_.from -ne 's2' }) } },
    @{ Name = 'unconnected tool is rejected (not_connected)'; Code = 'not_connected'; Break = {
        param($g) $g.edges = @($g.edges | Where-Object { $_.from -ne 't1' }) } },
    @{ Name = 'MCP tool not read_only is rejected (mcp_not_read_only)'; Code = 'mcp_not_read_only'; Break = {
        param($g) ($g.nodes | Where-Object { $_.id -eq 't2' }).config.mode = 'read_write' } }
)
foreach ($case in $broken) {
    Test-Check $case.Name {
        $g = Get-Graph 'full'
        & $case.Break $g
        $r = Invoke-Api 'POST' "$Api/pipeline/validate?project_id=$($script:Who.Project)" (ConvertTo-Body $g)
        $codes = Get-Codes $r
        if ($r.Json.valid -eq $false -and $codes -contains $case.Code -and $r.Json.errors[0].message) { $true }
        else { "valid=$($r.Json.valid), codes=$($codes -join ',')" }
    }
}
Test-Check '/pipeline/run refuses an invalid graph with 422 and the errors' {
    $g = Get-Graph 'full'
    $g.edges = @($g.edges | Where-Object { $_.from -ne 's1' })
    $r = Invoke-Api 'POST' "$Api/pipeline/run" (ConvertTo-Body $g '"refund policy?"')
    $codes = @($r.Json.detail.errors | ForEach-Object { $_.code })
    if ($r.Status -eq 422 -and $codes -contains 'not_connected') { $true } else { "HTTP $($r.Status), codes=$($codes -join ',')" }
}

# ---------- Status events ----------
Write-Host "`n== Live status events (SSE)"
foreach ($target in @(@{ Name = 'api :8000'; Url = $Api }, @{ Name = 'nginx :3000'; Url = $Web })) {
    Test-Check "SSE via $($target.Name): 10 stage events in order, then result" {
        $r = Invoke-Api 'POST' "$($target.Url)/pipeline/run" (ConvertTo-Body (Get-Graph 'full') '"What is the refund policy?"')
        if ($r.Status -ne 200) { return "HTTP $($r.Status)" }
        if ($r.ContentType -ne 'text/event-stream') { return "content type '$($r.ContentType)'" }
        $events = ConvertFrom-Sse $r.Text
        $order = (@($events | Where-Object { $_.type -eq 'stage' }) | ForEach-Object { "$($_.stage):$($_.status)" }) -join ' '
        if ($order -ne $ExpectedOrder) { return "order was: $order" }
        if ($events[-1].type -ne 'result') { return "last event is '$($events[-1].type)'" }
        $true
    }
}
Test-Check 'each done event carries its node id (n1..n5)' {
    $ids = @((Invoke-Run (Get-Graph 'full') '"refund policy?"').Events | Where-Object { $_.status -eq 'done' }) | ForEach-Object { $_.node_id }
    if (($ids -join ',') -eq 'n1,n2,n3,n4,n5') { $true } else { "got $($ids -join ',')" }
}

# ---------- Stages ----------
Write-Host "`n== Stages"
foreach ($case in @(@{ Name = 'Telugu'; Q = $Telugu; Lang = 'te' }, @{ Name = 'Hindi'; Q = $Hindi; Lang = 'hi' })) {
    Test-Check "$($case.Name) question: language $($case.Lang) detected from the script, not blocked" {
        # Agent set to English, so only real script detection can produce te / hi.
        $g = Get-Graph 'full'
        ($g.nodes | Where-Object { $_.type -eq 'agent' }).config.language = 'en'
        $run = Invoke-Run $g $case.Q
        $inputDone = @($run.Events | Where-Object { $_.stage -eq 'input' -and $_.status -eq 'done' })[0]
        $r = $run.Result
        if ($r.language -eq $case.Lang -and $inputDone.detail.chosen_by -eq 'question script' -and $r.blocked -eq $false) { $true }
        else { "language=$($r.language), chosen_by=$($inputDone.detail.chosen_by), blocked=$($r.blocked)" }
    }
}
Test-Check 'language selector overrides the agent setting' {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Body (Get-Graph 'full') '"What is the refund policy?"' 'hi')
    $lang = @($r.Json.events)[-1].language
    if ($lang -eq 'hi') { $true } else { "language=$lang" }
}
foreach ($case in @(
    @{ Name = 'empty question is blocked'; Q = '"   "'; Word = 'empty' },
    @{ Name = 'abusive question is blocked'; Q = '"you are a bastard"'; Word = 'abusive' },
    @{ Name = 'off-topic question is blocked'; Q = '"lottery result today"'; Word = 'outside its scope' }
)) {
    Test-Check "$($case.Name), reason shown, router and agent skipped" {
        $run = Invoke-Run (Get-Graph 'full') $case.Q
        $skipped = @($run.Events | Where-Object { $_.status -eq 'skipped' }) | ForEach-Object { $_.stage }
        $guard = @($run.Events | Where-Object { $_.stage -eq 'guardrails' })[-1]
        if ($run.Result.blocked -ne $true) { return 'not blocked' }
        if ($guard.status -ne 'blocked') { return "guardrails status '$($guard.status)'" }
        if (($skipped -join ',') -ne 'router,agent') { return "skipped: $($skipped -join ',')" }
        if ($run.Result.answer -notmatch [regex]::Escape($case.Word)) { return "reason: $($run.Result.answer)" }
        $true
    }
}
Test-Check 'normal question is allowed through guardrails' {
    $r = (Invoke-Run (Get-Graph 'full') '"Please summarise the refund policy"').Result
    if ($r.blocked -eq $false) { $true } else { "blocked: $($r.answer)" }
}
# Read the decision from the router's own stage event, so these checks do not depend on the model
# (since Phase 3 a "direct" route calls Gemini, which fails without a key).
function Get-Route($Graph, [string]$QuestionJson) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Body $Graph $QuestionJson)
    @(@($r.Json.events) | Where-Object { $_.stage -eq 'router' -and $_.status -eq 'done' })[0].detail.route
}
Test-Check 'router: calculation with a tool connected -> tool' {
    $route = Get-Route (Get-Graph 'full') '"what is 1200 * 0.18"'
    if ($route -eq 'tool') { $true } else { "route=$route" }
}
Test-Check 'router: sources connected -> search' {
    $route = Get-Route (Get-Graph 'full') '"What is the refund policy?"'
    if ($route -eq 'search') { $true } else { "route=$route" }
}
Test-Check 'router: nothing connected -> direct' {
    $route = Get-Route (Get-Graph 'default') '"What is the refund policy?"'
    if ($route -eq 'direct') { $true } else { "route=$route" }
}
Test-Check 'agent sees exactly the sources and tools drawn into it' {
    $g = Get-Graph 'full'
    $g.nodes = @($g.nodes | Where-Object { $_.id -ne 's2' -and $_.id -ne 't2' })
    $g.edges = @($g.edges | Where-Object { $_.from -ne 's2' -and $_.from -ne 't2' })
    $r = (Invoke-Run $g '"refund policy?"').Result
    if ((@($r.sources) -join ',') -eq 's1' -and (@($r.tools) -join ',') -eq 't1') { $true }
    else { "sources=$(@($r.sources) -join ','), tools=$(@($r.tools) -join ',')" }
}
# Phase 2 checked a placeholder answer here. Phase 3 replaced it with real retrieval, so this now checks
# the model-free path. Since Phase 8 an agent can only connect ready project sources (an empty source is refused
# before the run), so the path is reached by a question that no stored chunk matches: "I don't know", no model call.
Test-Check 'agent whose connected sources hold nothing relevant says it does not know, no model call' {
    $run = Invoke-Run (Get-Graph 'full') '"Who won the cricket world cup in 2011?"'
    $agentDone = @($run.Events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0]
    if ($agentDone.detail.model_called -eq $false -and $run.Result.supported -eq $false -and @($run.Result.citations).Count -eq 0) { $true }
    else { "model_called=$($agentDone.detail.model_called), answer=$($run.Result.answer)" }
}

# ---------- Saved graphs ----------
Write-Host "`n== Saved graphs (Postgres)"
Test-Check 'save a graph, load it back unchanged, run it by graph_id' {
    $g = Get-Graph 'full'
    $save = Invoke-Api 'POST' "$Api/graphs" ("{`"project_id`":$($script:Who.Project),`"name`":`"verify_phase2`",`"graph`":$(ConvertTo-Body $g)}")
    if ($save.Status -ne 201) { return "save HTTP $($save.Status): $($save.Text)" }
    $id = $save.Json.id
    $loaded = (Invoke-Api 'GET' "$Api/graphs/$id").Json
    if ((Get-Canonical $loaded) -ne (Get-Canonical $g)) { return 'loaded graph differs from the saved one' }
    $run = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" "{`"question`":`"refund policy?`",`"graph_id`":$id}"
    $result = @($run.Json.events)[-1]
    if ($result.type -eq 'result' -and (@($result.sources) -join ',') -eq 's1,s2') { $true } else { $run.Text }
}
Test-Check 'an invalid graph is not saved (422)' {
    $g = Get-Graph 'full'
    $g.edges = @($g.edges | Where-Object { $_.from -ne 't1' })
    $r = Invoke-Api 'POST' "$Api/graphs" ("{`"project_id`":$($script:Who.Project),`"name`":`"broken`",`"graph`":$(ConvertTo-Body $g)}")
    if ($r.Status -eq 422) { $true } else { "HTTP $($r.Status)" }
}
Test-Check 'unknown graph id returns 404' {
    $r = Invoke-Api 'GET' "$Api/graphs/999999"
    if ($r.Status -eq 404) { $true } else { "HTTP $($r.Status)" }
}

# ---------- Summary ----------
$total = $script:Pass + $script:Fail + $script:Skip
Write-Host ''
Write-Host "Result: $($script:Pass) passed, $($script:Fail) failed, $($script:Skip) skipped (of $total)"
if ($script:Fail -eq 0 -and $script:Skip -eq 0) { Write-Host 'Phase 2 pipeline verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 2 pipeline NOT fully verified.' -ForegroundColor Red; exit 1
