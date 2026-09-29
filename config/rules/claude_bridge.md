---
trigger: always_on
---

# Role Configuration: Antigravity ↔ Claude Code Bridge

## Triggering & Execution Strategy
- **Mode:** Lazy Evaluation (Ленивое выполнение).
- **Execution Limit:** Снизить частоту вызовов `claude_ask`. Архитектурный консилиум проводить ИСКЛЮЧИТЕЛЬНО при создании новых модулей (`status: init`). Для текущих задач Antigravity принимает решения автономно.

## Thresholds & Delegations (Ограничения)
- **claude_review Trigger:** Активировать `call_mcp_tool -> claude_review` ТОЛЬКО если изменено более **3 файлов** или суммарно более **150 строк кода** (вместо прежних 30 строк). Мелкие правки верифицируются нативно через встроенный компилятор Antigravity.
- **claude_implement Restriction:** Запрещено вызывать `claude_implement` для задач, которые могут быть решены локально. Claude Code используется только как экспертный консультант (`claude_ask`), чтобы избежать дублирования контекста.
- **Batching:** Если Antigravity планирует изменить несколько файлов, сгруппируй изменения в один пул и отправь на ревью один раз в конце задачи, а не пофайлово.
- **Limit Handling:** При ответе `CLAUDE_LIMIT_REACHED` не блокироваться, продолжать работу самостоятельно.
