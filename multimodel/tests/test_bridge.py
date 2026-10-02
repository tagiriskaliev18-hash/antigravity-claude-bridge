"""Offline tests of ai-bridge: a fake OpenAI-compatible gateway on 127.0.0.1 plays healthy, limited,
flaky, broken and key-rejecting pools. Run:  python -m unittest discover -s multimodel/tests -v
Nothing here touches the network or real keys."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "claude_bridge.py")
CALLS = []
FLAKY = {"n": 0}


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body, headers=None):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model = req["model"]
        text = req["messages"][-1]["content"]
        CALLS.append((model, text))
        if model == "limit":
            return self.reply(429, {"error": {"message": "rate limit"}}, {"Retry-After": "120"})
        if model == "badkey":
            return self.reply(401, {"error": {"message": "invalid api key"}})
        if model == "down":
            return self.reply(503, {"error": {"message": "unavailable"}})
        if model == "novision" and isinstance(text, list):
            return self.reply(400, {"error": {"message": "image input is not supported"}})
        if model == "flaky":
            FLAKY["n"] += 1
            if FLAKY["n"] % 2 == 1:
                return self.reply(502, {"error": {"message": "bad gateway"}})
        if "Второй раунд" in text:
            answer = f"Критика: участник ошибся. Уточнённое решение от {model}. Уверенность: 8/10"
        elif "Ты председатель" in text:
            answer = "Согласие есть. Уверенность итога: 9/10"
        else:
            answer = f"Ответ модели {model}. Уверенность: 6/10"
        self.reply(200, {"model": model, "choices": [{"message": {"content": answer}}],
                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})


def pool(model, account="acc"):
    return {"type": "openai_compatible", "account": account, "base_url": "http://127.0.0.1:%d/v1",
            "model": model, "tier": "paid", "privacy": "private"}


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        CALLS.clear()
        FLAKY["n"] = 0
        self.dir = tempfile.mkdtemp()
        shutil.copy(SRC, self.dir)
        port = self.server.server_address[1]
        provs = {"good": pool("good"), "good2": pool("good2"), "limited": pool("limit"), "broken": pool("down"),
                 "flaky": pool("flaky"), "rejected": pool("badkey"), "cheap": pool("cheap", "small")}
        for p in provs.values():
            p["base_url"] = p["base_url"] % port
        self.write("providers.json", {"default_provider": "good", "providers": provs,
                                      "accounts": {"acc": {"title": "Main", "balance_units": None},
                                                   "small": {"title": "Small", "balance_units": 20}}})
        role = lambda primary, *fb: {"name": primary, "primary_provider": primary, "fallback_providers": list(fb),
                                     "skills": [], "description": "test role"}
        self.write("agents.json", {"default_agent": "developer",
                                   "consilium": {"members": ["a1", "a2", "a3"], "chair": "chief", "rounds": 2},
                                   "agents": {"a1": role("good"), "a2": role("good2"), "a3": role("limited", "good"),
                                              "chief": role("good"), "developer": role("good"),
                                              "fast_developer": role("cheap", "good"), "reviewer": role("good2"),
                                              "security_auditor": role("good"), "architect": role("good"),
                                              "lim": role("limited", "good"), "brk": role("broken", "good"),
                                              "flk": role("flaky"), "key": role("rejected", "good"),
                                              "budget": role("cheap", "good")}})
        spec = importlib.util.spec_from_file_location("bridge_under_test", os.path.join(self.dir, "claude_bridge.py"))
        self.b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.b)
        self.b.log = lambda msg: None
        self._sleep = time.sleep
        time.sleep = lambda s: None  # no real waiting for the retry
        self.b.find_claude = lambda: None

    def tearDown(self):
        time.sleep = self._sleep
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def health(self):
        return self.b.read_health()

    def test_limit_pauses_pool_and_next_call_skips_it(self):
        res = self.b.agent_exec("lim", "задача")
        self.assertEqual(res["reply"].provider, "good")
        h = self.health()["limited"]
        self.assertAlmostEqual(h["until"] - time.time(), 120, delta=5)  # Retry-After honoured
        CALLS.clear()
        res = self.b.agent_exec("lim", "задача")
        self.assertEqual(res["reply"].provider, "good")
        self.assertNotIn("limit", [m for m, _ in CALLS])  # paused pool not called again
        self.assertIn("на паузе", res["tried"][0][1])

    def test_rejected_key_pauses_for_long(self):
        self.b.agent_exec("key", "задача")
        h = self.health()["rejected"]
        self.assertGreater(h["until"] - time.time(), 1700)
        self.assertIn("ключ не принят", h["reason"])

    def test_short_outage_is_retried_once(self):
        res = self.b.agent_exec("flk", "задача")
        self.assertTrue(res["reply"] and res["reply"].ok)
        self.assertEqual([m for m, _ in CALLS], ["flaky", "flaky"])

    def test_three_failures_in_a_row_pause_pool(self):
        for _ in range(2):
            self.b.agent_exec("brk", "задача")
            self.assertFalse(self.b.pause_of("broken", self.health()))
        self.b.agent_exec("brk", "задача")
        self.assertTrue(self.b.pause_of("broken", self.health()))

    def test_success_lifts_pause_and_resume_works(self):
        self.b.agent_exec("lim", "задача")
        self.assertEqual(self.b.resume_pools("limited"), ["limited"])
        self.assertFalse(self.b.pause_of("limited", self.health()))

    def test_paused_pool_is_tried_when_nothing_else_answers(self):
        self.b.agent_exec("flk", "задача")  # warm: flaky answers on retry
        h = self.health()
        h["flaky"] = {"until": time.time() + 600, "reason": "лимит", "fails": 1}
        self.b.write_json_atomic(self.b.health_path(), h)
        CALLS.clear()
        res = self.b.agent_exec("flk", "задача")
        self.assertTrue(res["reply"] and res["reply"].ok)
        self.assertFalse(self.b.pause_of("flaky", self.health()))

    def test_spent_budget_skips_pool(self):
        self.b.agent_exec("budget", "задача")  # 15 of 20 units
        self.b.agent_exec("budget", "задача")  # 30 of 20: spent
        CALLS.clear()
        res = self.b.agent_exec("budget", "задача")
        self.assertEqual(res["reply"].provider, "good")
        self.assertNotIn("cheap", [m for m, _ in CALLS])
        self.assertIn("исчерпан", res["tried"][0][1])

    def test_consilium_runs_two_rounds_and_reports_confidence(self):
        out = self.b.handle_consilium("Какой вариант лучше?")
        texts = [t for _, t in CALLS]
        self.assertEqual(sum("Второй раунд" in t for t in texts), 3)
        chair = [t for t in texts if "Ты председатель" in t][0]
        self.assertIn("после взаимной критики", chair)
        self.assertIn("Участник A", chair)
        self.assertIn("2 раунда", out)
        self.assertIn("6 → 8", out)
        self.assertIn("Уверенность итога: 9/10", out)

    def test_consilium_one_round(self):
        out = self.b.handle_consilium("Вопрос", rounds=1)
        self.assertFalse(any("Второй раунд" in t for _, t in CALLS))
        self.assertIn("1 раунд", out)

    def test_auto_route(self):
        r = self.b.route_task
        self.assertEqual(r("Проверь код на SQL-инъекции и утечки секретов")[0], "security_auditor")
        self.assertEqual(r("Сделай ревью этого diff")[0], "reviewer")
        self.assertEqual(r("Спроектируй архитектуру сервиса уведомлений")[0], "architect")
        self.assertEqual(r("Напиши функцию сортировки")[0], "fast_developer")
        self.assertEqual(r("Напиши функцию " + "x" * 400)[0], "developer")
        self.assertEqual(r("привет")[0], "fast_developer")

    def test_auto_run_says_what_it_chose(self):
        out = self.b.handle_auto_run("Проверь код на уязвимости")
        self.assertIn("Автовыбор: роль **security_auditor**", out)

    def test_retry_hint(self):
        self.assertEqual(self.b.retry_hint("", "30"), 30)
        self.assertEqual(self.b.retry_hint("Please try again in 1m30s"), 90)
        self.assertEqual(self.b.retry_hint("Resets in 2h6m"), 7560)
        self.assertIsNone(self.b.retry_hint("nothing"))

    def test_retire_old_bridge(self):
        home = os.path.join(self.dir, "home")
        os.makedirs(os.path.join(home, ".gemini", "config"))
        cfg = os.path.join(home, ".gemini", "config", "mcp_config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"mcpServers": {
                "claude-bridge": {"command": "python", "args": ["C:\\projects\\tools\\claude_bridge.py"]},
                "multillm-bridge": {"command": "python", "args": ["council_tap.py", "--", "python", "C:\\projects\\tools\\multillm_bridge.py"]}}}, fh)
        old = os.path.join(self.dir, "multillm_bridge.py")
        with open(old, "w") as fh:
            fh.write("KEY = 'x'\n")
        self.b.HOME = home
        import io, contextlib
        with contextlib.redirect_stdout(io.StringIO()):
            self.b.cli_retire_old()
        servers = json.load(open(cfg))["mcpServers"]
        self.assertEqual(list(servers), ["claude-bridge"])
        self.assertTrue([f for f in os.listdir(os.path.dirname(cfg)) if ".bak-" in f])
        self.assertFalse(os.path.exists(old))
        self.assertTrue([f for f in os.listdir(self.dir) if f.startswith("multillm_bridge.py.retired-")])

    def test_attachments_reach_every_consilium_member_and_chair(self):
        import zipfile
        note = os.path.join(self.dir, "plan.txt")
        with open(note, "w", encoding="utf-8") as fh:
            fh.write("ядро ОС: планировщик задач")
        doc = os.path.join(self.dir, "spec.docx")
        with zipfile.ZipFile(doc, "w") as z:
            z.writestr("word/document.xml", "<w:document><w:p><w:t>браузер: песочница</w:t></w:p></w:document>")
        secret = os.path.join(self.dir, ".env")
        with open(secret, "w") as fh:
            fh.write("KEY=1")
        out = self.b.call_tool("consilium", {"task": "оцени план", "rounds": 1, "attachments": [note, doc, secret]})
        self.assertIn("Уверенность итога", out)
        texts = [t for _, t in CALLS]
        self.assertTrue(texts and all("ядро ОС: планировщик задач" in t and "браузер: песочница" in t for t in texts))
        self.assertFalse(any("KEY=1" in t for t in texts))
        self.assertTrue(all(".env: похож на файл с ключами" in t for t in texts))

    def test_auto_run_routes_on_the_question_not_the_attachment(self):
        f = os.path.join(self.dir, "a.txt")
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("security уязвимость xss " * 20)
        out = self.b.call_tool("auto_run", {"task": "привет", "attachments": [f]})
        self.assertNotIn("security_auditor", out.split("\n")[0] + out[-300:])
        self.assertIn("security уязвимость", CALLS[-1][1])

    def test_images_go_to_vision_pools_and_others_get_text(self):
        img = os.path.join(self.dir, "screen.png")
        with open(img, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nfake")
        cfg = json.load(open(os.path.join(self.dir, "providers.json"), encoding="utf-8"))
        cfg["providers"]["good"]["vision"] = True
        cfg["providers"]["blind"] = dict(cfg["providers"]["good"], model="novision", vision=True)
        self.write("providers.json", cfg)
        self.b.call_tool("model_ask", {"provider": "good", "question": "что на экране?", "attachments": [img]})
        sent = CALLS[-1][1]
        self.assertIsInstance(sent, list)
        self.assertTrue(sent[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("screen.png", sent[0]["text"])
        CALLS.clear()
        self.b.call_tool("model_ask", {"provider": "good2", "question": "что на экране?", "attachments": [img]})
        self.assertIsInstance(CALLS[-1][1], str)  # model name without vision: text only, told about the picture
        self.assertIn("Если ты их не видишь", CALLS[-1][1])
        CALLS.clear()
        out = self.b.call_tool("model_ask", {"provider": "blind", "question": "что на экране?", "attachments": [img]})
        self.assertIn("Ответ модели novision", out)
        self.assertEqual([type(t) for _, t in CALLS], [list, str])  # rejected pictures, asked again with text

    def test_mcp_tools_list(self):
        names = [t["name"] for t in self.b.TOOLS]
        self.assertIn("auto_run", names)
        props = [t for t in self.b.TOOLS if t["name"] == "consilium"][0]["inputSchema"]["properties"]
        self.assertIn("rounds", props)


if __name__ == "__main__":
    unittest.main(verbosity=2)
