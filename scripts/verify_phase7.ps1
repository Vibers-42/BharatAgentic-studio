<#
Checks Phase 7 (real tools, source status, and the redesigned UI) on the running Docker stack.
Prints PASS / FAIL / SKIP / BLOCKED per check. Works in Windows PowerShell 5.1 and PowerShell 7.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase7.ps1            # everything
  powershell -ExecutionPolicy Bypass -File scripts\verify_phase7.ps1 -SkipUi    # without the browser journeys

What it checks:
- the MCP runner offers only read-only tools, and still has no network path to the database;
- validation refuses unknown MCP servers, write-mode MCP tools and tools that are not connected;
- a saved agent's PDF uploaded through the API shows up in the source listing the canvas uses;
- on the local model with local-only on: a refund question runs the calculator (1200 * 80% = 960) and
  the answer cites both the PDF and the calculator; an order question calls the MCP tool get_order through
  nginx's SSE stream; a graph without tools never calls one;
- if the MCP runner is down, the question still gets an answer with a clear warning (then it is restarted);
- the same tool question on Groq and on Gemini (BLOCKED if Gemini's daily quota is used up);
- usage rows add up every model call of a question; usage filters by model;
- the new UI bundle, the frontend unit tests (needs Node.js), and the real browser journeys in
  scripts\ui_journeys.py (needs Python with websockets, and Edge or Chrome; SKIP otherwise).
Model requests: about 6 local, 2 Groq, 2 Gemini. Exit 0 only if all pass.
#>
param([switch]$SkipUi)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0; $script:Fail = 0; $script:Skip = 0; $script:Blocked = 0
$Api = 'http://127.0.0.1:8000'
$Web = 'http://127.0.0.1:3000'

function Write-Result([string]$Result, [string]$Name, [string]$Detail = '') {
    $colors = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow'; BLOCKED = 'Magenta' }
    $line = '{0,-7} {1}' -f $Result, $Name
    if ($Detail) { $line += "  ($Detail)" }
    Write-Host $line -ForegroundColor $colors[$Result]
    switch ($Result) { 'PASS' { $script:Pass++ } 'FAIL' { $script:Fail++ } 'SKIP' { $script:Skip++ } 'BLOCKED' { $script:Blocked++ } }
}

class QuotaBlocked : System.Exception {
    [string]$Code
    QuotaBlocked([string]$code, [string]$message) : base($message) { $this.Code = $code }
}

function Test-Check([string]$Name, [scriptblock]$Test) {
    if (-not $script:ApiUp) { Write-Result 'SKIP' $Name 'api is not reachable'; return }
    try {
        $result = & $Test
        if ($result -is [bool] -and $result) { Write-Result 'PASS' $Name }
        elseif ("$result".StartsWith('SKIP:')) { Write-Result 'SKIP' $Name "$result".Substring(5) }
        elseif ($result) { Write-Result 'FAIL' $Name "$result" }
        else { Write-Result 'FAIL' $Name }
    } catch [QuotaBlocked] {
        Write-Result 'BLOCKED' $Name "$($_.Exception.Code): $($_.Exception.Message)"
    } catch {
        Write-Result 'FAIL' $Name $_.Exception.Message
    }
}

$script:Http = New-Object System.Net.Http.HttpClient
$script:Http.Timeout = [TimeSpan]::FromSeconds(900)

function Invoke-Api([string]$Method, [string]$Url, [string]$Body = $null) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)
    if ($Body) {
        $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
        $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    }
    $res = $script:Http.SendAsync($req).GetAwaiter().GetResult()
    $text = [System.Text.Encoding]::UTF8.GetString($res.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
    $json = $null
    try { $json = $text | ConvertFrom-Json } catch { }
    [pscustomobject]@{ Status = [int]$res.StatusCode; Text = $text; Json = $json }
}

