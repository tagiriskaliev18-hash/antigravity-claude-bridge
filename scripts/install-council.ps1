# One-file installer: Multi-LLM council + Claude bridge for Antigravity and Claude Code.
# Downloads everything from GitHub, so it can be run on any PC without cloning:
#   irm https://raw.githubusercontent.com/tagiriskaliev18-hash/antigravity-claude-bridge/main/scripts/install-council.ps1 | iex
# New API keys: set them as environment variables before running (or pass as parameters):
#   $env:OCTAVAPI_API_KEY="sk-..."; $env:XYVERO_API_KEY="sk-..."; $env:MULTILLM_API_KEY="sk-..."
param(
    [string]$Branch = $(if ($env:COUNCIL_BRANCH) { $env:COUNCIL_BRANCH } else { "main" }),
    [string]$ToolsDir = "C:\projects\tools",
    [string]$XyveroKey = $env:XYVERO_API_KEY,
    [string]$OctavKey = $env:OCTAVAPI_API_KEY,
    [string]$CheapVibeKey = $env:MULTILLM_API_KEY,
    [string]$ExtraProviders = $env:MULTILLM_EXTRA_PROVIDERS
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$raw = "https://raw.githubusercontent.com/tagiriskaliev18-hash/antigravity-claude-bridge/$Branch"
$geminiDir = Join-Path $env:USERPROFILE ".gemini\config"
$claudeDir = Join-Path $env:USERPROFILE ".claude"
$utf8 = New-Object System.Text.UTF8Encoding($false)

Write-Host "=== Multi-LLM Council installer (branch: $Branch) ===" -ForegroundColor Cyan

Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { $python = (Get-Command py -ErrorAction SilentlyContinue) }
if (-not $python) { throw "Python 3.9+ not found in PATH. Install it from https://www.python.org/downloads/ (tick 'Add to PATH') and run again." }
$pythonExe = $python.Source
Write-Host "  Python: $pythonExe" -ForegroundColor Green

Write-Host "[2/5] Downloading files..." -ForegroundColor Yellow
$files = @{
    "tools/multillm_bridge.py"              = (Join-Path $ToolsDir "multillm_bridge.py")
    "tools/claude_bridge.py"                = (Join-Path $ToolsDir "claude_bridge.py")
    "config/GEMINI.md"                      = (Join-Path $geminiDir "GEMINI.md")
    "config/rules/claude_bridge.md"         = (Join-Path $geminiDir "rules\claude_bridge.md")
    "config/skills/context_booster/SKILL.md" = (Join-Path $geminiDir "skills\context_booster\SKILL.md")
    "config/CLAUDE.md"                      = (Join-Path $claudeDir "CLAUDE.md")
    "config/COUNCIL_PROMPT.md"              = (Join-Path $ToolsDir "COUNCIL_PROMPT.md")
}
# Only multillm_bridge.py and the council prompt are ours to replace. Everything else (claude_bridge.py,
# GEMINI.md, CLAUDE.md, rules, skills) may belong to a newer setup such as ai-bridge / Council Engine,
# so it is installed only when missing and never overwritten.
$alwaysUpdate = @("tools/multillm_bridge.py", "config/COUNCIL_PROMPT.md")
foreach ($src in $files.Keys) {
    $dst = $files[$src]
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    if ((Test-Path $dst) -and ($alwaysUpdate -notcontains $src)) {
        Write-Host "  $dst already exists, kept as is" -ForegroundColor DarkGray
        continue
    }
    if (Test-Path $dst) { Copy-Item $dst "$dst.bak" -Force }
    Invoke-WebRequest -UseBasicParsing -Uri "$raw/$src" -OutFile $dst
    Write-Host "  $src -> $dst" -ForegroundColor Green
}
$multiPath = $files["tools/multillm_bridge.py"]
$bridgePath = $files["tools/claude_bridge.py"]

$keyEnv = [ordered]@{ "MULTILLM_DEFAULT_MODEL" = "deepseek-v4-pro" }
if ($XyveroKey) { $keyEnv["XYVERO_API_KEY"] = $XyveroKey }
if ($OctavKey) { $keyEnv["OCTAVAPI_API_KEY"] = $OctavKey }
if ($CheapVibeKey) { $keyEnv["MULTILLM_API_KEY"] = $CheapVibeKey }
if ($ExtraProviders) { $keyEnv["MULTILLM_EXTRA_PROVIDERS"] = $ExtraProviders }

Write-Host "[3/5] Configuring Antigravity (mcp_config.json)..." -ForegroundColor Yellow
$mcpFile = Join-Path $geminiDir "mcp_config.json"
$cfg = $null
if (Test-Path $mcpFile) {
    try { $cfg = Get-Content $mcpFile -Raw | ConvertFrom-Json } catch { $cfg = $null }
    Copy-Item $mcpFile "$mcpFile.bak" -Force
}
if (-not $cfg) { $cfg = New-Object PSObject }
if (-not $cfg.PSObject.Properties["mcpServers"]) { $cfg | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object PSObject) }
$servers = $cfg.mcpServers
if (-not $servers.PSObject.Properties["claude-bridge"]) {
    $servers | Add-Member -NotePropertyName "claude-bridge" -NotePropertyValue ([ordered]@{
        command = $pythonExe; args = @($bridgePath); env = [ordered]@{ CLAUDE_BRIDGE_MODEL = "opus" }
    })
}
$servers | Add-Member -Force -NotePropertyName "multillm-bridge" -NotePropertyValue ([ordered]@{
    command = $pythonExe; args = @($multiPath); env = $keyEnv
})
[IO.File]::WriteAllText($mcpFile, ($cfg | ConvertTo-Json -Depth 10), $utf8)
Write-Host "  Updated multillm-bridge in $mcpFile (existing claude-bridge and other servers kept, backup: mcp_config.json.bak)" -ForegroundColor Green

