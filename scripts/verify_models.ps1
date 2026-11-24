<#
Checks the model line-up and the local-only privacy option on the running Docker stack:
Gemini, Groq (openai/gpt-oss-120b), local Qwen 3 14B and the Qwen 2.5 3B backup all go through the same
pipeline (retrieval, citations, claim check, tokens, cost, usage log), and local-only never calls a cloud API.
Prints PASS / FAIL / SKIP / BLOCKED. Works in Windows PowerShell 5.1 and PowerShell 7.

  powershell -ExecutionPolicy Bypass -File scripts\verify_models.ps1

Part A uses the api as it is: one real request each to Gemini and Groq, three to the local models.
Part B restarts the api with Gemini/Groq pointed at tests\fake_gemini.py (needs Python) and the main local
model pointed at a model that is not pulled, to check the fallback chain down to the 3B backup.
Part C also breaks the backup and turns LOCAL_ONLY on for the whole server, to prove that local-only
never sends anything to a cloud API, not even when every local model fails.
The normal api is restored at the end. Add -SkipRestarts to run Part A only.
If the ollama container was started with docker-compose.gpu.yml it keeps its GPU; this script only
restarts the api. Exit code is 0 only if every check passes.
#>
param([switch]$SkipRestarts)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0; $script:Fail = 0; $script:Skip = 0; $script:Blocked = 0
$Api = 'http://127.0.0.1:8000'
$FakePort = 8765
$FakeUrl = "http://127.0.0.1:$FakePort"

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

function Send-Pdf([string]$SourceId, [string]$Path) {
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $file = [System.Net.Http.ByteArrayContent]::new([System.IO.File]::ReadAllBytes((Resolve-Path $Path)))
    $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/pdf')
    $form.Add($file, 'file', (Split-Path $Path -Leaf))
    # Phase 8: the file goes into the signed-in account's project; the graph's node points at it with source_ref.
    $res = $script:Http.PostAsync("$Api/projects/$($script:Who.Project)/sources/file", $form).GetAwaiter().GetResult()
    if ([int]$res.StatusCode -eq 201) {
        $script:SrcRef = ([System.Text.Encoding]::UTF8.GetString($res.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()) | ConvertFrom-Json).id
    }
    [int]$res.StatusCode
}

function Get-JsonString([int[]]$CodePoints) {
    $backslash = [char]92
    '"' + (($CodePoints | ForEach-Object { if ($_ -lt 128) { [string][char]$_ } else { "${backslash}u{0:x4}" -f $_ } }) -join '') + '"'
}

function New-Body([string]$QuestionJson, [string]$Model, [bool]$LocalOnly = $false, [bool]$NodeLocalOnly = $false) {
    $agent = @{ model = $Model; language = 'en' }
    if ($NodeLocalOnly) { $agent.local_only = $true }
    $graph = @{
        nodes = @(@{ id = 'n1'; type = 'input'; config = @{} }, @{ id = 'n2'; type = 'guardrails'; config = @{} },
                  @{ id = 'n3'; type = 'router'; config = @{} }, @{ id = 'n4'; type = 'agent'; config = $agent },
                  @{ id = 'n5'; type = 'output'; config = @{} }, @{ id = 'pm_refund'; type = 'source_doc'; config = @{ source_ref = $script:SrcRef } })
        edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' },
                  @{ from = 'n4'; to = 'n5' }, @{ from = 'pm_refund'; to = 'n4' })
    }
    $lo = if ($LocalOnly) { 'true' } else { 'false' }
    "{`"question`":$QuestionJson,`"model`":`"$Model`",`"local_only`":$lo,`"project_id`":$($script:Who.Project),`"graph`":$($graph | ConvertTo-Json -Depth 10 -Compress)}"
}

function Invoke-Run([string]$QuestionJson, [string]$Model, [bool]$LocalOnly = $false) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-Body $QuestionJson $Model $LocalOnly)
    if ($r.Status -ne 200) { throw "HTTP $($r.Status): $($r.Text)" }
    $events = @($r.Json.events)
    $agent = @($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0]
    [pscustomobject]@{ Last = $events[-1]; Agent = $agent.detail }
}

