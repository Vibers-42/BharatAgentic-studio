# Runs every api test, including the database tests, against a separate "bharat_test" database in the running
# Postgres container (the app's own database is never touched), then the web tests. Cloud API keys are blanked,
# so no test can call Groq or Gemini. The stack must be running: docker compose up -d
#   powershell -ExecutionPolicy Bypass -File scripts\test-all.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$db = (docker compose ps -q db).Trim()
$api = (docker compose ps -q api).Trim()
if (-not $db -or -not $api) { Write-Error "Start the stack first: docker compose up -d"; exit 1 }
$image = (docker inspect --format "{{.Config.Image}}" $api).Trim()
$network = (docker inspect --format "{{range `$k, `$v := .NetworkSettings.Networks}}{{`$k}} {{end}}" $db).Trim().Split(" ")[0]

# Create the test database once (with pgvector).
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 1 FROM pg_database WHERE datname = ''bharat_test''" | grep -q 1 || createdb -U "$POSTGRES_USER" bharat_test; psql -q -U "$POSTGRES_USER" -d bharat_test -c "CREATE EXTENSION IF NOT EXISTS vector"'

Write-Host "== api tests (unit + database) =="
docker run --rm --network $network --env-file .env -v "${root}:/w" $image sh /w/scripts/test-all.sh
$apiExit = $LASTEXITCODE

Write-Host "== web tests =="
docker run --rm -v "${root}\web:/w" -w /w node:24-alpine sh -c "npm ci --silent >/dev/null 2>&1; npx vitest run"
$webExit = $LASTEXITCODE

if ($apiExit -ne 0 -or $webExit -ne 0) { Write-Host "Some tests failed." -ForegroundColor Red; exit 1 }
Write-Host "All tests passed." -ForegroundColor Green
