<#
Checks Phase 4 (models, fallback, cost and usage) on the running Docker stack.
Prints PASS / FAIL / SKIP / BLOCKED per check. Works in Windows PowerShell 5.1 and PowerShell 7.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase4.ps1

Part A uses the api as it is: the real local model (qwen2.5:3b in Ollama, no quota), one real Gemini
answer streamed through nginx, Groq only if GROQ_API_KEY is set, and the usage API.
Part B (failure injection) restarts the api with Gemini and Groq pointed at tests\fake_gemini.py on
this PC (needs Python) and triggers faults: server errors, daily and per-minute quotas, a connection
dropped mid-answer, missing token counts. The fallback model is the REAL local model.
Part C also points the local model at a model that is not pulled, so every provider fails.
The normal api is restored at the end. Add -SkipFailureInjection to run Part A only.

Results: PASS verified. FAIL checked and wrong. SKIP could not be judged (for example no Groq key).
BLOCKED: Gemini's quota refused the request (HTTP 429), so nothing is known about that check.
Each run uses a new user (p4_<time>) so usage numbers can be checked exactly.
Exit code is 0 only if every check passes.
#>
param([switch]$SkipFailureInjection)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0
$script:Blocked = 0
$Api = 'http://127.0.0.1:8000'
$Web = 'http://127.0.0.1:3000/api'
$FakePort = 8765
$FakeUrl = "http://127.0.0.1:$FakePort"
$User = 'p4_' + (Get-Date -Format 'MMddHHmmss')
$script:LoggedRuns = 0     # model answers (or total failures) this script caused; each should be one usage row

function Write-Result([string]$Result, [string]$Name, [string]$Detail = '') {
    $colors = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow'; BLOCKED = 'Magenta' }
    $line = '{0,-7} {1}' -f $Result, $Name
    if ($Detail) { $line += "  ($Detail)" }
    Write-Host $line -ForegroundColor $colors[$Result]
    switch ($Result) {
        'PASS' { $script:Pass++ } 'FAIL' { $script:Fail++ } 'SKIP' { $script:Skip++ } 'BLOCKED' { $script:Blocked++ }
    }
}

class QuotaBlocked : System.Exception {
    [string]$Code
    QuotaBlocked([string]$code, [string]$message) : base($message) { $this.Code = $code }
}

# $Test returns $true (PASS), 'SKIP:<reason>' (SKIP), or other text (FAIL). QuotaBlocked -> BLOCKED.
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
$script:Http.Timeout = [TimeSpan]::FromSeconds(600)

function New-Result($res) {
    $text = [System.Text.Encoding]::UTF8.GetString($res.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
    $json = $null
    try { $json = $text | ConvertFrom-Json } catch { }
    [pscustomobject]@{ Status = [int]$res.StatusCode; Text = $text; Json = $json }
}

function Invoke-Api([string]$Method, [string]$Url, [string]$Body = $null) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)
    if ($Body) {
        $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
        $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    }
    New-Result ($script:Http.SendAsync($req).GetAwaiter().GetResult())
}

function Send-File([string]$SourceId, [string]$Path, [string]$FileName) {
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $file = [System.Net.Http.ByteArrayContent]::new([System.IO.File]::ReadAllBytes((Resolve-Path $Path)))
    $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/pdf')
    $form.Add($file, 'file', $FileName)
    New-Result ($script:Http.PostAsync("$Api/sources/$SourceId/pdf", $form).GetAwaiter().GetResult())
}

function New-Graph([string[]]$SourceIds, [string]$Model = 'gemini') {
    $nodes = @(
        @{ id = 'n1'; type = 'input'; config = @{} },
        @{ id = 'n2'; type = 'guardrails'; config = @{ block = @('empty', 'abuse', 'off_topic') } },
        @{ id = 'n3'; type = 'router'; config = @{} },
        @{ id = 'n4'; type = 'agent'; config = @{ model = $Model; language = 'en' } },
        @{ id = 'n5'; type = 'output'; config = @{} }
    )
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    foreach ($id in $SourceIds) {
        $nodes += @{ id = $id; type = 'source_doc'; config = @{ file = "$id.pdf" } }
        $edges += @{ from = $id; to = 'n4' }
    }
    @{ nodes = $nodes; edges = $edges }
}

