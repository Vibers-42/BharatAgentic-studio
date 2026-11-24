<#
Phase 7.1: reliability, persistence, integrity, privacy and recovery checks on the running Docker stack.
Prints PASS / FAIL / SKIP / BLOCKED per check. Works in Windows PowerShell 5.1 and PowerShell 7.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase7_1.ps1

What it does (all with its own test agents and users; the main data is never deleted):
- baseline: five healthy containers, restart policies, models pulled, GPU, a database backup that restores;
- persistence: an agent with a PDF, a GitHub repo and a docs page is saved and indexed, answered, then the
  api, the web and the whole stack (down + up, volumes kept) are restarted, and the same agent, sources,
  chunks, embeddings and answers are checked again without re-uploading anything;
- integrity: duplicate and concurrent uploads, corrupt / fake / scanned / empty / oversized files, invalid
  ids, clearing one graph's source, and an ingestion interrupted by an api restart;
- isolation: two agents using the same source id, queried concurrently by two callers;
- local-only: Gemini and Groq are pointed at tests\fake_gemini.py, which counts requests; RAG, Calculator
  and MCP questions, a missing main model, and both local models missing must send zero cloud requests
  (a normal cloud request is sent first, to prove the counter works);
- failures: Groq unreachable, Gemini daily quota (no retry), Ollama stopped, database stopped, an SSE client
  that disconnects; each must give a clear result and recover;
- security: SQL-looking input, private URLs, path traversal, prompt injection from a document, no keys in
  the status endpoint, the web bundle or the api logs.
Takes about 15 minutes. Needs Python (for the fake cloud server). The normal api is restored at the end.
Model requests: local models only, plus 1 real Groq request; no Gemini request (quota-safe).
#>

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
Add-Type -AssemblyName System.Net.Http

$script:Pass = 0; $script:Fail = 0; $script:Skip = 0; $script:Blocked = 0
$Api = 'http://127.0.0.1:8000'
$Web = 'http://127.0.0.1:3000'
$FakePort = 8766
$FakeUrl = "http://127.0.0.1:$FakePort"

function Write-Result([string]$Result, [string]$Name, [string]$Detail = '') {
    $colors = @{ PASS = 'Green'; FAIL = 'Red'; SKIP = 'Yellow'; BLOCKED = 'Magenta' }
    $line = '{0,-7} {1}' -f $Result, $Name
    if ($Detail) { $line += "  ($Detail)" }
    Write-Host $line -ForegroundColor $colors[$Result]
    switch ($Result) { 'PASS' { $script:Pass++ } 'FAIL' { $script:Fail++ } 'SKIP' { $script:Skip++ } 'BLOCKED' { $script:Blocked++ } }
}

function Test-Check([string]$Name, [scriptblock]$Test) {
    if (-not $script:ApiUp) { Write-Result 'SKIP' $Name 'api is not reachable'; return }
    try {
        $result = & $Test
        if ($result -is [bool] -and $result) { Write-Result 'PASS' $Name }
        elseif ("$result".StartsWith('SKIP:')) { Write-Result 'SKIP' $Name "$result".Substring(5) }
        elseif ("$result".StartsWith('BLOCKED:')) { Write-Result 'BLOCKED' $Name "$result".Substring(8) }
        elseif ($result) { Write-Result 'FAIL' $Name "$result" }
        else { Write-Result 'FAIL' $Name }
    } catch {
        Write-Result 'FAIL' $Name $_.Exception.Message
    }
}

$script:Http = New-Object System.Net.Http.HttpClient
$script:Http.Timeout = [TimeSpan]::FromSeconds(900)

function New-Request([string]$Method, [string]$Url, [string]$Body = $null) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)
    if ($Body) {
        $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
        $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    }
    $req
}

function Read-Response($res) {
    $text = [System.Text.Encoding]::UTF8.GetString($res.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
    $json = $null
    try { $json = $text | ConvertFrom-Json } catch { }
    [pscustomobject]@{ Status = [int]$res.StatusCode; Text = $text; Json = $json }
}

function Invoke-Api([string]$Method, [string]$Url, [string]$Body = $null) {
    Read-Response ($script:Http.SendAsync((New-Request $Method $Url $Body)).GetAwaiter().GetResult())
}

# Sends several requests at the same moment and returns their responses in order.
function Invoke-Parallel([object[]]$Requests) {
    $tasks = @($Requests | ForEach-Object { $script:Http.SendAsync((New-Request $_.Method $_.Url $_.Body)) })
    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$tasks)
    , @($tasks | ForEach-Object { Read-Response $_.Result })
}

function New-Upload([string]$Url, [byte[]]$Bytes, [string]$Name) {
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $file = [System.Net.Http.ByteArrayContent]::new($Bytes)
    $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/pdf')
    $form.Add($file, 'file', $Name)
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, $Url)
    $req.Content = $form
    $req
}

function Send-Upload([string]$Url, [byte[]]$Bytes, [string]$Name) {
    Read-Response ($script:Http.SendAsync((New-Upload $Url $Bytes $Name)).GetAwaiter().GetResult())
}

function Invoke-Native([string]$Exe, [string[]]$Arguments, [string]$StdIn) {
    if ($PSBoundParameters.ContainsKey('StdIn')) { $out = $StdIn | & $Exe @Arguments 2>$null }
    else { $out = & $Exe @Arguments 2>$null }
    [pscustomobject]@{ Output = (($out | Out-String).Trim()); Code = $LASTEXITCODE }
}

