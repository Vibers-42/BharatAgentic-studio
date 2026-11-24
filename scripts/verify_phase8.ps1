<#
Phase 8 acceptance: accounts, a company, projects, invitations, shared knowledge, a shared agent, private
conversations, tenant and project isolation, revocation, local-only privacy, persistence and modest concurrency,
on the running Docker stack, with real accounts and real model answers. PASS / FAIL / SKIP / BLOCKED.
Works in Windows PowerShell 5.1 and PowerShell 7. ASCII only.

  powershell -ExecutionPolicy Bypass -File scripts\verify_phase8.ps1

People (each a separately registered account with its own session cookie):
  A owner of "Bharat Technologies <stamp>", B knowledge contributor, C knowledge consumer (viewer),
  D outsider (own workspace only), E member of the same company but of another project.
Takes about 15-20 minutes (one full stack restart, about 20 local model answers, 3 Groq, 1 Gemini).
The api is restarted with a counting fake cloud for the local-only checks and restored afterwards.
#>

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent $PSScriptRoot)
. "$PSScriptRoot\_session.ps1"

$script:Pass = 0; $script:Fail = 0; $script:Skip = 0; $script:Blocked = 0
$Api = 'http://127.0.0.1:8000'
$Web = 'http://127.0.0.1:3000'
$FakePort = 8768
$Stamp = Get-Date -Format 'MMddHHmmss'

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
    } catch { Write-Result 'FAIL' $Name $_.Exception.Message }
}

function J($Object) { $Object | ConvertTo-Json -Depth 12 -Compress }
function Call($S, [string]$Method, [string]$Path, $Body = $null) {
    $text = if ($null -ne $Body) { J $Body } else { $null }
    Send-Json $S.Http $Method "$Api$Path" $text
}
function Ask($S, [hashtable]$Body) {
    $r = Call $S 'POST' '/pipeline/run?stream=false' $Body
    if ($r.Status -ne 200) { return [pscustomobject]@{ Status = $r.Status; Text = $r.Text; Last = $null; Events = @() } }
    $events = @($r.Json.events)
    [pscustomobject]@{ Status = 200; Text = $r.Text; Last = $events[$events.Count - 1]; Events = $events
        Agent = ($events | Where-Object { $_.stage -eq 'agent' -and $_.status -eq 'done' }).detail }
}
function Invoke-Native([string]$Exe, [string[]]$Arguments) {
    $out = & $Exe @Arguments 2>$null
    [pscustomobject]@{ Output = (($out | Out-String).Trim()); Code = $LASTEXITCODE }
}
function Invoke-Sql([string]$Sql) {
    $b64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Sql))
    $cmd = "echo $b64 | base64 -d | psql -U `"`$POSTGRES_USER`" -d `"`$POSTGRES_DB`" -At -v ON_ERROR_STOP=1"
    $b2 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($cmd))
    (Invoke-Native 'docker' @('compose', 'exec', '-T', 'db', 'sh', '-c', "echo $b2 | base64 -d | sh")).Output
}
function Get-ComposeFiles {
    $id = (docker compose ps -q ollama 2>$null | Out-String).Trim()
    $gpu = if ($id) { (docker inspect -f '{{json .HostConfig.DeviceRequests}}' $id 2>$null | Out-String).Trim() } else { 'null' }
    if ($gpu -and $gpu -ne 'null' -and $gpu -ne '[]') { @('-f', 'docker-compose.yml', '-f', 'docker-compose.gpu.yml') } else { @('-f', 'docker-compose.yml') }
}
function Wait-Api([int]$Seconds = 240) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) {
        try { if ((Invoke-WebRequest -UseBasicParsing "$Api/health" -TimeoutSec 3).StatusCode -eq 200) { return $true } } catch { }
        Start-Sleep -Seconds 2
    }
    $false
}
function Restart-Api([string[]]$EnvLines) {
    $files = Get-ComposeFiles
    if ($EnvLines) {
        $override = Join-Path $env:TEMP 'bharatagent-p8.override.yml'
        (@('services:', '  api:', '    environment:') + ($EnvLines | ForEach-Object { "      $_" })) | Set-Content -Path $override -Encoding ASCII
        docker compose @files -f $override up -d --no-deps api 2>$null | Out-Null
    } else { docker compose @files up -d --no-deps api 2>$null | Out-Null }
    Start-Sleep -Seconds 3
    Wait-Api
}
function Login([string]$Email) {
    $client = New-SessionClient
    $r = Send-Json $client 'POST' "$Api/auth/login" (J @{ email = $Email; password = $script:SessionPassword })
    if ($r.Status -ne 200) { throw "login failed for ${Email}: $($r.Status)" }
    $client.DefaultRequestHeaders.Add('X-CSRF-Token', $r.Json.csrf_token)
    $client
}
function Cited($Run, [int]$SourceId) { @($Run.Last.citations | Where-Object { $_.source_id -eq "s$SourceId" -or $_.source_id -eq "source_$SourceId" }).Count -gt 0 }