function Send-Pdf([string]$Url, [string]$Path) {
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $file = [System.Net.Http.ByteArrayContent]::new([System.IO.File]::ReadAllBytes((Resolve-Path $Path)))
    $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/pdf')
    $form.Add($file, 'file', (Split-Path $Path -Leaf))
    $res = $script:Http.PostAsync($Url, $form).GetAwaiter().GetResult()
    [int]$res.StatusCode
}

# Reads an SSE stream as the UI does: one event per "data:" line, in arrival order.
function Invoke-Stream([string]$Body) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$Web/api/pipeline/run")
    $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
    $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    $res = $script:Http.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    if ([int]$res.StatusCode -ne 200) { throw "HTTP $([int]$res.StatusCode)" }
    $reader = [System.IO.StreamReader]::new($res.Content.ReadAsStreamAsync().GetAwaiter().GetResult(), [System.Text.Encoding]::UTF8)
    $events = @()
    while ($null -ne ($line = $reader.ReadLine())) { if ($line.StartsWith('data: ')) { $events += ($line.Substring(6) | ConvertFrom-Json) } }
    $reader.Dispose()
    , $events
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$StdIn) {
    if ($PSBoundParameters.ContainsKey('StdIn')) { $out = $StdIn | & $Exe @Arguments 2>$null }
    else { $out = & $Exe @Arguments 2>$null }
    [pscustomobject]@{ Output = (($out | Out-String).Trim()); Code = $LASTEXITCODE }
}

function ConvertTo-Json10($Object) { $Object | ConvertTo-Json -Depth 10 -Compress }

# The five fixed blocks top to bottom, plus extra nodes drawn into the agent (n4).
function New-Graph([string]$Model = 'local', [object[]]$Extra = @()) {
    $main = @('input', 'guardrails', 'router', 'agent', 'output')
    $nodes = @()
    for ($i = 0; $i -lt 5; $i++) {
        $config = @{}
        if ($main[$i] -eq 'agent') { $config = @{ model = $Model; language = 'en' } }
        if ($main[$i] -eq 'guardrails') { $config = @{ block = @('empty', 'abuse', 'off_topic') } }
        $nodes += @{ id = "n$($i + 1)"; type = $main[$i]; config = $config; position = @{ x = 300; y = 140 * $i } }
    }
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    foreach ($n in $Extra) { $edges += @{ from = $n.id; to = 'n4' } }
    @{ nodes = $nodes + $Extra; edges = $edges }
}

# Phase 8: the node points at the PDF in the test account's project (source_ref is filled in after the upload).
$Pdf = @{ id = 's1'; type = 'source_doc'; config = @{ source_ref = 0 }; position = @{ x = 0; y = 420 } }
$Calc = @{ id = 't1'; type = 'tool_calculator'; config = @{}; position = @{ x = 600; y = 360 } }
$Mcp = @{ id = 't2'; type = 'tool_mcp'; config = @{ server = 'orders-db'; mode = 'read_only' }; position = @{ x = 600; y = 480 } }
$RefundQ = 'My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?'
$OrderQ = 'What is the status and refund of order ORD-1002?'

function Get-Run([string]$Body) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" $Body
    if ($r.Status -ne 200) { throw "HTTP $($r.Status): $($r.Text)" }
    , @($r.Json.events)
}

function Get-Final($Events) { $Events[$Events.Count - 1] }

# A cloud model that fell back because of its quota is BLOCKED, not FAILED and never PASSED.
function Assert-NotQuota($Final) {
    if ($Final.fallback -and "$($Final.fallback.code)".StartsWith('quota')) { throw [QuotaBlocked]::new($Final.fallback.code, $Final.fallback.reason) }
    $err = $Final | Where-Object { $_.type -eq 'error' }
    if ($err -and "$($err.code)".StartsWith('quota')) { throw [QuotaBlocked]::new($err.code, $err.message) }
}