Write-Host "[4/5] Configuring Claude Code..." -ForegroundColor Yellow
# Native commands write to stderr; with "Stop" Windows PowerShell 5.1 would treat that as a fatal error
$ErrorActionPreference = "Continue"
$claude = Get-Command claude -ErrorAction SilentlyContinue
if (-not $claude -and (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")) { $claude = Get-Command "$env:USERPROFILE\.local\bin\claude.exe" }
if ($claude) {
    & $claude.Source mcp remove -s user multillm-bridge 2>&1 | Out-Null
    $addArgs = @("mcp", "add", "-s", "user")
    foreach ($k in $keyEnv.Keys) { $addArgs += @("-e", "$k=$($keyEnv[$k])") }
    $addArgs += @("multillm-bridge", "--", $pythonExe, $multiPath)
    & $claude.Source @addArgs 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  multillm-bridge registered in Claude Code (user scope)" -ForegroundColor Green
    } else {
        Write-Host "  [!] 'claude mcp add' failed. Run manually: claude mcp add -s user multillm-bridge -- `"$pythonExe`" `"$multiPath`"" -ForegroundColor Red
    }
} else {
    Write-Host "  [!] Claude Code CLI not found, skipped. Install: npm i -g @anthropic-ai/claude-code" -ForegroundColor Red
}

Write-Host "[5/5] Self-test..." -ForegroundColor Yellow
$probe = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
$out = $probe | & $pythonExe $multiPath 2>$null
if ("$out" -match "multillm_council") {
    Write-Host "  OK: multillm_council is available" -ForegroundColor Green
} else {
    Write-Host "  [!] Server did not list multillm_council. Output: $out" -ForegroundColor Red
}

Write-Host "`nDone. Restart Antigravity and open a new Claude Code chat." -ForegroundColor Cyan
Write-Host "Prompt to paste into any chat: $($files['config/COUNCIL_PROMPT.md'])" -ForegroundColor Cyan
