# Global Claude Code Guidelines

## MCP Tools Integration: Multi-LLM Bridge
You have access to the `multillm-bridge` MCP server providing tools:
- `mcp__multillm-bridge__multillm_ask`
- `mcp__multillm-bridge__multillm_review`
- `mcp__multillm-bridge__multillm_council` (parallel multi-model council + judge synthesis)
- `mcp__multillm-bridge__multillm_list_models`

### Token & Quota Optimization Policy
To preserve your own Claude Opus 5.5 subscription quota:
1. **Delegate High-Volume / Draft Tasks:** When generating large volumes of boilerplate code, extensive test suites, or performing preliminary data analysis, you can delegate the task to `deepseek-v4.1-flash` via `multillm_ask`.
2. **Alternative Model Consultations:** You can query `gpt-6-astra` or `deepseek-v4-pro` when the user asks for a second opinion, algorithmic verification, or comparative analysis.
3. **Auxiliary Code Review:** You can run `multillm_review` with `model: "deepseek-v4.1-flash"` or `model: "gpt-6-astra"` for quick independent checks.
4. **Council for big decisions:** For app architecture, stack choice or a disputed design, call `multillm_council` with a short `context`; it fans out to several models in parallel and returns one synthesized verdict.