$script:ApiUp = Wait-Api 60
if (-not $script:ApiUp) { Write-Host 'The api is not reachable: this run is NOT valid. Start Docker Desktop and the stack first.' -ForegroundColor Red }

# ============================ Baseline and migration ============================
Write-Host "`n== Migration and existing data"
Test-Check 'migration 0001 is applied and legacy data is quarantined (no members), nothing lost' {
    $mig = Invoke-Sql "select version from schema_migrations"
    $legacy = Invoke-Sql "select count(*) from org_members m join organizations o on o.id = m.org_id where o.kind = 'legacy'"
    $counts = Invoke-Sql "select (select count(*) from graphs) >= 71, (select count(*) from chunks where source_ref is null), (select count(*) from usage) >= 1106"
    if ($mig -match '0001' -and $legacy.Trim() -eq '0' -and $counts -eq 't|0|t') { $true } else { "migration '$mig', legacy members $legacy, counts $counts" }
}
Test-Check 'only /health is public: agents, sources, usage, status need a session' {
    $anon = New-SessionClient
    $codes = @('/graphs/1', '/sources/1', '/usage/totals', '/status', '/models', '/projects?org_id=1', '/conversations?project_id=1') | ForEach-Object { (Send-Json $anon 'GET' "$Api$_").Status }
    if (@($codes | Where-Object { $_ -ne 401 }).Count -eq 0 -and (Send-Json $anon 'GET' "$Api/health").Status -eq 200) { $true } else { "codes $($codes -join ',')" }
}

# ============================ Accounts, company, invitations ============================
Write-Host "`n== Accounts, company, invitations, projects"
$A = $null; $B = $null; $C = $null; $D = $null; $E = $null
$script:Org = 0; $script:Proj = 0; $script:Hr = 0
Test-Check 'five people register (A owner, B contributor, C consumer, D outsider, E other project)' {
    $script:A = New-TestSession 'owner'; $script:B = New-TestSession 'contributor'; $script:C = New-TestSession 'consumer'
    $script:D = New-TestSession 'outsider'; $script:E = New-TestSession 'otherproject'
    $true
}
$A = $script:A; $B = $script:B; $C = $script:C; $D = $script:D; $E = $script:E
Test-Check 'A creates the company and two projects' {
    $script:Org = (Call $A 'POST' '/orgs' @{ name = "Bharat Technologies $Stamp"; description = 'Phase 8 verification' }).Json.id
    $script:Proj = (Call $A 'POST' '/projects' @{ org_id = $script:Org; name = 'E-commerce Platform' }).Json.id
    $script:Hr = (Call $A 'POST' '/projects' @{ org_id = $script:Org; name = 'HR Knowledge Base' }).Json.id
    if ($script:Org -and $script:Proj -and $script:Hr) { $true } else { 'not created' }
}
Test-Check 'invitations: link accepted only by the invited account, then confirmed by the owner' {
    $bad = @()
    foreach ($p in @($B, $C, $E)) {
        $inv = Call $A 'POST' "/orgs/$($script:Org)/invitations" @{ email = $p.Email; role = 'member' }
        if ((Call $D 'POST' '/invitations/accept' @{ token = $inv.Json.token }).Status -ne 403) { $bad += "D could accept $($p.Name)'s invitation" }
        $acc = Call $p 'POST' '/invitations/accept' @{ token = $inv.Json.token }
        if ($acc.Json.status -ne 'pending') { $bad += "$($p.Name): $($acc.Text)" }
        if ((Call $p 'GET' "/orgs/$($script:Org)").Status -ne 404) { $bad += "$($p.Name) had access before confirmation" }
        if ((Call $p 'POST' '/invitations/accept' @{ token = $inv.Json.token }).Status -ne 409) { $bad += 'token worked twice' }
        if ((Call $A 'POST' "/orgs/$($script:Org)/members/$($p.UserId)/confirm").Status -ne 200) { $bad += 'confirm failed' }
    }
    if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
}
Test-Check 'project access: B contributor and C viewer on E-commerce, E contributor on HR only' {
    $codes = @((Call $A 'PUT' "/projects/$($script:Proj)/members/$($B.UserId)" @{ role = 'contributor' }).Status,
               (Call $A 'PUT' "/projects/$($script:Proj)/members/$($C.UserId)" @{ role = 'viewer' }).Status,
               (Call $A 'PUT' "/projects/$($script:Hr)/members/$($E.UserId)" @{ role = 'contributor' }).Status)
    if (($codes -join ',') -eq '200,200,200') { $true } else { "codes $($codes -join ',')" }
}