function New-RunBody([string]$Question, [string]$Model, [string[]]$Sources = @('p4_refund'), [string]$UserName = $User) {
    $body = @{ question = $Question; model = $Model; user = $UserName; graph = (New-Graph $Sources $Model) }
    $body | ConvertTo-Json -Depth 10 -Compress
}

# Runs one question (JSON mode) and returns the final event, the agent detail and all events.
function Invoke-Run([string]$Question, [string]$Model, [string[]]$Sources = @('p4_refund')) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-RunBody $Question $Model $Sources)
    if ($r.Status -ne 200) { throw "HTTP $($r.Status): $($r.Text)" }
    $events = @($r.Json.events)
    $last = $events[-1]
    if ($last.model -or $last.code -eq 'all_providers_failed') { $script:LoggedRuns++ }
    $agent = @($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0]
    [pscustomobject]@{ Last = $last; Agent = $agent.detail; Events = $events }
}

# Streams one question and returns every event as it arrived.
function Invoke-Stream([string]$Url, [string]$Body) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $Url)
    $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
    $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    $res = $script:Http.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    if ([int]$res.StatusCode -ne 200) { throw "HTTP $([int]$res.StatusCode)" }
    $reader = [System.IO.StreamReader]::new($res.Content.ReadAsStreamAsync().GetAwaiter().GetResult(), [System.Text.Encoding]::UTF8)
    $events = @()
    while ($null -ne ($line = $reader.ReadLine())) {
        if ($line.StartsWith('data: ')) { $events += ($line.Substring(6) | ConvertFrom-Json) }
    }
    $reader.Dispose()
    $last = $events[-1]
    if ($last.model -or $last.code -eq 'all_providers_failed') { $script:LoggedRuns++ }
    , $events
}

# A Gemini 429 that ended in a local fallback (or an error) says nothing about Gemini: BLOCKED.
function Assert-GeminiNotBlocked($Last) {
    if ($Last.fallback -and "$($Last.fallback.code)".StartsWith('quota_')) {
        throw [QuotaBlocked]::new($Last.fallback.code, "Gemini refused; the local model answered instead")
    }
}

function Get-LatestRow { @((Invoke-Api 'GET' "$Api/usage/recent?user=$User&limit=1").Json)[0] }

# Expected cost from the /models price list, to check the api's arithmetic independently.
function Get-ExpectedUsd([string]$ModelKey, [int]$InputTokens, [int]$OutputTokens) {
    $m = @($script:Models.models | Where-Object { $_.key -eq $ModelKey })[0]
    if ($null -eq $m.input_per_million_usd) { return $null }
    ($InputTokens * [double]$m.input_per_million_usd + $OutputTokens * [double]$m.output_per_million_usd) / 1000000
}

function Test-Near([double]$A, [double]$B) { [Math]::Abs($A - $B) -le [Math]::Max(1e-9, [Math]::Abs($B) * 1e-4) }

function Wait-ApiHealthy {
    for ($i = 0; $i -lt 60; $i++) {
        $id = (docker compose ps -q api 2>$null | Out-String).Trim()
        if ($id -and ((docker inspect -f '{{.State.Health.Status}}' $id 2>$null | Out-String).Trim() -eq 'healthy')) { return $true }
        Start-Sleep -Seconds 2
    }
    $false
}

function Restart-Api([string[]]$EnvLines) {
    if ($EnvLines) {
        $override = Join-Path $env:TEMP 'bharatagent-phase4.override.yml'
        (@('services:', '  api:', '    environment:') + ($EnvLines | ForEach-Object { "      $_" })) |
            Set-Content -Path $override -Encoding ASCII
        docker compose -f docker-compose.yml -f $override up -d api 2>$null | Out-Null
    } else {
        docker compose up -d api 2>$null | Out-Null
    }
    Wait-ApiHealthy
}

$QLab = 'How much is refunded if a lab test is cancelled before the sample is collected?'

