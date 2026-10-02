# Multi-model bridge installer (Windows): ai-bridge 4 with roles, a two-round consilium, pool pauses, budgets and the token ledger.
# Copies multimodel\ from this clone into C:\projects\tools, where Antigravity's claude-bridge already points.
#   powershell -ExecutionPolicy Bypass -File .\scripts\install-multimodel.ps1
# Options: -ToolsDir <path>  -NoRule (do not add the Antigravity rule file)  -KeepOldBridge (leave multillm-bridge connected)
#          -NoPlugin (do not install the multimodel plugin into Claude Code)
# Keys are not part of this repo: after installing run  python C:\projects\tools\claude_bridge.py --import-keys  (or --keys)
param(
    [string]$ToolsDir = "C:\projects\tools",
    [switch]$NoRule,
    [switch]$KeepOldBridge,
    [switch]$NoPlugin
)

$ErrorActionPreference = "Stop"
$src = Join-Path (Split-Path -Parent $PSScriptRoot) "multimodel"
if (-not (Test-Path (Join-Path $src "claude_bridge.py"))) { throw "Run this script from a clone of the repository: $src not found." }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

Write-Host "=== ai-bridge 4 installer ===" -ForegroundColor Cyan

Write-Host "[1/7] Checking Python..." -ForegroundColor Yellow
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

Write-Host "[2/7] Copying the bridge to $ToolsDir..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path (Join-Path $ToolsDir "skills") -Force | Out-Null
$bridge = Join-Path $ToolsDir "claude_bridge.py"
if ((Test-Path $bridge) -and -not (Select-String -Path $bridge -Pattern 'VERSION = "[34]\.' -Quiet)) {
    Copy-Item $bridge "$bridge.bak-$stamp"
    Write-Host "  previous claude_bridge.py saved as claude_bridge.py.bak-$stamp" -ForegroundColor Green
}
Copy-Item (Join-Path $src "claude_bridge.py") $bridge -Force
Write-Host "  claude_bridge.py (ai-bridge 4)" -ForegroundColor Green
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

Write-Host "[3/7] Antigravity rule..." -ForegroundColor Yellow
if ($NoRule) {
    Write-Host "  Skipped (-NoRule)." -ForegroundColor DarkYellow
} else {
    $rules = Join-Path $env:USERPROFILE ".gemini\config\rules"
    New-Item -ItemType Directory -Path $rules -Force | Out-Null
    Copy-Item (Join-Path $src "rules\multimodel.md") (Join-Path $rules "multimodel.md") -Force
    Write-Host "  $rules\multimodel.md" -ForegroundColor Green
}

Write-Host "[4/7] Old multillm-bridge..." -ForegroundColor Yellow
if ($KeepOldBridge) {
    Write-Host "  Kept (-KeepOldBridge)." -ForegroundColor DarkYellow
} else {
    # its keys are written in its code; ai-bridge 4 does everything it did. Backups are made, the file is renamed, not deleted.
    $ErrorActionPreference = "Continue"
    & $python $bridge --retire-old
    $ErrorActionPreference = "Stop"
}

Write-Host "[5/7] Claude Code plugin multimodel..." -ForegroundColor Yellow
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = Get-Item "$env:USERPROFILE\.local\bin\claude.exe" }
if ($NoPlugin) {
    Write-Host "  Skipped (-NoPlugin)." -ForegroundColor DarkYellow
} elseif (-not $claude) {
    Write-Host "  Claude Code CLI not found: skipped." -ForegroundColor DarkYellow
} else {
    $claudeExe = if ($claude.Source) { $claude.Source } else { $claude.FullName }
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $ErrorActionPreference = "Continue"
    $servers = (& $claudeExe mcp list 2>&1 | Out-String)
    $ErrorActionPreference = "Stop"
}
if (-not $NoPlugin -and $claude -and $servers -match "(?m)^claude-bridge:") {
    # the bridge is already registered in Claude Code by hand; the plugin would start a second copy of it
    Write-Host "  claude-bridge is already connected to Claude Code (claude mcp list): plugin skipped." -ForegroundColor Green
} elseif (-not $NoPlugin -and $claude) {
    $ErrorActionPreference = "Continue"
    # the marketplace is this clone: /multimodel:consilium, :auto, :ask-model, :models and the ai-bridge MCP server
    & $claudeExe plugin marketplace add $repoRoot 2>&1 | Out-Null
    & $claudeExe plugin marketplace update council-engine 2>&1 | Out-Null
    & $claudeExe plugin install multimodel@council-engine 2>&1 | Out-Null
    & $claudeExe plugin update multimodel@council-engine 2>&1 | Out-Null
    $ErrorActionPreference = "Stop"
    $listed = (& $claudeExe plugin list 2>&1 | Out-String)
    if ($listed -match "multimodel") {
        Write-Host "  multimodel installed: /multimodel:consilium, /multimodel:auto, /multimodel:ask-model, /multimodel:models" -ForegroundColor Green
    } else {
        Write-Host "  Could not confirm the plugin. In Claude Code run: /plugin marketplace add $repoRoot  then  /plugin install multimodel@council-engine" -ForegroundColor DarkYellow
    }
}

Write-Host "[6/7] Self-check (offline)..." -ForegroundColor Yellow
$ErrorActionPreference = "Continue"
& $python $bridge --selftest
Write-Host ""
& $python $bridge --status
$ErrorActionPreference = "Stop"

Write-Host "[7/7] Done." -ForegroundColor Yellow
Write-Host "Next: move the keys your older bridges in this folder already use (names and lengths are printed, never values)," -ForegroundColor Cyan
Write-Host "enter any missing ones (input is hidden), then check every pool with one short request:" -ForegroundColor Cyan
Write-Host "  & `"$python`" `"$bridge`" --import-keys" -ForegroundColor White
Write-Host "  & `"$python`" `"$bridge`" --keys" -ForegroundColor White
Write-Host "  & `"$python`" `"$bridge`" --check" -ForegroundColor White
Write-Host "Then restart Antigravity and Claude Code so they start the new bridge." -ForegroundColor Cyan
if (Test-Path "$bridge.bak-$stamp") {
    Write-Host "Roll back: Copy-Item `"$bridge.bak-$stamp`" `"$bridge`" -Force" -ForegroundColor DarkGray
}
