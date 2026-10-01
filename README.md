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

Живая трёхмерная карта мультимодели на твоём компьютере (three.js): в центре мост и кольцо его инструментов, на ближней орбите роли из `agents.json`, на дальнем поясе пулы моделей из `providers.json`, сгруппированные по семействам (DeepSeek, GLM, MiniMax, Kimi, Qwen, Claude, Gemini, Llama, Grok, GPT), над плоскостью Antigravity, Claude Code и чат. Каждый пул и клиент показан объёмным логотипом своего семейства (кит DeepSeek, звезда Claude, арка Antigravity и так далее); логотип крутится и светится, пока эта модель отвечает, а у пула без ключа нарисован только контур. Каждый вызов моста летит по карте от клиента к роли и пулу, который реально отвечает; ответ и отказ пула видны вспышкой.

- **Чат**: задача роли, конкретной модели, консилиуму или Claude Code, с папкой проекта. Пока идёт ответ, под сообщением видно, какая модель отвечает сейчас, переходы на запасной пул и что делает каждый участник консилиума.
- **Уведомления**: о каждом идущем вызове (из Antigravity, Claude Code или чата) и отдельно о созыве консилиума: участники, их модели, кто уже ответил, когда председатель пишет итог.
- **Antigravity**: проекты из `.gemini\config\projects` и шаги последнего разговора из его локальных файлов (только чтение). Отправить сообщение прямо в окно Antigravity нельзя, у него нет для этого входа; кнопка «Скопировать для Antigravity» готовит текст.
- **Токены, журнал, процессы, настройки**: расход и балансы из `token_usage.json`, каждый вызов (кто вызвал, роль, пул, токены, результат), процессы Antigravity, Claude Code и мостов.

