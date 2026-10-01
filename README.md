# Antigravity ↔ Claude Code Bridge & Acceleration Pack

Оптимизированная конфигурация связки **Google Antigravity (Gemini)** и **Claude Code** через MCP-мост (`claude-bridge`), настроенная на максимальную скорость и параллельную работу.

---

## 🚀 Основные возможности и оптимизации

- **⚡ Ленивое ревью (Lazy Review):** `claude_review` вызывается только при масштабных изменениях (>3 файлов или >150 строк кода). Мелкие правки проверяются нативно без блокировки.
- **🔄 Пакетная отправка (Batching):** Все правки группируются и уходят на ревью единым пулом в конце задачи, исключая Context Loop.
- **👥 Параллельная декомпозиция (Parallel Subagents):** Antigravity разделяет сложные задачи на независимые роли через `define_subagent` и `invoke_subagent`.
- **🛑 Лимит итераций (Iteration Cap = 8):** Защита от бесконечного пинг-понга. Если субагент не справляется за 8 циклов, Antigravity принудительно передает концентрированный лог ошибки в `claude_ask`.
- **✂️ Сжатие контекста (Context Pruning):** Ограничение в 8,000 токенов на субагента, передача только целевых интерфейсов.
- **🏎️ Навык `context_booster`:** Быстрое извлечение карты зависимостей перед планированием без тяжелого рекурсивного сканирования файловой системы.

---

## 📦 Структура репозитория

```
antigravity-claude-bridge/
├── config/
│   ├── GEMINI.md                          # Глобальные правила оркестрации Antigravity
│   ├── mcp_config.json                    # Шаблон конфигурации MCP-сервера
│   ├── rules/
│   │   └── claude_bridge.md               # Правило порогов и ленивого ревью
│   └── skills/
│       └── context_booster/
│           └── SKILL.md                   # Навык ускорения извлечения контекста
├── scripts/
│   └── install.ps1                        # Скрипт автоматической установки для Windows
├── tools/
│   └── claude_bridge.py                   # Python MCP-сервер моста (claude_review, claude_ask, claude_implement)
└── README.md
```

---

## 🛠️ Быстрая установка на новом устройстве

### Требования
1. **Python 3.9+** (добавлен в `PATH`).
2. **Claude Code CLI**:
   ```bash
   npm i -g @anthropic-ai/claude-code
   claude
   ```
   *(войдите в свой аккаунт Anthropic при первом запуске)*.

### Установка в 1 команду (PowerShell)

1. Склонируйте этот репозиторий:
   ```bash
   git clone https://github.com/tagiriskaliev18-hash/antigravity-claude-bridge.git
   cd antigravity-claude-bridge
   ```

2. Запустите инсталлятор:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
   ```

Скрипт автоматически:
- Создаст структуру в `~/.gemini/config/`
- Скопирует `GEMINI.md`, `claude_bridge.md` и навык `context_booster`
- Развернет `claude_bridge.py` в `C:\projects\tools\`
- Настроит `~/.gemini/config/mcp_config.json`

---

## 📖 Ручная настройка

Если вы хотите разместить файлы по другим путям:
1. Поместите `tools/claude_bridge.py` в любую папку (например, `C:\projects\tools\claude_bridge.py`).
2. В файле `~/.gemini/config/mcp_config.json` укажите путь к скрипту:
   ```json
   {
     "mcpServers": {
       "claude-bridge": {
         "command": "python",
         "args": [
           "C:\\projects\\tools\\claude_bridge.py"
         ]
       }
     }
   }
   ```
3. Скопируйте содержимое `config/` в `~/.gemini/config/`.

---

## 🖥️ Council Engine: настольное приложение

Живая sci-fi карта мультимодели на твоём компьютере: ядро-мост, кольцо ролей из `agents.json` и кольцо пулов моделей из `providers.json` (DeepSeek, GLM, MiniMax, Qwen и другие, цвет по семейству), расход токенов и балансы из `token_usage.json`, процессы Antigravity, Claude Code и мостов и каждый вызов моста (кто вызвал, какая роль и какой пул отвечали, переходы на запасной пул, токены, результат). Приложение только читает, работает по адресу `127.0.0.1` и не показывает значения ключей.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1
```

Установщик кладёт приложение в `%LOCALAPPDATA%\CouncilEngine`, подключает журнал вызовов к `claude-bridge` и `multillm-bridge` (с резервной копией настроек), создаёт ярлыки на рабочем столе и в меню «Пуск». После установки перезапусти Antigravity и открой новый чат Claude Code.

| Файл | Что делает |
|---|---|
| `desktop/council_app.py` | Локальный сервер и окно приложения, сбор процессов, настроек и журнала |
| `desktop/council_tap.py` | Прослойка между клиентом и мостом: пропускает трафик без изменений и пишет журнал в `~/.council/activity` |
| `desktop/ui/index.html` | Интерфейс |

Команды: `python council_app.py --status` (сводка в консоли), `--wire` / `--unwire` (подключить или убрать журнал). Удаление: `%LOCALAPPDATA%\CouncilEngine\uninstall.ps1`.
