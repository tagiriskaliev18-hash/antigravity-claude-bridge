---
trigger: always_on
---

# Мультимодель ai-bridge

Сервер `claude-bridge` (ai-bridge 3) даёт тебе, кроме `claude_review`, `claude_ask` и `claude_implement`, роли на разных моделях и консилиум.

- `agent_run(agent, task)`: отдать задачу роли. Роли и их модели: `agents_list`. Основные: `architect` (DeepSeek V4 Pro), `glm_analyst` и `security_auditor` (GLM), `minimax_architect` (MiniMax), `kimi_researcher` (Kimi), `qwen_coder` и `fast_developer` (быстрые модели), `gpt_strategist` (GPT), `claude_chief` (Claude Code).
- `consilium(task)`: DeepSeek, GLM, MiniMax и Kimi отвечают одновременно, Claude пишет итог. Только для архитектурных решений и новых модулей (`status: init`), не для мелочей.
- `model_ask(provider, question)`: вопрос одной модели напрямую; `provider` можно указать словом: deepseek, glm, minimax, kimi, qwen, gpt, claude.
- `models_list`, `token_balance`: какие пулы подключены и сколько токенов потрачено.
- Если ответ начинается с `PROVIDER_NO_KEY`, `PROVIDER_LIMIT_REACHED` или `AGENT_ERROR`, не повторяй тот же вызов: продолжай сам или возьми другую роль.
- Экономия: рутину делай сам, быстрые роли (`fast_developer`, `qwen_coder`) для черновиков, тяжёлые (`architect`, `consilium`, `claude_*`) только по правилам Tier 2 из GEMINI.md.