# ============================ Shared knowledge ============================
Write-Host "`n== B uploads knowledge once"
$script:Pdf = 0; $script:Repo = 0; $script:Url = 0
Test-Check 'B adds a PDF, a GitHub repo and a docs page; all ready, with B as uploader' {
    $script:Pdf = Add-ProjectFile $B 'samples\ecommerce-architecture.pdf' $script:Proj
    $script:Repo = Add-ProjectLink $B 'repo' 'https://github.com/octocat/Spoon-Knife' $script:Proj
    $script:Url = Add-ProjectLink $B 'url' 'https://example.com' $script:Proj
    $list = @((Call $C 'GET' "/projects/$($script:Proj)/sources").Json)
    $bad = @($list | Where-Object { $_.status -ne 'ready' -or $_.uploaded_by_name -ne $B.Name -or $_.chunk_count -lt 1 })
    if ($list.Count -eq 3 -and $bad.Count -eq 0) { $true } else { "listed $($list.Count), not ready or wrong uploader: $($bad.Count)" }
}
Test-Check 'the stored chunks have their vectors and belong to the project' {
    $row = Invoke-Sql "select count(*), count(embedding), count(distinct scope) from chunks where source_ref in ($($script:Pdf), $($script:Repo), $($script:Url))"
    $p = $row -split '\|'
    if ([int]$p[0] -ge 3 -and $p[0] -eq $p[1] -and $p[2] -eq '1') { $true } else { "rows $row" }
}
$script:Agent = 0
Test-Check 'A builds the shared agent from those sources without uploading anything again' {
    $before = Invoke-Sql "select count(*) from sources where project_id = $($script:Proj)"
    $g = New-AgentGraph -Model 'local' -Sources @{ s1 = $script:Pdf; s2 = $script:Repo; s3 = $script:Url } -Extra @(@{ id = 't1'; type = 'tool_calculator'; config = @{} })
    $script:Agent = Save-Agent $A 'E-commerce assistant' $g $script:Proj
    $after = Invoke-Sql "select count(*) from sources where project_id = $($script:Proj)"
    if ($script:Agent -and $before -eq $after -and $after.Trim() -eq '3') { $true } else { "sources $before -> $after" }
}

# ============================ C asks (real Qwen 3 14B) ============================
Write-Host "`n== C asks the shared agent (Qwen 3 14B, local only)"
$script:Conv = 0
function Ask-C([string]$Question, [string]$Language = $null) {
    $body = @{ question = $Question; conversation_id = $script:Conv; model = 'local'; local_only = $true; request_id = [guid]::NewGuid().ToString() }
    if ($Language) { $body.language = $Language }
    Ask $C $body
}
Test-Check 'C sees the three sources and opens a conversation with the shared agent' {
    $script:Conv = (Call $C 'POST' '/conversations' @{ project_id = $script:Proj; graph_id = $script:Agent; title = 'Onboarding questions' }).Json.id
    if ($script:Conv -and @((Call $C 'GET' "/projects/$($script:Proj)/sources").Json).Count -eq 3) { $true } else { 'no conversation' }
}
Test-Check 'PDF knowledge: free delivery threshold (499), cited from the PDF' {
    $r = Ask-C 'Above what order value is delivery free?'
    if ($r.Last.answer -match '499' -and (Cited $r 1) -and "$($r.Last.model)".StartsWith('local:')) { $true } else { "answer: $($r.Last.answer) model $($r.Last.model)" }
}
Test-Check 'GitHub knowledge: what the repository is for, cited from the repo (README.md)' {
    $r = Ask-C 'What is the Spoon-Knife repository meant for?'
    $files = @($r.Last.citations | ForEach-Object { $_.file })
    # The README says the repo is an example for forking and for opening a pull request; either is a faithful answer.
    if ($r.Last.answer -match 'fork|pull request' -and $files -contains 'README.md') { $true } else { "answer: $($r.Last.answer) files $($files -join ',')" }
}
Test-Check 'docs page knowledge: what example.com is for, cited from the page' {
    $r = Ask-C 'What is the example.com domain for?'
    if ($r.Last.answer -match 'example' -and @($r.Last.citations | Where-Object { $_.source_id -eq 's3' }).Count -gt 0) { $true } else { "answer: $($r.Last.answer)" }
}
Test-Check 'a follow-up question uses the conversation (pending payment window: 30 minutes)' {
    $null = Ask-C 'Which service talks to the payment gateway?'
    $r = Ask-C 'What happens to new orders if it is down?'
    if ($r.Last.answer -match '30') { $true } else { "answer: $($r.Last.answer)" }
}
Test-Check 'a question the knowledge does not answer is declined' {
    $r = Ask-C 'What is the salary of the CEO?'
    if (-not $r.Last.supported -and @($r.Last.citations).Count -eq 0) { $true } else { "answer: $($r.Last.answer)" }
}
Test-Check 'Telugu and Hindi questions about the English PDF are answered in those languages, cited' {
    $te = Ask-C ([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('4LCV4LC44LGN4LCf4LCu4LCw4LGN4LCy4LGBIOCwjuCwqOCxjeCwqOCwvyDgsLDgsYvgsJzgsYHgsLLgsY3gsLLgsYvgsJfgsL4g4LC14LC44LGN4LCk4LGB4LC14LGB4LCy4LCo4LGBIOCwpOCwv+CwsOCwv+Cwl+CwvyDgsIfgsLXgsY3gsLXgsLXgsJrgsY3gsJrgsYE/'))) 'te'
    $hi = Ask-C ([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('4KSo4KS/4KSw4KS+4KSw4KWN4KSlIOCkleCkv+CkpOCkqOClhyDgpKbgpL/gpKjgpYvgpIIg4KSu4KWH4KSCIOCksuCljOCkn+CkvuCkjyDgpJzgpL4g4KS44KSV4KSk4KWHIOCkueCliOCkgj8='))) 'hi'
    $ok = $te.Last.language -eq 'te' -and $te.Last.language_match -ne $false -and $te.Last.answer -match '10' -and
          $hi.Last.language -eq 'hi' -and $hi.Last.language_match -ne $false -and $hi.Last.answer -match '10'
    if ($ok) { $true } else { "te: $($te.Last.answer) | hi: $($hi.Last.answer)" }
}
Test-Check 'the conversation history is stored with the real model, citations and tokens' {
    $msgs = @((Call $C 'GET' "/conversations/$($script:Conv)").Json.messages)
    $answers = @($msgs | Where-Object { $_.role -eq 'assistant' -and $_.status -eq 'ok' })
    if ($msgs.Count -ge 14 -and $answers.Count -ge 7 -and "$($answers[0].model)".StartsWith('local:') -and $answers[0].input_tokens -gt 0) { $true }
    else { "messages $($msgs.Count), ok answers $($answers.Count)" }
}

