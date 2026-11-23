<#
Checks Phase 6 (visual agent builder and usage dashboard) on the running Docker stack.
Prints PASS / FAIL / SKIP / BLOCKED per check. Works in Windows PowerShell 5.1 and PowerShell 7.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase6.ps1

The builder is a React Flow canvas in the browser; this script checks everything it relies on, through the
same API calls the canvas makes: graphs with all nine node types and their positions are saved, updated and
reloaded; the backend's validation errors name the broken node; a canvas graph runs against its saved
graph's PDF, GitHub and docs sources with retrieval limited to connected sources; the SSE stream gives a
status event per node id in pipeline order (what the canvas highlights); every agent model works; the agent
node's local-only setting refuses cloud models; the dashboard's usage endpoints answer; and the frontend's
own unit tests pass (needs Node.js; SKIP otherwise).
Model requests: 1 Groq, 1 Gemini (BLOCKED if its quota is used up), 3 local. Exit 0 only if all pass.
#>

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

# Reads an SSE stream as the canvas does: one event per "data:" line, in arrival order.
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

# A graph as the canvas builds it: the five fixed blocks (with positions) plus extra nodes.
function New-CanvasGraph([string]$Model = 'groq', [bool]$LocalOnly = $false, [object[]]$Extra = @(), [object[]]$ExtraEdges = @()) {
    $main = @('input', 'guardrails', 'router', 'agent', 'output')
    $nodes = @()
    for ($i = 0; $i -lt 5; $i++) {
        $config = @{}
        if ($main[$i] -eq 'agent') { $config = @{ model = $Model; language = 'en'; local_only = $LocalOnly } }
        if ($main[$i] -eq 'guardrails') { $config = @{ block = @('empty', 'abuse', 'off_topic') } }
        $nodes += @{ id = "n$($i + 1)"; type = $main[$i]; config = $config; position = @{ x = 40 + 230 * $i; y = 220 } }
    }
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    @{ nodes = $nodes + $Extra; edges = $edges + $ExtraEdges }
}

function ConvertTo-Json10($Object) { $Object | ConvertTo-Json -Depth 10 -Compress }

# The nine node types: PDF, docs link and GitHub repo sources, calculator and MCP tools, all wired to n4.
$Sources = @(
    @{ id = 'src1'; type = 'source_doc'; config = @{ file = 'refund-policy.pdf' }; position = @{ x = 100; y = 40 } },
    @{ id = 'src2'; type = 'source_doc'; config = @{ url = 'https://example.com' }; position = @{ x = 300; y = 40 } },
    @{ id = 'src3'; type = 'source_repo'; config = @{ url = 'https://github.com/octocat/Spoon-Knife' }; position = @{ x = 500; y = 40 } }
)
$Tools = @(
    @{ id = 'tool1'; type = 'tool_calculator'; config = @{}; position = @{ x = 700; y = 40 } },
    @{ id = 'tool2'; type = 'tool_mcp'; config = @{ server = 'orders-db'; mode = 'read_only' }; position = @{ x = 900; y = 40 } }
)
$AllExtra = $Sources + $Tools
$AllEdges = @($AllExtra | ForEach-Object { @{ from = $_.id; to = 'n4' } })

$script:ApiUp = $false
try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
$script:GraphId = 0

Write-Host "`n== Builder page"
Test-Check 'web serves the builder bundle with React Flow, the palette and the dashboard' {
    $html = (Invoke-Api 'GET' "$Web/").Text
    if ($html -notmatch 'src="(/assets/[^"]+\.js)"') { return 'no script tag' }
    $js = (Invoke-Api 'GET' "$Web$($Matches[1])").Text
    $missing = @('react-flow', 'GitHub repo', 'Docs link', 'MCP tool', 'Calculator', 'Requests per day', 'Local only', 'pipeline/validate') |
        Where-Object { $js -notmatch [regex]::Escape($_) }
    if ($missing.Count -eq 0) { $true } else { "missing: $($missing -join ', ')" }
}
Test-Check 'frontend unit tests pass (canvas model, SSE highlighting)' {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { return 'SKIP:Node.js is not installed' }
    Push-Location web
    try { $out = (npm test 2>&1 | Out-String) } finally { Pop-Location }
    if ($LASTEXITCODE -eq 0 -and $out -match 'Tests\s+\S*\s*(\d+) passed') { $true } else { ($out -split "`n" | Select-Object -Last 8) -join ' ' }
}

