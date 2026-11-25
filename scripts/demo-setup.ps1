# One-click demo: a demo account, the project "BharatAgent demo", its documents and three saved agents.
# Safe to run again (it skips what already exists). The stack must be running: docker compose up -d
#   powershell -ExecutionPolicy Bypass -File scripts\demo-setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$api = (docker compose ps -q api).Trim()
if (-not $api) { Write-Error "The api container is not running. Start the stack first: docker compose up -d"; exit 1 }
$image = (docker inspect --format "{{.Config.Image}}" $api).Trim()

# Run the setup inside the api image, on the api container's own network (the api is then at localhost:8000).
docker run --rm --network "container:$api" -v "${root}:/w" $image python -I /w/scripts/demo_setup.py
exit $LASTEXITCODE