# ============================ Private conversations, isolation ============================
Write-Host "`n== Private conversations and isolation"
$script:ConvB = 0
Test-Check 'B and C share the knowledge but not their conversations (owner A cannot read them either)' {
    $script:ConvB = (Call $B 'POST' '/conversations' @{ project_id = $script:Proj; graph_id = $script:Agent }).Json.id
    $codes = @((Call $B 'GET' "/conversations/$($script:Conv)").Status, (Call $C 'GET' "/conversations/$($script:ConvB)").Status,
               (Call $A 'GET' "/conversations/$($script:Conv)").Status)
    $listB = @((Call $B 'GET' "/conversations?project_id=$($script:Proj)").Json | ForEach-Object { $_.id })
    if (($codes -join ',') -eq '404,404,404' -and $listB -notcontains $script:Conv) { $true } else { "codes $($codes -join ',')" }
}
function Test-Outsider($S, [switch]$SameCompany) {
    $bad = @()
    # A colleague in the same company (E) may open the company and its member list (no emails, no project names,
    # counts only over E's own projects); an outsider (D) may not. Everything project-scoped is refused to both.
    $orgPaths = @("/orgs/$($script:Org)", "/orgs/$($script:Org)/members")
    if ($SameCompany) {
        $o = Call $S 'GET' "/orgs/$($script:Org)"
        $m = @((Call $S 'GET' "/orgs/$($script:Org)/members").Json)
        $leak = @($m | Where-Object { $_.user_id -ne $S.UserId -and ($_.email -or $_.projects) }).Count
        if ($o.Status -ne 200 -or $leak -gt 0 -or $o.Json.sources -gt 0) { $bad += "company view: HTTP $($o.Status), leaked $leak, sources $($o.Json.sources)" }
        $orgPaths = @()
    }
    $paths = $orgPaths + @("/projects/$($script:Proj)", "/projects/$($script:Proj)/sources",
                     "/graphs?project_id=$($script:Proj)", "/graphs/$($script:Agent)", "/sources/$($script:Pdf)", "/sources/$($script:Pdf)/file",
                     "/conversations/$($script:Conv)", "/conversations?project_id=$($script:Proj)", "/usage/recent?project_id=$($script:Proj)")
    foreach ($p in $paths) {
        $code = (Call $S 'GET' $p).Status
        if ($code -ne 404) { $bad += "$p=$code" }
    }
    foreach ($body in @(@{ question = 'delivery'; graph_id = $script:Agent }, @{ question = 'delivery'; project_id = $script:Proj },
                        @{ question = 'delivery'; conversation_id = $script:Conv },
                        @{ question = 'delivery'; project_id = $S.Project; graph = (New-AgentGraph -Sources @{ s1 = $script:Pdf }) },
                        @{ question = 'delivery'; graph_id = $script:Agent; graph = (New-AgentGraph -Sources @{ s1 = $script:Pdf }) })) {
        $code = (Call $S 'POST' '/pipeline/run?stream=false' $body).Status
        if ($code -ne 404 -and $code -ne 422) { $bad += "run=$code" }
    }
    $writes = @((Call $S 'DELETE' "/sources/$($script:Pdf)").Status, (Call $S 'DELETE' "/graphs/$($script:Agent)").Status,
                (Call $S 'POST' "/projects/$($script:Proj)/sources/url" @{ url = 'https://example.com' }).Status,
                (Call $S 'POST' '/graphs' @{ project_id = $S.Project; name = 'steal'; graph = (New-AgentGraph -Sources @{ s1 = $script:Pdf }) }).Status)
    if (($writes -join ',') -ne '404,404,404,422') { $bad += "writes $($writes -join ',')" }
    $bad
}
Test-Check 'outsider D (another workspace) is refused on every route, by direct ids and modified payloads' {
    $bad = Test-Outsider $D
    if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
}
Test-Check 'E (same company, other project) sees the company but is refused every E-commerce project route' {
    $bad = Test-Outsider $E -SameCompany
    if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
}
Test-Check 'removing C from the project takes effect at once, without signing C out' {
    $null = Call $A 'DELETE' "/projects/$($script:Proj)/members/$($C.UserId)"
    $me = (Call $C 'GET' '/auth/me').Status
    $codes = @("/projects/$($script:Proj)", "/projects/$($script:Proj)/sources", "/conversations/$($script:Conv)", "/sources/$($script:Pdf)/file", "/graphs/$($script:Agent)") | ForEach-Object { (Call $C 'GET' $_).Status }
    $run = (Call $C 'POST' '/pipeline/run?stream=false' @{ question = 'delivery'; conversation_id = $script:Conv }).Status
    $null = Call $A 'PUT' "/projects/$($script:Proj)/members/$($C.UserId)" @{ role = 'viewer' }
    $back = (Call $C 'GET' "/conversations/$($script:Conv)").Status
    if ($me -eq 200 -and @($codes | Where-Object { $_ -ne 404 }).Count -eq 0 -and $run -eq 404 -and $back -eq 200) { $true } else { "me $me, codes $($codes -join ','), run $run, back $back" }
}