function Test-ToolAnswer($Events, [string]$Model) {
    $final = Get-Final $Events
    Assert-NotQuota $final
    $call = @($Events | Where-Object { $_.type -eq 'tool_call' -and $_.tool -eq 'calculator' })
    $result = @($Events | Where-Object { $_.type -eq 'tool_result' -and $_.node_id -eq 't1' })
    $cited = @($final.citations | ForEach-Object { $_.source_id })
    if ($call.Count -eq 0) { return "no calculator call (route $($final.route))" }
    if (-not $result[0].ok -or $result[0].output -notmatch '= 960$') { return "calculator result: $($result[0].output) $($result[0].error)" }
    if ($final.answer -notmatch '960') { return "answer has no 960: $($final.answer)" }
    if (-not ($cited -contains 't1' -and $cited -contains 's1')) { return "citations: $($cited -join ', ')" }
    if (-not "$($final.model)".StartsWith("$($Model):")) { return "answered by $($final.model)" }
    $true
}

$script:ApiUp = $false
try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
$Stamp = Get-Date -Format 'MMddHHmmss'
$script:GraphId = 0
if ($script:ApiUp) {
    # Phase 8: a fresh test account (real registration, own session and CSRF token) replaces the p7_<time> user.
    . "$PSScriptRoot\_session.ps1"
    $script:Who = New-TestSession 'p7'
    $script:Http = $script:Who.Http
}
$Pid8 = $script:Who.Project

Write-Host "`n== Containers and MCP security"
Test-Check 'all five containers are running' {
    $ps = Invoke-Native 'docker' @('compose', 'ps', '--format', '{{.Service}} {{.State}}')
    $running = @($ps.Output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match ' running$' } | ForEach-Object { ($_ -split ' ')[0] })
    $missing = @('web', 'api', 'db', 'mcp-runner', 'ollama') | Where-Object { $running -notcontains $_ }
    if ($missing.Count -eq 0) { $true } else { "not running: $($missing -join ', ')" }
}
Test-Check 'MCP runner offers get_order, find_orders and order_totals, all read-only' {
    $s = (Invoke-Api 'GET' "$Api/tools").Json.mcp_servers | Where-Object { $_.name -eq 'orders-db' }
    if (-not $s.ok) { return "orders-db: $($s.error)" }
    $names = @($s.tools | ForEach-Object { $_.name })
    $writes = @($s.tools | Where-Object { -not $_.read_only })
    if ((@('get_order', 'find_orders', 'order_totals') | Where-Object { $names -notcontains $_ }).Count -eq 0 -and $writes.Count -eq 0) { $true }
    else { "tools: $($names -join ', '); write tools: $($writes.Count)" }
}
Test-Check 'MCP runner still has no network path to the database' {
    $code = "import socket`ntry:`n    socket.create_connection(('db', 5432), 3).close()`n    print('REACHABLE')`nexcept OSError as e:`n    print('BLOCKED', type(e).__name__)`n"
    $r = Invoke-Native 'docker' @('compose', 'exec', '-T', 'mcp-runner', 'python', '-') $code
    if ($r.Output -match '^BLOCKED') { $true } elseif ($r.Output -match '^REACHABLE') { 'SECURITY: mcp-runner can reach db' } else { "probe did not run (exit $($r.Code))" }
}

