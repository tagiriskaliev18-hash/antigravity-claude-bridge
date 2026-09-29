# Installation script for Antigravity + Claude Bridge
param(
    [string]$ToolsDir = "C:\projects\tools"
)

$ErrorActionPreference = "Stop"

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  Antigravity + Claude Bridge Setup                  " -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
$geminiConfigDir = Join-Path $env:USERPROFILE ".gemini\config"

Write-Host "[1/4] Preparing directories..." -ForegroundColor Yellow
New-Item -ItemType Directory -Path "$geminiConfigDir\rules" -Force | Out-Null
New-Item -ItemType Directory -Path "$geminiConfigDir\skills\context_booster" -Force | Out-Null
New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null

Write-Host "[2/4] Deploying bridge tools..." -ForegroundColor Yellow
$sourceBridge = Join-Path $repoRoot "tools\claude_bridge.py"
$targetBridge = Join-Path $ToolsDir "claude_bridge.py"
Copy-Item $sourceBridge -Destination $targetBridge -Force
Write-Host "  Deployed claude_bridge to $targetBridge" -ForegroundColor Green

$sourceMulti = Join-Path $repoRoot "tools\multillm_bridge.py"
$targetMulti = Join-Path $ToolsDir "multillm_bridge.py"
if (Test-Path $sourceMulti) {
    Copy-Item $sourceMulti -Destination $targetMulti -Force
    Write-Host "  Deployed multillm_bridge to $targetMulti" -ForegroundColor Green
}

Write-Host "[3/4] Deploying global Antigravity rules and skills..." -ForegroundColor Yellow
Copy-Item (Join-Path $repoRoot "config\GEMINI.md") -Destination "$geminiConfigDir\GEMINI.md" -Force
Copy-Item (Join-Path $repoRoot "config\rules\claude_bridge.md") -Destination "$geminiConfigDir\rules\claude_bridge.md" -Force
Copy-Item (Join-Path $repoRoot "config\skills\context_booster\SKILL.md") -Destination "$geminiConfigDir\skills\context_booster\SKILL.md" -Force

$escapedToolsPath = ($targetBridge -replace '\\', '\\')
$escapedMultiPath = ($targetMulti -replace '\\', '\\')
$mcpConfigJson = @"
{
  "mcpServers": {
    "claude-bridge": {
      "command": "python",
      "args": [
        "$escapedToolsPath"
      ],
      "env": {
        "CLAUDE_BRIDGE_MODEL": "opus"
      }
    },
    "multillm-bridge": {
      "command": "python",
      "args": [
        "$escapedMultiPath"
      ],
      "env": {
        "MULTILLM_DEFAULT_MODEL": "deepseek-v4-pro"
      }
    }
  }
}
"@
Set-Content -Path "$geminiConfigDir\mcp_config.json" -Value $mcpConfigJson -Encoding utf8
Write-Host "  Configured $geminiConfigDir\mcp_config.json" -ForegroundColor Green

Write-Host "[4/4] Verifying prerequisites..." -ForegroundColor Yellow
$pythonCheck = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCheck) {
    Write-Host "  [OK] Python found: $($pythonCheck.Source)" -ForegroundColor Green
} else {
    Write-Host "  [!] Python not found in PATH. Please install Python 3.9+." -ForegroundColor Red
}

$claudePath = "$env:USERPROFILE\.local\bin\claude.exe"
if (Test-Path $claudePath) {
    Write-Host "  [OK] Claude CLI found at $claudePath" -ForegroundColor Green
} else {
    $claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
    if ($claudeCmd) {
        Write-Host "  [OK] Claude CLI found in PATH: $($claudeCmd.Source)" -ForegroundColor Green
    } else {
        Write-Host "  [!] Claude CLI not found yet. Install Claude Code (npm i -g @anthropic-ai/claude-code) and run 'claude' once to authenticate." -ForegroundColor Yellow
    }
}

Write-Host "`nSetup completed successfully!" -ForegroundColor Green
Write-Host "Settings applied:" -ForegroundColor Cyan
Write-Host "  - Lazy review: triggered only for >3 files or >150 lines" -ForegroundColor Cyan
Write-Host "  - Parallel execution: subagents decomposed via define_subagent & invoke_subagent" -ForegroundColor Cyan
Write-Host "  - Iteration Cap: max 8 iterations per subtask" -ForegroundColor Cyan
Write-Host "  - Context Booster: enabled" -ForegroundColor Cyan
