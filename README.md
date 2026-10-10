# Antigravity ↔ Claude Code Bridge & Acceleration Pack

> Часть экосистемы **[MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem)** · автор **Тагир Искалиев** ([@tagiriskaliev18-hash](https://github.com/tagiriskaliev18-hash))

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

## 🌐 Часть экосистемы MindTagSystem

Antigravity ↔ Claude Code Bridge входит в **[MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem)** — экосистему для программистов, которую создаёт **Тагир Искалиев** ([@tagiriskaliev18-hash](https://github.com/tagiriskaliev18-hash)): своя операционная система, браузер, IDE, ИИ-ядро и приложения, которые работают вместе и которые можно встроить в любое устройство.

**Роль в экосистеме:** мост между ИИ-агентами разработчика (слой «ИИ-ядро»).

| Слой | Проект | Что делает |
|---|---|---|
| Платформа | [AIsktagOS](https://github.com/tagiriskaliev18-hash/AisktagOS) | Операционная система для разработчиков в стиле macOS на любом железе |
| Инструменты разработчика | [Mind IDE](https://github.com/tagiriskaliev18-hash/Mind-IDE) | ИИ-среда разработки: один чат с моделями, Claude Code и Antigravity |
| Инструменты разработчика | [ITIS Browser](https://github.com/tagiriskaliev18-hash/ITIS-browser) | Браузер с ИИ-агентом, который сам кликает и листает страницы |
| ИИ-ядро | [AI Duo (multimodel-agent)](https://github.com/tagiriskaliev18-hash/multimodel-agent) | Единый ИИ-шлюз с OpenAI-совместимым API для всех моделей |
| ИИ-ядро | **Antigravity ↔ Claude Code Bridge** ← вы здесь | MCP-мост, который связывает Antigravity, Claude Code и пул моделей |
| ИИ-ядро | [Qwen 14B Coder Dev](https://github.com/tagiriskaliev18-hash/qwen14b-coder-dev) | Локальная офлайн-модель для программирования в Ollama |
| Приложения | [MindMail](https://github.com/tagiriskaliev18-hash/MindMail) | Все почтовые ящики в одном и лучшее из Gmail, Mail.ru, Outlook и Яндекс Почты |
| Приложения | [FileHub AI](https://github.com/tagiriskaliev18-hash/filehub-ai) | Хранилище файлов с ИИ-агентом для Word, PowerPoint и Excel |
| Приложения | [SortApp (анализатор логов)](https://github.com/tagiriskaliev18-hash/sortapp) | Анализатор журналов доступа к сетевым папкам с отчётами Excel |
| Приложения | [ИИ Доктор (medical-ai-assistant)](https://github.com/tagiriskaliev18-hash/medical-ai-assistant) | Офлайн-ассистент врача приёмного покоя |

Как проекты связаны между собой: [архитектура MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem/blob/main/docs/ARCHITECTURE.md). Автор всех проектов экосистемы — Тагир Искалиев.