Приложение работает только по адресу `127.0.0.1`, не показывает значения ключей и ничего не отправляет само: запрос уходит только по кнопке «Отправить» в чате. three.js и шрифты (Geologica, Martian Mono, лицензия OFL) лежат в `desktop/ui/vendor` и `desktop/ui/fonts`, интернет для интерфейса не нужен.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop.ps1
```

Установщик кладёт приложение в `%LOCALAPPDATA%\CouncilEngine`, подключает журнал вызовов к `claude-bridge` и `multillm-bridge` (с резервной копией настроек), создаёт ярлыки на рабочем столе и в меню «Пуск». После установки перезапусти Antigravity и открой новый чат Claude Code.

| Файл | Что делает |
|---|---|
| `desktop/council_app.py` | Локальный сервер и окно приложения, сбор процессов, настроек и журнала |
| `desktop/council_tap.py` | Прослойка между клиентом и мостом: пропускает трафик без изменений и пишет журнал в `~/.council/activity` |
| `desktop/ui/index.html` | Интерфейс: 3D-карта, чат, разделы |
| `desktop/ui/vendor/`, `desktop/ui/fonts/` | three.js r170, шрифты и контуры логотипов из [Lobe Icons](https://github.com/lobehub/lobe-icons) (MIT; сами логотипы принадлежат их компаниям), поставляются вместе с приложением |

Команды: `python council_app.py --status` (сводка в консоли), `--wire` / `--unwire` (подключить или убрать журнал). Удаление: `%LOCALAPPDATA%\CouncilEngine\uninstall.ps1`.

---

## 🧠 Мультимодель ai-bridge 4

Папка `multimodel/` — MCP-мост для Antigravity, который подключает Claude Code CLI и пулы моделей DeepSeek, GLM, MiniMax, Kimi, Qwen, Grok, GPT, Llama. Он заменяет `C:\projects\tools\claude_bridge.py`: инструменты `claude_review`, `claude_ask`, `claude_implement` остаются, добавляются роли, консилиум и учёт токенов.

| Инструмент | Что делает |
|---|---|
| `agent_run(agent, task)` | Задача роли из `agents.json`. Роль идёт по своей цепочке пулов: если основной не ответил (нет ключа, лимит, ошибка), отвечает запасной |
| `consilium(task, rounds)` | Раунд 1: DeepSeek, GLM, MiniMax, Kimi и Grok отвечают независимо. Раунд 2: каждый читает ответы других без имён, критикует и уточняет свой. Claude как председатель пишет итог: согласие, расхождения (по буквам участников), решение, план и уверенность N/10. `rounds: 1` — без второго раунда, вдвое дешевле. Протокол обоих раундов сохраняется в `consilium\` |
| `auto_run(task)` | Мост сам выбирает роль по словам в задаче (безопасность, ревью, архитектура, модели и промпты, алгоритмы, исследование, код), короткие задачи отдаёт быстрой дешёвой модели и пишет, кого выбрал и почему. Правила можно заменить списком `routing` в `agents.json` |
| `model_ask(provider, question)` | Вопрос одному пулу; `provider` можно указать словом: `deepseek`, `glm`, `minimax`, `kimi`, `qwen`, `grok`, `gemini`, `gpt`, `claude` |
| `models_list(check)` | Пулы, ключи (есть или нет, без значений), роли, токены; `check=true` проверяет шлюзы без расхода токенов |
| `token_balance()` | Расход токенов по пулам и счетам, стоимость, остатки |
| `model_switch(model)` | Пул по умолчанию для `model_ask` |
| `agents_list`, `skills_list`, `soup_recipe` | Справочники |

**Надёжность и экономия.** Пул, который ответил лимитом (HTTP 429), берёт паузу до сброса, если шлюз его назвал (`Retry-After`, «try again in…»), иначе на 10 минут; после трёх сбоев подряд — на 5 минут; если ключ не принят (401/403) — на 30 минут. Пока пул на паузе, роли сразу идут к запасным и не тратят на него время; если ни один запасной не ответил, мост всё же пробует пул на паузе. Первый успешный ответ снимает паузу, `--resume` снимает её вручную. Короткий сбой (таймаут, обрыв, HTTP 5xx) повторяется один раз через 2 с. Пул, у счёта которого кончился баланс (`accounts → balance_units` в `providers.json`), пропускается, а при остатке меньше 10 % мост пишет предупреждение. Состояние пулов лежит в `pool_health.json` и видно в `models_list`, `--status` и Council Engine.

У `agent_run`, `auto_run`, `model_ask` и `consilium` есть `work_folder` и `files`: мост сам читает карту папки, README и названные файлы и прикладывает их к запросу (до 40 000 символов, `AI_BRIDGE_CONTEXT_CHARS`). Файлы `.env`, ключи, токены и базы данных не отправляются никогда.

Ключей в репозитории нет: в `providers.json` у каждого пула указано имя переменной (`api_key_env`), а значение лежит в `C:\projects\tools\.env` только на этом компьютере.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-multimodel.ps1
python C:\projects\tools\claude_bridge.py --import-keys   # перенести ключи из старых мостов в этой папке
python C:\projects\tools\claude_bridge.py --keys    # ввести ключи, ввод скрыт
python C:\projects\tools\claude_bridge.py --check   # по одному короткому запросу в каждый пул
python C:\projects\tools\claude_bridge.py --resume  # снять паузу со всех пулов (или --resume <пул>)
```

Установщик также убирает старый `multillm-bridge` (ключи были записаны прямо в его коде) из настроек Antigravity и Claude Code с резервной копией и переименовывает файл в `multillm_bridge.py.retired-<дата>`; оставить его: `-KeepOldBridge`. Тесты моста без сети и ключей: `python -m unittest discover -s multimodel/tests`.

### Плагин multimodel для Claude Code

`plugins/multimodel` подключает тот же мост к Claude Code: MCP-сервер `ai-bridge` (через журнал Council Engine, если он установлен) и команды `/multimodel:consilium`, `/multimodel:auto`, `/multimodel:ask-model`, `/multimodel:models`, а также навык, который подсказывает Claude Code, когда звать консилиум или другую модель. Установщик `install-multimodel.ps1` ставит его сам; вручную в Claude Code:

```
/plugin marketplace add C:\projects\council-engine-src
/plugin install multimodel@council-engine
```

После установки перезапусти Antigravity и Claude Code. Council Engine показывает роли, пулы, у каких пулов нет ключа, консилиум и токены из `token_usage.json`.

