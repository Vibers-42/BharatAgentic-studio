# Runs every verification script in order against the running stack and prints one summary line each.
# PASS, FAIL, SKIP and BLOCKED stay separate in each script's own result line; this runner only collects them.
# Logs go to docs\results\verify-<time>\ (git-ignored output; they hold no secrets, passwords or tokens).
#
#   powershell -ExecutionPolicy Bypass -File scripts\verify_all.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\verify_all.ps1 -Only verify_phase8,verify_phase7_1
#
# Set $env:JOURNEY_PYTHON to the Python that has the test packages (websockets, httpx) if it is not on PATH.
# Windows PowerShell 5.1 compatible, ASCII only.

param([string[]]$Only = @())

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path $PSScriptRoot -Parent)

$all = @('verify_phase1', 'verify_phase2', 'verify_phase3', 'verify_phase4', 'verify_phase5', 'verify_models',
         'verify_phase6', 'verify_phase7', 'verify_phase7_1', 'verify_phase8')
# With -File, "-Only a,b" arrives as the single string "a,b": split it.
$Only = @($Only | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$run = if ($Only.Count -gt 0) { @($all | Where-Object { $Only -contains $_ }) } else { $all }
$unknown = @($Only | Where-Object { $all -notcontains $_ })
if ($unknown.Count -gt 0 -or $run.Count -eq 0) {
    Write-Host "Unknown or no scripts selected: $($unknown -join ', '). Choose from: $($all -join ', ')" -ForegroundColor Red
    exit 2
}

$dir = Join-Path 'docs\results' ("verify-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force $dir | Out-Null
$rows = @()
foreach ($s in $run) {
    $start = Get-Date
    Write-Host "== $s" -ForegroundColor Cyan
    # Piped through Out-File so the log is UTF-8 (a plain *> redirect writes UTF-16 in Windows PowerShell 5.1).
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\$s.ps1" *>&1 | Out-File -FilePath (Join-Path $dir "$s.log") -Encoding utf8
    $code = $LASTEXITCODE
    $summary = @(Get-Content (Join-Path $dir "$s.log") | Where-Object { $_ -match '^Result' }) | Select-Object -Last 1
    # A script that crashed before running any check must never count as passed.
    if ($code -eq 0 -and (-not $summary -or $summary -match '\(of 0\)')) { $code = 3; $summary = "$summary (no checks ran: counted as failed)" }
    $line = "{0,-16} exit={1} {2,6:n0}s  {3}" -f $s, $code, ((Get-Date) - $start).TotalSeconds, $summary
    $rows += $line
    $color = if ($code -eq 0) { 'Green' } elseif ($code -eq 2) { 'Yellow' } else { 'Red' }
    Write-Host $line -ForegroundColor $color
}
$rows | Out-File -FilePath (Join-Path $dir 'summary.txt') -Encoding ascii
Write-Host ''
Write-Host "Summary and logs: $dir"
$failed = @($rows | Where-Object { $_ -notmatch 'exit=0 ' }).Count
if ($failed -eq 0) { Write-Host 'Every script passed.' -ForegroundColor Green; exit 0 }
Write-Host "$failed script(s) did not fully pass (failures, skips or quota blocks); read their logs." -ForegroundColor Red
exit 1
