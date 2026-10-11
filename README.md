# Antigravity ↔ Claude Code Bridge & Acceleration Pack

> Часть экосистемы **[MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem)** · автор **Тагир Искалиев** ([@tagiriskaliev18-hash](https://github.com/tagiriskaliev18-hash))

Оптимизированная конфигурация связки **Google Antigravity (Gemini)** и **Claude Code** через MCP-мост (`claude-bridge`), плюс мультипровайдерный шлюз `multillm-bridge`.

---

## Основные возможности

- **Ленивое ревью (Lazy Review):** `claude_review` вызывается только при масштабных изменениях (>3 файлов или >150 строк кода). Мелкие правки проверяются нативно без блокировки.
- **Пакетная отправка (Batching):** Все правки группируются и уходят на ревью единым пулом в конце задачи, исключая Context Loop.
- **Параллельная декомпозиция (Parallel Subagents):** Antigravity разделяет сложные задачи на независимые роли через `define_subagent` и `invoke_subagent`.
- **Лимит итераций (Iteration Cap = 8):** Защита от бесконечного пинг-понга. Если субагент не справляется за 8 циклов, Antigravity передает концентрированный лог ошибки в `claude_ask`.
- **Сжатие контекста (Context Pruning):** Ограничение в 8,000 токенов на субагента, передача только целевых интерфейсов.
- **Навык `context_booster`:** Быстрое извлечение карты зависимостей перед планированием без тяжелого рекурсивного сканирования файловой системы.
- **MultiLLM bridge:** маршрутизация OpenAI-совместимых запросов по приоритету провайдера. Ключи читаются только из окружения. Список моделей задается конфигом.

---

## Структура репозитория

```
antigravity-claude-bridge/
├── config/
│   ├── GEMINI.md                          # Глобальные правила оркестрации Antigravity
│   ├── mcp_config.json                    # Шаблон конфигурации MCP-серверов
│   ├── multillm_providers.json            # Провайдеры и списки моделей
│   ├── rules/
│   │   └── claude_bridge.md               # Правило порогов и ленивого ревью
│   └── skills/
│       ├── context_booster/SKILL.md
│       └── multillm-orchestrator/SKILL.md
├── scripts/
│   └── install.ps1                        # Установка для Windows
├── tools/
│   ├── claude_bridge.py                   # MCP: claude_review, claude_ask, claude_implement
│   └── multillm_bridge.py                 # MCP: multillm_ask, multillm_review, multillm_list_models
├── tests/                                 # pytest, HTTP замокан
├── .env.example
├── LICENSE
└── README.md
```

---

## Установка

### Требования

- Python 3.9+ (в `PATH`). Сторонние пакеты для запуска моста не нужны.
- Claude Code CLI, если нужен `claude-bridge`:

```bash
npm i -g @anthropic-ai/claude-code
claude
```

### Ключи и локальный запуск

```bash
git clone https://github.com/tagiriskaliev18-hash/antigravity-claude-bridge.git
cd antigravity-claude-bridge
cp .env.example .env
# заполните ключи провайдеров, которыми пользуетесь
set -a && source .env && set +a
python tools/multillm_bridge.py
```

`multillm_bridge.py` сам подхватывает `.env` из корня репозитория, из каталога `tools/` и из текущей директории. Уже заданные переменные окружения не перезаписываются. Файл `.env` в git не попадает.

Если ключ выбранного провайдера пустой, вызов возвращает `LLM_ERROR` и имя переменной, которую нужно задать. Запросов к API при этом нет.

### Установка в Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

Скрипт копирует правила и навыки в `~/.gemini/config/`, кладет мосты и `multillm_providers.json` в `C:\projects\tools\` и пишет `~/.gemini/config/mcp_config.json`. После этого добавьте ключи в блок `env` сервера `multillm-bridge` (или в `.env` рядом со скриптом).

### Ручная настройка MCP

```json
{
  "mcpServers": {
    "claude-bridge": {
      "command": "python",
      "args": ["C:\\projects\\tools\\claude_bridge.py"],
      "env": {
        "CLAUDE_BRIDGE_MODEL": "opus"
      }
    },
    "multillm-bridge": {
      "command": "python",
      "args": ["C:\\projects\\tools\\multillm_bridge.py"],
      "env": {
        "MULTILLM_CONFIG": "C:\\projects\\tools\\multillm_providers.json",
        "MULTILLM_DEFAULT_MODEL": "deepseek-v4-pro",
        "XYVERO_API_KEY": "<your key>",
        "OCTAVAPI_API_KEY": "<your key>",
        "MULTILLM_API_KEY": "<your key>"
      }
    }
  }
}
```

Скопируйте `config/` в `~/.gemini/config/`. Шаблон без секретов лежит в `config/mcp_config.json`.

---

## Переменные окружения

| Переменная | Обязательность | Назначение |
|---|---|---|
| `XYVERO_API_KEY` | для пула Xyvero | Ключ провайдера `xyvero` |
| `OCTAVAPI_API_KEY` | для пула OctavAPI | Ключ провайдера `octavapi` |
| `MULTILLM_API_KEY` | для пула CheapVibeCode / aikeysforyou | Ключ третьего провайдера |
| `XYVERO_BASE_URL` | нет | Перекрывает URL Xyvero |
| `OCTAVAPI_BASE_URL` | нет | Перекрывает URL OctavAPI |
| `MULTILLM_BASE_URL` | нет | Перекрывает URL CheapVibeCode |
| `MULTILLM_DEFAULT_MODEL` | нет | Модель, если в вызове она не передана |
| `MULTILLM_CONFIG` | нет | Путь к JSON со списком провайдеров и моделей |
| `XYVERO_MODELS` | нет | Список моделей Xyvero через запятую |
| `OCTAVAPI_MODELS` | нет | Список моделей OctavAPI через запятую |
| `MULTILLM_MODELS` | нет | Список моделей CheapVibeCode через запятую |
| `CLAUDE_BRIDGE_MODEL` | нет | Модель Claude CLI (`opus` по умолчанию) |

Ключи не хранятся в репозитории. Если ключ когда-либо был закоммичен, перевыпустите его у провайдера: история git его уже содержит.

---

## Список моделей

Маршрутизация читает `supported_models` из [`config/multillm_providers.json`](config/multillm_providers.json). Провайдер с большим `priority` выбирается первым среди тех, кто обслуживает запрошенную модель. Если модель не указана ни у кого, используется исходный порядок провайдеров.

Переменная `*_MODELS` заменяет список только у своего провайдера. Другой файл можно подставить через `MULTILLM_CONFIG`.

`multillm_list_models` печатает каталог из этого же файла (`catalog_title`, `catalog_entries`, `catalog_footer`). У OctavAPI в поставке `catalog_visible: false`, поэтому текст каталога совпадает с прежним списком, а модели пула по-прежнему участвуют в маршрутизации. Переменная `OCTAVAPI_MODELS` включает пул в каталог.

---

## Тесты

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

Тесты не ходят в сеть: HTTP мокается. GitHub Actions запускает их на Python 3.9, 3.11 и 3.12.

---

## Часть экосистемы MindTagSystem

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
| Приложения | [FileHub AI](https://github.com/tagiriskaliev18-hash/filehub-ai) | Хранилище файлов с ИИ-агентом для Word, PowerPoint и Excel |
| Приложения | [SortApp (анализатор логов)](https://github.com/tagiriskaliev18-hash/sortapp) | Анализатор журналов доступа к сетевым папкам с отчётами Excel |
| Приложения | [ИИ Доктор (medical-ai-assistant)](https://github.com/tagiriskaliev18-hash/medical-ai-assistant) | Офлайн-ассистент врача приёмного покоя |

Как проекты связаны между собой: [архитектура MindTagSystem](https://github.com/tagiriskaliev18-hash/MindTagSystem/blob/main/docs/ARCHITECTURE.md). Автор всех проектов экосистемы — Тагир Искалиев.

## Лицензия

[MIT](LICENSE). Copyright (c) 2026 Tagir Iskaliev.
