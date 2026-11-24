<#
Shared helpers for the verification scripts since Phase 8 (dot-source it: . "$PSScriptRoot\_session.ps1").
Every script signs in like a real user: it registers a fresh test account through POST /auth/register, keeps the
session cookie in its own cookie container, and sends the CSRF token on every state-changing request. There is no
test-only bypass in the api. Works in Windows PowerShell 5.1 and PowerShell 7. ASCII only.

  $s = New-TestSession 'p3'            # a new account with its personal workspace and first project
  $s.Http                               # an HttpClient that carries this account's session
  $s.Project                            # the id of the account's first project
  $id = Add-ProjectFile $s 'samples\refund-policy.pdf'    # a ready source id in that project
  $g = New-AgentGraph -Model 'groq' -Sources @{ s1 = $id } # a graph whose source node uses that source
#>

Add-Type -AssemblyName System.Net.Http

$script:SessionPassword = 'Verify-pass-' + (Get-Random -Minimum 100000 -Maximum 999999)

function New-SessionClient {
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.CookieContainer = New-Object System.Net.CookieContainer
    $handler.UseCookies = $true
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(900)
    $client
}

function Send-Json($Client, [string]$Method, [string]$Url, [string]$Body = $null) {
    $req = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::new($Method), $Url)
    if ($Body) {
        $req.Content = [System.Net.Http.ByteArrayContent]::new([System.Text.Encoding]::UTF8.GetBytes($Body))
        $req.Content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/json; charset=utf-8')
    }
    $res = $Client.SendAsync($req).GetAwaiter().GetResult()
    $text = [System.Text.Encoding]::UTF8.GetString($res.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
    $json = $null
    try { $json = $text | ConvertFrom-Json } catch { }
    [pscustomobject]@{ Status = [int]$res.StatusCode; Text = $text; Json = $json }
}

function New-TestSession([string]$Label, [string]$Api = 'http://127.0.0.1:8000', [string]$Email = '') {
    $client = New-SessionClient
    if (-not $Email) { $Email = '{0}-{1}@example.test' -f $Label.ToLower(), ([guid]::NewGuid().ToString('N').Substring(0, 8)) }
    $body = @{ full_name = "Verify $Label"; email = $Email; password = $script:SessionPassword; confirm_password = $script:SessionPassword } | ConvertTo-Json -Compress
    $r = Send-Json $client 'POST' "$Api/auth/register" $body
    if ($r.Status -ne 201) { throw "could not register a test account ($($r.Status)): $($r.Text)" }
    $client.DefaultRequestHeaders.Add('X-CSRF-Token', $r.Json.csrf_token)
    $personal = ($r.Json.workspaces | Where-Object { $_.kind -eq 'personal' }).id
    $project = @((Send-Json $client 'GET' "$Api/projects?org_id=$personal").Json)[0].id
    [pscustomobject]@{ Http = $client; Email = $Email; UserId = $r.Json.user.id; Name = "Verify $Label"; Personal = $personal
                       Project = $project; Api = $Api }
}

function Add-ProjectFile($Session, [string]$Path, [int]$ProjectId = 0, [string]$Name = '') {
    if (-not $ProjectId) { $ProjectId = $Session.Project }
    if (-not $Name) { $Name = Split-Path $Path -Leaf }
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $file = [System.Net.Http.ByteArrayContent]::new([System.IO.File]::ReadAllBytes((Resolve-Path $Path)))
    $file.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/pdf')
    $form.Add($file, 'file', $Name)
    $res = $Session.Http.PostAsync("$($Session.Api)/projects/$ProjectId/sources/file", $form).GetAwaiter().GetResult()
    $text = $res.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    if ([int]$res.StatusCode -ne 201) { throw "upload of $Name failed ($([int]$res.StatusCode)): $text" }
    ($text | ConvertFrom-Json).id
}

function Add-ProjectLink($Session, [string]$Kind, [string]$Url, [int]$ProjectId = 0) {
    if (-not $ProjectId) { $ProjectId = $Session.Project }
    $r = Send-Json $Session.Http 'POST' "$($Session.Api)/projects/$ProjectId/sources/$Kind" (@{ url = $Url } | ConvertTo-Json -Compress)
    if ($r.Status -ne 201) { throw "adding $Url failed ($($r.Status)): $($r.Text)" }
    $r.Json.id
}

# The five main blocks plus source nodes (node id -> registry source id) and extra nodes (tools), all into n4.
function New-AgentGraph([string]$Model = 'local', [hashtable]$Sources = @{}, [object[]]$Extra = @(), [string]$Language = 'en',
                        [bool]$LocalOnly = $false) {
    $main = @('input', 'guardrails', 'router', 'agent', 'output')
    $nodes = @()
    for ($i = 0; $i -lt 5; $i++) {
        $config = @{}
        if ($main[$i] -eq 'agent') { $config = @{ model = $Model; language = $Language; local_only = $LocalOnly } }
        if ($main[$i] -eq 'guardrails') { $config = @{ block = @('empty', 'abuse', 'off_topic') } }
        $nodes += @{ id = "n$($i + 1)"; type = $main[$i]; config = $config; position = @{ x = 300; y = 140 * $i } }
    }
    $edges = @(@{ from = 'n1'; to = 'n2' }, @{ from = 'n2'; to = 'n3' }, @{ from = 'n3'; to = 'n4' }, @{ from = 'n4'; to = 'n5' })
    foreach ($k in ($Sources.Keys | Sort-Object)) {
        $nodes += @{ id = $k; type = 'source_doc'; config = @{ source_ref = $Sources[$k] } }
        $edges += @{ from = $k; to = 'n4' }
    }
    foreach ($n in $Extra) { $nodes += $n; $edges += @{ from = $n.id; to = 'n4' } }
    @{ nodes = $nodes; edges = $edges }
}

function Save-Agent($Session, [string]$Name, $Graph, [int]$ProjectId = 0) {
    if (-not $ProjectId) { $ProjectId = $Session.Project }
    $r = Send-Json $Session.Http 'POST' "$($Session.Api)/graphs" (@{ project_id = $ProjectId; name = $Name; graph = $Graph } | ConvertTo-Json -Depth 12 -Compress)
    if ($r.Status -ne 201) { throw "saving agent $Name failed ($($r.Status)): $($r.Text)" }
    $r.Json.id
}
