# Council Engine desktop app installer (Windows).
# Installs the app to %LOCALAPPDATA%\CouncilEngine, routes the MCP bridges through the call log,
# creates Desktop and Start Menu shortcuts and starts the app.
#   From a clone:   powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1
#   Without clone:  irm https://raw.githubusercontent.com/tagiriskaliev18-hash/antigravity-claude-bridge/main/scripts/install-desktop.ps1 | iex
# Options: -Branch <name>  -NoWire (do not touch MCP configs)  -NoLaunch
param(
    [string]$Branch = $(if ($env:COUNCIL_BRANCH) { $env:COUNCIL_BRANCH } else { "main" }),
    [switch]$NoWire,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repoRaw = "https://raw.githubusercontent.com/tagiriskaliev18-hash/antigravity-claude-bridge/$Branch"
$appDir = Join-Path $env:LOCALAPPDATA "CouncilEngine"
$files = @("desktop/council_app.py", "desktop/council_tap.py", "desktop/ui/index.html", "desktop/ui/classic.html", "desktop/council.ico",
    # three.js for the 3D map and the fonts are shipped with the app, so it works offline
    "desktop/ui/vendor/three.module.min.js", "desktop/ui/vendor/THREE-LICENSE",
    "desktop/ui/vendor/addons/controls/OrbitControls.js", "desktop/ui/vendor/addons/renderers/CSS2DRenderer.js",
    "desktop/ui/vendor/addons/postprocessing/EffectComposer.js", "desktop/ui/vendor/addons/postprocessing/RenderPass.js",
    "desktop/ui/vendor/addons/postprocessing/UnrealBloomPass.js", "desktop/ui/vendor/addons/postprocessing/OutputPass.js",
    "desktop/ui/vendor/addons/postprocessing/ShaderPass.js", "desktop/ui/vendor/addons/postprocessing/MaskPass.js",
    "desktop/ui/vendor/addons/postprocessing/Pass.js", "desktop/ui/vendor/addons/shaders/CopyShader.js",
    "desktop/ui/vendor/addons/shaders/LuminosityHighPassShader.js", "desktop/ui/vendor/addons/shaders/OutputShader.js",
    "desktop/ui/vendor/addons/loaders/SVGLoader.js", "desktop/ui/vendor/lobe-icons/logos.js", "desktop/ui/vendor/lobe-icons/LICENSE",
    "desktop/ui/fonts/geologica-cyrillic-full-normal.woff2", "desktop/ui/fonts/geologica-latin-full-normal.woff2",
    "desktop/ui/fonts/martian-mono-cyrillic-standard-normal.woff2", "desktop/ui/fonts/martian-mono-latin-standard-normal.woff2",
    "desktop/ui/fonts/OFL-Geologica.txt", "desktop/ui/fonts/OFL-MartianMono.txt",
    "desktop/ui/fonts/inter-cyrillic-wght-normal.woff2", "desktop/ui/fonts/inter-latin-wght-normal.woff2",
    "desktop/ui/fonts/jetbrains-mono-cyrillic-wght-normal.woff2", "desktop/ui/fonts/jetbrains-mono-latin-wght-normal.woff2",
    "desktop/ui/fonts/OFL-Inter.txt", "desktop/ui/fonts/OFL-JetBrainsMono.txt")

Write-Host "=== Council Engine installer ===" -ForegroundColor Cyan

Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow
$python = $null
# a python from a project venv can disappear with that project, so always resolve to its base interpreter
$probe = "import os, sys; exe = os.path.join(sys.base_prefix, 'python.exe') if sys.prefix != sys.base_prefix else sys.executable; print(exe if sys.version_info >= (3, 8) else '')"
foreach ($cand in @("py", "python")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $exe = (& $cmd.Source -c $probe 2>$null | Select-Object -Last 1)
        if ($exe -and (Test-Path $exe)) { $python = $exe; break }
    } catch { }
}
if (-not $python) { throw "Python 3.8+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH') and run again." }
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) { $pythonw = $python }
Write-Host "  Python: $python" -ForegroundColor Green