Write-Host "`n== Graph rules for tools"
Test-Check 'an MCP tool pointing at an unknown server is refused' {
    $bad = @{ id = 't2'; type = 'tool_mcp'; config = @{ server = 'http://169.254.169.254/'; mode = 'read_only' } }
    $errs = @((Invoke-Api 'POST' "$Api/pipeline/validate?project_id=$Pid8" (ConvertTo-Json10 (New-Graph 'local' @($bad)))).Json.errors)
    if ($errs.Count -eq 1 -and $errs[0].code -eq 'unknown_mcp_server' -and $errs[0].node_id -eq 't2') { $true } else { ($errs | ForEach-Object { $_.code }) -join ', ' }
}
Test-Check 'an MCP tool in write mode is refused unless writing is enabled on purpose' {
    $bad = @{ id = 't2'; type = 'tool_mcp'; config = @{ server = 'orders-db'; mode = 'read_write' } }
    $errs = @((Invoke-Api 'POST' "$Api/pipeline/validate?project_id=$Pid8" (ConvertTo-Json10 (New-Graph 'local' @($bad)))).Json.errors)
    if ($errs.Count -eq 1 -and $errs[0].code -eq 'mcp_not_read_only') { $true } else { ($errs | ForEach-Object { $_.code }) -join ', ' }
}
Test-Check 'a run with a tool that is not connected to the agent is refused (422 not_connected)' {
    $g = New-Graph 'local' @()
    $g.nodes += $Calc
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Json10 @{ question = $RefundQ; project_id = $Pid8; graph = $g })
    $codes = @($r.Json.detail.errors | ForEach-Object { $_.code })
    if ($r.Status -eq 422 -and $codes -contains 'not_connected') { $true } else { "HTTP $($r.Status) $($codes -join ', ')" }
}

Write-Host "`n== Saved agent and its documents"
Test-Check 'requests are made as the signed-in test account (the open user list is gone)' {
    $me = (Invoke-Api 'GET' "$Api/auth/me").Json.user
    $list = Invoke-Api 'GET' "$Api/users"
    if ($me.id -eq $script:Who.UserId -and $list.Status -in 404, 405) { $true } else { "me=$($me.id), GET /users HTTP $($list.Status)" }
}
Test-Check 'PDF uploaded to the project through the API; agent with PDF, calculator and MCP tool saved' {
    $code = Send-Pdf "$Api/projects/$Pid8/sources/file" 'samples\refund-policy.pdf'
    if ($code -ne 201) { return "upload: HTTP $code" }
    $Pdf.config.source_ref = @((Invoke-Api 'GET' "$Api/projects/$Pid8/sources").Json | Where-Object { $_.title -eq 'refund-policy.pdf' })[0].id
    $r = Invoke-Api 'POST' "$Api/graphs" (ConvertTo-Json10 @{ project_id = $Pid8; name = "verify_phase7 $Stamp"; graph = (New-Graph 'local' @($Pdf, $Calc, $Mcp)) })
    if ($r.Status -ne 201) { return "save: HTTP $($r.Status) $($r.Text)" }
    $script:GraphId = $r.Json.id
    $true
}
Test-Check 'the project source listing the canvas uses shows the PDF, its chunks and the agent using it' {
    $s = @((Invoke-Api 'GET' "$Api/projects/$Pid8/sources").Json | Where-Object { $_.id -eq $Pdf.config.source_ref })[0]
    $agents = @((Invoke-Api 'GET' "$Api/sources/$($s.id)").Json.agents | ForEach-Object { $_.id })
    if ($s.status -eq 'ready' -and $s.chunk_count -gt 0 -and $agents -contains $script:GraphId) { $true } else { "listing: $(ConvertTo-Json10 $s), agents $($agents -join ',')" }
}

