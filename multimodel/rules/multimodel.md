---
trigger: always_on
---

# Мультимодель ai-bridge

Сервер `claude-bridge` (ai-bridge 4) даёт тебе, кроме `claude_review`, `claude_ask` и `claude_implement`, роли на разных моделях и консилиум.

- `agent_run(agent, task)`: отдать задачу роли. Роли и их модели: `agents_list`. Основные: `developer` (Kimi, запасная DeepSeek V4 Pro: главный по написанию кода), `architect` (DeepSeek V4 Pro), `glm_analyst` и `security_auditor` (GLM), `minimax_architect` (MiniMax), `kimi_researcher` (Kimi), `qwen_coder` и `fast_developer` (быстрые модели), `gpt_strategist` (GPT), `claude_chief` (Claude Code).
- `auto_run(task)`: мост сам выберет роль по задаче и напишет, кого выбрал. Удобно, когда не ясно, какой роли отдать.
- `consilium(task)`: DeepSeek, GLM, MiniMax, Kimi и Grok отвечают независимо, затем критикуют ответы друг друга, Claude пишет итог с уверенностью. Дорого (два раунда); только для архитектурных решений и новых модулей (`status: init`), не для мелочей. `rounds: 1` — один раунд, дешевле.
- `model_ask(provider, question)`: вопрос одной модели напрямую; `provider` можно указать словом: deepseek, glm, minimax, kimi, qwen, grok, gpt, claude, gemini.
- Передавай `work_folder` (папку проекта), когда задача про код: модели получат карту проекта, README и файлы, которые названы в задаче или перечислены в `files`. Файлы `.env`, ключи и базы данных мост не отправляет.
- `models_list`, `token_balance`: какие пулы подключены и сколько токенов потрачено.
- Если ответ начинается с `PROVIDER_NO_KEY`, `PROVIDER_LIMIT_REACHED` или `AGENT_ERROR`, не повторяй тот же вызов: продолжай сам или возьми другую роль. Пулы после лимита мост сам ставит на паузу, и роли идут к запасным.
- Экономия: рутину делай сам, быстрые роли (`fast_developer`, `qwen_coder`) для черновиков, тяжёлые (`architect`, `consilium`, `claude_*`) только по правилам Tier 2 из GEMINI.md.