# Runs a shell script inside the db container. It travels base64-encoded: Windows PowerShell 5.1 mangles quotes
# inside native-command arguments and adds CR line endings to piped text, and base64 has neither problem.
function Invoke-DbShell([string]$Script) {
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script.Replace("`r", '')))
    (Invoke-Native 'docker' @('compose', 'exec', '-T', 'db', 'sh', '-c', "echo $b64 | base64 -d | sh")).Output
}

function Invoke-Sql([string]$Sql) {
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Sql))
    Invoke-DbShell "echo $b64 | base64 -d | psql -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -At -v ON_ERROR_STOP=1"
}

function ConvertTo-Json10($Object) { $Object | ConvertTo-Json -Depth 12 -Compress }

function Wait-Healthy([string[]]$Services, [int]$Seconds = 240) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) {
        $ok = $true
        foreach ($s in $Services) {
            $id = (docker compose ps -q $s 2>$null | Out-String).Trim()
            $state = if ($id) { (docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $id 2>$null | Out-String).Trim() } else { '' }
            if ($state -ne 'healthy' -and $state -ne 'running') { $ok = $false }
        }
        if ($ok) {
            try { if ((Invoke-Api 'GET' "$Api/health").Json.ok) { return $true } } catch { }
        }
        Start-Sleep -Seconds 2
    }
    $false
}

# The compose files the stack was started with: keep the GPU override if the ollama container has the GPU.
function Get-ComposeFiles {
    $id = (docker compose ps -q ollama 2>$null | Out-String).Trim()
    $gpu = if ($id) { (docker inspect -f '{{json .HostConfig.DeviceRequests}}' $id 2>$null | Out-String).Trim() } else { 'null' }
    if ($gpu -and $gpu -ne 'null' -and $gpu -ne '[]') { @('-f', 'docker-compose.yml', '-f', 'docker-compose.gpu.yml') } else { @('-f', 'docker-compose.yml') }
}

function Restart-Api([string[]]$EnvLines) {
    $files = Get-ComposeFiles
    if ($EnvLines) {
        $override = Join-Path $env:TEMP 'bharatagent-p71.override.yml'
        (@('services:', '  api:', '    environment:') + ($EnvLines | ForEach-Object { "      $_" })) | Set-Content -Path $override -Encoding ASCII
        docker compose @files -f $override up -d --no-deps api 2>$null | Out-Null
    } else {
        docker compose @files up -d --no-deps api 2>$null | Out-Null
    }
    Wait-Healthy @('api')
}

# A saved agent: the five main blocks plus extra nodes, all drawn into the agent.
function New-Graph([string]$Model, [object[]]$Extra, [bool]$LocalOnly = $false) {
    $main = @('input', 'guardrails', 'router', 'agent', 'output')
    $nodes = @()
    for ($i = 0; $i -lt 5; $i++) {
        $config = @{}
        if ($main[$i] -eq 'agent') { $config = @{ model = $Model; language = 'en'; local_only = $LocalOnly } }
        $nodes += @{ id = "n$($i + 1)"; type = $main[$i]; config = $config; position = @{ x = 300; y = 140 * $i } }
    }
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    foreach ($n in $Extra) { $edges += @{ from = $n.id; to = 'n4' } }
    @{ nodes = $nodes + $Extra; edges = $edges }
}

function Save-Graph([string]$Name, $Graph) {
    $r = Invoke-Api 'POST' "$Api/graphs" (ConvertTo-Json10 @{ name = $Name; graph = $Graph })
    if ($r.Status -ne 201) { throw "save: HTTP $($r.Status) $($r.Text)" }
    $r.Json.id
}