# ============================ Other models ============================
Write-Host "`n== The shared agent on the other models"
Test-Check 'Groq GPT-OSS 120B: cited project answer, calculator tool, priced cost' {
    $r = Ask $C @{ question = 'My order is Rs. 450. How much more do I need to spend to get free delivery?'; graph_id = $script:Agent; model = 'groq' }
    if ($r.Last.fallback -and "$($r.Last.fallback.code)".StartsWith('quota')) { return "BLOCKED:$($r.Last.fallback.code)" }
    $tool = @($r.Last.tool_calls | Where-Object { $_.tool -eq 'calculator' -and $_.ok }).Count
    if ("$($r.Last.model)".StartsWith('groq:') -and $r.Last.supported -and $tool -ge 1 -and $r.Last.cost.usd -gt 0) { $true }
    else { "model $($r.Last.model), tools $tool, answer $($r.Last.answer)" }
}
Test-Check 'Qwen 2.5 3B (local backup): local answer from the project knowledge' {
    $r = Ask $C @{ question = 'Within how many days can customers return items?'; graph_id = $script:Agent; model = 'local_small'; local_only = $true }
    if ("$($r.Last.model)".StartsWith('local_small:') -and $r.Last.answer -match '10') { $true } else { "model $($r.Last.model): $($r.Last.answer)" }
}
Test-Check 'Gemini 2.5 Flash: a real request (BLOCKED if the free quota is used up)' {
    $r = Ask $C @{ question = 'Above what order value is delivery free?'; graph_id = $script:Agent; model = 'gemini' }
    if ($r.Last.fallback -and "$($r.Last.fallback.code)".StartsWith('quota')) { return "BLOCKED:$($r.Last.fallback.code) (answered by $($r.Last.model))" }
    if ("$($r.Last.model)".StartsWith('gemini:') -and $r.Last.answer -match '499') { $true } else { "model $($r.Last.model): $($r.Last.answer)" }
}
Test-Check 'usage rows belong to the signed-in person, project and agent; members see only their own' {
    $row = Invoke-Sql "select user_id || '|' || org_id || '|' || project_id || '|' || graph_id from usage order by id desc limit 1"
    # C asked every question in the company so far, so the owner's company-wide total equals C's; B (who only
    # uploaded) sees zero requests of their own, never C's.
    $mine = (Call $C 'GET' "/usage/totals?org_id=$($script:Org)").Json
    $bUse = (Call $B 'GET' "/usage/totals?org_id=$($script:Org)").Json
    $org = (Call $A 'GET' "/usage/totals?org_id=$($script:Org)").Json
    if ($row -eq "$($C.UserId)|$($script:Org)|$($script:Proj)|$($script:Agent)" -and -not $mine.everyone -and -not $bUse.everyone -and $org.everyone -and
        $mine.requests -gt 0 -and $bUse.requests -eq 0 -and $org.requests -ge $mine.requests) { $true }
    else { "row $row, C $($mine.requests) (everyone=$($mine.everyone)), B $($bUse.requests), org $($org.requests) (everyone=$($org.everyone))" }
}

