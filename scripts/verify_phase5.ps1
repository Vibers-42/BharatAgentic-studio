<#
Checks Phase 5 (more sources, per-graph isolation, claim checks, languages) on the running Docker stack.
Prints PASS / FAIL / SKIP / BLOCKED per check. Works in Windows PowerShell 5.1 and PowerShell 7.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase5.ps1

Part A uses the api as it is. It downloads two public test sources made for this purpose: GitHub's demo
repository octocat/Spoon-Knife and the IANA example page https://example.com. Answers use -Model
(default groq; pass -Model gemini or -Model local) - one request per answer check.
Part B restarts the api with the cloud models pointed at tests\fake_gemini.py (needs Python) to prove the
claim check removes a wrong number and an uncited sentence, then restores the normal api.
Add -SkipFakeModel to run Part A only. Exit code is 0 only if every check passes.
#>
param([string]$Model = 'groq', [switch]$SkipFakeModel)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0; $script:Fail = 0; $script:Skip = 0; $script:Blocked = 0
$Api = 'http://127.0.0.1:8000'
$FakePort = 8765
$Repo = 'https://github.com/octocat/Spoon-Knife'
$Page = 'https://example.com'

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

function Send-File([string]$Url, [string]$Path, [string]$FileName) {
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $file = [System.Net.Http.ByteArrayContent]::new([System.IO.File]::ReadAllBytes((Resolve-Path $Path)))
    $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/pdf')
    $form.Add($file, 'file', $FileName)
    New-Result ($script:Http.PostAsync($Url, $form).GetAwaiter().GetResult())
}

# A JSON string literal built from code points, so this file stays ASCII (PowerShell 5.1 safe).
function Get-JsonString([int[]]$CodePoints) {
    $backslash = [char]92
    '"' + (($CodePoints | ForEach-Object { if ($_ -lt 128) { [string][char]$_ } else { "${backslash}u{0:x4}" -f $_ } }) -join '') + '"'
}

function New-Graph([object[]]$Sources) {
    $nodes = @(
        @{ id = 'n1'; type = 'input'; config = @{} },
        @{ id = 'n2'; type = 'guardrails'; config = @{ block = @('empty', 'abuse', 'off_topic') } },
        @{ id = 'n3'; type = 'router'; config = @{} },
        @{ id = 'n4'; type = 'agent'; config = @{ model = $Model; language = 'en' } },
        @{ id = 'n5'; type = 'output'; config = @{} }
    )
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    foreach ($s in $Sources) { $nodes += $s; $edges += @{ from = $s.id; to = 'n4' } }
    @{ nodes = $nodes; edges = $edges }
}

