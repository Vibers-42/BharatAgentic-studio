<#
Checks Phase 3 (PDF RAG) on the running Docker stack and prints PASS / FAIL / SKIP per check.
Works in Windows PowerShell 5.1 and PowerShell 7. Run verify_phase1.ps1 and verify_phase2.ps1 too.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase3.ps1
      Uses the api as it is. Checks that need Gemini run only if GEMINI_API_KEY is set in .env.
  powershell -ExecutionPolicy Bypass -File scripts\verify_phase3.ps1 -UseFakeGemini
      Starts tests\fake_gemini.py on this PC (needs Python), points the api at it for the run, then
      restores the normal api. Proves citations and streaming without a key or quota. Checks that
      need real Gemini wording (answer content, Telugu/Hindi text) are SKIP in this mode.

Test data: samples\refund-policy.pdf and samples\hostel-rules.pdf (fictional, made for this project),
stored under source ids p3_refund and p3_hostel, removed again at the end.
Exit code is 0 only if every check passes.
#>
param([switch]$UseFakeGemini)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0
$script:Fail = 0
$script:Skip = 0
$Api = 'http://127.0.0.1:8000'
$Web = 'http://127.0.0.1:3000/api'
$FakePort = 8765

function Write-Result([string]$Result, [string]$Name, [string]$Detail = '') {
    $colors = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow' }
    $line = '{0,-5} {1}' -f $Result, $Name
    if ($Detail) { $line += "  ($Detail)" }
    Write-Host $line -ForegroundColor $colors[$Result]
    switch ($Result) { 'PASS' { $script:Pass++ } 'FAIL' { $script:Fail++ } 'SKIP' { $script:Skip++ } }
}

# -Model: needs a model (real or fake). -RealModel: needs real Gemini wording. Otherwise model-free.
function Test-Check([string]$Name, [scriptblock]$Test, [switch]$Model, [switch]$RealModel) {
    if (-not $script:ApiUp) { Write-Result 'SKIP' $Name 'api is not reachable'; return }
    if (($Model -or $RealModel) -and $script:Mode -eq 'none') {
        Write-Result 'SKIP' $Name 'GEMINI_API_KEY not set (or run with -UseFakeGemini)'; return
    }
    if ($RealModel -and $script:Mode -eq 'fake') { Write-Result 'SKIP' $Name 'needs real Gemini'; return }
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
$script:Http.Timeout = [TimeSpan]::FromSeconds(120)

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

# Reads an SSE response line by line as it arrives and stamps each event with arrived_ms (ms since send).
function Invoke-Stream([string]$Url, [string]$Body) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $Url)
    $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
    $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    $clock = [System.Diagnostics.Stopwatch]::StartNew()
    $res = $script:Http.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    if ([int]$res.StatusCode -ne 200) { throw "HTTP $([int]$res.StatusCode)" }
    $reader = [System.IO.StreamReader]::new($res.Content.ReadAsStreamAsync().GetAwaiter().GetResult(), [System.Text.Encoding]::UTF8)
    $events = @()
    while ($null -ne ($line = $reader.ReadLine())) {
        if ($line.StartsWith('data: ')) {
            $ev = $line.Substring(6) | ConvertFrom-Json
            $ev | Add-Member -NotePropertyName arrived_ms -NotePropertyValue $clock.ElapsedMilliseconds
            $events += $ev
        }
    }
    $reader.Dispose()
    , $events
}

# A JSON string literal built from code points, so this file stays ASCII (PowerShell 5.1 safe).
function Get-JsonString([int[]]$CodePoints) {
    $backslash = [char]92
    '"' + (($CodePoints | ForEach-Object { if ($_ -lt 128) { [string][char]$_ } else { "${backslash}u{0:x4}" -f $_ } }) -join '') + '"'
}

function New-Graph([string[]]$SourceIds, [string]$Language = 'en') {
    $nodes = @(
        @{ id = 'n1'; type = 'input'; config = @{} },
        @{ id = 'n2'; type = 'guardrails'; config = @{ block = @('empty', 'abuse', 'off_topic') } },
        @{ id = 'n3'; type = 'router'; config = @{} },
        @{ id = 'n4'; type = 'agent'; config = @{ model = 'gemini'; language = $Language } },
        @{ id = 'n5'; type = 'output'; config = @{} }
    )
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    foreach ($id in $SourceIds) {
        $nodes += @{ id = $id; type = 'source_doc'; config = @{ file = "$id.pdf" } }
        $edges += @{ from = $id; to = 'n4' }
    }
    @{ nodes = $nodes; edges = $edges }
}