# ============================ Local-only privacy (instrumented) ============================
Write-Host "`n== Local-only project (Gemini and Groq pointed at a counting fake server)"
$fake = $null
$pyExe = if ($env:JOURNEY_PYTHON) { $env:JOURNEY_PYTHON } else { 'python' }
try {
    $script:Secret = (Call $A 'POST' '/projects' @{ org_id = $script:Org; name = 'Confidential'; local_only = $true }).Json.id
    $secretPdf = Add-ProjectFile $A 'samples\ecommerce-architecture.pdf' $script:Secret
    $calc = @{ id = 't1'; type = 'tool_calculator'; config = @{} }
    $script:SecretAgent = Save-Agent $A 'Confidential agent' (New-AgentGraph -Model 'groq' -Sources @{ s1 = $secretPdf } -Extra @($calc)) $script:Secret
    Test-Check 'a local-only project refuses cloud models: agent setting, chosen model, project chat, unsaved graph' {
        $codes = @((Call $A 'POST' '/pipeline/run?stream=false' @{ question = 'delivery'; graph_id = $script:SecretAgent }).Status,
                   (Call $A 'POST' '/pipeline/run?stream=false' @{ question = 'delivery'; graph_id = $script:SecretAgent; model = 'gemini' }).Status,
                   (Call $A 'POST' '/pipeline/run?stream=false' @{ question = 'delivery'; project_id = $script:Secret; model = 'groq' }).Status,
                   (Call $A 'POST' '/pipeline/run?stream=false' @{ question = 'delivery'; project_id = $script:Secret; graph = (New-AgentGraph -Model 'groq' -Sources @{ s1 = $secretPdf }) }).Status)
        if (($codes -join ',') -eq '400,400,400,400') { $true } else { "codes $($codes -join ',')" }
    }
    if (-not (Get-Command $pyExe -ErrorAction SilentlyContinue)) { throw [System.OperationCanceledException]::new('Python is not installed') }
    $fake = Start-Process $pyExe -ArgumentList @('tests\fake_gemini.py', "$FakePort", '0.01') -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 2
    $cloud = @("GEMINI_BASE_URL: http://host.docker.internal:$FakePort", 'GEMINI_API_KEY: fake-key',
               "GROQ_BASE_URL: http://host.docker.internal:$FakePort", 'GROQ_API_KEY: fake-key')
    if (-not (Restart-Api $cloud)) { throw 'api did not come back with the fake cloud' }
    function Get-Count { $n = (Send-Json (New-SessionClient) 'GET' "http://127.0.0.1:$FakePort/_requests").Json.all; if ($null -eq $n) { 0 } else { [int]$n } }
    Test-Check 'control: a normal project request to Groq reaches the counting fake server' {
        $c0 = Get-Count
        $r = Ask $C @{ question = 'Above what order value is delivery free?'; graph_id = $script:Agent; model = 'groq' }
        if ((Get-Count) -gt $c0 -and "$($r.Last.model)".StartsWith('groq:')) { $true } else { "count $c0 -> $(Get-Count)" }
    }
    $c0 = Get-Count
    Test-Check 'local-only project: RAG and Calculator answers use only local models (plan and answer)' {
        $bad = @()
        foreach ($q in @('Above what order value is delivery free?', 'My order is Rs. 450. How much more do I need to spend to get free delivery?')) {
            $r = Ask $A @{ question = $q; graph_id = $script:SecretAgent; model = 'local' }
            $steps = @($r.Agent.generation.steps | ForEach-Object { $_.model })
            if (-not $r.Last.local_only -or @($steps | Where-Object { $_ -notlike 'local*' }).Count -gt 0 -or $steps.Count -eq 0) { $bad += "steps $($steps -join ',')" }
        }
        if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
    }
    if (-not (Restart-Api ($cloud + @('OLLAMA_CHAT_MODEL: not-pulled-model:14b', 'OLLAMA_BACKUP_MODEL: not-pulled-model:3b')))) { throw 'api did not come back' }
    Test-Check 'local-only project with both local models missing: a clear error, no cloud fallback' {
        $r = Ask $A @{ question = 'Above what order value is delivery free?'; graph_id = $script:SecretAgent; model = 'local' }
        $tried = @($r.Last.attempts | ForEach-Object { $_.model } | Sort-Object -Unique) -join ','
        if ($r.Last.type -eq 'error' -and $r.Last.code -eq 'all_providers_failed' -and $tried -eq 'local,local_small') { $true } else { "type $($r.Last.type) tried $tried" }
    }
    Test-Check 'the fake cloud received zero requests during the local-only project questions' {
        $n = Get-Count
        if ($n -eq $c0) { $true } else { "cloud got $($n - $c0) request(s)" }
    }
} catch [System.OperationCanceledException] { Write-Result 'SKIP' 'instrumented local-only checks' $_.Exception.Message }
catch { Write-Result 'FAIL' 'instrumented local-only checks could not run' $_.Exception.Message }
finally {
    if ($fake) { Stop-Process -Id $fake.Id -Force -ErrorAction SilentlyContinue }
    Write-Host 'Restoring the normal api...'
    if (Restart-Api $null) { Write-Host 'api restored.' } else { Write-Host 'api did not come back; run: docker compose up -d api' -ForegroundColor Red }
}