Write-Host "`n== Tools in real answers (Qwen 3 14B, local only)"
$script:LocalRun = $null
Test-Check 'refund question runs the calculator, and the answer cites the PDF and the calculator' {
    $script:LocalRun = Get-Run (ConvertTo-Json10 @{ question = $RefundQ; graph_id = $script:GraphId; model = 'local'; local_only = $true })
    Test-ToolAnswer $script:LocalRun 'local'
}
Test-Check 'local only: every model call of that question (tool plan and answer) was a local model' {
    $final = Get-Final $script:LocalRun
    $agent = $script:LocalRun | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' }
    $steps = @($agent.detail.generation.steps)
    $cloud = @($steps | Where-Object { -not ("$($_.model)".StartsWith('local:')) })
    if ($final.local_only -and $steps.Count -eq 2 -and $cloud.Count -eq 0 -and -not $final.cost.api_fee) { $true }
    else { "local_only=$($final.local_only), steps=$(($steps | ForEach-Object { "$($_.step):$($_.model)" }) -join ', ')" }
}
Test-Check 'order question calls the MCP tool get_order, streamed through nginx as SSE events' {
    $events = Invoke-Stream (ConvertTo-Json10 @{ question = $OrderQ; graph_id = $script:GraphId; model = 'local'; local_only = $true })
    $types = @($events | ForEach-Object { $_.type })
    $call = [array]::IndexOf($types, 'tool_call'); $res = [array]::IndexOf($types, 'tool_result'); $end = [array]::IndexOf($types, 'result')
    $final = Get-Final $events
    $tool = $events | Where-Object { $_.type -eq 'tool_call' }
    if ($call -lt 0 -or -not ($call -lt $res -and $res -lt $end)) { return "event order: $($types -join ',')" }
    if ($tool.tool -ne 'get_order' -or $tool.node_id -ne 't2' -or $tool.args.order_id -ne 'ORD-1002') { return "call: $(ConvertTo-Json10 $tool)" }
    $cited = @($final.citations | ForEach-Object { $_.source_id })
    if ($final.supported -and $cited -contains 't2' -and $final.answer -match 'ORD-1002|960|cancel') { $true } else { "answer: $($final.answer) cites $($cited -join ', ')" }
}
Test-Check 'without a tool drawn into the agent, the same question never calls a tool' {
    $events = Get-Run (ConvertTo-Json10 @{ question = $RefundQ; graph = (New-Graph 'local' @($Pdf)); graph_id = $script:GraphId; local_only = $true })
    $calls = @($events | Where-Object { $_.type -eq 'tool_call' })
    $final = Get-Final $events
    if ($calls.Count -eq 0 -and $final.route -eq 'search' -and @($final.tool_calls).Count -eq 0) { $true } else { "tool calls: $($calls.Count), route $($final.route)" }
}
Test-Check 'with the MCP runner stopped, the order question still answers with a clear warning' {
    $null = Invoke-Native 'docker' @('compose', 'stop', 'mcp-runner')
    try {
        $events = Get-Run (ConvertTo-Json10 @{ question = $OrderQ; graph_id = $script:GraphId; model = 'local'; local_only = $true })
    } finally {
        $null = Invoke-Native 'docker' @('compose', 'start', 'mcp-runner')
    }
    $final = Get-Final $events
    $warn = @($final.warnings) -join ' '
    if ($final.type -eq 'result' -and $warn -match 'unavailable' -and @($events | Where-Object { $_.type -eq 'tool_call' }).Count -eq 0) { $true }
    else { "type=$($final.type) warnings=$warn" }
}
Test-Check 'MCP runner is back and healthy' {
    for ($i = 0; $i -lt 30; $i++) {
        $s = (Invoke-Api 'GET' "$Api/tools").Json.mcp_servers | Where-Object { $_.name -eq 'orders-db' }
        if ($s.ok) { return $true }
        Start-Sleep -Seconds 2
    }
    "orders-db: $($s.error)"
}

Write-Host "`n== The same tools on the cloud models"
Test-Check 'Groq (GPT-OSS 120B): calculator result reaches the model and is cited' {
    $events = Get-Run (ConvertTo-Json10 @{ question = $RefundQ; graph_id = $script:GraphId; model = 'groq' })
    $ok = Test-ToolAnswer $events 'groq'
    if ($ok -ne $true) { return $ok }
    $final = Get-Final $events
    if ($final.cost.priced -and $final.cost.usd -gt 0) { $true } else { "cost: $(ConvertTo-Json10 $final.cost)" }
}
Test-Check 'Gemini (2.5 Flash): calculator result reaches the model and is cited' {
    $events = Get-Run (ConvertTo-Json10 @{ question = $RefundQ; graph_id = $script:GraphId; model = 'gemini' })
    Test-ToolAnswer $events 'gemini'
}

