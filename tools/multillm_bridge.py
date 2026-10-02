import sys
import json
import os
import urllib.request
import urllib.error
import subprocess
import traceback
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

PROVIDERS = [
    {
        "name": "xyvero (10M Pool)",
        "base_url": os.environ.get("XYVERO_BASE_URL", "https://xyvero.space/v1"),
        "api_key": os.environ.get("XYVERO_API_KEY", "sk-FOQVMuXIGrWuWQvMF3wlFM5NDZcsBAQ"),
        "supported_models": ["deepseek-v4.1-flash"],
        "priority": 10
    },
    {
        "name": "octavapi (GPT-6 Astra & Opus 5.5 Pool)",
        "base_url": os.environ.get("OCTAVAPI_BASE_URL", "https://octavapi.shop/v1"),
        "api_key": os.environ.get("OCTAVAPI_API_KEY", "sk-octava-live_rkUKBERsL9BNXccsliKLaJ_BqRSWK2LY"),
        "supported_models": [
            "gpt-6-astra",
            "gpt-6-sol",
            "gpt-5.6-sol",
            "gpt-5.6-terra",
            "gpt-5.5",
            "claude-opus-5-5",
            "claude-sonnet-5"
        ],
        "priority": 8
    },
    {
        "name": "cheapvibecode (5M Pool)",
        "base_url": os.environ.get("MULTILLM_BASE_URL", "https://api.aikeysforyou.com/v1"),
        "api_key": os.environ.get("MULTILLM_API_KEY", "sk-cvc-14fcd3078026472914eeee63d17063a371ef607aad4c98317f6af669328a1ed5"),
        "supported_models": [
            "deepseek-v4-pro",
            "deepseek-v4.1-flash",
            "deepseek-v4-flash",
            "qwen3.8-flash",
            "qwen3.8-max",
            "glm-5.3",
            "glm-5.3-flash",
            "glm-5.2",
            "minimax-m3",
            "mimo-v2.5-pro",
            "mimo-v2.5",
            "claude-opus-5-5",
            "claude-sonnet-5",
            "gpt-6-astra",
            "gpt-5.6-terra"
        ],
        "priority": 5
    }
]

# Дополнительные провайдеры (новые ключи) без правки кода: JSON-список в переменной окружения, например
# MULTILLM_EXTRA_PROVIDERS='[{"name":"mykey","base_url":"https://host/v1","api_key":"sk-...","supported_models":["gpt-6-astra"],"priority":9}]'
try:
    PROVIDERS.extend(json.loads(os.environ.get("MULTILLM_EXTRA_PROVIDERS", "[]")))
except Exception as e:
    sys.stderr.write(f"[multillm-bridge] MULTILLM_EXTRA_PROVIDERS ignored: {e}\n")
PROVIDERS = [p for p in PROVIDERS if p.get("api_key")]

DEFAULT_MODEL = os.environ.get("MULTILLM_DEFAULT_MODEL", "deepseek-v4.1-flash")

def log(msg):
    sys.stderr.write(f"[multillm-bridge] {msg}\n")
    sys.stderr.flush()

def get_candidates(model):
    target_model = model.strip() if model else DEFAULT_MODEL
    matching = []
    fallback = []
    for p in PROVIDERS:
        if target_model in p["supported_models"]:
            matching.append(p)
        else:
            fallback.append(p)
    matching.sort(key=lambda x: x["priority"], reverse=True)
    return target_model, matching or fallback

def call_single_provider(provider, model, messages, temperature=0.7, max_tokens=4096):
    url = f"{provider['base_url']}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AntigravityMultiLLM/1.0"
    }

    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
        choices = res_data.get("choices", [])
        if not choices:
            raise ValueError("Ответ пуст (нет choices в ответе API).")
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        reasoning = msg.get("reasoning_content", "")

        result = ""
        if reasoning and not content:
            result = f"### Рассуждение ({model} @ {provider['name']}):\n{reasoning}"
        elif reasoning and content:
            result = f"{content}\n\n<details><summary>Мысли модели ({provider['name']})</summary>\n{reasoning}\n</details>"
        else:
            result = content
        return result.strip() if result else "Пустой ответ от модели."

