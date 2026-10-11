import io
import json
import os
import sys
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError

import multillm_bridge as bridge

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

HISTORICAL_CATALOG = "\n".join([
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
])


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def http_error(code, body):
    headers = Message()
    return HTTPError(
        url="https://provider.example/v1/chat/completions",
        code=code,
        msg="error",
        hdrs=headers,
        fp=io.BytesIO(body.encode("utf-8")),
    )


def chat_payload(content="", reasoning=""):
    message = {"content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return {"choices": [{"message": message}]}


def test_missing_api_key_is_clear_and_does_not_call_http():
    with patch("urllib.request.urlopen") as urlopen:
        result = bridge.handle_ask("hello", model="gpt-5.5")

    urlopen.assert_not_called()
    assert result.startswith("LLM_ERROR:")
    assert "OCTAVAPI_API_KEY" in result
    assert "Missing API key" in result
    assert "XYVERO_API_KEY" not in result
    assert "MULTILLM_API_KEY" not in result


def test_blank_api_key_is_treated_as_missing(monkeypatch):
    monkeypatch.setenv("OCTAVAPI_API_KEY", "   ")
    with patch("urllib.request.urlopen") as urlopen:
        result = bridge.handle_ask("hello", model="gpt-5.5")
    urlopen.assert_not_called()
    assert "OCTAVAPI_API_KEY" in result


def test_request_uses_env_key_and_configured_endpoint(monkeypatch):
    monkeypatch.setenv("XYVERO_API_KEY", "test-xyvero-key")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        return FakeResponse(chat_payload("pong"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("ping", model="deepseek-v4.1-flash", system_prompt="be brief")

    assert result == "pong"
    req = captured["req"]
    assert req.full_url == "https://xyvero.space/v1/chat/completions"
    assert req.get_header("Authorization") == "Bearer test-xyvero-key"
    assert req.get_header("Content-type") == "application/json"
    assert captured["timeout"] == 120
    body = json.loads(req.data.decode("utf-8"))
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 4096
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "ping"},
    ]


def test_highest_priority_provider_is_selected(monkeypatch):
    monkeypatch.setenv("OCTAVAPI_API_KEY", "octava-key")
    monkeypatch.setenv("MULTILLM_API_KEY", "cvc-key")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return FakeResponse(chat_payload("from-octava"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("compare", model="gpt-6-astra")

    assert result == "from-octava"
    assert captured["url"] == "https://octavapi.shop/v1/chat/completions"
    assert captured["auth"] == "Bearer octava-key"


def test_http_error_falls_back_to_next_provider(monkeypatch):
    monkeypatch.setenv("XYVERO_API_KEY", "xyvero-key")
    monkeypatch.setenv("MULTILLM_API_KEY", "cvc-key")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise http_error(503, "upstream down")
        return FakeResponse(chat_payload("recovered"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("retry", model="deepseek-v4.1-flash")

    assert result == "recovered"
    assert calls == [
        "https://xyvero.space/v1/chat/completions",
        "https://api.aikeysforyou.com/v1/chat/completions",
    ]


def test_missing_preferred_key_falls_back_without_http(monkeypatch):
    monkeypatch.setenv("MULTILLM_API_KEY", "cvc-key")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, req.get_header("Authorization")))
        return FakeResponse(chat_payload("fallback"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("go", model="deepseek-v4.1-flash")

    assert result == "fallback"
    assert calls == [(
        "https://api.aikeysforyou.com/v1/chat/completions",
        "Bearer cvc-key",
    )]


def test_all_candidate_keys_missing_lists_each_variable():
    with patch("urllib.request.urlopen") as urlopen:
        result = bridge.handle_ask("go", model="deepseek-v4.1-flash")
    urlopen.assert_not_called()
    assert result.startswith("LLM_ERROR:")
    assert "XYVERO_API_KEY" in result
    assert "MULTILLM_API_KEY" in result
    assert "OCTAVAPI_API_KEY" not in result


def test_unknown_model_keeps_config_order_not_priority_sort(tmp_path, monkeypatch):
    config_path = tmp_path / "providers.json"
    config_path.write_text(json.dumps({
        "default_model": "a",
        "providers": [
            {
                "name": "low",
                "base_url": "https://low.example/v1",
                "api_key_env": "LOW_KEY",
                "supported_models": ["a"],
                "priority": 1,
            },
            {
                "name": "high",
                "base_url": "https://high.example/v1",
                "api_key_env": "HIGH_KEY",
                "supported_models": ["b", "shared"],
                "priority": 9,
            },
            {
                "name": "low-shared",
                "base_url": "https://shared.example/v1",
                "api_key_env": "LOW_KEY",
                "supported_models": ["shared"],
                "priority": 3,
            },
        ],
    }), encoding="utf-8")
    monkeypatch.setenv("MULTILLM_CONFIG", str(config_path))
    monkeypatch.setenv("LOW_KEY", "low-secret")
    monkeypatch.setenv("HIGH_KEY", "high-secret")
    calls = []

    def fake_urlopen(req, timeout=None):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise http_error(500, "nope")
        return FakeResponse(chat_payload("second"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("x", model="not-listed")

    assert result == "second"
    assert calls == [
        "https://low.example/v1/chat/completions",
        "https://high.example/v1/chat/completions",
    ]

    calls.clear()

    def shared_urlopen(req, timeout=None):
        calls.append(req.full_url)
        return FakeResponse(chat_payload("shared-hit"))

    with patch("urllib.request.urlopen", side_effect=shared_urlopen):
        shared = bridge.handle_ask("x", model="shared")
    assert shared == "shared-hit"
    assert calls == ["https://high.example/v1/chat/completions"]


def test_models_env_overrides_provider_list(monkeypatch):
    monkeypatch.setenv("XYVERO_API_KEY", "xyvero-key")
    monkeypatch.setenv("XYVERO_MODELS", " custom-model , deepseek-v4.1-flash ")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(chat_payload("custom"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("hi", model="custom-model")

    assert result == "custom"
    assert captured["url"] == "https://xyvero.space/v1/chat/completions"
    assert captured["body"]["model"] == "custom-model"


def test_base_url_and_default_model_env(monkeypatch):
    monkeypatch.setenv("MULTILLM_API_KEY", "cvc-key")
    monkeypatch.setenv("MULTILLM_BASE_URL", "https://override.example/v1/")
    monkeypatch.setenv("MULTILLM_DEFAULT_MODEL", "deepseek-v4-pro")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(chat_payload("defaulted"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("no model argument")

    assert result == "defaulted"
    assert captured["url"] == "https://override.example/v1/chat/completions"
    assert captured["body"]["model"] == "deepseek-v4-pro"


def test_custom_config_file(tmp_path, monkeypatch):
    config_path = tmp_path / "local.json"
    config_path.write_text(json.dumps({
        "default_model": "local-model",
        "providers": [{
            "name": "local",
            "base_url": "https://llm.example/v1",
            "api_key_env": "LOCAL_LLM_API_KEY",
            "supported_models": ["local-model"],
            "priority": 1,
        }],
    }), encoding="utf-8")
    monkeypatch.setenv("MULTILLM_CONFIG", str(config_path))
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "local-key")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        return FakeResponse(chat_payload("local-ok"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_ask("hi")

    assert result == "local-ok"
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["auth"] == "Bearer local-key"


def test_missing_config_file_raises(tmp_path, monkeypatch):
    missing = tmp_path / "missing.json"
    monkeypatch.setenv("MULTILLM_CONFIG", str(missing))
    try:
        bridge.load_config()
    except FileNotFoundError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
    assert "MULTILLM_CONFIG" in message
    assert str(missing) in message


def test_provider_without_api_key_env_is_rejected(tmp_path, monkeypatch):
    config_path = tmp_path / "bad.json"
    config_path.write_text(json.dumps({
        "default_model": "m",
        "providers": [{"name": "bare", "base_url": "https://x/v1", "supported_models": ["m"]}],
    }), encoding="utf-8")
    monkeypatch.setenv("MULTILLM_CONFIG", str(config_path))
    try:
        bridge.load_config()
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert "api_key_env" in message


def test_reasoning_and_empty_choices(monkeypatch):
    monkeypatch.setenv("OCTAVAPI_API_KEY", "octava-key")
    responses = [
        FakeResponse(chat_payload(reasoning="think")),
        FakeResponse(chat_payload(content="answer", reasoning="think")),
        FakeResponse({"choices": []}),
    ]

    def fake_urlopen(req, timeout=None):
        return responses.pop(0)

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        reasoning_only = bridge.handle_ask("q", model="gpt-5.5")
        both = bridge.handle_ask("q", model="gpt-5.5")
        empty = bridge.handle_ask("q", model="gpt-5.5")

    assert reasoning_only == (
        "### Рассуждение (gpt-5.5 @ octavapi (GPT-6 Astra & Opus 5.5 Pool)):\nthink"
    )
    assert both == (
        "answer\n\n<details><summary>Мысли модели "
        "(octavapi (GPT-6 Astra & Opus 5.5 Pool))</summary>\nthink\n</details>"
    )
    assert empty.startswith("LLM_ERROR:")
    assert "Ответ пуст" in empty


def test_list_models_matches_historical_catalog():
    assert bridge.handle_list_models() == HISTORICAL_CATALOG


def test_list_models_follows_env_override(monkeypatch):
    monkeypatch.setenv("OCTAVAPI_MODELS", "gpt-6-astra,my-model")
    catalog = bridge.handle_list_models()
    assert "my-model" in catalog
    assert "2. octavapi (GPT-6 Astra & Opus 5.5 Pool):" in catalog
    assert "Пул CheapVibeCode" in catalog


def test_tool_schema_uses_config_copy_until_models_are_overridden(monkeypatch):
    tools = {tool["name"]: tool for tool in bridge.get_tools()}
    assert tools["multillm_ask"]["inputSchema"]["properties"]["model"]["default"] == "deepseek-v4.1-flash"
    assert "deepseek-v4.1-flash" in tools["multillm_ask"]["inputSchema"]["properties"]["model"]["description"]
    monkeypatch.setenv("MULTILLM_DEFAULT_MODEL", "deepseek-v4-pro")
    still_config_default = bridge.get_tools()[0]["inputSchema"]["properties"]["model"]["default"]
    assert still_config_default == "deepseek-v4.1-flash"
    monkeypatch.setenv("XYVERO_MODELS", "only-this")
    overridden = {tool["name"]: tool for tool in bridge.get_tools()}
    description = overridden["multillm_ask"]["inputSchema"]["properties"]["model"]["description"]
    assert "only-this" in description


def test_review_prompt_includes_focus(tmp_path, monkeypatch):
    monkeypatch.setenv("XYVERO_API_KEY", "xyvero-key")
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResponse(chat_payload("reviewed"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = bridge.handle_review(str(tmp_path), focus="безопасность")

    assert result == "reviewed"
    user_text = captured["body"]["messages"][-1]["content"]
    assert str(tmp_path) in user_text
    assert "безопасность" in user_text
    assert captured["body"]["messages"][0]["role"] == "system"


def test_mcp_server_routes_tool_call(monkeypatch, capsys):
    monkeypatch.setenv("XYVERO_API_KEY", "xyvero-key")
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "multillm_ask",
                "arguments": {"prompt": "hi", "model": "deepseek-v4.1-flash"},
            },
        }),
        json.dumps({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "multillm_ask", "arguments": {"prompt": "hi", "model": "gpt-5.5"}},
        }),
    ]) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(requests))

    def fake_urlopen(req, timeout=None):
        return FakeResponse(chat_payload("pong"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        bridge.main()

    captured = capsys.readouterr()
    messages = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    by_id = {message["id"]: message for message in messages}
    assert by_id[1]["result"]["serverInfo"]["name"] == "multillm-bridge"
    assert by_id[2]["result"]["tools"][0]["name"] == "multillm_ask"
    assert by_id[3]["result"]["isError"] is False
    assert by_id[3]["result"]["content"][0]["text"] == "pong"
    assert by_id[4]["result"]["isError"] is True
    assert "OCTAVAPI_API_KEY" in by_id[4]["result"]["content"][0]["text"]


def test_main_exits_when_config_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MULTILLM_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    try:
        bridge.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected SystemExit")
    captured = capsys.readouterr()
    assert "Failed to load MultiLLM config" in captured.err
    assert "MULTILLM_CONFIG" in captured.err


def test_dotenv_fills_missing_keys_without_overriding(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "# comment",
            "OCTAVAPI_API_KEY=from-file",
            'export XYVERO_API_KEY="quoted-key"',
            "MULTILLM_API_KEY='single'",
            "NOT_A_PAIR",
            "",
        ]),
        encoding="utf-8",
    )
    monkeypatch.setenv("OCTAVAPI_API_KEY", "from-env")
    monkeypatch.delenv("XYVERO_API_KEY", raising=False)
    monkeypatch.delenv("MULTILLM_API_KEY", raising=False)
    bridge._load_dotenv_file(str(env_file))
    assert os.environ["OCTAVAPI_API_KEY"] == "from-env"
    assert os.environ["XYVERO_API_KEY"] == "quoted-key"
    assert os.environ["MULTILLM_API_KEY"] == "single"


def test_repository_has_no_hardcoded_api_keys():
    needles = (
        "sk-" + "FOQVMu",
        "sk-" + "octava-live_",
        "sk-" + "cvc-",
    )
    skip_dirs = {".git", ".pytest_cache", "__pycache__", "venv", ".venv"}
    offenders = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs]
        for filename in filenames:
            path = os.path.join(dirpath, filename)
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    text = handle.read()
            except OSError:
                continue
            for needle in needles:
                if needle in text:
                    offenders.append(f"{path}: {needle}")
    assert offenders == []

    bridge_source = open(os.path.join(ROOT, "tools", "multillm_bridge.py"), encoding="utf-8").read()
    assert "sk-" not in bridge_source
