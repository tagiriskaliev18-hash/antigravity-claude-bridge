import sys
import json
import os
import urllib.request
import urllib.error
import subprocess
import traceback

API_KEY = os.environ.get("MULTILLM_API_KEY", "sk-cvc-14fcd3078026472914eeee63d17063a371ef607aad4c98317f6af669328a1ed5")
BASE_URL = os.environ.get("MULTILLM_BASE_URL", "https://api.aikeysforyou.com/v1")
DEFAULT_MODEL = os.environ.get("MULTILLM_DEFAULT_MODEL", "deepseek-v4-pro")

POPULAR_MODELS = [
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
    "gpt-6-astra",
    "gpt-5.6-terra",
    "claude-opus-5-5",
    "claude-sonnet-5"
]

def log(msg):
    sys.stderr.write(f"[multillm-bridge] {msg}\n")
    sys.stderr.flush()

def call_llm(model, messages, temperature=0.7, max_tokens=4096):
    url = f"{BASE_URL}/chat/completions"
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            choices = res_data.get("choices", [])
            if not choices:
                return "Ответ пуст (нет choices в ответе API)."
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            reasoning = msg.get("reasoning_content", "")
            
            result = ""
            if reasoning and not content:
                result = f"### Рассуждение ({model}):\n{reasoning}"
            elif reasoning and content:
                result = f"{content}\n\n<details><summary>Мысли модели</summary>\n{reasoning}\n</details>"
            else:
                result = content
            return result.strip() if result else "Пустой ответ от модели."
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        log(f"HTTP Error {e.code}: {err_body}")
        return f"LLM_ERROR (HTTP {e.code}): {err_body}"
    except Exception as e:
        log(f"Exception calling LLM: {e}")
        return f"LLM_ERROR: {str(e)}"

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
        "Доступные популярные модели в вашем ключе (CheapVibeCode):",
        "- deepseek-v4-pro (0.5 ед./токен) - топовая модель для сложного рассуждения и архитектуры",
        "- deepseek-v4.1-flash / deepseek-v4-flash - сверхбыстрые и экономные",
        "- qwen3.8-flash / qwen3.8-max (1 ед./токен) - отличные для общего кода и скриптов",
        "- glm-5.3 (1.5 ед./токен) / glm-5.3-flash (0.5 ед./токен) - мощные модели GLM",
        "- minimax-m3 - MiniMax M3",
        "- mimo-v2.5-pro / mimo-v2.5 - мультимодальные модели MiMo",
        "- claude-opus-5-5 / claude-sonnet-5 - модели Claude через шлюз",
        "- gpt-6-astra / gpt-5.6-terra - модели OpenAI через шлюз",
        "\nЛимит ключа: 5 000 000 единиц."
    ]
    return "\n".join(lines)

TOOLS = [
    {
        "name": "multillm_ask",
        "description": "Задать вопрос или передать задачу в DeepSeek (v4 pro / flash), Qwen, GLM, MiniMax или другую доступную модель.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Текст запроса / задачи для модели"
                },
                "model": {
                    "type": "string",
                    "description": "Идентификатор модели (по умолчанию deepseek-v4-pro, доступно: deepseek-v4-pro, deepseek-v4.1-flash, qwen3.8-flash, glm-5.3, minimax-m3, mimo-v2.5-pro и др.)",
                    "default": "deepseek-v4-pro"
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
        "description": "Провести код-ревью через DeepSeek V4 Pro, Qwen или GLM.",
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
                    "description": "Модель (по умолчанию deepseek-v4-pro)"
                }
            },
            "required": ["work_folder"]
        }
    },
    {
        "name": "multillm_list_models",
        "description": "Получить список поддерживаемых моделей и тарифных единиц по ключу.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

def main():
    log("multillm-bridge MCP server starting...")
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
                        "version": "1.0.0"
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