def call_llm(model, messages, temperature=0.7, max_tokens=4096):
    target_model, providers = get_candidates(model)
    errors = []

    for p in providers:
        log(f"Routing {target_model} to provider '{p['name']}'...")
        try:
            return call_single_provider(p, target_model, messages, temperature, max_tokens)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            log(f"Provider '{p['name']}' HTTP {e.code}: {err_body[:100]}")
            errors.append(f"{p['name']} HTTP {e.code}: {err_body}")
        except Exception as e:
            log(f"Provider '{p['name']}' failed: {e}")
            errors.append(f"{p['name']} error: {str(e)}")

    return f"LLM_ERROR: Все провайдеры вернули ошибку для модели '{target_model}':\n" + "\n".join(errors)

def handle_ask(prompt, model=None, system_prompt=None, temperature=0.7):
    chosen_model = model.strip() if model else DEFAULT_MODEL
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return call_llm(chosen_model, messages, temperature=temperature)

def handle_review(work_folder, focus="", model=None):
    chosen_model = model.strip() if model else DEFAULT_MODEL
    diff_info = ""
    try:
        if os.path.exists(os.path.join(work_folder, ".git")):
            git_st = subprocess.run(
                ["git", "status", "--short"],
                cwd=work_folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            ).stdout.strip()
            git_diff = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=work_folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace"
            ).stdout.strip()
            if git_st or git_diff:
                diff_info = f"\n\nGit статус:\n{git_st}\nИзменения:\n{git_diff}"
    except Exception:
        pass

    prompt = (
        f"Ты выступаешь в роли старшего ревьюера кода. Проанализируй проект: {work_folder}.\n"
        f"Фокус ревью: {focus if focus else 'Архитектура, потенциальные баги, чистота кода, производительность'}\n"
        f"{diff_info}\n\n"
        "Сформируй четкий, структурированный отчет: критические проблемы, предупреждения и рекомендации."
    )
    return handle_ask(prompt, model=chosen_model, system_prompt="Ты высококвалифицированный сеньор-разработчик и ревьюер.")

# ---------------------------------------------------------------------------
# Консилиум: несколько моделей отвечают параллельно, затем судья сводит ответы
# ---------------------------------------------------------------------------

COUNCIL_MEMBERS = [m.strip() for m in os.environ.get(
    "MULTILLM_COUNCIL_MEMBERS",
    "deepseek-v4-pro,gpt-6-astra,qwen3.8-max,glm-5.3,claude-opus-5-5"
).split(",") if m.strip()]
COUNCIL_JUDGE = os.environ.get("MULTILLM_COUNCIL_JUDGE", "claude-opus-5-5")
CLAUDE_CLI_MEMBER = "claude-cli"  # локальный Claude Code CLI (подписка), а не API-ключ

ROLE_HINTS = [
    "архитектор: структура приложения, модули, стек, масштабирование",
    "инженер по надежности и безопасности: ошибки, уязвимости, крайние случаи",
    "продуктовый разработчик: UX, MVP, что сделать первым",
    "прагматик-реализатор: конкретный код, библиотеки, шаги внедрения",
    "критик: слабые места предложенных подходов и альтернативы",
]