# Runs a question; QuestionJson is a JSON string literal. Returns the final event and agent detail.
function Invoke-Run([string]$QuestionJson, $Graph, [int]$GraphId = 0) {
    $target = if ($GraphId) { "`"graph_id`":$GraphId" } else { "`"graph`":$($Graph | ConvertTo-Json -Depth 10 -Compress)" }
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" "{`"question`":$QuestionJson,`"model`":`"$Model`",$target}"
    if ($r.Status -ne 200) { throw "HTTP $($r.Status): $($r.Text)" }
    $events = @($r.Json.events)
    $last = $events[-1]
    if ($last.type -eq 'error') {
        if (@($last.attempts | Where-Object { "$($_.code)".StartsWith('quota_') }).Count) { throw [QuotaBlocked]::new('quota', $last.message) }
        throw "pipeline error: $($last.code) $($last.message)"
    }
    if ($last.fallback -and "$($last.fallback.code)".StartsWith('quota_')) { throw [QuotaBlocked]::new($last.fallback.code, "$Model refused; the local model answered instead") }
    $agent = @($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' })[0]
    [pscustomobject]@{ Last = $last; Agent = $agent.detail }
}

function Test-Answered($Run, [string]$File, [string]$Lang) {
    $l = $Run.Last
    if ($l.model -notlike "${Model}:*") { return "answered by $($l.model) (fallback $($l.fallback.code))" }
    if ($l.supported -ne $true) { return "not supported: $($l.answer)" }
    $files = @($l.citations | ForEach-Object { $_.file })
    if ($files -notcontains $File) { return "cited $($files -join ', ') instead of $File" }
    $bad = @($l.claims | Where-Object { $_.verdict -ne 'supported' })
    if ($l.language -ne $Lang) { return "language $($l.language), expected $Lang" }
    Write-Host "        $($l.model) [$Lang]: claims $(@($l.claims).Count) checked, $($bad.Count) removed" -ForegroundColor DarkGray
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

$TeluguLetters = '[{0}-{1}]' -f [char]0x0C00, [char]0x0C7F
$HindiLetters = '[{0}-{1}]' -f [char]0x0900, [char]0x097F
# Telugu: "what is the example.com domain used for?"
$QTeluguPage = Get-JsonString (@(0x0C08, 0x0C21, 0x0C4A, 0x0C2E, 0x0C48, 0x0C28, 0x0C4D, 32) + [int[]][char[]]'example.com ' +
    @(0x0C26, 0x0C47, 0x0C28, 0x0C3F, 0x0C15, 0x0C3F, 32, 0x0C09, 0x0C2A, 0x0C2F, 0x0C4B, 0x0C17, 0x0C3F, 0x0C38, 0x0C4D, 0x0C24, 0x0C3E, 0x0C30, 0x0C41, 63))
# Hindi: "in how many days is the refund given?"
$QHindiRefund = Get-JsonString @(0x0930, 0x093F, 0x092B, 0x0902, 0x0921, 32, 0x0915, 0x093F, 0x0924, 0x0928, 0x0947, 32,
    0x0926, 0x093F, 0x0928, 0x094B, 0x0902, 32, 0x092E, 0x0947, 0x0902, 32, 0x092E, 0x093F, 0x0932, 0x0924, 0x093E, 32, 0x0939, 0x0948, 63)

# ---------- Preflight ----------
$script:ApiUp = $false
try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { }
if (-not $script:ApiUp) { Write-Host 'The api is not reachable on :8000. Run: docker compose up -d --build' -ForegroundColor Red }
$ready = $false
try { $ready = (@((Invoke-Api 'GET' "$Api/models").Json.models | Where-Object { $_.key -eq $Model })[0]).ready } catch { }
Write-Host "Answer model: $Model (ready: $ready)"

$fakeProcess = $null
try {
    # ================= Part A =================
    Write-Host "`n== GitHub repo connector ($Repo)"
    $script:RepoInfo = $null
    Test-Check 'repo is downloaded and its text files are chunked per file' {
        $r = Invoke-Api 'POST' "$Api/sources/p5_repo/repo" "{`"url`":`"$Repo`"}"
        if ($r.Status -ne 201) { return "HTTP $($r.Status): $($r.Text)" }
        $script:RepoInfo = $r.Json
        if ($r.Json.kind -eq 'repo' -and $r.Json.files -ge 2 -and $r.Json.chunks -ge 2 -and $r.Json.repo -match '^octocat/Spoon-Knife@[0-9a-f]+$') { $true } else { $r.Text }
    }
    Test-Check 'repo chunks link to the exact file and commit on GitHub (db)' {
        $envValues = @{}
        foreach ($line in Get-Content '.env') { if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $envValues[$Matches[1]] = $Matches[2].Trim() } }
        $u = if ($envValues['POSTGRES_USER']) { $envValues['POSTGRES_USER'] } else { 'bharat' }
        $d = if ($envValues['POSTGRES_DB']) { $envValues['POSTGRES_DB'] } else { 'bharatagent' }
        $sql = "SELECT count(*) || ',' || count(*) FILTER (WHERE origin LIKE 'https://github.com/octocat/Spoon-Knife/blob/%' AND content LIKE 'File: %') FROM chunks WHERE scope = 'inline' AND source_id = 'p5_repo';"
        $out = ($sql | docker compose exec -T db psql -U $u -d $d -tA 2>$null | Out-String).Trim()
        $parts = $out -split ','
        if ($parts.Count -eq 2 -and [int]$parts[0] -gt 0 -and $parts[0] -eq $parts[1]) { $true } else { "chunks,linked = '$out'" }
    }
    Test-Check 'a link that is not a GitHub repo is refused with a clear message (422)' {
        $r = Invoke-Api 'POST' "$Api/sources/p5_repo_bad/repo" '{"url":"https://example.com/not-a-repo"}'
        if ($r.Status -eq 422 -and $r.Json.detail -match 'GitHub repository link') { $true } else { "HTTP $($r.Status): $($r.Text)" }
    }

    Write-Host "`n== Docs URL connector ($Page)"
    Test-Check 'web page is fetched, its text extracted and chunked, with title and link' {
        $r = Invoke-Api 'POST' "$Api/sources/p5_url/url" "{`"url`":`"$Page`"}"
        if ($r.Status -ne 201) { return "HTTP $($r.Status): $($r.Text)" }
        if ($r.Json.kind -eq 'url' -and $r.Json.chunks -ge 1 -and $r.Json.file -eq 'Example Domain' -and $r.Json.url -like 'https://example.com*') { $true } else { $r.Text }
    }
    foreach ($bad in @('http://ollama:11434/api/tags', 'http://db:5432/', 'http://127.0.0.1:8000/health',
                       'http://169.254.169.254/latest/meta-data', 'http://host.docker.internal:3000/', 'file:///etc/passwd')) {
        Test-Check "internal or non-web link is refused: $bad" {
            $r = Invoke-Api 'POST' "$Api/sources/p5_bad/url" "{`"url`":`"$bad`"}"
            if ($r.Status -eq 400 -and $r.Json.detail -match 'private or internal|Only http') { $true } else { "HTTP $($r.Status): $($r.Text)" }
        }
    }

    Write-Host "`n== Saved graphs keep their chunks apart (same source id 'shared_s1')"
    $script:GraphA = 0; $script:GraphB = 0
    Test-Check 'two saved graphs with the same source id hold different PDFs' {
        foreach ($name in @('A', 'B')) {
            $g = New-Graph @(@{ id = 'shared_s1'; type = 'source_doc'; config = @{ file = "$name.pdf" } })
            $r = Invoke-Api 'POST' "$Api/graphs" "{`"name`":`"p5 graph $name`",`"graph`":$($g | ConvertTo-Json -Depth 10 -Compress)}"
            if ($r.Status -ne 201) { return "save $name : HTTP $($r.Status)" }
            Set-Variable -Scope Script -Name "Graph$name" -Value $r.Json.id
        }
        $a = Send-File "$Api/sources/shared_s1/pdf?graph_id=$($script:GraphA)" 'samples\refund-policy.pdf' 'refund-policy.pdf'
        $b = Send-File "$Api/sources/shared_s1/pdf?graph_id=$($script:GraphB)" 'samples\hostel-rules.pdf' 'hostel-rules.pdf'
        if ($a.Status -eq 201 -and $b.Status -eq 201 -and $a.Json.scope -eq "graph:$($script:GraphA)" -and $b.Json.scope -eq "graph:$($script:GraphB)") { $true }
        else { "A: HTTP $($a.Status) $($a.Json.scope); B: HTTP $($b.Status) $($b.Json.scope)" }
    }
    Test-Check 'each graph searches only its own chunks; the inline scope sees neither' {
        $ra = (Invoke-Run '"What time does the hostel gate close?"' $null $script:GraphA).Agent.retrieval
        $rb = (Invoke-Run '"What time does the hostel gate close?"' $null $script:GraphB).Agent.retrieval
        $fa = @($ra.candidates | ForEach-Object { $_.file } | Sort-Object -Unique) -join ','
        $fb = @($rb.candidates | ForEach-Object { $_.file } | Sort-Object -Unique) -join ','
        $inline = (Invoke-Api 'GET' "$Api/sources/shared_s1").Json.chunks
        if ($fa -eq 'refund-policy.pdf' -and $fb -eq 'hostel-rules.pdf' -and $ra.scope -eq "graph:$($script:GraphA)" -and $inline -eq 0) { $true }
        else { "A searched '$fa' ($($ra.scope)), B searched '$fb', inline chunks $inline" }
    }
    Test-Check 'graph A answers "I don''t know" about the hostel; graph B answers it' {
        $a = Invoke-Run '"What time does the hostel gate close?"' $null $script:GraphA
        $b = Invoke-Run '"What time does the hostel gate close?"' $null $script:GraphB
        if ($a.Last.supported -eq $false -and $b.Last.supported -eq $true -and $b.Last.answer -match '9:30') { $true }
        else { "A supported=$($a.Last.supported); B supported=$($b.Last.supported): $($b.Last.answer)" }
    }
    Test-Check 'upload to a node the saved graph does not have is refused (404)' {
        $r = Send-File "$Api/sources/not_there/pdf?graph_id=$($script:GraphA)" 'samples\refund-policy.pdf' 'x.pdf'
        if ($r.Status -eq 404) { $true } else { "HTTP $($r.Status)" }
    }
    Test-Check 'a saved graph ingests its link sources from node settings (POST /graphs/{id}/ingest)' {
        $g = New-Graph @(@{ id = 'docs'; type = 'source_doc'; config = @{ url = $Page } })
        $id = (Invoke-Api 'POST' "$Api/graphs" "{`"name`":`"p5 links`",`"graph`":$($g | ConvertTo-Json -Depth 10 -Compress)}").Json.id
        $out = (Invoke-Api 'POST' "$Api/graphs/$id/ingest").Json
        $docs = @($out.sources | Where-Object { $_.node -eq 'docs' })[0]
        if ($docs.ok -and $docs.scope -eq "graph:$id" -and $docs.chunks -ge 1) { $true } else { $out | ConvertTo-Json -Compress -Depth 5 }
    }

    Write-Host "`n== Cited answers from the new sources, claims checked ($Model)"
    if (-not $ready) { Write-Host "        $Model is not set up; these checks will be SKIP" -ForegroundColor Yellow }
    $repoGraph = New-Graph @(@{ id = 'p5_repo'; type = 'source_repo'; config = @{ url = $Repo } })
    $pageGraph = New-Graph @(@{ id = 'p5_url'; type = 'source_doc'; config = @{ url = $Page } })
    Test-Check 'repo question: cites README.md with a GitHub link; every kept sentence backed by it' {
        if (-not $ready) { return "SKIP:$Model is not set up" }
        $run = Invoke-Run '"What is the Spoon-Knife repository meant for?"' $repoGraph
        $ok = Test-Answered $run 'README.md' 'en'
        if ($ok -ne $true) { return $ok }
        if (@($run.Last.citations | Where-Object { $_.origin -like 'https://github.com/octocat/Spoon-Knife/blob/*' }).Count -lt 1) { return 'no GitHub link on the citation' }
        $true
    }
    Test-Check 'docs page question: cites the page with its link' {
        if (-not $ready) { return "SKIP:$Model is not set up" }
        $run = Invoke-Run '"What is the example.com domain for?"' $pageGraph
        $ok = Test-Answered $run 'Example Domain' 'en'
        if ($ok -ne $true) { return $ok }
        if (@($run.Last.citations | Where-Object { $_.origin -like 'https://example.com*' }).Count -lt 1) { return 'no link on the citation' }
        $true
    }
    Test-Check 'Telugu question about the English page: Telugu answer, cited, claims backed across languages' {
        if (-not $ready) { return "SKIP:$Model is not set up" }
        $run = Invoke-Run $QTeluguPage $pageGraph
        $ok = Test-Answered $run 'Example Domain' 'te'
        if ($ok -ne $true) { return $ok }
        if ($run.Last.answer -notmatch $TeluguLetters) { return "not written in Telugu: $($run.Last.answer)" }
        $true
    }
    Test-Check 'Hindi question to saved graph A: Hindi answer with the right number (5 days)' {
        if (-not $ready) { return "SKIP:$Model is not set up" }
        $run = Invoke-Run $QHindiRefund $null $script:GraphA
        $ok = Test-Answered $run 'refund-policy.pdf' 'hi'
        if ($ok -ne $true) { return $ok }
        if ($run.Last.answer -notmatch $HindiLetters -or $run.Last.answer -notmatch '5') { return "answer: $($run.Last.answer)" }
        $true
    }

    Test-Check 'local model, Telugu question: the answer says honestly whether it is in Telugu' {
        $saved = $Model; Set-Variable -Name Model -Value 'local' -Scope Script
        # Telugu: "what time is the hostel gate closed?"
        $QTeluguGate = Get-JsonString @(0x0C39, 0x0C3E, 0x0C38, 0x0C4D, 0x0C1F, 0x0C32, 0x0C4D, 32, 0x0C17, 0x0C47, 0x0C1F, 0x0C4D, 32, 0x0C0E, 0x0C28, 0x0C4D, 0x0C28, 0x0C3F, 32, 0x0C17, 0x0C02, 0x0C1F, 0x0C32, 0x0C15, 0x0C41, 32, 0x0C2E, 0x0C42, 0x0C38, 0x0C3F, 0x0C35, 0x0C47, 0x0C38, 0x0C4D, 0x0C24, 0x0C3E, 0x0C30, 0x0C41, 63)
        try { $run = Invoke-Run $QTeluguGate $null $script:GraphB }
        finally { Set-Variable -Name Model -Value $saved -Scope Script }
        $l = $run.Last
        if ($l.model -notlike 'local:*') { return "answered by $($l.model)" }
        $isTelugu = $l.answer -match $TeluguLetters
        $warned = @($l.warnings | Where-Object { $_ -match 'not written in Telugu' }).Count -gt 0
        Write-Host "        local said: $($l.answer)  (language_match=$($l.language_match))" -ForegroundColor DarkGray
        if ($l.language -eq 'te' -and $l.language_match -eq $isTelugu -and $warned -eq (-not $isTelugu)) { $true }
        else { "language_match=$($l.language_match), Telugu text=$isTelugu, warned=$warned" }
    }

    Write-Host "`n== Language evaluation"
    Test-Check 'eval set: 10-15 questions per language, each with an expected source' {
        $spec = Get-Content 'eval\questions.json' -Raw -Encoding UTF8 | ConvertFrom-Json
        $qs = @($spec.questions)
        $missing = @($qs | Where-Object { -not $_.en -or -not $_.te -or -not $_.hi -or ($_.answerable -and -not $_.file) })
        if ($qs.Count -ge 10 -and $qs.Count -le 15 -and $missing.Count -eq 0) { $true } else { "$($qs.Count) questions, $($missing.Count) incomplete" }
    }
    Test-Check 'eval results are recorded per model and language (eval/RESULTS.md)' {
        if (-not (Test-Path 'eval\RESULTS.md')) { return 'run: python scripts\eval_languages.py' }
        $text = Get-Content 'eval\RESULTS.md' -Raw -Encoding UTF8
        if ($text -match '\| Model \| en \| te \| hi \|' -and $text -match '\| local[ (|]') { $true } else { 'RESULTS.md has no per-model table' }
    }

    if ($SkipFakeModel) { throw [System.OperationCanceledException]::new('skip') }

    # ================= Part B: claim check against deliberately bad answers =================
    Write-Host "`n== Claim check: restarting the api with the cloud models pointed at the fake model..."
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python is needed for Part B (or run with -SkipFakeModel)' }
    $fakeProcess = Start-Process $python.Source -ArgumentList @('tests\fake_gemini.py', "$FakePort", '0.02') -PassThru -WindowStyle Hidden
    $override = Join-Path $env:TEMP 'bharatagent-phase5.override.yml'
    @('services:', '  api:', '    environment:', "      GEMINI_BASE_URL: http://host.docker.internal:$FakePort", '      GEMINI_API_KEY: fake-key',
      "      GROQ_BASE_URL: http://host.docker.internal:$FakePort", '      GROQ_API_KEY: fake-key') | Set-Content -Path $override -Encoding ASCII
    docker compose -f docker-compose.yml -f $override up -d --no-deps api 2>$null | Out-Null
    if (-not (Wait-ApiHealthy)) { throw 'api did not become healthy with the fake model' }
    $refundGraph = $script:GraphA

    Test-Check 'a sentence whose number is not in the cited source is removed ("I don''t know")' {
        $run = Invoke-Run '"What is the consultation fee? FAKE_WRONG_NUMBER"' $null $refundGraph
        $c = @($run.Last.claims)[0]
        if ($run.Last.supported -eq $false -and $c.verdict -eq 'unsupported' -and $c.reason -match '98765' -and $run.Last.answer -notmatch '98765') { $true }
        else { "supported=$($run.Last.supported), claims=$($run.Last.claims | ConvertTo-Json -Compress)" }
    }
    Test-Check 'an uncited sentence is removed; the cited one is kept' {
        $run = Invoke-Run '"What is the consultation fee? FAKE_UNCITED"' $null $refundGraph
        $verdicts = @($run.Last.claims | ForEach-Object { $_.verdict }) -join ','
        if ($run.Last.supported -eq $true -and $verdicts -eq 'supported,uncited' -and $run.Last.answer -notmatch 'most generous') { $true }
        else { "verdicts=$verdicts, answer=$($run.Last.answer)" }
    }
}
catch [System.OperationCanceledException] { }
catch {
    Write-Result 'FAIL' 'Part B could not run' $_.Exception.Message
}
finally {
    if (-not $SkipFakeModel) {
        if ($fakeProcess) { Stop-Process -Id $fakeProcess.Id -Force -ErrorAction SilentlyContinue }
        Write-Host "`nRestoring the normal api..."
        docker compose up -d --no-deps api 2>$null | Out-Null
        if (Wait-ApiHealthy) { Write-Host 'api restored.' } else { Write-Host 'api did not become healthy; run: docker compose up -d api' -ForegroundColor Red }
    }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by quota (of {4}), answer model {5}" -f
    $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total, $Model)
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Phase 5 verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 5 NOT fully verified.' -ForegroundColor Red; exit 1
