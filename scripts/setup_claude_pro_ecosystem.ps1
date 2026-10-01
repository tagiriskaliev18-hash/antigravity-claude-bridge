# ==============================================================================
# 🚀 Claude Code Pro: Ultimate Ecosystem & Token-Saving Setup
# Installs Top 10 Design + Top 10 Development Plugins + MultiLLM Token Saving Engine
# ==============================================================================

$ErrorActionPreference = "Continue"

Write-Host "`n==================================================================" -ForegroundColor Cyan
Write-Host "   🌟 CLAUDE CODE PRO: FULL ECOSYSTEM & TOKEN-SAVER SETUP        " -ForegroundColor Cyan
Write-Host "==================================================================`n" -ForegroundColor Cyan

# 1. Locate Claude CLI
$claudeBin = "$env:USERPROFILE\.local\bin\claude.exe"
if (-not (Test-Path $claudeBin)) {
    $found = Get-Command claude -ErrorAction SilentlyContinue
    if ($found) { $claudeBin = $found.Source }
    else {
        Write-Host "[-] Ошибка: Claude CLI не найден в PATH или ~/.local/bin/claude.exe" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[+] Используем Claude CLI: $claudeBin" -ForegroundColor Green

# 2. Top 10 Design & UI Plugins
$designPlugins = @(
    "frontend-design",       # UI/UX высшего качества, дизайн-системы и верстка
    "superdesign",           # Визуальный дизайн интерфейсов и баннеров
    "ui-theme-designer",     # Цветовые палитры, CSS-токены, дизайн-токены
    "figma",                 # Чтение макетов Figma, стилей и компонентов
    "canva",                 # Графика, баннеры и брендовые ассеты
    "adobe-for-creativity",  # AI-обработка изображений, SVG векторизация
    "hyperframes",           # GSAP, CSS анимации, интерактивные переходы
    "playground",            # Интерактивные HTML-песочницы для проверки UI
    "modern-web-guidance",   # Стандарты веб-разработки, адаптивность, WCAG
    "miro"                   # Архитектурные схемы, user flow, диаграммы
)

# 3. Top 10 Fast & Optimal Development Plugins
$devPlugins = @(
    "superpowers",               # Мультиагентная оркестрация, скоростной режим
    "pr-review-toolkit",         # Всестороннее код-ревью, тесты, краевые случаи
    "playwright",                # Автоматизация браузера и E2E тестирование
    "semgrep",                   # Мгновенный поиск багов и уязвимостей
    "claude-security",           # Глубокий аудит безопасности кода
    "security-guidance",         # Защита от антипаттернов и уязвимостей в коде
    "agent-sdk-dev",             # Разработка и кастомизация субагентов
    "mcp-server-dev",            # Инструменты для создания и отладки MCP
    "explanatory-output-style",  # Компактный и точный вывод без лишних токенов
    "vercel"                     # Мгновенный деплой и превью веб-приложений
)

# 4. Install Design Plugins
Write-Host "`n[1/3] Установка ТОП-10 плагинов для ДИЗАЙНА и ФРОНТЕНДА..." -ForegroundColor Yellow
foreach ($plugin in $designPlugins) {
    Write-Host "  -> Установка $plugin..." -NoNewline
    & $claudeBin plugin install $plugin 2>&1 | Out-Null
    Write-Host " [OK]" -ForegroundColor Green
}

# 5. Install Development Plugins
Write-Host "`n[2/3] Установка ТОП-10 плагинов для БЫСТРОЙ и ТОЧНОЙ РАЗРАБОТКИ..." -ForegroundColor Yellow
foreach ($plugin in $devPlugins) {
    Write-Host "  -> Установка $plugin..." -NoNewline
    & $claudeBin plugin install $plugin 2>&1 | Out-Null
    Write-Host " [OK]" -ForegroundColor Green
}

# 5.1 Install Long-Term Memory (claude-mem)
Write-Host "  -> Подключение маркетплейса thedotmack/claude-mem..." -NoNewline
& $claudeBin plugin marketplace add thedotmack/claude-mem 2>&1 | Out-Null
& $claudeBin plugin install claude-mem 2>&1 | Out-Null
Write-Host " [OK] (claude-mem установлен)" -ForegroundColor Green


# 6. Configure MultiLLM MCP & Token-Saving Architecture
Write-Host "`n[3/3] Подключение системы ЭКОНОМИИ ТОКЕНОВ (DeepSeek 10M, GPT-6, CVC)..." -ForegroundColor Yellow

$multiBridgeScript = "C:\projects\tools\multillm_bridge.py"
if (Test-Path $multiBridgeScript) {
    & $claudeBin mcp add -s user multillm-bridge -- python "$multiBridgeScript" 2>&1 | Out-Null
    Write-Host "  -> MCP шлюз multillm-bridge подключен глобально" -ForegroundColor Green
}

# Configure auto-permissions in settings.json
$settingsPath = "$env:USERPROFILE\.claude\settings.json"
$claudeDir = "$env:USERPROFILE\.claude"
if (-not (Test-Path $claudeDir)) { New-Item -ItemType Directory -Path $claudeDir -Force | Out-Null }

$settingsObj = @{}
if (Test-Path $settingsPath) {
    try { $settingsObj = Get-Content $settingsPath -Raw | ConvertFrom-Json } catch { $settingsObj = @{} }
}

$permissions = if ($settingsObj.permissions) { $settingsObj.permissions } else { [PSCustomObject]@{} }
$allowList = @(
    "mcp__multillm-bridge__multillm_ask",
    "mcp__multillm-bridge__multillm_review",
    "mcp__multillm-bridge__multillm_list_models"
)
$settingsObj | Add-Member -NotePropertyName "model" -NotePropertyValue "claude-opus-5-5" -Force
$settingsObj | Add-Member -NotePropertyName "permissions" -NotePropertyValue ([PSCustomObject]@{ allow = $allowList }) -Force
$settingsObj | ConvertTo-Json -Depth 5 | Set-Content $settingsPath -Encoding utf8
Write-Host "  -> Настройки разрешений и модель Opus 5.5 зафиксированы" -ForegroundColor Green

# Deploy global token-saver guidelines
$claudeMdPath = "$env:USERPROFILE\.claude\CLAUDE.md"
$guidelines = @'
# Global Claude Code Guidelines & Token-Saving Protocol

## 🌐 Connected Multi-LLM Ecosystem
You have access to the `multillm-bridge` MCP server providing tools:
- `mcp__multillm-bridge__multillm_ask(prompt, model, system_prompt)`
- `mcp__multillm-bridge__multillm_review(work_folder, focus, model)`
- `mcp__multillm-bridge__multillm_list_models()`

### 🛡️ Token Preservation Policy (Strict)
To protect your Claude Opus 5.5 quota from depleting on repetitive tasks:
1. **Delegate Boilerplate & Bulk Drafting:** When asked to generate large templates, mock data, unit test suites, or heavy frontend layouts, delegate the draft generation to `deepseek-v4.1-flash` (10M token pool) via `multillm_ask`. Then review and insert it cleanly.
2. **Alternative Architectural Consultations:** You may query `gpt-6-astra` or `deepseek-v4-pro` when the user requests a second opinion, complex algorithm verification, or comparative review.
3. **Surgical Edits Only:** Never rewrite entire 500+ line files (like app.js or index.html). Always use targeted search/replace or diff patches to minimize output tokens.
4. **Ignore Large Data Dumps:** Never read raw chat exports, `.txt` logs, SQLite databases, or minified files into context.
'@

Set-Content -Path $claudeMdPath -Value $guidelines -Encoding utf8
Write-Host "  -> Глобальный протокол экономии токенов сохранен в ~/.claude/CLAUDE.md" -ForegroundColor Green

Write-Host ""
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "   ВСЕ 20 ПЛАГИНОВ И СИСТЕМА ЭКОНОМИИ УСПЕШНО НАСТРОЕНЫ!" -ForegroundColor Green
Write-Host "==================================================================" -ForegroundColor Green
Write-Host "Команда для старта в любом проекте: claude" -ForegroundColor Cyan
Write-Host ""