Write-Host "`n== Graph with all nine node types: validate, save, update, reload"
Test-Check 'a canvas graph with all nine node types is valid' {
    $r = Invoke-Api 'POST' "$Api/pipeline/validate" (ConvertTo-Json10 (New-CanvasGraph 'groq' $false $AllExtra $AllEdges))
    $types = @((New-CanvasGraph 'groq' $false $AllExtra $AllEdges).nodes | ForEach-Object { $_.type } | Sort-Object -Unique)
    if ($r.Json.valid -eq $true -and $types.Count -eq 9) { $true } else { "valid=$($r.Json.valid), types=$($types.Count): $($r.Text)" }
}
Test-Check 'save, update and reload keep every node, edge and position' {
    $g = New-CanvasGraph 'groq' $false $AllExtra $AllEdges
    $r = Invoke-Api 'POST' "$Api/graphs" "{`"name`":`"p6 canvas`",`"graph`":$(ConvertTo-Json10 $g)}"
    if ($r.Status -ne 201) { return "save HTTP $($r.Status)" }
    $script:GraphId = $r.Json.id
    $g.nodes[0].position = @{ x = 11; y = 22 }  # move a node, as dragging on the canvas does
    $u = Invoke-Api 'PUT' "$Api/graphs/$($script:GraphId)" "{`"name`":`"p6 canvas moved`",`"graph`":$(ConvertTo-Json10 $g)}"
    $back = (Invoke-Api 'GET' "$Api/graphs/$($script:GraphId)").Json
    $positions = @($back.nodes | Where-Object { $null -eq $_.position }).Count
    if ($u.Status -eq 200 -and @($back.nodes).Count -eq 10 -and @($back.edges).Count -eq 9 -and $positions -eq 0 -and
        $back.nodes[0].position.x -eq 11 -and $back.nodes[0].position.y -eq 22) { $true }
    else { "update HTTP $($u.Status), nodes=$(@($back.nodes).Count), edges=$(@($back.edges).Count), first=$($back.nodes[0].position | ConvertTo-Json -Compress)" }
}

Write-Host "`n== Validation errors the canvas shows on nodes"
$cases = @(
    @{ Name = 'source with no line to the agent'; Code = 'not_connected'; Node = 'src2'; Graph = (New-CanvasGraph 'groq' $false $Sources @(@{ from = 'src1'; to = 'n4' }, @{ from = 'src3'; to = 'n4' })) },
    @{ Name = 'agent not connected to output'; Code = 'main_line_gap'; Node = 'n4'; Graph = (& { $g = New-CanvasGraph; $g.edges = @($g.edges | Where-Object { $_.to -ne 'n5' }); $g }) },
    @{ Name = 'source wired to the router instead of the agent'; Code = 'wrong_target'; Node = 'src1'; Graph = (New-CanvasGraph 'groq' $false @($Sources[0]) @(@{ from = 'src1'; to = 'n3' })) },
    @{ Name = 'shortcut input -> agent'; Code = 'main_line_shortcut'; Node = 'n1'; Graph = (& { $g = New-CanvasGraph; $g.edges += @{ from = 'n1'; to = 'n4' }; $g }) },
    @{ Name = 'MCP tool set to write'; Code = 'mcp_not_read_only'; Node = 'tool2'; Graph = (New-CanvasGraph 'groq' $false @(@{ id = 'tool2'; type = 'tool_mcp'; config = @{ server = 'x'; mode = 'read_write' } }) @(@{ from = 'tool2'; to = 'n4' })) },
    @{ Name = 'agent model that does not exist'; Code = 'bad_model'; Node = 'n4'; Graph = (New-CanvasGraph 'gpt-9') }
)
foreach ($c in $cases) {
    Test-Check "error for $($c.Name) names node $($c.Node) ($($c.Code))" {
        $r = Invoke-Api 'POST' "$Api/pipeline/validate" (ConvertTo-Json10 $c.Graph)
        $hit = @($r.Json.errors | Where-Object { $_.code -eq $c.Code -and $_.node_id -eq $c.Node -and $_.message })
        if ($r.Json.valid -eq $false -and $hit.Count -ge 1) { $true } else { "errors: $(@($r.Json.errors | ForEach-Object { "$($_.code)/$($_.node_id)" }) -join ', ')" }
    }
}
Test-Check 'saving a broken graph is refused with the same errors (422)' {
    $r = Invoke-Api 'POST' "$Api/graphs" "{`"name`":`"broken`",`"graph`":$(ConvertTo-Json10 $cases[0].Graph)}"
    if ($r.Status -eq 422 -and @($r.Json.detail.errors | Where-Object { $_.node_id -eq 'src2' }).Count -ge 1) { $true } else { "HTTP $($r.Status)" }
}