Write-Host "[2/5] Copying app files to $appDir..." -ForegroundColor Yellow
# stop a running copy so files can be replaced
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { $_.CommandLine -like "*council_app.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path (Join-Path $appDir "ui") -Force | Out-Null
$localRoot = $null
if ($PSScriptRoot) {
    $cand = Split-Path -Parent $PSScriptRoot
    if (Test-Path (Join-Path $cand "desktop\council_app.py")) { $localRoot = $cand }
}
foreach ($f in $files) {
    $dst = Join-Path $appDir ($f.Substring("desktop/".Length) -replace "/", "\")
    New-Item -ItemType Directory -Path (Split-Path -Parent $dst) -Force | Out-Null
    if ($localRoot) {
        Copy-Item (Join-Path $localRoot ($f -replace "/", "\")) $dst -Force
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri "$repoRaw/$f" -OutFile $dst
    }
    Write-Host "  $f -> $dst" -ForegroundColor Green
}
$app = Join-Path $appDir "council_app.py"

Write-Host "[3/5] Connecting the call log to the MCP bridges..." -ForegroundColor Yellow
if ($NoWire) {
    Write-Host "  Skipped (-NoWire). Bridge calls will not appear in the log." -ForegroundColor DarkYellow
} else {
    $ErrorActionPreference = "Continue"
    & $python $app --wire
    $ErrorActionPreference = "Stop"
}

Write-Host "[4/5] Creating shortcuts..." -ForegroundColor Yellow
$shell = New-Object -ComObject WScript.Shell
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Council Engine.lnk"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "Council Engine.lnk")
)
foreach ($lnkPath in $targets) {
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $pythonw
    $lnk.Arguments = "`"$app`""
    $lnk.WorkingDirectory = $appDir
    $lnk.IconLocation = (Join-Path $appDir "council.ico")
    $lnk.Description = "Council Engine: live map of Antigravity, Claude Code and the MCP bridges"
    $lnk.Save()
    Write-Host "  $lnkPath" -ForegroundColor Green
}

$uninstall = @"
# Council Engine uninstaller: restores bridge commands, removes shortcuts and app files.
# The call log in %USERPROFILE%\.council stays; add -Purge to delete it too.
param([switch]`$Purge)
Get-CimInstance Win32_Process -Filter "Name like 'python%'" | Where-Object { `$_.CommandLine -like "*council_app.py*" } |
    ForEach-Object { Stop-Process -Id `$_.ProcessId -Force -ErrorAction SilentlyContinue }
& "$python" "$app" --unwire
Remove-Item "$($targets[0])", "$($targets[1])" -Force -ErrorAction SilentlyContinue
Remove-Item "$appDir" -Recurse -Force -ErrorAction SilentlyContinue
if (`$Purge) { Remove-Item (Join-Path `$env:USERPROFILE ".council") -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host "Council Engine removed. Restart Antigravity and open a new Claude Code chat."
"@
[IO.File]::WriteAllText((Join-Path $appDir "uninstall.ps1"), $uninstall, (New-Object System.Text.UTF8Encoding($true)))

Write-Host "[5/5] Self-check..." -ForegroundColor Yellow
$ErrorActionPreference = "Continue"
& $python $app --status
$ErrorActionPreference = "Stop"

if (-not $NoLaunch) { Start-Process -FilePath $pythonw -ArgumentList "`"$app`"" -WorkingDirectory $appDir }

Write-Host "`nDone. Open 'Council Engine' from the Desktop or Start Menu." -ForegroundColor Cyan
Write-Host "Restart Antigravity and open a new Claude Code chat so the bridges start writing the call log." -ForegroundColor Cyan
Write-Host "Uninstall: powershell -ExecutionPolicy Bypass -File `"$appDir\uninstall.ps1`"" -ForegroundColor DarkGray
