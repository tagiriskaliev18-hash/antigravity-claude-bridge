# Multi-model bridge installer (Windows): ai-bridge 3 with roles, consilium and the token ledger.
# Copies multimodel\ from this clone into C:\projects\tools, where Antigravity's claude-bridge already points.
#   powershell -ExecutionPolicy Bypass -File .\scripts\install-multimodel.ps1
# Options: -ToolsDir <path>  -NoRule (do not add the Antigravity rule file)
# Keys are not part of this repo: after installing run  python C:\projects\tools\claude_bridge.py --import-keys  (or --keys)
param(
    [string]$ToolsDir = "C:\projects\tools",
    [switch]$NoRule
)

$ErrorActionPreference = "Stop"
$src = Join-Path (Split-Path -Parent $PSScriptRoot) "multimodel"
if (-not (Test-Path (Join-Path $src "claude_bridge.py"))) { throw "Run this script from a clone of the repository: $src not found." }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

Write-Host "=== ai-bridge 3 installer ===" -ForegroundColor Cyan

Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow
$python = $null
$probe = "import os, sys; exe = os.path.join(sys.base_prefix, 'python.exe') if sys.prefix != sys.base_prefix else sys.executable; print(exe if sys.version_info >= (3, 8) else '')"
foreach ($cand in @("py", "python")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $exe = (& $cmd.Source -c $probe 2>$null | Select-Object -Last 1)
        if ($exe -and (Test-Path $exe)) { $python = $exe; break }
    } catch { }
}
if (-not $python) { throw "Python 3.8+ not found. Install it from https://www.python.org/downloads/ and run again." }
Write-Host "  Python: $python" -ForegroundColor Green

Write-Host "[2/5] Copying the bridge to $ToolsDir..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path (Join-Path $ToolsDir "skills") -Force | Out-Null
$bridge = Join-Path $ToolsDir "claude_bridge.py"
if ((Test-Path $bridge) -and -not (Select-String -Path $bridge -Pattern 'VERSION = "3\.' -Quiet)) {
    Copy-Item $bridge "$bridge.bak-$stamp"
    Write-Host "  previous claude_bridge.py saved as claude_bridge.py.bak-$stamp" -ForegroundColor Green
}
Copy-Item (Join-Path $src "claude_bridge.py") $bridge -Force
Write-Host "  claude_bridge.py (ai-bridge 3)" -ForegroundColor Green
foreach ($name in @("providers.json", "agents.json")) {
    $dst = Join-Path $ToolsDir $name
    if (Test-Path $dst) {
        # your copy may hold your own edits (balances, default pool): keep it, put the new one next to it
        $alt = $name -replace "\.json$", ".default.json"
        Copy-Item (Join-Path $src $name) (Join-Path $ToolsDir $alt) -Force
        Write-Host "  $name kept as is; the version from the repository is $alt" -ForegroundColor DarkYellow
    } else {
        Copy-Item (Join-Path $src $name) $dst
        Write-Host "  $name" -ForegroundColor Green
    }
}
Copy-Item (Join-Path $src "skills\*.md") (Join-Path $ToolsDir "skills") -Force
Write-Host "  skills\*.md" -ForegroundColor Green
$envFile = Join-Path $ToolsDir ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $src ".env.example") $envFile
    Write-Host "  .env created from the template (keys are empty)" -ForegroundColor Green
} else {
    Write-Host "  .env kept as is" -ForegroundColor Green
}
# the key file is readable only by this Windows user
$ErrorActionPreference = "Continue"
& icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(F)" | Out-Null
$ErrorActionPreference = "Stop"

Write-Host "[3/5] Antigravity rule..." -ForegroundColor Yellow
if ($NoRule) {
    Write-Host "  Skipped (-NoRule)." -ForegroundColor DarkYellow
} else {
    $rules = Join-Path $env:USERPROFILE ".gemini\config\rules"
    New-Item -ItemType Directory -Path $rules -Force | Out-Null
    Copy-Item (Join-Path $src "rules\multimodel.md") (Join-Path $rules "multimodel.md") -Force
    Write-Host "  $rules\multimodel.md" -ForegroundColor Green
}

Write-Host "[4/5] Self-check (offline)..." -ForegroundColor Yellow
$ErrorActionPreference = "Continue"
& $python $bridge --selftest
Write-Host ""
& $python $bridge --status
$ErrorActionPreference = "Stop"

Write-Host "[5/5] Done." -ForegroundColor Yellow
Write-Host "Next: move the keys your older bridges in this folder already use (names and lengths are printed, never values)," -ForegroundColor Cyan
Write-Host "enter any missing ones (input is hidden), then check every pool with one short request:" -ForegroundColor Cyan
Write-Host "  & `"$python`" `"$bridge`" --import-keys" -ForegroundColor White
Write-Host "  & `"$python`" `"$bridge`" --keys" -ForegroundColor White
Write-Host "  & `"$python`" `"$bridge`" --check" -ForegroundColor White
Write-Host "Then restart Antigravity so it starts the new bridge." -ForegroundColor Cyan
if (Test-Path "$bridge.bak-$stamp") {
    Write-Host "Roll back: Copy-Item `"$bridge.bak-$stamp`" `"$bridge`" -Force" -ForegroundColor DarkGray
}