function New-RunBody($Graph, [string]$QuestionJson, [string]$Language) {
    $g = $Graph | ConvertTo-Json -Depth 10 -Compress
    $lang = if ($Language) { ",`"language`":`"$Language`"" } else { '' }
    "{`"question`":$QuestionJson$lang,`"graph`":$g}"
}

function Invoke-Run($Graph, [string]$QuestionJson, [string]$Language) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (New-RunBody $Graph $QuestionJson $Language)
    if ($r.Status -ne 200) { throw "HTTP $($r.Status): $($r.Text)" }
    $events = @($r.Json.events)
    $last = $events[-1]
    if ($last.type -eq 'error') { throw "pipeline error in $($last.stage): $($last.message)" }
    $agent = @($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0]
    [pscustomobject]@{ Result = $last; Agent = $agent.detail; Events = $events }
}

function Wait-ApiHealthy {
    for ($i = 0; $i -lt 60; $i++) {
        $id = (docker compose ps -q api 2>$null | Out-String).Trim()
        if ($id -and ((docker inspect -f '{{.State.Health.Status}}' $id 2>$null | Out-String).Trim() -eq 'healthy')) { return $true }
        Start-Sleep -Seconds 2
    }
    $false
}

# Questions. Telugu: "if I cancel a lab test, how much refund?"  Hindi: "in how many days is the refund given?"
$QLab = '"How much is refunded if a lab test is cancelled before the sample is collected?"'
$QHostel = '"What time does the hostel gate close?"'
$QUnrelated = '"Who won the cricket world cup in 2011?"'
$QTelugu = Get-JsonString @(0x0C32, 0x0C4D, 0x0C2F, 0x0C3E, 0x0C2C, 0x0C4D, 32, 0x0C1F, 0x0C46, 0x0C38, 0x0C4D, 0x0C1F, 0x0C4D, 32,
    0x0C30, 0x0C26, 0x0C4D, 0x0C26, 0x0C41, 32, 0x0C1A, 0x0C47, 0x0C38, 0x0C4D, 0x0C24, 0x0C47, 32, 0x0C0E, 0x0C02, 0x0C24, 32,
    0x0C35, 0x0C3E, 0x0C2A, 0x0C38, 0x0C41, 32, 0x0C35, 0x0C38, 0x0C4D, 0x0C24, 0x0C41, 0x0C02, 0x0C26, 0x0C3F, 63)
$QHindi = Get-JsonString @(0x0930, 0x093F, 0x092B, 0x0902, 0x0921, 32, 0x0915, 0x093F, 0x0924, 0x0928, 0x0947, 32,
    0x0926, 0x093F, 0x0928, 0x094B, 0x0902, 32, 0x092E, 0x0947, 0x0902, 32, 0x092E, 0x093F, 0x0932, 0x0924, 0x093E, 32, 0x0939, 0x0948, 63)
$TeluguLetters = '[{0}-{1}]' -f [char]0x0C00, [char]0x0C7F
$HindiLetters = '[{0}-{1}]' -f [char]0x0900, [char]0x097F

# ---------- Preflight and mode ----------
$script:ApiUp = $false
$fakeProcess = $null
$envKey = (Get-Content '.env' -ErrorAction SilentlyContinue | Where-Object { $_ -match '^GEMINI_API_KEY=.+' }).Count -gt 0