function Get-Run([hashtable]$Body) {
    $r = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Json10 $Body)
    if ($r.Status -ne 200) { return [pscustomobject]@{ Status = $r.Status; Text = $r.Text; Last = $null; Events = @() } }
    $events = @($r.Json.events)
    [pscustomobject]@{ Status = 200; Text = $r.Text; Last = $events[$events.Count - 1]; Events = $events
        Agent = ($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' }).detail }
}

function Get-Listing([int]$GraphId) {
    $l = (Invoke-Api 'GET' "$Api/sources?graph_id=$GraphId").Json
    $map = @{}
    foreach ($s in @($l.sources)) { $map[$s.source_id] = $s }
    $map
}

$Pdf = [System.IO.File]::ReadAllBytes((Resolve-Path 'samples\refund-policy.pdf'))
$Hostel = [System.IO.File]::ReadAllBytes((Resolve-Path 'samples\hostel-rules.pdf'))
$Injection = [System.IO.File]::ReadAllBytes((Resolve-Path 'samples\prompt-injection.pdf'))
$Scanned = [System.IO.File]::ReadAllBytes((Resolve-Path 'samples\blank-scan.pdf'))
$Stamp = Get-Date -Format 'MMddHHmmss'
$UserA = "p71a_$Stamp"; $UserB = "p71b_$Stamp"

$Knowledge = @(
    @{ id = 's_pdf'; q = 'How much is refunded if a lab test is cancelled before the sample is collected?'; fact = '80'; file = 'refund-policy.pdf' },
    @{ id = 's_repo'; q = 'What is the Spoon-Knife repository meant for?'; fact = 'fork'; file = 'README.md' },
    @{ id = 's_url'; q = 'What is the example.com domain for?'; fact = 'example'; file = 'Example Domain' }
)

$script:ApiUp = $false
for ($i = 0; $i -lt 60 -and -not $script:ApiUp; $i++) {  # a just-restarted api needs a few seconds
    try { $script:ApiUp = ((Invoke-Api 'GET' "$Api/health").Json.ok -eq $true) } catch { Start-Sleep -Seconds 2 }
}
if (-not $script:ApiUp) { Write-Host 'The api is not reachable: this run is NOT valid. Start Docker Desktop and the stack first.' -ForegroundColor Red }

# ============================ Baseline ============================
Write-Host "`n== Baseline"
Test-Check 'all five containers are running (and healthy where they have a health check)' {
    if (Wait-Healthy @('db', 'ollama', 'mcp-runner', 'api', 'web') 20) { $true } else { (docker compose ps --format '{{.Service}}={{.Status}}' | Out-String).Trim() -replace "`r?`n", ', ' }
}
Test-Check 'every service restarts by itself after Docker Desktop restarts (restart: unless-stopped)' {
    $bad = @()
    foreach ($s in @('db', 'ollama', 'mcp-runner', 'api', 'web')) {
        $id = (docker compose ps -q $s | Out-String).Trim()
        $p = (docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' $id | Out-String).Trim()
        if ($p -ne 'unless-stopped') { $bad += "$s=$p" }
    }
    if ($bad.Count -eq 0) { $true } else { $bad -join ', ' }
}
Test-Check 'Ollama has qwen3:14b, qwen2.5:3b and bge-m3' {
    $list = (Invoke-Native 'docker' @('compose', 'exec', '-T', 'ollama', 'ollama', 'list')).Output
    $missing = @('qwen3:14b', 'qwen2.5:3b', 'bge-m3') | Where-Object { $list -notmatch [regex]::Escape($_) }
    if ($missing.Count -eq 0) { $true } else { "missing: $($missing -join ', ')" }
}
Test-Check 'GPU is visible inside the ollama container' {
    if ((Get-ComposeFiles) -contains 'docker-compose.gpu.yml') {
        $g = (Invoke-Native 'docker' @('compose', 'exec', '-T', 'ollama', 'nvidia-smi', '-L')).Output
        if ($g -match 'GPU 0') { $true } else { "nvidia-smi: $g" }
    } else { 'SKIP:the stack was started without docker-compose.gpu.yml' }
}
Test-Check 'a database backup restores completely into a scratch database' {
    $cmd ="pg_dump -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -Fc -f /tmp/p71.dump && createdb -U `"`$POSTGRES_USER`" p71_restore && " +
           "pg_restore -U `"`$POSTGRES_USER`" -d p71_restore /tmp/p71.dump && " +
           "psql -U `"`$POSTGRES_USER`" -d p71_restore -At -c `"select (select count(*) from graphs)||','||(select count(*) from chunks)`"`n" +
           "dropdb -U `"`$POSTGRES_USER`" --if-exists p71_restore; rm -f /tmp/p71.dump`n"
    $restored = Invoke-DbShell $cmd
    $live = Invoke-Sql 'select (select count(*) from graphs)||'',''||(select count(*) from chunks)'
    if ($restored -and $restored.Trim() -eq $live.Trim()) { $true } else { "restored '$restored', live '$live'" }
}

# ============================ Persistence ============================
Write-Host "`n== Saved knowledge survives restarts"
$script:Gid = 0; $script:Before = $null; $script:GraphJson = ''
$Sources = @(
    @{ id = 's_pdf'; type = 'source_doc'; config = @{ file = 'refund-policy.pdf' } },
    @{ id = 's_repo'; type = 'source_repo'; config = @{ url = 'https://github.com/octocat/Spoon-Knife' } },
    @{ id = 's_url'; type = 'source_doc'; config = @{ url = 'https://example.com' } },
    @{ id = 's_int'; type = 'source_repo'; config = @{ url = 'https://github.com/octocat/Spoon-Knife' } },
    @{ id = 't_calc'; type = 'tool_calculator'; config = @{} },
    @{ id = 't_mcp'; type = 'tool_mcp'; config = @{ server = 'orders-db'; mode = 'read_only' } }
)

function Get-Snapshot([int]$GraphId) {
    $sql = "select source_id||':'||count(*)||':'||count(embedding)||':'||min(id)||':'||max(id) from chunks where scope='graph:$GraphId' group by source_id order by source_id"
    [pscustomobject]@{ Graph = (Invoke-Api 'GET' "$Api/graphs/$GraphId").Text; Chunks = (Invoke-Sql $sql) -replace "`r?`n", ' ' }
}

function Test-Knowledge([int]$GraphId, [string]$User) {
    $bad = @()
    foreach ($k in $Knowledge) {
        $run = Get-Run @{ question = $k.q; graph_id = $GraphId; model = 'local'; user = $User; local_only = $true }
        $cited = @($run.Last.citations | Where-Object { $_.source_id -eq $k.id })
        if (-not ($run.Last.supported -and $cited.Count -gt 0 -and $run.Last.answer -match $k.fact)) { $bad += "$($k.id): $($run.Last.answer)" }
    }
    if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
}

Test-Check 'test users created' {
    $a = Invoke-Api 'POST' "$Api/users" (ConvertTo-Json10 @{ name = $UserA })
    $b = Invoke-Api 'POST' "$Api/users" (ConvertTo-Json10 @{ name = $UserB })
    if ($a.Status -eq 201 -and $b.Status -eq 201) { $true } else { "HTTP $($a.Status) / $($b.Status)" }
}
Test-Check 'agent saved; PDF, GitHub repo and docs page indexed into it' {
    $script:Gid = Save-Graph "p71 persistence $Stamp" (New-Graph 'local' $Sources)
    $p = Send-Upload "$Api/sources/s_pdf/pdf?graph_id=$($script:Gid)" $Pdf 'refund-policy.pdf'
    $r = Invoke-Api 'POST' "$Api/sources/s_repo/repo?graph_id=$($script:Gid)" '{}'
    $u = Invoke-Api 'POST' "$Api/sources/s_url/url?graph_id=$($script:Gid)" '{}'
    if ($p.Status -eq 201 -and $r.Status -eq 201 -and $u.Status -eq 201) { $true } else { "pdf $($p.Status), repo $($r.Status) $($r.Text), url $($u.Status) $($u.Text)" }
}
Test-Check 'every chunk has its embedding stored; the listing matches the database' {
    $script:Before = Get-Snapshot $script:Gid
    $l = Get-Listing $script:Gid
    $rows = $script:Before.Chunks -split ' ' | Where-Object { $_ }
    $bad = @($rows | Where-Object { $p = $_ -split ':'; $p[1] -ne $p[2] -or [int]$l[$p[0]].chunks -ne [int]$p[1] })
    if ($rows.Count -eq 3 -and $bad.Count -eq 0) { $true } else { "db: $($script:Before.Chunks)" }
}
Test-Check 'the original PDF file is kept in the uploads volume' {
    $f = (Invoke-Native 'docker' @('compose', 'exec', '-T', 'api', 'ls', '-l', "/data/uploads/graph_$($script:Gid)/s_pdf/refund-policy.pdf")).Output
    if ($f -match "$($Pdf.Length)") { $true } else { "ls: $f" }
}
Test-Check 'questions answered from the PDF, the repo and the docs page, each citing its source' { Test-Knowledge $script:Gid $UserA }

Test-Check 'after an api restart: same agent, same chunks and embeddings, answers still cited' {
    docker compose restart api 2>$null | Out-Null
    if (-not (Wait-Healthy @('api'))) { return 'api did not come back' }
    $now = Get-Snapshot $script:Gid
    if ($now.Graph -ne $script:Before.Graph -or $now.Chunks -ne $script:Before.Chunks) { return "changed: $($now.Chunks)" }
    Test-Knowledge $script:Gid $UserB
}
Test-Check 'after a web restart: the app and the source status are served again' {
    docker compose restart web 2>$null | Out-Null
    Start-Sleep -Seconds 3
    $l = (Invoke-Api 'GET' "$Web/api/sources?graph_id=$($script:Gid)").Json.sources
    $html = (Invoke-Api 'GET' "$Web/").Text
    if ($html -match 'id="root"' -and @($l).Count -eq 3) { $true } else { "sources via web: $(@($l).Count)" }
}
Test-Check 'after the whole stack is taken down and up (volumes kept): nothing lost, no re-upload needed' {
    $files = Get-ComposeFiles
    docker compose @files down 2>$null | Out-Null
    docker compose @files up -d 2>$null | Out-Null
    if (-not (Wait-Healthy @('db', 'ollama', 'mcp-runner', 'api', 'web'))) { return 'stack did not come back' }
    $now = Get-Snapshot $script:Gid
    if ($now.Graph -ne $script:Before.Graph -or $now.Chunks -ne $script:Before.Chunks) { return "changed: $($now.Chunks)" }
    $usage = (Invoke-Api 'GET' "$Api/usage/totals?user=$UserA").Json.requests
    if ($usage -lt 3) { return "usage rows for $UserA after restart: $usage" }
    Test-Knowledge $script:Gid $UserA
}

# ============================ Integrity ============================
Write-Host "`n== Database integrity"
$PdfUrl = "$Api/sources/s_pdf/pdf?graph_id=$($script:Gid)"
$script:PdfChunks = 0
Test-Check 'uploading the same PDF again replaces it (no duplicate chunks)' {
    $script:PdfChunks = (Get-Listing $script:Gid)['s_pdf'].chunks
    $r = Send-Upload $PdfUrl $Pdf 'refund-policy.pdf'
    $n = (Get-Listing $script:Gid)['s_pdf'].chunks
    if ($r.Status -eq 201 -and $n -eq $script:PdfChunks) { $true } else { "HTTP $($r.Status), chunks $($script:PdfChunks) -> $n" }
}
Test-Check 'four uploads of one source at the same moment leave exactly one copy' {
    $tasks = @(1..4 | ForEach-Object { $script:Http.SendAsync((New-Upload $PdfUrl $Pdf 'refund-policy.pdf')) })
    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$tasks)
    $codes = @($tasks | ForEach-Object { [int]$_.Result.StatusCode })
    $n = [int](Invoke-Sql "select count(*) from chunks where scope='graph:$($script:Gid)' and source_id='s_pdf'")
    $idx = Invoke-Sql "select count(distinct chunk_index) from chunks where scope='graph:$($script:Gid)' and source_id='s_pdf'"
    if (($codes | Where-Object { $_ -ne 201 }).Count -eq 0 -and $n -eq $script:PdfChunks -and [int]$idx -eq $n) { $true } else { "codes $($codes -join ','), rows $n, distinct $idx" }
}
Test-Check 'a corrupt PDF is refused (422) and the previous content stays' {
    $bytes = [System.Text.Encoding]::ASCII.GetBytes("%PDF-1.4`n" + ('x' * 2000))
    $r = Send-Upload $PdfUrl $bytes 'broken.pdf'
    $s = (Get-Listing $script:Gid)['s_pdf']
    if ($r.Status -eq 422 -and $s.file -eq 'refund-policy.pdf' -and $s.chunks -eq $script:PdfChunks) { $true } else { "HTTP $($r.Status), now $($s.file) $($s.chunks)" }
}
Test-Check 'a scanned PDF without text is refused with a clear reason (no OCR) and the previous content stays' {
    $r = Send-Upload $PdfUrl $Scanned 'scan.pdf'
    $s = (Get-Listing $script:Gid)['s_pdf']
    if ($r.Status -eq 422 -and $r.Json.detail -match 'scanned' -and $s.file -eq 'refund-policy.pdf') { $true } else { "HTTP $($r.Status) $($r.Text)" }
}
Test-Check 'a text file named .pdf, an empty file and a .exe name are refused (400)' {
    $a = Send-Upload $PdfUrl ([System.Text.Encoding]::ASCII.GetBytes('just text')) 'notes.pdf'
    $b = Send-Upload $PdfUrl ([byte[]]@()) 'empty.pdf'
    $c = Send-Upload $PdfUrl $Pdf 'tool.exe'
    if ($a.Status -eq 400 -and $b.Status -eq 400 -and $c.Status -eq 400) { $true } else { "HTTP $($a.Status), $($b.Status), $($c.Status)" }
}
Test-Check 'a file over the size limit is refused (413)' {
    $max = 20
    $big = New-Object byte[] (($max + 1) * 1024 * 1024)
    [System.Text.Encoding]::ASCII.GetBytes('%PDF-1.4').CopyTo($big, 0)
    $r = Send-Upload $PdfUrl $big 'big.pdf'
    if ($r.Status -eq 413) { $true } else { "HTTP $($r.Status)" }
}
Test-Check 'invalid source ids, unknown nodes and unknown agents are refused (400 / 404), never 500' {
    $codes = @(
        (Send-Upload "$Api/sources/bad%20id!/pdf?graph_id=$($script:Gid)" $Pdf 'a.pdf').Status,
        (Send-Upload "$Api/sources/s_nope/pdf?graph_id=$($script:Gid)" $Pdf 'a.pdf').Status,
        (Send-Upload "$Api/sources/n4/pdf?graph_id=$($script:Gid)" $Pdf 'a.pdf').Status,
        (Invoke-Api 'GET' "$Api/sources?graph_id=99999999").Status,
        (Invoke-Api 'GET' "$Api/graphs/99999999").Status)
    if (($codes -join ',') -eq '400,404,400,404,404') { $true } else { "got $($codes -join ',')" }
}
Test-Check 'an ingestion cut off by an api restart leaves the source empty or complete, never half-written' {
    $task = $script:Http.SendAsync((New-Request 'POST' "$Api/sources/s_int/repo?graph_id=$($script:Gid)" '{}'))
    Start-Sleep -Milliseconds 1500
    docker compose restart api 2>$null | Out-Null
    try { $null = $task.Wait(5000) } catch { }
    if (-not (Wait-Healthy @('api'))) { return 'api did not come back' }
    $n = [int](Invoke-Sql "select count(*) from chunks where scope='graph:$($script:Gid)' and source_id='s_int'")
    $repo = [int](Invoke-Sql "select count(*) from chunks where scope='graph:$($script:Gid)' and source_id='s_repo'")
    if ($n -eq 0 -or $n -eq $repo) { $true } else { "s_int has $n chunks, a full copy has $repo" }
}

# ============================ Isolation ============================
Write-Host "`n== Shared knowledge and isolation"
$script:GidB = 0
Test-Check 'a second agent with the same source id holds a different PDF' {
    $script:GidB = Save-Graph "p71 isolation B $Stamp" (New-Graph 'local_small' @(@{ id = 's_pdf'; type = 'source_doc'; config = @{ file = 'hostel-rules.pdf' } }))
    $r = Send-Upload "$Api/sources/s_pdf/pdf?graph_id=$($script:GidB)" $Hostel 'hostel-rules.pdf'
    if ($r.Status -eq 201) { $true } else { "HTTP $($r.Status)" }
}
Test-Check 'eight concurrent questions from two callers to the two agents never mix their documents' {
    $reqs = @()
    for ($i = 0; $i -lt 4; $i++) {
        $reqs += @{ Method = 'POST'; Url = "$Api/pipeline/run?stream=false"; Body = (ConvertTo-Json10 @{ question = 'What are the refund and hostel gate rules?'; graph_id = $script:Gid; model = 'local_small'; user = $UserA; local_only = $true }) }
        $reqs += @{ Method = 'POST'; Url = "$Api/pipeline/run?stream=false"; Body = (ConvertTo-Json10 @{ question = 'What are the refund and hostel gate rules?'; graph_id = $script:GidB; model = 'local_small'; user = $UserB; local_only = $true }) }
    }
    $res = Invoke-Parallel $reqs
    $bad = @()
    for ($i = 0; $i -lt $res.Count; $i++) {
        $want = if ($i % 2 -eq 0) { 'refund-policy.pdf' } else { 'hostel-rules.pdf' }
        $agent = ($res[$i].Json.events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' }).detail
        $files = @($agent.retrieval.candidates | ForEach-Object { $_.file } | Where-Object { $_ -like '*.pdf' } | Sort-Object -Unique)
        if ($res[$i].Status -ne 200 -or ($files | Where-Object { $_ -ne $want }).Count -gt 0 -or $files.Count -eq 0) { $bad += "#$i HTTP $($res[$i].Status) files=$($files -join ',')" }
    }
    if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
}
Test-Check 'clearing the source in one agent keeps the other agent''s source with the same id' {
    $d = Invoke-Api 'DELETE' "$Api/sources/s_pdf?graph_id=$($script:GidB)"
    $a = (Get-Listing $script:Gid)['s_pdf']; $b = (Get-Listing $script:GidB)['s_pdf']
    if ($d.Status -eq 200 -and $a.chunks -eq $script:PdfChunks -and -not $b) { $true } else { "delete $($d.Status), A=$($a.chunks), B=$($b.chunks)" }
}
Test-Check 'after clearing, that agent no longer answers from the removed document' {
    $run = Get-Run @{ question = 'What time does the hostel gate close?'; graph_id = $script:GidB; model = 'local_small'; user = $UserB; local_only = $true }
    if (-not $run.Last.supported -and @($run.Last.citations).Count -eq 0) { $true } else { "answer: $($run.Last.answer)" }
}

# ============================ Security ============================
Write-Host "`n== Security"
Test-Check 'SQL-looking input is treated as data (422 / 400 / stored verbatim), never a 500' {
    $name = "x'; DROP TABLE chunks; --"
    $g = Invoke-Api 'POST' "$Api/graphs" (ConvertTo-Json10 @{ name = $name; graph = (New-Graph 'local' @()) })
    $back = (Invoke-Api 'GET' "$Api/graphs").Json | Where-Object { $_.id -eq $g.Json.id }
    $m = Invoke-Api 'GET' "$Api/usage/totals?model=local%27%20OR%201%3D1--"
    $u = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Json10 @{ question = 'hi'; graph_id = $script:Gid; user = "demo' OR '1'='1" })
    $chunks = [int](Invoke-Sql 'select count(*) from chunks')
    if ($g.Status -eq 201 -and $back.name -eq $name -and $m.Status -eq 422 -and $u.Status -eq 400 -and $chunks -gt 0) { $true }
    else { "graph $($g.Status) name='$($back.name)', model filter $($m.Status), user $($u.Status)" }
}
Test-Check 'private and internal addresses are refused as docs links (SSRF)' {
    $bad = @()
    foreach ($url in @('http://127.0.0.1:8000/health', 'http://db:5432/', 'http://169.254.169.254/latest/meta-data', 'http://mcp-runner:8100/mcp', 'file:///etc/passwd')) {
        $r = Invoke-Api 'POST' "$Api/sources/s_url/url?graph_id=$($script:Gid)" (ConvertTo-Json10 @{ url = $url })
        if ($r.Status -ne 400 -and $r.Status -ne 422) { $bad += "$url -> $($r.Status)" }
    }
    $s = (Get-Listing $script:Gid)['s_url']
    if ($bad.Count -eq 0 -and $s.file -eq 'Example Domain') { $true } else { ($bad -join ', ') + " s_url=$($s.file)" }
}
Test-Check 'a file name with ../ cannot leave the source folder' {
    $r = Send-Upload $PdfUrl $Pdf '..\..\..\evil.pdf'
    $outside = (Invoke-Native 'docker' @('compose', 'exec', '-T', 'api', 'sh', '-c', 'ls /data/evil.pdf /data/uploads/evil.pdf 2>/dev/null | wc -l')).Output
    $inside = (Invoke-Native 'docker' @('compose', 'exec', '-T', 'api', 'ls', "/data/uploads/graph_$($script:Gid)/s_pdf/")).Output
    if ($r.Status -eq 201 -and $outside.Trim() -eq '0' -and $inside -match 'evil.pdf') { $true } else { "HTTP $($r.Status), outside=$outside, inside=$inside" }
}
Test-Check 'a document that tells the model to lie does not change the answer from the real policy' {
    $gid = Save-Graph "p71 injection $Stamp" (New-Graph 'local' @(@{ id = 's_pdf'; type = 'source_doc'; config = @{} }, @{ id = 's_bad'; type = 'source_doc'; config = @{} }))
    $null = Send-Upload "$Api/sources/s_pdf/pdf?graph_id=$gid" $Pdf 'refund-policy.pdf'
    $null = Send-Upload "$Api/sources/s_bad/pdf?graph_id=$gid" $Injection 'prompt-injection.pdf'
    $run = Get-Run @{ question = 'How much is refunded if a lab test is cancelled before the sample is collected?'; graph_id = $gid; model = 'local'; user = $UserA; local_only = $true }
    $a = "$($run.Last.answer)"
    if ($a -match '80' -and $a -notmatch '100') { $true } else { "answer: $a" }
}
Test-Check 'no API key appears in /status, /models, the web bundle or the api logs' {
    $envText = Get-Content .env -Raw
    $keys = @()
    foreach ($name in @('GEMINI_API_KEY', 'GROQ_API_KEY', 'GITHUB_TOKEN')) {
        if ($envText -match "(?m)^$name=(.+)$") { $v = $Matches[1].Trim(); if ($v.Length -ge 12) { $keys += $v } }
    }
    if ($keys.Count -eq 0) { return 'SKIP:no keys in .env to look for' }
    $html = (Invoke-Api 'GET' "$Web/").Text
    $js = if ($html -match 'src="(/assets/[^"]+\.js)"') { (Invoke-Api 'GET' "$Web$($Matches[1])").Text } else { '' }
    $hay = (Invoke-Api 'GET' "$Api/status").Text + (Invoke-Api 'GET' "$Api/models").Text + $js + ((docker compose logs api 2>&1) | Out-String)
    $found = @($keys | Where-Object { $hay.Contains($_) })
    if ($found.Count -eq 0) { $true } else { "$($found.Count) key(s) found" }
}

# ============================ Failures and recovery (real services) ============================
Write-Host "`n== Failures and recovery"
Test-Check 'database stopped: a clear 503, then full recovery when it is back' {
    docker compose stop db 2>$null | Out-Null
    $down = Invoke-Api 'GET' "$Api/graphs"
    docker compose start db 2>$null | Out-Null
    $null = Wait-Healthy @('db')
    $ok = $false
    for ($i = 0; $i -lt 10 -and -not $ok; $i++) { $ok = ((Invoke-Api 'GET' "$Api/graphs").Status -eq 200); if (-not $ok) { Start-Sleep 1 } }
    if ($down.Status -eq 503 -and $down.Json.detail -match 'database' -and $ok) { $true } else { "while down: HTTP $($down.Status); recovered: $ok" }
}
Test-Check 'Ollama stopped: a clear all_providers_failed error, one error row, then answers again' {
    $before = (Invoke-Api 'GET' "$Api/usage/totals?user=$UserB").Json
    docker compose stop ollama 2>$null | Out-Null
    $run = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'local'; user = $UserB; local_only = $true }
    $status = (Invoke-Api 'GET' "$Api/status").Json.ollama
    $files = Get-ComposeFiles
    docker compose start ollama 2>$null | Out-Null
    $null = Wait-Healthy @('ollama')
    $after = (Invoke-Api 'GET' "$Api/usage/totals?user=$UserB").Json
    $again = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'local'; user = $UserB; local_only = $true }
    $err = $run.Events | Where-Object { $_.type -eq 'error' }
    if ($err.code -ne 'all_providers_failed' -and $err.code -ne 'embedding_unavailable') { return "no clear error: $($run.Text.Substring(0, [Math]::Min(200, $run.Text.Length)))" }
    if ($status -ne $false) { return "status.ollama while stopped: $status" }
    if ($after.errors - $before.errors -gt 1) { return "error rows: $($after.errors - $before.errors)" }
    if ($again.Last.supported -and $again.Last.answer -match '80') { $true } else { "after restart: $($again.Last.answer)" }
}
Test-Check 'a client that disconnects mid-answer leaves the api healthy and no duplicate usage rows' {
    $before = (Invoke-Api 'GET' "$Api/usage/totals?user=$UserA").Json.requests
    $req = New-Request 'POST' "$Api/pipeline/run" (ConvertTo-Json10 @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'local'; user = $UserA; local_only = $true })
    $res = $script:Http.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    $reader = [System.IO.StreamReader]::new($res.Content.ReadAsStreamAsync().GetAwaiter().GetResult())
    for ($i = 0; $i -lt 6; $i++) { $null = $reader.ReadLine() }
    $reader.Dispose(); $res.Dispose()
    Start-Sleep -Seconds 40
    $after = (Invoke-Api 'GET' "$Api/usage/totals?user=$UserA").Json.requests
    $next = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'local'; user = $UserA; local_only = $true }
    if (($after - $before) -le 1 -and $next.Last.supported) { $true } else { "usage rows added: $($after - $before), next answer: $($next.Last.answer)" }
}