# ---------- Preflight ----------
$script:ApiUp = $false
try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
if (-not $script:ApiUp) { Write-Host 'The api is not reachable on :8000. Run: docker compose up -d --build' -ForegroundColor Red }
$script:Models = $null
try { $script:Models = (Invoke-Api 'GET' "$Api/models").Json } catch { }
Write-Host "Test user for this run: $User"

$fakeProcess = $null
try {
    # ================= Part A: the api as configured =================
    Write-Host "`n== Models, prices and users"
    Test-Check 'GET /models lists gemini, groq and local with model ids' {
        $keys = @($script:Models.models | ForEach-Object { "$($_.key)=$($_.model_id)" }) -join ', '
        if (@($script:Models.models).Count -eq 3 -and $keys -match 'gemini=' -and $keys -match 'groq=' -and $keys -match 'local=') { $true } else { $keys }
    }
    Test-Check 'price table has a check date; local model has no API price' {
        $local = @($script:Models.models | Where-Object { $_.key -eq 'local' })[0]
        if ($script:Models.prices_checked_on -and $local.input_per_million_usd -eq 0 -and $local.output_per_million_usd -eq 0 -and $script:Models.fallback -eq 'local') { $true }
        else { "checked_on=$($script:Models.prices_checked_on), local=$($local.input_per_million_usd)/$($local.output_per_million_usd)" }
    }
    Test-Check 'a model without a published price is marked unpriced, not free' {
        $unpriced = @($script:Models.models | Where-Object { -not $_.priced })
        if ($unpriced.Count -eq 0) { return 'SKIP:every model has a price in prices.json' }
        if (@($unpriced | Where-Object { $null -ne $_.input_per_million_usd }).Count -eq 0) { $true } else { 'unpriced model shows a price' }
    }
    Test-Check 'local model is pulled and ready' {
        $local = @($script:Models.models | Where-Object { $_.key -eq 'local' })[0]
        if ($local.ready) { $true } else { "not ready: $($local.why_not_ready)" }
    }
    Test-Check "users: demo exists and $User can be added" {
        $add = Invoke-Api 'POST' "$Api/users" "{`"name`":`"$User`"}"
        $names = @((Invoke-Api 'GET' "$Api/users").Json | ForEach-Object { $_.name })
        if ($add.Status -eq 201 -and $names -contains 'demo' -and $names -contains $User) { $true } else { "HTTP $($add.Status), users=$($names -join ',')" }
    }
    Test-Check 'unknown user and unknown model are refused (400) before anything runs' {
        $u = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-RunBody 'hi' 'gemini' @() 'nobody_here')
        $m = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" ((@{ question = 'hi'; model = 'gpt-9'; user = $User; graph = (New-Graph @()) }) | ConvertTo-Json -Depth 10 -Compress)
        if ($u.Status -eq 400 -and $m.Status -eq 400) { $true } else { "user: HTTP $($u.Status), model: HTTP $($m.Status)" }
    }
    Test-Check 'an agent node with an unknown model is a graph error (bad_model)' {
        $r = Invoke-Api 'POST' "$Api/pipeline/validate" ((New-Graph @() 'gpt-9') | ConvertTo-Json -Depth 10 -Compress)
        if (@($r.Json.errors | ForEach-Object { $_.code }) -contains 'bad_model') { $true } else { $r.Text }
    }
    Test-Check 'upload the refund PDF (local embeddings, no model API)' {
        $r = Send-File 'p4_refund' 'samples\refund-policy.pdf' 'refund-policy.pdf'
        if ($r.Status -eq 201) { $true } else { "HTTP $($r.Status): $($r.Text)" }
    }

    Write-Host "`n== Local model (qwen2.5:3b in Ollama, real)"
    $script:LocalRun = $null
    Test-Check 'local answer: cited, actual token counts, model name' {
        $script:LocalRun = Invoke-Run $QLab 'local'
        $l = $script:LocalRun.Last
        if ($l.type -ne 'result') { return "$($l.code): $($l.message)" }
        if ($l.model -notlike 'local:*' -or $l.usage.estimated -ne $false -or $l.usage.output_tokens -lt 1) { return "model=$($l.model), usage=$($l.usage | ConvertTo-Json -Compress)" }
        if ($l.supported -ne $true) { return "not supported: $($l.answer)" }
        Write-Host "        $($l.answer)" -ForegroundColor DarkGray
        $true
    }
    Test-Check 'local answer: no API fee, local compute time reported separately' {
        $l = $script:LocalRun.Last
        if ($l.cost.api_fee -eq $false -and $l.cost.usd -eq 0 -and $l.cost.local_compute_ms -gt 0 -and $l.cost.local_compute_ms -eq $l.response_ms) { $true }
        else { "cost=$($l.cost | ConvertTo-Json -Compress)" }
    }
    Test-Check 'local answer: the same tokens priced on the cloud models (comparison line)' {
        $l = $script:LocalRun.Last
        $g = @($l.comparison | Where-Object { $_.model -eq 'gemini' })[0]
        $expected = Get-ExpectedUsd 'gemini' $l.usage.input_tokens $l.usage.output_tokens
        if (@($l.comparison).Count -eq 3 -and (Test-Near $g.usd $expected)) { $true } else { "gemini comparison $($g.usd), expected $expected" }
    }
    Test-Check 'local answer is logged: model, tokens, zero cost, compute time, sources' {
        $row = Get-LatestRow
        $l = $script:LocalRun.Last
        if ($row.model -eq $l.model -and $row.input_tokens -eq $l.usage.input_tokens -and $row.output_tokens -eq $l.usage.output_tokens -and
            $row.cost_usd -eq 0 -and $row.local_compute_ms -eq $l.response_ms -and (@($row.sources) -join ',') -eq 'p4_refund' -and $row.status -eq 'ok') { $true }
        else { "row=$($row | ConvertTo-Json -Compress)" }
    }

    Write-Host "`n== Gemini (1 real request, streamed through nginx)"
    $script:GeminiEvents = $null
    $geminiReady = (@($script:Models.models | Where-Object { $_.key -eq 'gemini' })[0]).ready
    Test-Check 'streamed Gemini answer: result event carries model, actual tokens and cost' {
        if (-not $geminiReady) { return 'SKIP:GEMINI_API_KEY not set' }
        $script:GeminiEvents = Invoke-Stream "$Web/pipeline/run" (New-RunBody $QLab 'gemini')
        $l = $script:GeminiEvents[-1]
        if ($l.type -eq 'error') { return "$($l.code): $($l.message)" }
        Assert-GeminiNotBlocked $l
        if ($l.model -notlike 'gemini:*') { return "answered by $($l.model) (fallback: $($l.fallback.code))" }
        $tokens = @($script:GeminiEvents | Where-Object { $_.type -eq 'token' }).Count
        if ($tokens -lt 1 -or $l.usage.estimated -ne $false -or $l.cost.priced -ne $true -or $l.cost.usd -le 0) {
            return "tokens events=$tokens, usage=$($l.usage | ConvertTo-Json -Compress), cost=$($l.cost | ConvertTo-Json -Compress)"
        }
        Write-Host "        $($l.model): $($l.usage.input_tokens) in / $($l.usage.output_tokens) out, INR $($l.cost.inr) (USD $($l.cost.usd))" -ForegroundColor DarkGray
        $true
    }
    Test-Check 'Gemini cost = tokens x prices.json, and INR = USD x USD_TO_INR' {
        if (-not $script:GeminiEvents) { return 'SKIP:no Gemini answer' }
        $l = $script:GeminiEvents[-1]
        Assert-GeminiNotBlocked $l
        if ($l.model -notlike 'gemini:*') { return 'SKIP:Gemini did not answer' }
        $expected = Get-ExpectedUsd 'gemini' $l.usage.input_tokens $l.usage.output_tokens
        if ((Test-Near $l.cost.usd $expected) -and (Test-Near $l.cost.inr ($expected * $script:Models.usd_to_inr))) { $true }
        else { "usd=$($l.cost.usd) expected $expected; inr=$($l.cost.inr)" }
    }
    Test-Check 'Gemini answer is logged with the same tokens and cost' {
        if (-not $script:GeminiEvents) { return 'SKIP:no Gemini answer' }
        $l = $script:GeminiEvents[-1]
        Assert-GeminiNotBlocked $l
        $row = Get-LatestRow
        if ($row.model -eq $l.model -and $row.input_tokens -eq $l.usage.input_tokens -and (Test-Near $row.cost_usd $l.cost.usd)) { $true }
        else { "row=$($row | ConvertTo-Json -Compress)" }
    }

    Write-Host "`n== Groq"
    Test-Check 'Groq answer with real API (needs GROQ_API_KEY)' {
        $groq = @($script:Models.models | Where-Object { $_.key -eq 'groq' })[0]
        if (-not $groq.ready) { return 'SKIP:GROQ_API_KEY not set; Groq is checked against the fake API in Part B' }
        $run = Invoke-Run $QLab 'groq'
        if ($run.Last.model -like 'groq:*' -and $run.Last.usage.output_tokens -gt 0) { $true } else { "model=$($run.Last.model), fallback=$($run.Last.fallback.code)" }
    }

    Write-Host "`n== Usage API"
    Test-Check 'totals for this user match its rows (requests, tokens, cost)' {
        $rows = @((Invoke-Api 'GET' "$Api/usage/recent?user=$User&limit=200").Json)
        $t = (Invoke-Api 'GET' "$Api/usage/totals?user=$User").Json
        $inSum = ($rows | Measure-Object -Property input_tokens -Sum).Sum
        $costSum = ($rows | Where-Object { $null -ne $_.cost_usd } | Measure-Object -Property cost_usd -Sum).Sum
        if ($rows.Count -eq $script:LoggedRuns -and $t.requests -eq $rows.Count -and $t.input_tokens -eq $inSum -and (Test-Near $t.cost_usd ([double]$costSum))) { $true }
        else { "runs=$($script:LoggedRuns), rows=$($rows.Count), totals=$($t | ConvertTo-Json -Compress)" }
    }
    Test-Check 'by-user, by-model and by-day include this run' {
        $u = @((Invoke-Api 'GET' "$Api/usage/by-user").Json | Where-Object { $_.user -eq $User })[0]
        $m = @((Invoke-Api 'GET' "$Api/usage/by-model?user=$User").Json | ForEach-Object { $_.model })
        $d = @((Invoke-Api 'GET' "$Api/usage/by-day?user=$User&days=2").Json)
        if ($u.requests -eq $script:LoggedRuns -and ($m -join ',') -match 'local:' -and ($d | Measure-Object -Property requests -Sum).Sum -eq $script:LoggedRuns) { $true }
        else { "by-user=$($u.requests), models=$($m -join ','), by-day=$(($d | Measure-Object -Property requests -Sum).Sum)" }
    }

    if ($SkipFailureInjection) { throw [System.OperationCanceledException]::new('skip') }

    # ================= Part B: failure injection (fake cloud APIs, real local model) =================
    Write-Host "`n== Failure injection: restarting the api with Gemini and Groq pointed at the fake APIs..."
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python is needed for failure injection (or run with -SkipFailureInjection)' }
    $fakeProcess = Start-Process $python.Source -ArgumentList @('tests\fake_gemini.py', "$FakePort", '0.05') -PassThru -WindowStyle Hidden
    $cloudFake = @("GEMINI_BASE_URL: http://host.docker.internal:$FakePort", 'GEMINI_API_KEY: fake-key',
                   "GROQ_BASE_URL: http://host.docker.internal:$FakePort", 'GROQ_API_KEY: fake-key')
    if (-not (Restart-Api $cloudFake)) { throw 'api did not become healthy with the fake APIs' }
    $fallbacksBefore = @(@((Invoke-Api 'GET' "$Api/usage/recent?user=$User&limit=200").Json) | Where-Object { $_.fallback_from }).Count
    function Get-FakeCount([string]$Name) { $c = (Invoke-Api 'GET' "$FakeUrl/_requests").Json.$Name; if ($c) { [int]$c } else { 0 } }

    Test-Check 'Gemini HTTP 500: retried exactly once, then the local model answered' {
        $run = Invoke-Run "$QLab FAULT_500" 'gemini'
        $l = $run.Last
        $tries = @($run.Agent.generation.attempts | ForEach-Object { "$($_.model):$($_.code)" }) -join ','
        $sent = Get-FakeCount 'gemini:FAULT_500'
        $fallbackEvent = @($run.Events | Where-Object { $_.type -eq 'fallback' }).Count
        if ($l.model -like 'local:*' -and $l.fallback.from -eq 'gemini' -and $l.fallback.code -eq 'server_error' -and $sent -eq 2 -and
            $tries -eq 'gemini:server_error,gemini:server_error' -and $fallbackEvent -eq 1) { $true }
        else { "model=$($l.model), fake got $sent requests, attempts=$tries, fallback events=$fallbackEvent" }
    }
    Test-Check 'Gemini daily quota: not retried, fallback to local' {
        $run = Invoke-Run "$QLab FAULT_DAILY" 'gemini'
        $sent = Get-FakeCount 'gemini:FAULT_DAILY'
        if ($run.Last.model -like 'local:*' -and $run.Last.fallback.code -eq 'quota_daily' -and $sent -eq 1) { $true }
        else { "model=$($run.Last.model), fallback=$($run.Last.fallback.code), fake got $sent requests" }
    }
    Test-Check 'Gemini per-minute limit once: retried after Retry-After, Gemini answered (no fallback)' {
        $run = Invoke-Run "$QLab FAULT_RATE_ONCE" 'gemini'
        $sent = Get-FakeCount 'gemini:FAULT_RATE_ONCE'
        if ($run.Last.model -like 'gemini:*' -and -not $run.Last.fallback -and $sent -eq 2 -and
            @($run.Agent.generation.attempts).Count -eq 1 -and $run.Agent.generation.attempts[0].code -eq 'quota_rate') { $true }
        else { "model=$($run.Last.model), fake got $sent requests" }
    }
    Test-Check 'connection dropped mid-answer: partial text discarded (token_reset), then local answered' {
        $events = Invoke-Stream "$Api/pipeline/run" (New-RunBody "$QLab FAULT_MIDSTREAM" 'gemini')
        $kinds = @($events | ForEach-Object { $_.type })
        $resets = @($events | Where-Object { $_.type -eq 'token_reset' }).Count
        $l = $events[-1]
        $firstReset = [array]::IndexOf($kinds, 'token_reset'); $firstToken = [array]::IndexOf($kinds, 'token')
        if ($resets -ge 1 -and $firstToken -lt $firstReset -and $l.model -like 'local:*' -and $l.fallback.code -eq 'network_error' -and
            $l.answer -notmatch 'According to the document') { $true }
        else { "resets=$resets, model=$($l.model), fallback=$($l.fallback.code), answer=$($l.answer)" }
    }
    Test-Check 'no token counts from the provider: estimated with the tokenizer and labelled' {
        $run = Invoke-Run "$QLab FAULT_NO_USAGE" 'gemini'
        $l = $run.Last
        $row = Get-LatestRow
        if ($l.model -like 'gemini:*' -and $l.usage.estimated -eq $true -and $l.usage.output_tokens -gt 0 -and $l.cost.priced -and
            $row.tokens_estimated -eq $true) { $true }
        else { "model=$($l.model), usage=$($l.usage | ConvertTo-Json -Compress), row estimated=$($row.tokens_estimated)" }
    }
    Test-Check 'Groq answer (fake API): actual tokens; no published price -> no cost, logged as null' {
        $run = Invoke-Run $QLab 'groq'
        $l = $run.Last
        $row = Get-LatestRow
        if ($l.model -like 'groq:*' -and $l.usage.estimated -eq $false -and $l.cost.priced -eq $false -and $null -eq $l.cost.usd -and
            $null -eq $row.cost_usd -and $row.model -like 'groq:*') { $true }
        else { "model=$($l.model), cost=$($l.cost | ConvertTo-Json -Compress), row cost=$($row.cost_usd)" }
    }
    Test-Check 'Groq daily limit: not retried, fallback to local' {
        $run = Invoke-Run "$QLab FAULT_DAILY" 'groq'
        $sent = Get-FakeCount 'groq:FAULT_DAILY'
        if ($run.Last.model -like 'local:*' -and $run.Last.fallback.code -eq 'quota_daily' -and $sent -eq 1) { $true }
        else { "model=$($run.Last.model), fallback=$($run.Last.fallback.code), fake got $sent requests" }
    }
    Test-Check 'fallback answers are logged with fallback_from' {
        $rows = @((Invoke-Api 'GET' "$Api/usage/recent?user=$User&limit=200").Json)
        $fallbacks = @($rows | Where-Object { $_.fallback_from }).Count
        if ($fallbacks -eq $fallbacksBefore + 4) { $true } else { "rows with fallback_from: $fallbacks (expected $($fallbacksBefore + 4))" }
    }

    # ================= Part C: every provider fails =================
    Write-Host "`n== Every provider fails: restarting the api with the local model pointed at a model that is not pulled..."
    if (-not (Restart-Api ($cloudFake + @('OLLAMA_CHAT_MODEL: not-pulled-model:1b')))) { throw 'api did not become healthy' }
    Test-Check 'Gemini 500 and local missing: one clear error listing every attempt, no answer' {
        $events = Invoke-Stream "$Web/pipeline/run" (New-RunBody "$QLab FAULT_500" 'gemini')
        $l = $events[-1]
        $tries = @($l.attempts | ForEach-Object { "$($_.model):$($_.code)" }) -join ','
        $results = @($events | Where-Object { $_.type -eq 'result' }).Count
        if ($l.type -eq 'error' -and $l.code -eq 'all_providers_failed' -and $results -eq 0 -and
            $tries -eq 'gemini:server_error,gemini:server_error,local:model_not_found' -and $l.message -match 'ollama pull') { $true }
        else { "type=$($l.type), code=$($l.code), attempts=$tries" }
    }
    Test-Check 'local model requested and missing: error names only the local attempt' {
        $run = Invoke-Run $QLab 'local'
        $tries = @($run.Last.attempts | ForEach-Object { "$($_.model):$($_.code)" }) -join ','
        if ($run.Last.code -eq 'all_providers_failed' -and $tries -eq 'local:model_not_found') { $true } else { "code=$($run.Last.code), attempts=$tries" }
    }
    Test-Check 'total failures are logged as errors with zero tokens' {
        $rows = @((Invoke-Api 'GET' "$Api/usage/recent?user=$User&limit=2").Json)
        $bad = @($rows | Where-Object { $_.status -ne 'error' -or $_.error_code -ne 'all_providers_failed' -or $_.output_tokens -ne 0 })
        if ($rows.Count -eq 2 -and $bad.Count -eq 0) { $true } else { "rows=$($rows | ConvertTo-Json -Compress)" }
    }
    Test-Check 'usage totals count errors and fallbacks for this user' {
        $t = (Invoke-Api 'GET' "$Api/usage/totals?user=$User").Json
        if ($t.requests -eq $script:LoggedRuns -and $t.errors -eq 2 -and $t.fallbacks -ge 4 -and $t.estimated_requests -eq 1) { $true }
        else { "runs=$($script:LoggedRuns), totals=$($t | ConvertTo-Json -Compress)" }
    }
}
catch [System.OperationCanceledException] { }
catch {
    Write-Result 'FAIL' 'failure injection could not run' $_.Exception.Message
}
finally {
    if (-not $SkipFailureInjection) {
        Write-Host "`nRestoring the normal api..."
        if ($fakeProcess) { Stop-Process -Id $fakeProcess.Id -Force -ErrorAction SilentlyContinue }
        if (Restart-Api $null) { Write-Host 'api restored.' } else { Write-Host 'api did not become healthy; run: docker compose up -d api' -ForegroundColor Red }
    }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by Gemini quota (of {4}), test user {5}" -f
    $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total, $User)
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Phase 4 verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 4 NOT fully verified.' -ForegroundColor Red; exit 1