def call_claude_cli(prompt, work_folder=None):
    exe = shutil.which("claude") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("Claude Code CLI не найден в PATH")
    proc = subprocess.run(
        [exe, "-p", prompt],
        cwd=work_folder or os.getcwd(),
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        raise RuntimeError(f"claude exit {proc.returncode}: {(proc.stderr or out)[:300]}")
    return out

def ask_member(member, messages, work_folder=None):
    started = time.time()
    try:
        if member == CLAUDE_CLI_MEMBER:
            text = call_claude_cli("\n\n".join(m["content"] for m in messages), work_folder)
        else:
            text = call_llm(member, messages)
        ok = not text.startswith("LLM_ERROR")
    except Exception as e:
        text, ok = f"LLM_ERROR: {e}", False
    log(f"Council member {member}: {'ok' if ok else 'failed'} in {time.time() - started:.1f}s")
    return {"member": member, "ok": ok, "text": text}

def fan_out(members, build_messages, work_folder=None):
    with ThreadPoolExecutor(max_workers=len(members)) as pool:
        futures = [pool.submit(ask_member, m, build_messages(i, m), work_folder) for i, m in enumerate(members)]
        return [f.result() for f in futures]

def format_answers(answers):
    return "\n\n".join(f"=== Эксперт {i + 1} ({a['member']}) ===\n{a['text']}" for i, a in enumerate(answers))

def handle_council(task, members=None, judge=None, context="", rounds=1, use_roles=True, work_folder=None):
    if isinstance(members, str):
        members = [m.strip() for m in members.split(",") if m.strip()]
    members = members or COUNCIL_MEMBERS
    judge = (judge or COUNCIL_JUDGE).strip()
    rounds = max(1, min(int(rounds or 1), 3))
    base = f"Задача:\n{task}"
    if context:
        base += f"\n\nКонтекст проекта:\n{context}"

    def first_round(i, member):
        role = ROLE_HINTS[i % len(ROLE_HINTS)] if use_roles else "независимый эксперт"
        return [
            {"role": "system", "content": f"Ты участник консилиума экспертов по разработке ПО. Твоя роль: {role}. Отвечай по существу, конкретно, на русском."},
            {"role": "user", "content": base},
        ]

    answers = fan_out(members, first_round, work_folder)
    failed = [a for a in answers if not a["ok"]]

    for r in range(2, rounds + 1):
        alive = [a for a in answers if a["ok"]]
        if len(alive) < 2:
            break
        others = format_answers(alive)
        def critique_round(i, member, others=others):
            return [
                {"role": "system", "content": "Ты участник консилиума экспертов по разработке ПО. Отвечай на русском."},
                {"role": "user", "content": f"{base}\n\nОтветы консилиума в предыдущем раунде:\n{others}\n\n"
                    "Найди ошибки и слабые места в чужих ответах, учти сильные идеи и дай свой улучшенный итоговый вариант."},
            ]
        answers = fan_out([a["member"] for a in alive], critique_round, work_folder)
        failed += [a for a in answers if not a["ok"]]

    good = [a for a in answers if a["ok"]]
    report = [f"## Консилиум: {len(good)}/{len(members)} моделей ответили, раундов: {rounds}"]
    if failed:
        report.append("Не ответили: " + ", ".join(f"{a['member']} ({a['text'][:120].strip()})" for a in failed))
    if not good:
        return "LLM_ERROR: ни одна модель консилиума не ответила.\n" + "\n".join(report)

    verdict = ask_member(judge, [
        {"role": "system", "content": "Ты председатель консилиума и главный архитектор. Отвечай на русском."},
        {"role": "user", "content": f"{base}\n\nОтветы экспертов:\n{format_answers(good)}\n\n"
            "Сведи ответы в одно итоговое решение: 1) итоговая рекомендация и план шагов; "
            "2) в чем эксперты согласны; 3) где расходятся и чью позицию ты выбираешь и почему; "
            "4) риски. Не пересказывай ответы целиком."},
    ], work_folder)
    if not verdict["ok"]:
        report.append(f"Судья {judge} не ответил ({verdict['text'][:200]}), ниже сырые ответы.")
        report.append(format_answers(good))
        return "\n\n".join(report)

    report.append(f"### Итог (судья: {judge})\n{verdict['text']}")
    report.append("<details><summary>Ответы участников</summary>\n\n" + format_answers(good) + "\n</details>")
    return "\n\n".join(report)

def handle_list_models():
    lines = [
        "Подключенные пулы токенов и модели:",
        "1. Пул Xyvero (10 000 000 единиц):",
        "   - deepseek-v4.1-flash — быстрый, экономный анализ и генерация кода (маршрутизируется по умолчанию)",
        "",
        "2. Пул CheapVibeCode (5 000 000 единиц):",
        "   - deepseek-v4-pro (0.5 ед./токен) — топовая модель глубоких рассуждений",
        "   - qwen3.8-flash / qwen3.8-max (1 ед./токен) — Qwen",
        "   - glm-5.3 / glm-5.3-flash (0.5 - 1.5 ед./токен) — GLM",
        "   - minimax-m3, mimo-v2.5-pro",
        "   - claude-opus-5-5, claude-sonnet-5",
        "",
        "Общий доступный резерв: 15 000 000 единиц токенов.",
        "",
        "Консилиум (multillm_council):",
        f"   - участники по умолчанию: {', '.join(COUNCIL_MEMBERS)}",
        f"   - судья: {COUNCIL_JUDGE}",
        f"   - '{CLAUDE_CLI_MEMBER}' — локальный Claude Code CLI по подписке",
        f"   - активные провайдеры (с ключом): {', '.join(p['name'] for p in PROVIDERS)}"
    ]
    return "\n".join(lines)

TOOLS = [
    {
        "name": "multillm_ask",
        "description": "Задать вопрос или передать задачу в DeepSeek (v4.1 flash / v4 pro), Qwen, GLM, MiniMax или другую модель из объединенного пула токенов (15M).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Текст запроса / задачи для модели"
                },
                "model": {
                    "type": "string",
                    "description": "Идентификатор модели (deepseek-v4.1-flash, deepseek-v4-pro, qwen3.8-flash, glm-5.3, minimax-m3, mimo-v2.5-pro)",
                    "default": "deepseek-v4.1-flash"
                },
                "system_prompt": {
                    "type": "string",
                    "description": "Опциональная системная инструкция"
                }
            },
            "required": ["prompt"]
        }
    },
    {
        "name": "multillm_review",
        "description": "Провести код-ревью через DeepSeek (v4.1-flash или v4-pro), Qwen или GLM.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_folder": {
                    "type": "string",
                    "description": "Абсолютный путь к рабочей папке проекта"
                },
                "focus": {
                    "type": "string",
                    "description": "Фокус ревью (баги, безопасность, архитектура)"
                },
                "model": {
                    "type": "string",
                    "description": "Модель (по умолчанию deepseek-v4.1-flash)"
                }
            },
            "required": ["work_folder"]
        }
    },
    {
        "name": "multillm_council",
        "description": "Консилиум: задача параллельно уходит нескольким моделям (DeepSeek, GPT, Qwen, GLM, Claude и др. через все подключенные ключи), опционально они критикуют ответы друг друга, затем модель-судья сводит всё в одно решение. Для архитектуры, выбора стека, плана разработки приложения, сложных багов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Задача или вопрос для консилиума"},
                "context": {"type": "string", "description": "Контекст: описание проекта, ключевые интерфейсы, лог ошибки (кратко)"},
                "members": {"type": "array", "items": {"type": "string"}, "description": "Модели-участники; 'claude-cli' = локальный Claude Code. По умолчанию MULTILLM_COUNCIL_MEMBERS"},
                "judge": {"type": "string", "description": "Модель-судья (по умолчанию claude-opus-5-5)"},
                "rounds": {"type": "integer", "description": "1 = только независимые ответы; 2-3 = раунды взаимной критики", "default": 1},
                "use_roles": {"type": "boolean", "description": "Раздать участникам разные роли (архитектор, безопасность, продукт...)", "default": True},
                "work_folder": {"type": "string", "description": "Папка проекта (для участника claude-cli)"}
            },
            "required": ["task"]
        }
    },
    {
        "name": "multillm_list_models",
        "description": "Получить список поддерживаемых моделей, провайдеров и балансов в объединенном пуле.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

def main():
    log("multillm-bridge MCP server starting (Multi-Provider: Xyvero 10M + CheapVibeCode 5M)...")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            log(f"Failed to parse JSON: {e}")
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "multillm-bridge",
                        "version": "1.2.0"
                    }
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            pass

        elif method == "ping":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {}
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": TOOLS
                }
            }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments", {})

            log(f"Tool call: {tool_name}")
            try:
                if tool_name == "multillm_ask":
                    text = handle_ask(
                        prompt=args.get("prompt", ""),
                        model=args.get("model"),
                        system_prompt=args.get("system_prompt")
                    )
                elif tool_name == "multillm_review":
                    text = handle_review(
                        work_folder=args.get("work_folder", os.getcwd()),
                        focus=args.get("focus", ""),
                        model=args.get("model")
                    )
                elif tool_name == "multillm_council":
                    text = handle_council(
                        task=args.get("task", ""),
                        members=args.get("members"),
                        judge=args.get("judge"),
                        context=args.get("context", ""),
                        rounds=args.get("rounds", 1),
                        use_roles=args.get("use_roles", True),
                        work_folder=args.get("work_folder")
                    )
                elif tool_name == "multillm_list_models":
                    text = handle_list_models()
                else:
                    text = f"Неизвестный инструмент: {tool_name}"

                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": text
                            }
                        ],
                        "isError": text.startswith("LLM_ERROR")
                    }
                }
            except Exception as e:
                log(f"Error handling tool call: {traceback.format_exc()}")
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"LLM_ERROR: {str(e)}"
                            }
                        ],
                        "isError": True
                    }
                }
            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()

        else:
            if req_id is not None:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}"
                    }
                }
                sys.stdout.write(json.dumps(res) + "\n")
                sys.stdout.flush()

if __name__ == "__main__":
    main()