# ============================ Concurrency ============================
Write-Host "`n== Modest concurrency"
Test-Check '10 registrations and 5 project creations at the same moment: all distinct, nothing duplicated' {
    $tasks = @(1..10 | ForEach-Object {
        $client = New-SessionClient
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$Api/auth/register")
        $body = J @{ full_name = "Load $_"; email = "load$_-$Stamp@example.test"; password = $script:SessionPassword; confirm_password = $script:SessionPassword }
        $req.Content = [System.Net.Http.StringContent]::new($body, [System.Text.Encoding]::UTF8, 'application/json')
        $client.SendAsync($req) })
    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$tasks)
    $reg = @($tasks | Where-Object { [int]$_.Result.StatusCode -eq 201 }).Count
    $ptasks = @(1..5 | ForEach-Object {
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$Api/projects")
        $req.Content = [System.Net.Http.StringContent]::new((J @{ org_id = $script:Org; name = "Parallel $_" }), [System.Text.Encoding]::UTF8, 'application/json')
        $A.Http.SendAsync($req) })
    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$ptasks)
    $proj = @($ptasks | Where-Object { [int]$_.Result.StatusCode -eq 201 }).Count
    $dupes = Invoke-Sql "select count(*) from (select lower(email) from users where email is not null group by 1 having count(*) > 1) d"
    if ($reg -eq 10 -and $proj -eq 5 -and $dupes.Trim() -eq '0') { $true } else { "registrations $reg, projects $proj, duplicate emails $dupes" }
}
Test-Check '25 concurrent reads from three companies'' people: everyone gets only their own data' {
    $jobs = @()
    for ($i = 0; $i -lt 25; $i++) {
        $who = @($B, $C, $D, $E, $A)[$i % 5]
        $path = if ($who -eq $D) { "/projects/$($D.Project)/sources" } elseif ($who -eq $E) { "/projects/$($script:Hr)/sources" } else { "/projects/$($script:Proj)/sources" }
        $jobs += [pscustomobject]@{ Who = $who; Task = $who.Http.GetAsync("$Api$path") }
    }
    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($jobs | ForEach-Object { $_.Task }))
    $bad = @()
    foreach ($j in $jobs) {
        $text = $j.Task.Result.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        $parsed = ConvertFrom-Json $text  # PowerShell 5.1 returns a JSON array as one object; .Count is its length
        $count = @($parsed).Count; if ($parsed -is [array]) { $count = $parsed.Length }
        $want = if ($j.Who -eq $D -or $j.Who -eq $E) { 0 } else { 3 }
        if ([int]$j.Task.Result.StatusCode -ne 200 -or $count -ne $want) { $bad += "$($j.Who.Name): $([int]$j.Task.Result.StatusCode)/$count" }
    }
    if ($bad.Count -eq 0) { $true } else { $bad -join ' | ' }
}
Test-Check 'three people ask the shared agent at once (Qwen 2.5 3B): each conversation gets exactly its own answer' {
    $convs = @($A, $B, $C) | ForEach-Object { (Call $_ 'POST' '/conversations' @{ project_id = $script:Proj; graph_id = $script:Agent }).Json.id }
    $people = @($A, $B, $C)
    $tasks = @()
    for ($i = 0; $i -lt 3; $i++) {
        $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Post, "$Api/pipeline/run?stream=false")
        $body = J @{ question = 'Within how many days can customers return items?'; conversation_id = $convs[$i]; model = 'local_small'; local_only = $true }
        $req.Content = [System.Net.Http.StringContent]::new($body, [System.Text.Encoding]::UTF8, 'application/json')
        $tasks += $people[$i].Http.SendAsync($req)
    }
    [System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]$tasks)
    $counts = for ($i = 0; $i -lt 3; $i++) { @((Call $people[$i] 'GET' "/conversations/$($convs[$i])").Json.messages).Count }
    if (($counts -join ',') -eq '2,2,2') { $true } else { "messages per conversation $($counts -join ',')" }
}