Write-Host "`n== Usage"
Test-Check 'one usage row per question, with the tool plan and the answer added up' {
    $rows = @((Invoke-Api 'GET' "$Api/usage/recent?model=local&limit=50").Json)
    $first = Get-Final $script:LocalRun
    $match = $rows | Where-Object { $_.input_tokens -eq $first.usage.input_tokens -and $_.output_tokens -eq $first.usage.output_tokens }
    $agent = $script:LocalRun | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' }
    $sum = ($agent.detail.generation.steps | Measure-Object -Property input_tokens -Sum).Sum
    if ($match -and $sum -eq $first.usage.input_tokens -and @($match)[0].sources -contains 't1') { $true }
    else { "rows=$($rows.Count), steps sum=$sum, answer=$($first.usage.input_tokens)" }
}
Test-Check 'usage filters by model and refuses a malformed model name' {
    $local = (Invoke-Api 'GET' "$Api/usage/totals?model=local").Json
    $all = (Invoke-Api 'GET' "$Api/usage/totals").Json
    $bad = Invoke-Api 'GET' "$Api/usage/totals?model=x%27%3B"
    if ($local.requests -ge 4 -and $local.cost_usd -eq 0 -and $all.requests -gt $local.requests -and $bad.Status -eq 422) { $true }
    else { "local=$($local.requests), all=$($all.requests), bad=$($bad.Status)" }
}

Write-Host "`n== Frontend"
Test-Check 'web serves the redesigned app (builder, chat, knowledge, usage, status)' {
    $html = (Invoke-Api 'GET' "$Web/").Text
    if ($html -notmatch 'src="(/assets/[^"]+\.js)"') { return 'no script tag' }
    $js = (Invoke-Api 'GET' "$Web$($Matches[1])").Text
    $missing = @('react-flow', 'Knowledge', 'Live events', 'Inspector', 'Tidy', 'GitHub repo', 'Docs link', 'MCP tool', 'Calculator',
        'Requests per day', 'Local only', 'pipeline/validate', 'tool_result') | Where-Object { $js -notmatch [regex]::Escape($_) }
    if ($missing.Count -eq 0) { $true } else { "missing: $($missing -join ', ')" }
}
Test-Check 'frontend unit tests pass' {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { return 'SKIP:Node.js is not installed' }
    Push-Location web
    try { $out = (npm test 2>&1 | Out-String) } finally { Pop-Location }
    if ($LASTEXITCODE -eq 0 -and $out -match 'Tests\s+\S*\s*(\d+) passed') { $true } else { ($out -split "`n" | Select-Object -Last 8) -join ' ' }
}
Test-Check 'browser journeys pass (builder, knowledge, chat, usage, status, 4 screen sizes)' {
    if ($SkipUi) { return 'SKIP:-SkipUi was given' }
    # $env:JOURNEY_PYTHON can name the Python to use (for example a virtual environment's python.exe).
    $pyExe = if ($env:JOURNEY_PYTHON) { $env:JOURNEY_PYTHON } else { 'python' }
    if (-not (Get-Command $pyExe -ErrorAction SilentlyContinue)) { return 'SKIP:Python is not installed' }
    $null = & $pyExe -c 'import websockets' 2>$null
    if ($LASTEXITCODE -ne 0) { return 'SKIP:Python package websockets is missing (pip install -r tests\requirements.txt)' }
    $out = (& $pyExe scripts\ui_journeys.py 2>&1 | Out-String)
    if ($out -match 'no Edge or Chrome') { return 'SKIP:no Edge or Chrome found' }
    if ($LASTEXITCODE -eq 0 -and $out -match '(\d+) passed, 0 failed') { $true } else { (($out -split "`n") | Where-Object { $_ -match 'FAIL|passed' }) -join ' ' }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by quota (of {4})" -f $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total)
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Phase 7 verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 7 NOT fully verified.' -ForegroundColor Red; exit 1