Write-Host "`n== Sources on the canvas: PDF, docs link and GitHub repo in one saved graph"
Test-Check 'PDF upload, docs link and GitHub repo are added to the saved graph''s own documents' {
    if (-not $script:GraphId) { return 'no saved graph' }
    $pdf = Send-Pdf "$Api/sources/src1/pdf?graph_id=$($script:GraphId)" 'samples\refund-policy.pdf'
    $url = Invoke-Api 'POST' "$Api/sources/src2/url?graph_id=$($script:GraphId)" '{"url":"https://example.com"}'
    $repo = Invoke-Api 'POST' "$Api/sources/src3/repo?graph_id=$($script:GraphId)" '{"url":"https://github.com/octocat/Spoon-Knife"}'
    if ($pdf -eq 201 -and $url.Status -eq 201 -and $repo.Status -eq 201 -and $url.Json.scope -eq "graph:$($script:GraphId)") { $true }
    else { "pdf $pdf, url $($url.Status), repo $($repo.Status)" }
}
Test-Check 'retrieval searches exactly the sources connected on the canvas' {
    $two = New-CanvasGraph 'groq' $false @($Sources[0], $Sources[2]) @(@{ from = 'src1'; to = 'n4' }, @{ from = 'src3'; to = 'n4' })
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" "{`"question`":`"Who won the cricket world cup in 2011?`",`"graph_id`":$($script:GraphId),`"graph`":$(ConvertTo-Json10 $two)}"
    $agent = @(@($r.Json.events) | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0].detail
    $searched = @($agent.retrieval.searched) -join ','
    $files = @($agent.retrieval.candidates | ForEach-Object { $_.source_id } | Sort-Object -Unique) -join ','
    if ($searched -eq 'src1,src3' -and $files -notmatch 'src2' -and $agent.retrieval.scope -eq "graph:$($script:GraphId)") { $true }
    else { "searched=$searched, candidates from=$files, scope=$($agent.retrieval.scope)" }
}

Write-Host "`n== Running the canvas graph: real SSE events per node"
$script:Run = $null
Test-Check 'stream gives running/done per node id in pipeline order, then a cited result' {
    $g = New-CanvasGraph 'groq' $false @($Sources[0]) @(@{ from = 'src1'; to = 'n4' })
    $events = Invoke-Stream "{`"question`":`"How much is refunded if a lab test is cancelled before the sample is collected?`",`"graph_id`":$($script:GraphId),`"graph`":$(ConvertTo-Json10 $g)}"
    $script:Run = $events
    $order = (@($events | Where-Object { $_.type -eq 'stage' }) | ForEach-Object { "$($_.node_id):$($_.status)" }) -join ' '
    $expected = 'n1:running n1:done n2:running n2:done n3:running n3:done n4:running n4:done n5:running n5:done'
    $last = $events[-1]
    if ($order -ne $expected) { return "order: $order" }
    if ($last.type -ne 'result' -or $last.supported -ne $true) { return "last: $($last.type) supported=$($last.supported)" }
    $true
}
Test-Check 'agent event names the searched source and the result cites it (source node highlight)' {
    if (-not $script:Run) { return 'no run' }
    $agent = @($script:Run | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0].detail
    $cited = @($script:Run[-1].citations | ForEach-Object { $_.source_id })
    if ((@($agent.retrieval.searched) -join ',') -eq 'src1' -and $cited -contains 'src1') { $true } else { "searched=$(@($agent.retrieval.searched) -join ','), cited=$($cited -join ',')" }
}
Test-Check 'result carries model, real tokens and cost for the answer card' {
    if (-not $script:Run) { return 'no run' }
    $l = $script:Run[-1]
    if ($l.model -like 'groq:*' -and $l.usage.output_tokens -gt 0 -and $l.usage.estimated -eq $false -and $l.cost.usd -gt 0) { $true }
    else { "model=$($l.model), usage=$($l.usage | ConvertTo-Json -Compress), cost=$($l.cost | ConvertTo-Json -Compress)" }
}

Write-Host "`n== Every agent model, chosen on the agent node"
foreach ($m in @('local', 'local_small', 'gemini')) {
    Test-Check "agent model '$m' answers the canvas graph with a cited answer" {
        $g = New-CanvasGraph $m ($m -ne 'gemini') @($Sources[0]) @(@{ from = 'src1'; to = 'n4' })
        $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" "{`"question`":`"Within how many working days are approved refunds paid?`",`"graph_id`":$($script:GraphId),`"graph`":$(ConvertTo-Json10 $g)}"
        $l = @($r.Json.events)[-1]
        if ($l.fallback -and "$($l.fallback.code)".StartsWith('quota_')) { throw [QuotaBlocked]::new($l.fallback.code, "$m refused; a local model answered") }
        if ($l.model -like "${m}:*" -and $l.supported -eq $true -and $l.usage.output_tokens -gt 0) {
            Write-Host ("        {0}: {1:N1}s | {2}" -f $l.model, ($l.response_ms / 1000), $l.answer) -ForegroundColor DarkGray
            $true
        } else { "model=$($l.model) ($($l.type) $($l.code)) supported=$($l.supported)" }
    }
}
Test-Check 'local-only on the agent node refuses a cloud model (400)' {
    $g = New-CanvasGraph 'groq' $true
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" "{`"question`":`"hi`",`"graph`":$(ConvertTo-Json10 $g)}"
    if ($r.Status -eq 400 -and $r.Json.detail -match 'Local-only') { $true } else { "HTTP $($r.Status)" }
}
Test-Check 'GET /models offers all four agent models' {
    $keys = @((Invoke-Api 'GET' "$Api/models").Json.models | ForEach-Object { $_.key }) -join ','
    if ($keys -eq 'gemini,groq,local,local_small') { $true } else { $keys }
}

Write-Host "`n== Usage dashboard"
Test-Check 'dashboard data: totals, by model, by user, by day and recent answers' {
    $t = (Invoke-Api 'GET' "$Api/usage/totals").Json
    $m = @((Invoke-Api 'GET' "$Api/usage/by-model").Json)
    $u = @((Invoke-Api 'GET' "$Api/usage/by-user").Json)
    $d = @((Invoke-Api 'GET' "$Api/usage/by-day?days=14").Json)
    $r = @((Invoke-Api 'GET' "$Api/usage/recent?limit=5").Json)
    if ($t.requests -gt 0 -and $m.Count -gt 0 -and $u.Count -gt 0 -and $d.Count -gt 0 -and $r.Count -gt 0 -and
        ($m | Measure-Object -Property requests -Sum).Sum -eq $t.requests) { $true }
    else { "totals=$($t.requests), models=$($m.Count), users=$($u.Count), days=$($d.Count), recent=$($r.Count)" }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by quota (of {4})" -f $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total)
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Phase 6 verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 6 NOT fully verified.' -ForegroundColor Red; exit 1