# ============================ Persistence ============================
Write-Host "`n== Persistence across a full restart (volumes kept)"
Test-Check 'after the whole stack is taken down and up, people sign in again and continue without re-uploading' {
    $files = Get-ComposeFiles
    docker compose @files down 2>$null | Out-Null
    docker compose @files up -d 2>$null | Out-Null
    if (-not (Wait-Api 300)) { return 'api did not come back' }
    Start-Sleep -Seconds 5
    $c2 = Login $C.Email
    $SessC = [pscustomobject]@{ Http = $c2; Api = $Api }  # not $C2: PowerShell names ignore case, $C2 is $c2
    $sources = @((Send-Json $c2 'GET' "$Api/projects/$($script:Proj)/sources").Json | Where-Object { $_.status -eq 'ready' }).Count
    $msgs = @((Send-Json $c2 'GET' "$Api/conversations/$($script:Conv)").Json.messages).Count
    $agent = (Send-Json $c2 'GET' "$Api/graphs/$($script:Agent)").Status
    $usage = (Send-Json $c2 'GET' "$Api/usage/totals").Json.requests
    $r = Ask $SessC @{ question = 'Up to what order value is cash on delivery allowed?'; conversation_id = $script:Conv; model = 'local'; local_only = $true }
    if ($sources -eq 3 -and $msgs -ge 14 -and $agent -eq 200 -and $usage -ge 8 -and $r.Last.answer -match '5000' -and $r.Last.supported) { $true }
    else { "sources $sources, messages $msgs, agent $agent, usage $usage, answer $($r.Last.answer)" }
}
Test-Check 'memberships survive the restart; old sessions of signed-out people stay invalid' {
    $a2 = Login $A.Email
    $m = @((Send-Json $a2 'GET' "$Api/orgs/$($script:Org)/members").Json | Where-Object { $_.status -eq 'active' }).Count
    $null = Send-Json $B.Http 'POST' "$Api/auth/logout"
    $after = (Send-Json $B.Http 'GET' "$Api/auth/me").Status
    if ($m -eq 4 -and $after -eq 401) { $true } else { "active members $m, logged-out session $after" }
}

# ============================ Frontend ============================
Write-Host "`n== Frontend"
Test-Check 'web serves the Phase 8 app (landing, sign-in, workspaces, members, conversations)' {
    $html = (Invoke-WebRequest -UseBasicParsing "$Web/" -TimeoutSec 10).Content
    if ($html -notmatch 'src="(/assets/[^"]+\.js)"') { return 'no script tag' }
    $js = (Invoke-WebRequest -UseBasicParsing "$Web$($Matches[1])" -TimeoutSec 10).Content
    $missing = @('One intelligent workspace', 'Create your account', 'Sign in', 'Workspace', 'Invite a colleague', 'Your conversations',
                 'Project knowledge', 'Local-only project', 'X-CSRF-Token', 'react-flow') | Where-Object { $js -notmatch [regex]::Escape($_) }
    if ($missing.Count -eq 0) { $true } else { "missing: $($missing -join ', ')" }
}

$total = $script:Pass + $script:Fail + $script:Skip + $script:Blocked
Write-Host ''
Write-Host ("Result: {0} passed, {1} failed, {2} skipped, {3} blocked by quota (of {4}), company '{5}'" -f $script:Pass, $script:Fail, $script:Skip, $script:Blocked, $total, "Bharat Technologies $Stamp")
if (-not $script:ApiUp) { Write-Host 'INVALID RUN: the api was not reachable.' -ForegroundColor Red; exit 2 }
if ($script:Fail -eq 0 -and $script:Skip -eq 0 -and $script:Blocked -eq 0) { Write-Host 'Phase 8 verified.' -ForegroundColor Green; exit 0 }
Write-Host 'Phase 8 NOT fully verified.' -ForegroundColor Red; exit 1
