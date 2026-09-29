import sys
import json
import os
import urllib.request
import urllib.error
import subprocess
import traceback

PROVIDERS = [
    {
        "name": "xyvero (10M Pool)",
        "base_url": os.environ.get("XYVERO_BASE_URL", "https://xyvero.space/v1"),
        "api_key": os.environ.get("XYVERO_API_KEY", "sk-FOQVMuXIGrWuWQvMF3wlFM5NDZcsBAQ"),
        "supported_models": ["deepseek-v4.1-flash"],
        "priority": 10
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
        "Общий доступный резерв: 15 000 000 единиц токенов."
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
                        "version": "1.1.0"
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
