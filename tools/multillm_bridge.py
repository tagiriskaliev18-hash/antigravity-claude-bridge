"""MCP bridge that routes chat requests across OpenAI-compatible providers.

API keys are read only from the environment (a local .env file is loaded
without overriding variables that are already set). Model lists and provider
metadata come from config/multillm_providers.json, or from the file named by
MULTILLM_CONFIG. Per-provider comma-separated *_MODELS variables replace that
provider's model list.
"""
import sys
import json
import os
import urllib.request
import urllib.error
import subprocess
import traceback

CONFIG_FILENAME = "multillm_providers.json"


class MissingApiKeyError(RuntimeError):
    """Raised when a provider's API key environment variable is unset or blank."""


def log(msg):
    sys.stderr.write(f"[multillm-bridge] {msg}\n")
    sys.stderr.flush()


def _script_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _repo_root():
    return os.path.dirname(_script_dir())


def _load_dotenv_file(path):
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def load_dotenv():
    candidates = [
        os.path.join(_repo_root(), ".env"),
        os.path.join(_script_dir(), ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]
    seen = set()
    for path in candidates:
        normalized = os.path.abspath(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        _load_dotenv_file(normalized)


def resolve_config_path():
    override = os.environ.get("MULTILLM_CONFIG", "").strip()
    if override:
        return override
    repo_config = os.path.join(_repo_root(), "config", CONFIG_FILENAME)
    beside_script = os.path.join(_script_dir(), CONFIG_FILENAME)
    for path in (repo_config, beside_script):
        if os.path.isfile(path):
            return path
    return repo_config


def load_config(path=None):
    config_path = path or resolve_config_path()
    if not os.path.isfile(config_path):
        raise FileNotFoundError(
            f"MultiLLM config not found at {config_path}. "
            "Set MULTILLM_CONFIG to a providers JSON file."
        )
    with open(config_path, encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in MultiLLM config {config_path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
        raise ValueError(f"Invalid MultiLLM config {config_path}: 'providers' must be a list.")
    default_model = data.get("default_model")
    if not isinstance(default_model, str) or not default_model.strip():
        raise ValueError(f"Invalid MultiLLM config {config_path}: 'default_model' is required.")
    for index, entry in enumerate(data["providers"]):
        _validate_provider(entry, index)
    data["_path"] = config_path
    return data


def _validate_provider(entry, index):
    if not isinstance(entry, dict):
        raise ValueError(f"Provider at index {index} must be an object.")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"Provider at index {index} is missing name.")
    api_key_env = entry.get("api_key_env")
    if not isinstance(api_key_env, str) or not api_key_env.strip():
        raise ValueError(
            f"Provider '{name}' is missing api_key_env. "
            "API keys are read only from environment variables."
        )
    has_url = isinstance(entry.get("base_url"), str) and entry.get("base_url").strip()
    has_url_env = isinstance(entry.get("base_url_env"), str) and entry.get("base_url_env").strip()
    if not has_url and not has_url_env:
        raise ValueError(f"Provider '{name}' is missing base_url.")
    models = entry.get("supported_models", [])
    if not isinstance(models, list) or not all(isinstance(item, str) and item.strip() for item in models):
        raise ValueError(f"Provider '{name}' supported_models must be a list of model id strings.")
    if "priority" in entry:
        try:
            int(entry["priority"])
        except (TypeError, ValueError):
            raise ValueError(f"Provider '{name}' priority must be an integer.")


def _split_models(raw):
    return [part.strip() for part in raw.split(",") if part.strip()]


def build_providers(config=None):
    config = config or load_config()
    providers = []
    for entry in config["providers"]:
        models_env = (entry.get("models_env") or "").strip()
        models_from_env = False
        env_models = os.environ.get(models_env, "").strip() if models_env else ""
        if env_models:
            models = _split_models(env_models)
            models_from_env = True
        else:
            models = [item.strip() for item in entry.get("supported_models", [])]
        base_url_env = (entry.get("base_url_env") or "").strip()
        base_url = (entry.get("base_url") or "").strip()
        if base_url_env and os.environ.get(base_url_env, "").strip():
            base_url = os.environ[base_url_env].strip()
        providers.append({
            "id": (entry.get("id") or entry["name"]).strip(),
            "name": entry["name"].strip(),
            "base_url": base_url.rstrip("/"),
            "base_url_env": base_url_env,
            "api_key_env": entry["api_key_env"].strip(),
            "supported_models": models,
            "priority": int(entry.get("priority", 0)),
            "catalog_title": entry.get("catalog_title"),
            "catalog_visible": entry.get("catalog_visible", True),
            "catalog_entries": entry.get("catalog_entries"),
            "_models_from_env": models_from_env,
        })
    return providers


def effective_default_model(config=None):
    env_model = os.environ.get("MULTILLM_DEFAULT_MODEL", "").strip()
    if env_model:
        return env_model
    config = config or load_config()
    return config["default_model"].strip()


def require_api_key(provider):
    env_name = provider["api_key_env"]
    value = os.environ.get(env_name)
    if value is None or not str(value).strip():
        raise MissingApiKeyError(
            f"Missing API key for provider '{provider['name']}'. "
            f"Set the {env_name} environment variable."
        )
    return str(value).strip()


def get_candidates(model, providers=None, default_model=None):
    providers = build_providers() if providers is None else providers
    if default_model is None:
        default_model = effective_default_model()
    target_model = model.strip() if model else default_model
    matching = []
    fallback = []
    for provider in providers:
        if target_model in provider["supported_models"]:
            matching.append(provider)
        else:
            fallback.append(provider)
    matching.sort(key=lambda item: item["priority"], reverse=True)
    return target_model, matching or fallback


def call_single_provider(provider, model, messages, temperature=0.7, max_tokens=4096):
    api_key = require_api_key(provider)
    base_url = provider.get("base_url") or ""
    if not base_url:
        env_name = provider.get("base_url_env") or "base_url"
        raise ValueError(
            f"Provider '{provider['name']}' has no base URL. "
            f"Set {env_name} or base_url in the MultiLLM config."
        )
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
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

    for provider in providers:
        log(f"Routing {target_model} to provider '{provider['name']}'...")
        try:
            return call_single_provider(provider, target_model, messages, temperature, max_tokens)
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            log(f"Provider '{provider['name']}' HTTP {exc.code}: {err_body[:100]}")
            errors.append(f"{provider['name']} HTTP {exc.code}: {err_body}")
        except MissingApiKeyError as exc:
            log(str(exc))
            errors.append(str(exc))
        except Exception as exc:
            log(f"Provider '{provider['name']}' failed: {exc}")
            errors.append(f"{provider['name']} error: {str(exc)}")

    return f"LLM_ERROR: Все провайдеры вернули ошибку для модели '{target_model}':\n" + "\n".join(errors)


def handle_ask(prompt, model=None, system_prompt=None, temperature=0.7):
    chosen_model = model.strip() if model else effective_default_model()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    return call_llm(chosen_model, messages, temperature=temperature)


def handle_review(work_folder, focus="", model=None):
    chosen_model = model.strip() if model else effective_default_model()
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


def _catalog_entries(provider):
    if provider.get("_models_from_env") or not provider.get("catalog_entries"):
        return list(provider["supported_models"])
    return list(provider["catalog_entries"])


def handle_list_models():
    config = load_config()
    providers = build_providers(config)
    lines = ["Подключенные пулы токенов и модели:"]
    visible = []
    for provider in providers:
        if provider.get("_models_from_env") or provider.get("catalog_visible", True):
            visible.append(provider)
    for index, provider in enumerate(visible, start=1):
        title = provider.get("catalog_title") or provider["name"]
        lines.append(f"{index}. {title}:")
        for entry in _catalog_entries(provider):
            lines.append(f"   - {entry}")
        if index != len(visible):
            lines.append("")
    footer = config.get("catalog_footer")
    if isinstance(footer, str) and footer.strip():
        lines.append("")
        lines.append(footer.strip())
    return "\n".join(lines)


def _configured_model_ids(providers):
    model_ids = []
    for provider in providers:
        for model_id in provider["supported_models"]:
            if model_id not in model_ids:
                model_ids.append(model_id)
    return model_ids


def get_tools(config=None):
    config = config or load_config()
    providers = build_providers(config)
    default_model = config["default_model"].strip()
    copy = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    models_overridden = any(provider.get("_models_from_env") for provider in providers)
    model_ids = _configured_model_ids(providers)
    live_model_description = (
        "Идентификатор модели (" + ", ".join(model_ids) + ")"
        if model_ids else "Идентификатор модели"
    )

    def text(key, fallback):
        if models_overridden and key in ("ask_model_description", "review_model_description"):
            return fallback
        value = copy.get(key)
        if isinstance(value, str) and value.strip():
            return value
        return fallback

    return [
        {
            "name": "multillm_ask",
            "description": text(
                "ask_description",
                "Задать вопрос или передать задачу выбранной модели из настроенного пула провайдеров."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Текст запроса / задачи для модели"
                    },
                    "model": {
                        "type": "string",
                        "description": text("ask_model_description", live_model_description),
                        "default": default_model
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
            "description": text(
                "review_description",
                "Провести код-ревью через модель из настроенного пула провайдеров."
            ),
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
                        "description": text(
                            "review_model_description",
                            f"Модель (по умолчанию {default_model})"
                        )
                    }
                },
                "required": ["work_folder"]
            }
        },
        {
            "name": "multillm_list_models",
            "description": text(
                "list_description",
                "Получить список поддерживаемых моделей и провайдеров из текущей конфигурации."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]


def main():
    try:
        config = load_config()
        providers = build_providers(config)
    except (OSError, ValueError) as exc:
        log(f"Failed to load MultiLLM config: {exc}")
        raise SystemExit(1)
    names = ", ".join(provider["name"] for provider in providers)
    log(f"multillm-bridge MCP server starting ({names}); config {config['_path']}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            log(f"Failed to parse JSON: {exc}")
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
                    "tools": get_tools()
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
            except Exception as exc:
                log(f"Error handling tool call: {traceback.format_exc()}")
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": f"LLM_ERROR: {str(exc)}"
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


load_dotenv()


if __name__ == "__main__":
    main()