try {
    if ($UseFakeGemini) {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) { Write-Host 'Python is needed for -UseFakeGemini.' -ForegroundColor Red; exit 1 }
        $fakeProcess = Start-Process $python.Source -ArgumentList @('tests\fake_gemini.py', "$FakePort") -PassThru -WindowStyle Hidden
        $override = Join-Path $env:TEMP 'bharatagent-fake-gemini.override.yml'
        @(
            'services:',
            '  api:',
            '    environment:',
            "      GEMINI_BASE_URL: http://host.docker.internal:$FakePort",
            '      GEMINI_API_KEY: fake-key-for-local-streaming-test'
        ) | Set-Content -Path $override -Encoding ASCII
        Write-Host "Starting fake Gemini on :$FakePort and restarting api against it..."
        docker compose -f docker-compose.yml -f $override up -d api 2>$null | Out-Null
        if (-not (Wait-ApiHealthy)) { Write-Host 'api did not become healthy.' -ForegroundColor Red }
    }

    try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
    $status = $null
    try { $status = (Invoke-Api 'GET' "$Api/status").Json } catch { }
    $script:Mode = if ($UseFakeGemini) { 'fake' } elseif ($status.keys.gemini -eq $true) { 'real' } else { 'none' }
    Write-Host "Mode: $($script:Mode) Gemini   (.env key present: $envKey)"
    if (-not $script:ApiUp) { Write-Host 'The api is not reachable on :8000. Run: docker compose up -d --build' -ForegroundColor Red }

    # ---------- Ingestion ----------
    Write-Host "`n== Ingestion (PDF -> text -> chunks -> bge-m3 -> pgvector)"
    $script:RefundChunks = 0
    Test-Check 'upload refund-policy.pdf: 2 pages, chunks stored' {
        $r = Send-File 'p3_refund' 'samples\refund-policy.pdf' 'refund-policy.pdf'
        if ($r.Status -ne 201) { return "HTTP $($r.Status): $($r.Text)" }
        $script:RefundChunks = $r.Json.chunks
        if ($r.Json.pages -eq 2 -and $r.Json.chunks -ge 2) { $true } else { $r.Text }
    }
    Test-Check 'upload hostel-rules.pdf as a second source' {
        $r = Send-File 'p3_hostel' 'samples\hostel-rules.pdf' 'hostel-rules.pdf'
        if ($r.Status -eq 201 -and $r.Json.chunks -ge 1) { $true } else { "HTTP $($r.Status): $($r.Text)" }
    }
    Test-Check 'GET /sources/p3_refund reports the stored chunks' {
        $r = Invoke-Api 'GET' "$Api/sources/p3_refund"
        if ($r.Json.chunks -eq $script:RefundChunks -and $r.Json.file -eq 'refund-policy.pdf') { $true } else { $r.Text }
    }
    Test-Check 'chunks are tagged with the source id and keep page numbers (db)' {
        $envValues = @{}
        foreach ($line in Get-Content '.env') { if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $envValues[$Matches[1]] = $Matches[2].Trim() } }
        $u = if ($envValues['POSTGRES_USER']) { $envValues['POSTGRES_USER'] } else { 'bharat' }
        $d = if ($envValues['POSTGRES_DB']) { $envValues['POSTGRES_DB'] } else { 'bharatagent' }
        $sql = "SELECT count(DISTINCT page) || ',' || count(*) || ',' || count(*) FILTER (WHERE vector_dims(embedding) = 1024) FROM chunks WHERE source_id = 'p3_refund';"
        $out = ($sql | docker compose exec -T db psql -U $u -d $d -tA 2>$null | Out-String).Trim()
        if ($out -eq "2,$($script:RefundChunks),$($script:RefundChunks)") { $true } else { "pages,chunks,1024-dim = '$out'" }
    }
    Test-Check 'a file that is not a PDF is rejected (400)' {
        $tmp = Join-Path $env:TEMP 'bharatagent-not-a-pdf.pdf'
        [System.IO.File]::WriteAllText($tmp, 'hello, I am not a PDF')
        $r = Send-File 'p3_refund' $tmp 'notes.pdf'
        Remove-Item $tmp -ErrorAction SilentlyContinue
        if ($r.Status -eq 400) { $true } else { "HTTP $($r.Status)" }
    }
    Test-Check 'a PDF with no text (scanned) is rejected with a clear message (422)' {
        $r = Send-File 'p3_scan' 'samples\blank-scan.pdf' 'blank-scan.pdf'
        if ($r.Status -eq 422 -and $r.Text -match 'no extractable text') { $true } else { "HTTP $($r.Status): $($r.Text)" }
    }
    Test-Check 'a path in the file name cannot escape the upload folder' {
        $r = Send-File 'p3_esc' 'samples\hostel-rules.pdf' '..\..\evil.pdf'
        if ($r.Status -eq 201 -and $r.Json.file -eq 'evil.pdf') { $true } else { "HTTP $($r.Status), file=$($r.Json.file)" }
    }

    # ---------- Graph connections decide what the agent can see ----------
    Write-Host "`n== Only connected sources are used"
    Test-Check 'hostel question with only the refund PDF connected: "I don''t know", no model call' {
        $run = Invoke-Run (New-Graph @('p3_refund')) $QHostel 'en'
        $searched = @($run.Agent.retrieval.searched) -join ','
        $fromOther = @($run.Agent.retrieval.candidates | Where-Object { $_.source_id -ne 'p3_refund' }).Count
        if ($run.Result.supported -eq $false -and @($run.Result.citations).Count -eq 0 -and $run.Agent.model_called -eq $false -and
            $searched -eq 'p3_refund' -and $fromOther -eq 0) { $true }
        else { "supported=$($run.Result.supported), model_called=$($run.Agent.model_called), searched=$searched, other=$fromOther" }
    }
    Test-Check 'refund question with only the hostel PDF connected never cites the refund PDF' {
        $run = Invoke-Run (New-Graph @('p3_hostel')) $QLab 'en'
        $files = @($run.Result.citations | ForEach-Object { $_.file }) + @($run.Agent.retrieval.candidates | ForEach-Object { $_.file })
        if ($files -contains 'refund-policy.pdf') { return 'refund-policy.pdf reached the answer' }
        if ($run.Result.supported -eq $true) { return 'answered as supported from the hostel PDF' }
        $true
    }
    Test-Check 'unrelated question: "I don''t know" without calling the model' {
        $run = Invoke-Run (New-Graph @('p3_refund', 'p3_hostel')) $QUnrelated 'en'
        if ($run.Result.supported -eq $false -and $run.Agent.model_called -eq $false -and @($run.Result.citations).Count -eq 0) { $true }
        else { "supported=$($run.Result.supported), model_called=$($run.Agent.model_called)" }
    }
    Test-Check 'both PDFs connected: both are searched' {
        $run = Invoke-Run (New-Graph @('p3_refund', 'p3_hostel')) $QUnrelated 'en'
        if ((@($run.Agent.retrieval.searched) -join ',') -eq 'p3_refund,p3_hostel') { $true } else { "searched=$(@($run.Agent.retrieval.searched) -join ',')" }
    }
    Test-Check 'a source in the graph without a line to the agent is refused (422)' {
        $g = New-Graph @('p3_refund')
        $g.edges = @($g.edges | Where-Object { $_.from -ne 'p3_refund' })
        $r = Invoke-Api 'POST' "$Api/pipeline/run" (New-RunBody $g $QLab 'en')
        $codes = @($r.Json.detail.errors | ForEach-Object { $_.code })
        if ($r.Status -eq 422 -and $codes -contains 'not_connected') { $true } else { "HTTP $($r.Status)" }
    }

    # ---------- Answers from the model ----------
    Write-Host "`n== Cited answers"
    Test-Check 'cited answer from the refund PDF (file, page, chunk exist)' {
        $run = Invoke-Run (New-Graph @('p3_refund')) $QLab 'en'
        $c = @($run.Result.citations)
        if ($run.Result.supported -ne $true -or $c.Count -eq 0) { return "supported=$($run.Result.supported): $($run.Result.answer)" }
        $bad = @($c | Where-Object { $_.file -ne 'refund-policy.pdf' -or $_.page -lt 1 -or $_.page -gt 2 -or $_.chunk -ge $script:RefundChunks })
        if ($bad.Count -eq 0 -and $run.Result.answer -match '\[\d+\]') { $true } else { "bad citations: $($bad | ConvertTo-Json -Compress)" }
    } -Model
    Test-Check 'cited answer states the right fact (80 percent)' {
        $run = Invoke-Run (New-Graph @('p3_refund')) $QLab 'en'
        if ($run.Result.answer -match '80') { $true } else { $run.Result.answer }
    } -RealModel
    Test-Check 'answer shows the model and real token counts' {
        $run = Invoke-Run (New-Graph @('p3_refund')) $QLab 'en'
        if ($run.Result.model -like 'gemini:*' -and $run.Result.usage.input_tokens -gt 0 -and $run.Result.usage.output_tokens -gt 0) { $true }
        else { "model=$($run.Result.model), usage=$($run.Result.usage | ConvertTo-Json -Compress)" }
    } -Model
    Test-Check 'no sources connected: direct reply, marked not from sources, no citations' {
        $run = Invoke-Run (New-Graph @()) '"Namaste!"' 'en'
        if ($run.Result.grounded -eq $false -and @($run.Result.citations).Count -eq 0 -and $run.Agent.mode -eq 'direct') { $true }
        else { "grounded=$($run.Result.grounded), mode=$($run.Agent.mode)" }
    } -Model
    Test-Check 'Telugu question finds the English PDF and is cited' {
        $run = Invoke-Run (New-Graph @('p3_refund') 'en') $QTelugu
        $files = @($run.Result.citations | ForEach-Object { $_.file })
        if ($run.Result.language -eq 'te' -and $run.Result.supported -eq $true -and $files -contains 'refund-policy.pdf') { $true }
        else { "language=$($run.Result.language), supported=$($run.Result.supported)" }
    } -Model
    Test-Check 'Telugu answer is written in Telugu' {
        $run = Invoke-Run (New-Graph @('p3_refund') 'en') $QTelugu
        if ($run.Result.answer -match $TeluguLetters -and $run.Result.supported -eq $true) { $true } else { $run.Result.answer }
    } -RealModel
    Test-Check 'Hindi question is answered in Hindi with a citation (5 working days)' {
        $run = Invoke-Run (New-Graph @('p3_refund') 'en') $QHindi
        if ($run.Result.language -eq 'hi' -and $run.Result.answer -match $HindiLetters -and $run.Result.answer -match '5' -and
            @($run.Result.citations).Count -gt 0) { $true }
        else { $run.Result.answer }
    } -RealModel

    # ---------- Streaming ----------
    Write-Host "`n== Live streaming through nginx while the model writes"
    foreach ($target in @(@{ Name = 'nginx :3000'; Url = "$Web/pipeline/run" }, @{ Name = 'api :8000'; Url = "$Api/pipeline/run" })) {
        Test-Check "token events arrive over time via $($target.Name), before the result" {
            $events = Invoke-Stream $target.Url (New-RunBody (New-Graph @('p3_refund')) $QLab 'en')
            $tokens = @($events | Where-Object { $_.type -eq 'token' })
            $result = @($events | Where-Object { $_.type -eq 'result' })[0]
            $agentRun = @($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'running' })[0]
            if ($tokens.Count -lt 2) { return "only $($tokens.Count) token events" }
            if ($null -eq $result) { return 'no result event' }
            $spread = $tokens[-1].arrived_ms - $tokens[0].arrived_ms
            $lead = $result.arrived_ms - $tokens[0].arrived_ms
            if ($agentRun.arrived_ms -gt $tokens[0].arrived_ms) { return 'agent running arrived after the first token' }
            if ($spread -lt 100 -or $lead -lt 100) { return "events arrived together (token spread $spread ms, first token $lead ms before result): buffered?" }
            $true
        } -Model
    }

    # ---------- Web ----------
    Write-Host "`n== Chat page"
    Test-Check 'web serves the chat page bundle (upload, stages, citations)' {
        $html = (Invoke-Api 'GET' 'http://127.0.0.1:3000/').Text
        if ($html -notmatch 'src="(/assets/[^"]+\.js)"') { return 'no script tag' }
        $js = (Invoke-Api 'GET' "http://127.0.0.1:3000$($Matches[1])").Text
        $missing = @('chat_pdf', 'Upload a PDF', 'Connected to agent', 'pipeline/run') | Where-Object { $js -notmatch [regex]::Escape($_) }
        if ($missing.Count -eq 0) { $true } else { "missing: $($missing -join ', ')" }
    }
}
finally {
    foreach ($id in @('p3_refund', 'p3_hostel', 'p3_esc', 'p3_scan')) { try { Invoke-Api 'DELETE' "$Api/sources/$id" | Out-Null } catch { } }
    if ($UseFakeGemini) {
        Write-Host "`nRestoring the normal api (without the fake Gemini)..."
        if ($fakeProcess) { Stop-Process -Id $fakeProcess.Id -Force -ErrorAction SilentlyContinue }
        docker compose up -d api 2>$null | Out-Null
        if (Wait-ApiHealthy) { Write-Host 'api restored.' } else { Write-Host 'api did not become healthy; run: docker compose up -d api' -ForegroundColor Red }
    }
}

# ---------- Summary ----------
$total = $script:Pass + $script:Fail + $script:Skip
Write-Host ''
Write-Host "Result: $($script:Pass) passed, $($script:Fail) failed, $($script:Skip) skipped (of $total), mode: $($script:Mode) Gemini"
if ($script:Fail -eq 0 -and $script:Skip -eq 0) { Write-Host 'Phase 3 RAG verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 3 RAG NOT fully verified.' -ForegroundColor Red; exit 1