# A model answer that is cited, has real token counts, and was answered by the model asked for.
function Test-Answer($Run, [string]$Model, [string]$ModelId) {
    $l = $Run.Last
    if ($l.type -eq 'error') { return "$($l.code): $($l.message)" }
    if ($l.fallback -and "$($l.fallback.code)".StartsWith('quota_')) { throw [QuotaBlocked]::new($l.fallback.code, "$Model refused; a local model answered") }
    if ($l.model -ne "${Model}:$ModelId") { return "answered by $($l.model) (fallback $($l.fallback.code))" }
    if ($l.supported -ne $true) { return "not supported: $($l.answer)" }
    if ($l.usage.estimated -ne $false -or $l.usage.output_tokens -lt 1) { return "usage=$($l.usage | ConvertTo-Json -Compress)" }
    Write-Host ("        {0}: {1:N1}s, {2} in / {3} out, cost {4} | {5}" -f $l.model, ($l.response_ms / 1000), $l.usage.input_tokens,
        $l.usage.output_tokens, $(if ($l.cost.api_fee) { "INR $($l.cost.inr)" } else { 'no API fee' }), $l.answer) -ForegroundColor DarkGray
    $true
}

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
        $override = Join-Path $env:TEMP 'bharatagent-models.override.yml'
        (@('services:', '  api:', '    environment:') + ($EnvLines | ForEach-Object { "      $_" })) | Set-Content -Path $override -Encoding ASCII
        docker compose -f docker-compose.yml -f $override up -d --no-deps api 2>$null | Out-Null
    } else {
        docker compose up -d --no-deps api 2>$null | Out-Null
    }
    Wait-ApiHealthy
}

$QLab = '"How much is refunded if a lab test is cancelled before the sample is collected?"'
$QDays = '"Within how many working days are approved refunds paid?"'
# Telugu: "if I cancel a lab test, how much refund?"
$QTelugu = Get-JsonString @(0x0C32, 0x0C4D, 0x0C2F, 0x0C3E, 0x0C2C, 0x0C4D, 32, 0x0C1F, 0x0C46, 0x0C38, 0x0C4D, 0x0C1F, 0x0C4D, 32,
    0x0C30, 0x0C26, 0x0C4D, 0x0C26, 0x0C41, 32, 0x0C1A, 0x0C47, 0x0C38, 0x0C4D, 0x0C24, 0x0C47, 32, 0x0C0E, 0x0C02, 0x0C24, 32,
    0x0C35, 0x0C3E, 0x0C2A, 0x0C38, 0x0C41, 32, 0x0C35, 0x0C38, 0x0C4D, 0x0C24, 0x0C41, 0x0C02, 0x0C26, 0x0C3F, 63)
$TeluguLetters = '[{0}-{1}]' -f [char]0x0C00, [char]0x0C7F

$script:ApiUp = $false
try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
$script:SrcRef = 0
if ($script:ApiUp) {
    # Phase 8: a fresh test account (real registration, own session and CSRF token) replaces the open "demo" user.
    . "$PSScriptRoot\_session.ps1"
    $script:Who = New-TestSession 'models'
    $script:Http = $script:Who.Http
}
$models = $null
try { $models = (Invoke-Api 'GET' "$Api/models").Json } catch { }
$ids = @{}
foreach ($m in @($models.models)) { $ids[$m.key] = $m.model_id }