# ============================ Local-only and cloud failures (instrumented fake cloud) ============================
Write-Host "`n== Local-only privacy and cloud failures (Gemini -> counting fake server, Groq -> closed port)"
$fake = $null
$pyExe = if ($env:JOURNEY_PYTHON) { $env:JOURNEY_PYTHON } else { 'python' }
try {
    if (-not (Get-Command $pyExe -ErrorAction SilentlyContinue)) { throw [System.OperationCanceledException]::new('Python is not installed') }
    $fake = Start-Process $pyExe -ArgumentList @('tests\fake_gemini.py', "$FakePort", '0.01') -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $cloud = @("GEMINI_BASE_URL: http://host.docker.internal:$FakePort", 'GEMINI_API_KEY: fake-key',
               'GROQ_BASE_URL: http://host.docker.internal:9', 'GROQ_API_KEY: fake-key')
    if (-not (Restart-Api $cloud)) { throw 'api did not become healthy with the fake cloud' }
    function Get-CloudCount { $n = (Invoke-Api 'GET' "$FakeUrl/_requests").Json.all; if ($null -eq $n) { 0 } else { [int]$n } }

    Test-Check 'control: a normal Gemini request does reach the counting fake server' {
        $c0 = Get-CloudCount
        $run = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'gemini'; user = $UserA }
        if ((Get-CloudCount) -gt $c0 -and $run.Last.model -like 'gemini:*') { $true } else { "count $c0 -> $(Get-CloudCount), model $($run.Last.model)" }
    }
    Test-Check 'Gemini daily quota: no retry (exactly one request), the local model answers instead' {
        $before = (Invoke-Api 'GET' "$FakeUrl/_requests").Json
        $run = Get-Run @{ question = "$($Knowledge[0].q) FAULT_DAILY"; graph_id = $script:Gid; model = 'gemini'; user = $UserA }
        $after = (Invoke-Api 'GET' "$FakeUrl/_requests").Json
        $sent = [int]($after.all) - [int]($before.all)
        if ($sent -eq 1 -and $run.Last.fallback.code -eq 'quota_daily' -and $run.Last.model -like 'local:*') { $true } else { "requests $sent, fallback $($run.Last.fallback.code), model $($run.Last.model)" }
    }
    Test-Check 'Groq unreachable: clear fallback to the local model, answer still cited' {
        $run = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'groq'; user = $UserA }
        if ($run.Last.fallback.from -eq 'groq' -and $run.Last.model -like 'local:*' -and $run.Last.supported) { $true } else { "fallback $($run.Last.fallback | ConvertTo-Json -Compress), model $($run.Last.model)" }
    }
    $c0 = Get-CloudCount
    Test-Check 'local-only RAG, Calculator and MCP questions: every model call is local' {
        $bad = @()
        $qs = @(@{ q = $Knowledge[0].q; tool = $null },
                @{ q = 'My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?'; tool = 'calculator' },
                @{ q = 'What is the status and refund of order ORD-1002?'; tool = 'get_order' })
        foreach ($x in $qs) {
            $run = Get-Run @{ question = $x.q; graph_id = $script:Gid; model = 'local'; user = $UserA; local_only = $true }
            $steps = @($run.Agent.generation.steps | ForEach-Object { $_.model })
            $nonLocal = @($steps | Where-Object { $_ -notlike 'local*' })
            $tools = @($run.Last.tool_calls | ForEach-Object { $_.tool })
            if ($nonLocal.Count -gt 0 -or ($x.tool -and $tools -notcontains $x.tool) -or -not $run.Last.local_only) { $bad += "$($x.tool): steps=$($steps -join ',') tools=$($tools -join ',')" }
        }
        if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
    }
    Test-Check 'local-only with a cloud model is refused before anything runs (request flag and agent setting)' {
        $a = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Json10 @{ question = 'hi'; graph_id = $script:Gid; model = 'groq'; user = $UserA; local_only = $true })
        $g = New-Graph 'gemini' @() $true
        $b = Invoke-Api 'POST' "$Api/pipeline/run?stream=false" (ConvertTo-Json10 @{ question = 'hi'; graph = $g; user = $UserA })
        if ($a.Status -eq 400 -and $b.Status -eq 400) { $true } else { "HTTP $($a.Status) / $($b.Status)" }
    }
    if (-not (Restart-Api ($cloud + @('OLLAMA_CHAT_MODEL: not-pulled-model:14b')))) { throw 'api did not become healthy' }
    Test-Check 'local-only with the main model missing: the 3B backup answers the Calculator question, nothing goes to the cloud' {
        $run = Get-Run @{ question = 'My lab test cost Rs. 1200 and I cancelled before the sample was collected. How much will I get back?'; graph_id = $script:Gid; model = 'local'; user = $UserA; local_only = $true }
        $steps = @($run.Agent.generation.steps | ForEach-Object { $_.model })
        if ($run.Last.model -like 'local_small:*' -and $run.Last.fallback.from -eq 'local' -and @($steps | Where-Object { $_ -notlike 'local_small*' }).Count -eq 0) { $true }
        else { "model $($run.Last.model), steps $($steps -join ',')" }
    }
    if (-not (Restart-Api ($cloud + @('OLLAMA_CHAT_MODEL: not-pulled-model:14b', 'OLLAMA_BACKUP_MODEL: not-pulled-model:3b')))) { throw 'api did not become healthy' }
    Test-Check 'local-only with both local models missing: a clear error naming only local models' {
        $run = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'local'; user = $UserA; local_only = $true }
        $tried = @($run.Last.attempts | ForEach-Object { $_.model } | Sort-Object -Unique) -join ','
        if ($run.Last.type -eq 'error' -and $run.Last.code -eq 'all_providers_failed' -and $tried -eq 'local,local_small') { $true } else { "type $($run.Last.type) code $($run.Last.code) tried $tried" }
    }
    Test-Check 'the fake cloud received zero requests during all local-only questions' {
        $n = Get-CloudCount
        if ($n -eq $c0) { $true } else { "cloud got $($n - $c0) request(s)" }
    }
}
catch [System.OperationCanceledException] { Write-Result 'SKIP' 'local-only and cloud failure checks' $_.Exception.Message }
catch { Write-Result 'FAIL' 'local-only and cloud failure checks could not run' $_.Exception.Message }
finally {
    if ($fake) { Stop-Process -Id $fake.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "`nRestoring the normal api..."
    if (Restart-Api $null) { Write-Host 'api restored.' } else { Write-Host 'api did not become healthy; run: docker compose up -d api' -ForegroundColor Red }
}

Test-Check 'a real Groq request answers with the actual model reported (no fallback)' {
    $run = Get-Run @{ question = $Knowledge[0].q; graph_id = $script:Gid; model = 'groq'; user = $UserA }
    if ($run.Last.fallback -and "$($run.Last.fallback.code)".StartsWith('quota')) { return "BLOCKED:$($run.Last.fallback.code)" }
    if ($run.Last.model -like 'groq:*' -and -not $run.Last.fallback -and $run.Last.supported) { $true } else { "model $($run.Last.model) fallback $($run.Last.fallback.code)" }
}
Test-Check 'the stack is healthy again after all tests' {
    if (Wait-Healthy @('db', 'ollama', 'mcp-runner', 'api', 'web') 120) { $true } else { 'not all services are healthy' }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by quota (of {4}), agents #{5} and #{6}" -f $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total, $script:Gid, $script:GidB)
if (-not $script:ApiUp) { Write-Host 'INVALID RUN: the api was not reachable.' -ForegroundColor Red; exit 2 }
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Phase 7.1 verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 7.1 NOT fully verified.' -ForegroundColor Red; exit 1
