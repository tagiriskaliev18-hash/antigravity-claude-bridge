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

## 🧠 Консилиум моделей (`multillm_council`)

Инструмент MCP-сервера `multillm-bridge`, который заставляет все подключенные ключи работать одновременно:

1. Задача параллельно уходит всем участникам (по умолчанию `deepseek-v4-pro, gpt-6-astra, qwen3.8-max, glm-5.3, claude-opus-5-5`), каждому со своей ролью: архитектор, безопасность, продукт, реализатор, критик.
2. При `rounds: 2–3` участники видят ответы друг друга, критикуют и улучшают свои варианты.
3. Судья (`claude-opus-5-5`) сводит всё в одно решение: план, где эксперты согласны, где расходятся и почему выбран вариант, риски.

Упавший провайдер не ломает консилиум: модель пропускается, а в отчете указано, кто не ответил. Участник `claude-cli` подключает локальный Claude Code по подписке вместо API-ключа.

Пример вызова из Antigravity или Claude Code:

```json
{"task": "Спроектируй мобильное приложение для учета привычек: стек, модули, план MVP",
 "context": "Flutter + Supabase, один разработчик, запуск через месяц",
 "rounds": 2}
```

Настройка через переменные окружения (в `env` блока `multillm-bridge` в `mcp_config.json`):

| Переменная | Назначение |
|---|---|
| `XYVERO_API_KEY`, `OCTAVAPI_API_KEY`, `MULTILLM_API_KEY` | ключи существующих провайдеров |
| `MULTILLM_EXTRA_PROVIDERS` | JSON-список дополнительных провайдеров `[{"name","base_url","api_key","supported_models","priority"}]` |
| `MULTILLM_COUNCIL_MEMBERS` | участники через запятую |
| `MULTILLM_COUNCIL_JUDGE` | модель-судья |

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
│   ├── claude_bridge.py                   # Python MCP-сервер моста (claude_review, claude_ask, claude_implement)
│   └── multillm_bridge.py                 # Multi-LLM MCP-сервер (multillm_ask, multillm_review, multillm_council)
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