$fakeProcess = $null
try {
    # ================= Part A =================
    Write-Host "`n== Model line-up"
    Test-Check 'four models: gemini, groq (gpt-oss-120b), local (qwen3:14b), local_small (qwen2.5:3b), all ready' {
        $rows = @($models.models | ForEach-Object { "$($_.key)=$($_.model_id) ready=$($_.ready)" })
        $ok = @($models.models).Count -eq 4 -and $ids['gemini'] -like 'gemini-*' -and $ids['groq'] -eq 'openai/gpt-oss-120b' -and
              $ids['local'] -eq 'qwen3:14b' -and $ids['local_small'] -eq 'qwen2.5:3b' -and @($models.models | Where-Object { -not $_.ready }).Count -eq 0
        if ($ok) { $true } else { $rows -join '; ' }
    }
    Test-Check 'local models have no API price; cloud models have a real price' {
        $bad = @($models.models | Where-Object { ($_.local -and ($_.input_per_million_usd -ne 0 -or $_.output_per_million_usd -ne 0)) -or
                                                 (-not $_.local -and -not ($_.input_per_million_usd -gt 0)) })
        if ($bad.Count -eq 0) { $true } else { "wrong prices: $(@($bad | ForEach-Object { $_.key }) -join ', ')" }
    }
    Test-Check 'sample PDF is loaded as a source' {
        $code = Send-Pdf 'pm_refund' 'samples\refund-policy.pdf'
        if ($code -eq 201) { $true } else { "HTTP $code" }
    }

    Write-Host "`n== Every model through the same pipeline (cited, real tokens, cost)"
    Test-Check 'local Qwen 3 14B: cited answer, real tokens, no API fee (first call may load the model)' {
        $run = Invoke-Run $QLab 'local' $true
        $ok = Test-Answer $run 'local' $ids['local']
        if ($ok -ne $true) { return $ok }
        if ($run.Last.cost.api_fee -ne $false -or $run.Last.cost.local_compute_ms -le 0) { return "cost=$($run.Last.cost | ConvertTo-Json -Compress)" }
        $true
    }
    Test-Check 'local Qwen 3 14B in Telugu: Telugu answer, cited, language check passes' {
        $run = Invoke-Run $QTelugu 'local' $true
        $ok = Test-Answer $run 'local' $ids['local']
        if ($ok -ne $true) { return $ok }
        if ($run.Last.language -ne 'te' -or $run.Last.language_match -ne $true -or $run.Last.answer -notmatch $TeluguLetters) { return "language_match=$($run.Last.language_match): $($run.Last.answer)" }
        $true
    }
    Test-Check 'local backup Qwen 2.5 3B still answers on request' {
        Test-Answer (Invoke-Run $QDays 'local_small' $true) 'local_small' $ids['local_small']
    }
    Test-Check 'Groq GPT-OSS 120B still integrated: cited answer with cost' {
        $run = Invoke-Run $QDays 'groq'
        $ok = Test-Answer $run 'groq' $ids['groq']
        if ($ok -ne $true) { return $ok }
        if (-not ($run.Last.cost.usd -gt 0)) { return "cost=$($run.Last.cost | ConvertTo-Json -Compress)" }
        $true
    }
    Test-Check 'Gemini still integrated: cited answer with cost' {
        $run = Invoke-Run $QDays 'gemini'
        $ok = Test-Answer $run 'gemini' $ids['gemini']
        if ($ok -ne $true) { return $ok }
        if (-not ($run.Last.cost.usd -gt 0)) { return "cost=$($run.Last.cost | ConvertTo-Json -Compress)" }
        $true
    }
    Test-Check 'answers are logged per model with zero cost for local ones' {
        $rows = @((Invoke-Api 'GET' "$Api/usage/recent?limit=10").Json)
        $local = @($rows | Where-Object { $_.model -eq "local:$($ids['local'])" })[0]
        if ($local -and $local.cost_usd -eq 0 -and $local.local_compute_ms -gt 0 -and $local.output_tokens -gt 0) { $true }
        else { "no zero-cost row for local:$($ids['local'])" }
    }

    Write-Host "`n== Local-only privacy option"
    Test-Check 'local-only request for a cloud model is refused before anything runs (400)' {
        $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-Body $QDays 'gemini' $true)
        if ($r.Status -eq 400 -and $r.Json.detail -match 'Local-only') { $true } else { "HTTP $($r.Status): $($r.Text)" }
    }
    Test-Check 'local-only set on the agent node also refuses a cloud model (400)' {
        $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-Body $QDays 'groq' $false $true)
        if ($r.Status -eq 400) { $true } else { "HTTP $($r.Status)" }
    }
    Test-Check 'the chat page has the local-only switch' {
        $html = (Invoke-Api 'GET' 'http://127.0.0.1:3000/').Text
        if ($html -notmatch 'src="(/assets/[^"]+\.js)"') { return 'no script tag' }
        $js = (Invoke-Api 'GET' "http://127.0.0.1:3000$($Matches[1])").Text
        if ($js -match 'Local only' -and $js -match 'local_only') { $true } else { 'switch not found in the bundle' }
    }

    if ($SkipRestarts) { throw [System.OperationCanceledException]::new('skip') }

    # ================= Part B: fallback down to the backup =================
    Write-Host "`n== Fallback chain: restarting the api with the main local model missing and fake cloud APIs..."
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python is needed for Parts B and C (or run with -SkipRestarts)' }
    $fakeProcess = Start-Process $python.Source -ArgumentList @('tests\fake_gemini.py', "$FakePort", '0.02') -PassThru -WindowStyle Hidden
    $cloudFake = @("GEMINI_BASE_URL: http://host.docker.internal:$FakePort", 'GEMINI_API_KEY: fake-key',
                   "GROQ_BASE_URL: http://host.docker.internal:$FakePort", 'GROQ_API_KEY: fake-key')
    if (-not (Restart-Api ($cloudFake + @('OLLAMA_CHAT_MODEL: not-pulled-model:14b')))) { throw 'api did not become healthy' }
    Test-Check 'main local model missing: the 3B backup answers (fallback local -> local_small)' {
        $run = Invoke-Run $QDays 'local' $true
        $l = $run.Last
        if ($l.model -eq "local_small:$($ids['local_small'])" -and $l.fallback.from -eq 'local' -and $l.fallback.code -eq 'model_not_found') { $true }
        else { "model=$($l.model), fallback=$($l.fallback | ConvertTo-Json -Compress)" }
    }
    Test-Check 'cloud fails and main local missing: chain gemini x2 -> local -> local_small answers' {
        $run = Invoke-Run '"Within how many working days are approved refunds paid? FAULT_500"' 'gemini'
        $tries = @($run.Agent.generation.attempts | ForEach-Object { "$($_.model):$($_.code)" }) -join ','
        if ($run.Last.model -like 'local_small:*' -and $tries -eq 'gemini:server_error,gemini:server_error,local:model_not_found') { $true }
        else { "model=$($run.Last.model), attempts=$tries" }
    }

    # ================= Part C: server-wide LOCAL_ONLY, every local model failing =================
    Write-Host "`n== Server-wide LOCAL_ONLY: restarting the api with both local models missing..."
    if (-not (Restart-Api ($cloudFake + @('OLLAMA_CHAT_MODEL: not-pulled-model:14b', 'OLLAMA_BACKUP_MODEL: not-pulled-model:3b', 'LOCAL_ONLY: "true"')))) {
        throw 'api did not become healthy'
    }
    $before = (Invoke-Api 'GET' "$FakeUrl/_requests").Json.all
    if ($null -eq $before) { $before = 0 }
    Test-Check 'GET /models reports local-only for the whole server' {
        if ((Invoke-Api 'GET' "$Api/models").Json.local_only -eq $true) { $true } else { 'local_only is not true' }
    }
    Test-Check 'with LOCAL_ONLY on, a cloud model is refused even without the request flag (400)' {
        $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-Body $QDays 'gemini')
        if ($r.Status -eq 400) { $true } else { "HTTP $($r.Status)" }
    }
    Test-Check 'every local model failing ends in an error naming only local models; no cloud fallback' {
        $run = Invoke-Run $QDays 'local'
        $tries = @($run.Last.attempts | ForEach-Object { $_.model } | Sort-Object -Unique) -join ','
        if ($run.Last.type -eq 'error' -and $run.Last.code -eq 'all_providers_failed' -and $tries -eq 'local,local_small') { $true }
        else { "type=$($run.Last.type), code=$($run.Last.code), models tried=$tries" }
    }
    Test-Check 'the fake cloud APIs received zero requests while local-only was on' {
        $after = (Invoke-Api 'GET' "$FakeUrl/_requests").Json.all
        if ($null -eq $after) { $after = 0 }
        if ($after -eq $before) { $true } else { "cloud APIs got $($after - $before) request(s)" }
    }
}
catch [System.OperationCanceledException] { }
catch {
    Write-Result 'FAIL' 'restart parts could not run' $_.Exception.Message
}
finally {
    if (-not $SkipRestarts) {
        if ($fakeProcess) { Stop-Process -Id $fakeProcess.Id -Force -ErrorAction SilentlyContinue }
        Write-Host "`nRestoring the normal api..."
        if (Restart-Api $null) { Write-Host 'api restored.' } else { Write-Host 'api did not become healthy; run: docker compose up -d api' -ForegroundColor Red }
    }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by quota (of {4})" -f $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total)
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Models and privacy verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Models and privacy NOT fully verified.' -ForegroundColor Red; exit 1
