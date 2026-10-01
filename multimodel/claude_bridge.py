#!/usr/bin/env python3
"""ai-bridge 3: MCP server between Antigravity and the multi-model pool.

It gives Antigravity Claude Code CLI (claude_review, claude_ask, claude_implement), roles from
agents.json with their own model chains (agent_run), a council of several models that answer in
parallel and a chair that writes the verdict (consilium), and a token ledger (token_usage.json)
filled from the usage that every answer reports.

  python claude_bridge.py              MCP server on stdin/stdout (Antigravity starts it)
  python claude_bridge.py --keys       type API keys into .env next to providers.json (input hidden)
  python claude_bridge.py --import-keys   copy keys that older bridges on this PC keep in their code into .env
  python claude_bridge.py --check      one short real request to every pool that has a key
  python claude_bridge.py --status     pools, keys (names only), roles and token totals
  python claude_bridge.py --selftest   offline check of providers.json, agents.json and skills

Keys never go into providers.json: each pool names the variable that holds its key (api_key_env),
and the value comes from the environment or from .env. Nothing here prints a key.
"""
import contextvars
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

VERSION = "3.0.0"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ai-bridge/" + VERSION
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_BRIDGE_TIMEOUT", "300"))
HISTORY_KEEP = 200
ERROR_PREFIXES = ("CLAUDE_ERROR", "CLAUDE_LIMIT_REACHED", "PROVIDER_ERROR", "PROVIDER_OFFLINE",
                  "PROVIDER_TIMEOUT", "PROVIDER_LIMIT_REACHED", "PROVIDER_NO_KEY", "AGENT_ERROR",
                  "SOUP_ERROR", "BRIDGE_ERROR", "MODEL_SWITCH_ERROR", "CONSILIUM_ERROR")
LIMIT_SIGNALS = ("rate limit", "usage limit", "exhausted your current quota", "hit your limit",
                 "too many requests", "limit reached", "insufficient_quota", "quota exceeded")
# values set outside .env (Windows variables, the MCP config "env" block) win over the file
ORIGINAL_ENV = dict(os.environ)

DEFAULT_PROVIDERS = {
    "default_provider": "claude",
    "providers": {
        "claude": {"type": "cli", "command": "claude", "model": "opus", "model_env": "CLAUDE_BRIDGE_MODEL",
                   "tier": "subscription", "privacy": "private", "description": "Claude Code CLI"},
        "pollinations": {"type": "openai_compatible", "base_url": "https://text.pollinations.ai/openai",
                         "chat_url": "https://text.pollinations.ai/openai", "model": "openai-fast",
                         "tier": "free", "privacy": "public", "description": "Открытый шлюз без ключа"},
    },
}

LOG_LOCK = threading.Lock()
OUT_LOCK = threading.Lock()
LEDGER_LOCK = threading.Lock()


# the JSON-RPC id of the tool call a log line belongs to, so the call log can tell parallel calls apart
RPC_ID = contextvars.ContextVar("rpc_id", default=None)


def log(msg):
    rid = RPC_ID.get()
    tag = f"[rpc {json.dumps(rid)}] " if rid is not None else ""
    with LOG_LOCK:
        sys.stderr.write(f"[ai-bridge] {tag}{msg}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------- files and settings

def config_dirs():
    dirs = [BASE_DIR, os.path.join(HOME, ".aiduo")]
    if IS_WIN:
        dirs.append(r"C:\projects\tools")
    out = []
    for d in dirs:
        if os.path.normcase(os.path.abspath(d)) not in [os.path.normcase(os.path.abspath(x)) for x in out]:
            out.append(d)
    return out


def find_file(name):
    for d in config_dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def home_dir():
    """The ledger, .env and consilium reports live next to providers.json (or next to this script)."""
    p = find_file("providers.json")
    return os.path.dirname(p) if p else BASE_DIR


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def write_json_atomic(path, data):
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    # on Windows the target can be open for a moment by a reader (Council Engine); retry briefly
    for _ in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05)
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise PermissionError(f"не удалось записать {path}")


class FileLock:
    """Cross-process lock: several bridge processes (one per client) share one ledger."""

    def __init__(self, path, timeout=10):
        self.path, self.timeout, self.fd = path, timeout, None

    def __enter__(self):
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 30:
                        os.remove(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError(f"файл занят: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
            os.remove(self.path)
        except OSError:
            pass


_ENV_CACHE = {}


def parse_env_file(path):
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (st.st_mtime, st.st_size)
    hit = _ENV_CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                if name.lower().startswith("export "):
                    name = name[7:].strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[name] = value
    except OSError:
        pass
    _ENV_CACHE[path] = (key, values)
    return values


def env_files():
    files = [os.path.join(home_dir(), ".env"), os.path.join(BASE_DIR, ".env"),
             os.path.join(os.path.dirname(BASE_DIR), ".env")]
    out = []
    for f in files:
        if os.path.normcase(f) not in [os.path.normcase(x) for x in out]:
            out.append(f)
    return out


def env_value(name):
    """A setting from the process environment, else from .env (re-read when the file changes)."""
    if not name:
        return ""
    v = ORIGINAL_ENV.get(name)
    if v:
        return v
    for f in env_files():
        v = parse_env_file(f).get(name)
        if v:
            return v
    return ""


def key_source(name):
    if not name:
        return None
    if ORIGINAL_ENV.get(name):
        return "переменная окружения"
    for f in env_files():
        if parse_env_file(f).get(name):
            return ".env"
    return None


def load_providers():
    path = find_file("providers.json")
    data = read_json(path) if path else None
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        if path:
            log(f"Ошибка чтения {path}, использую встроенный список")
        return DEFAULT_PROVIDERS, None
    return data, path


def load_agents():
    path = find_file("agents.json")
    data = read_json(path) if path else None
    if not isinstance(data, dict) or not isinstance(data.get("agents"), dict):
        return {"default_agent": "developer", "agents": {}}, None
    return data, path


def skill_dirs():
    return [os.path.join(d, "skills") for d in config_dirs()]


def load_skill(name):
    if not name:
        return ""
    safe = os.path.basename(str(name).strip())
    fname = safe if safe.endswith(".md") else safe + ".md"
    for d in skill_dirs():
        p = os.path.join(d, fname)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    return fh.read().strip()
            except OSError as e:
                log(f"Ошибка чтения навыка {p}: {e}")
    return ""


def list_skills():
    found = {}
    for d in skill_dirs():
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if fname.endswith(".md") and fname[:-3] not in found:
                title = fname[:-3]
                try:
                    with open(os.path.join(d, fname), "r", encoding="utf-8", errors="replace") as fh:
                        first = fh.readline().strip()
                    if first.startswith("#"):
                        title = first.lstrip("#").strip()
                except OSError:
                    pass
                found[fname[:-3]] = {"title": title, "path": os.path.join(d, fname)}
    return found


# ---------------------------------------------------------------- pools

def prov_model(p):
    return env_value(p.get("model_env")) or p.get("model") or "default"


def prov_base(p):
    return (env_value(p.get("base_url_env")) or p.get("base_url") or "").rstrip("/")


def prov_key(p):
    return p.get("api_key") or env_value(p.get("api_key_env"))


def key_state(p):
    """('cli' | 'none' | 'set' | 'missing', where it comes from)."""
    if p.get("type") == "cli":
        return "cli", None
    if p.get("api_key"):
        return "set", "providers.json"
    if not p.get("api_key_env"):
        return "none", None
    src = key_source(p["api_key_env"])
    return ("set", src) if src else ("missing", None)


def host_of(url):
    try:
        return urllib.parse.urlparse(url).netloc or url
    except Exception:
        return url


def resolve_provider(name, cfg):
    """A pool id from an id, a model name or a family word (deepseek, glm, minimax, kimi, qwen, gpt, claude)."""
    provs = cfg.get("providers") or {}
    n = (name or "").strip()
    if not n or n.lower() == "default":
        return cfg.get("default_provider") if cfg.get("default_provider") in provs else next(iter(provs), None)
    if n in provs:
        return n
    low = n.lower()
    for pid in provs:
        if pid.lower() == low:
            return pid
    for pid, p in provs.items():
        if str(p.get("model", "")).lower() == low:
            return pid
    for pid in provs:
        if pid.lower().startswith(low):
            return pid
    for pid, p in provs.items():
        if low in pid.lower() or low in str(p.get("model", "")).lower():
            return pid
    return None


def ssl_context():
    ca = os.path.join(BASE_DIR, "corporate_ca.pem")
    if os.path.isfile(ca):
        try:
            return ssl.create_default_context(cafile=ca)
        except Exception:
            pass
    return None


class Reply:
    def __init__(self, provider, ok, text="", kind=None, model=None, usage=None, ms=None):
        self.provider, self.ok, self.text, self.kind = provider, ok, text, kind
        self.model, self.usage, self.ms = model, usage, ms

    def short_why(self):
        return {"PROVIDER_NO_KEY": "нет ключа", "PROVIDER_LIMIT_REACHED": "лимит", "CLAUDE_LIMIT_REACHED": "лимит",
                "PROVIDER_TIMEOUT": "таймаут", "PROVIDER_OFFLINE": "недоступен", "CLI_MISSING": "Claude CLI не найден",
                }.get(self.kind) or (re.search(r"HTTP \d+", self.text or "") or [None])[0] or "ошибка"


def num(v):
    try:
        return int(v) if v is not None and float(v) == int(float(v)) else (float(v) if v is not None else None)
    except (TypeError, ValueError):
        return None


def parse_usage(u, data=None):
    if not isinstance(u, dict):
        return None
    prompt = num(u.get("prompt_tokens", u.get("input_tokens")))
    completion = num(u.get("completion_tokens", u.get("output_tokens")))
    total = num(u.get("total_tokens"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    if prompt is None and completion is None and total is None:
        return None
    cost = u.get("cost", u.get("cost_usd", (data or {}).get("cost")))
    credit = None
    for k in ("credit", "credits", "remaining_credit", "balance", "remaining"):
        if u.get(k) is not None:
            credit = u.get(k)
            break
    return {"prompt": prompt, "completion": completion, "total": total,
            "cost": num(cost) if not isinstance(cost, dict) else None, "credit": credit}


def http_json(url, payload=None, key=None, timeout=60):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def call_openai(pid, p, messages, max_tokens=None, timeout=None):
    model = prov_model(p)
    key = prov_key(p)
    if p.get("api_key_env") and not key:
        return Reply(pid, False, f"PROVIDER_NO_KEY: у пула '{pid}' нет ключа: переменная {p['api_key_env']} не задана "
                                 f"(ввести: python claude_bridge.py --keys)", "PROVIDER_NO_KEY", model)
    url = p.get("chat_url") or prov_base(p) + "/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": p.get("temperature", 0.2)}
    if max_tokens or p.get("max_tokens"):
        payload["max_tokens"] = max_tokens or p.get("max_tokens")
    t0 = time.time()
    ms = lambda: int((time.time() - t0) * 1000)
    try:
        data = http_json(url, payload, key, timeout or p.get("timeout", 180))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:600]
        except Exception:
            pass
        if e.code == 429 or any(s in body.lower() for s in LIMIT_SIGNALS):
            return Reply(pid, False, f"PROVIDER_LIMIT_REACHED (HTTP {e.code}): {body}".strip(), "PROVIDER_LIMIT_REACHED", model, ms=ms())
        return Reply(pid, False, f"PROVIDER_ERROR (HTTP {e.code}): {e.reason}. {body}".strip(), "PROVIDER_ERROR", model, ms=ms())
    except urllib.error.URLError as e:
        if isinstance(e.reason, (socket.timeout, TimeoutError)):
            return Reply(pid, False, f"PROVIDER_TIMEOUT: {host_of(url)} не ответил вовремя", "PROVIDER_TIMEOUT", model, ms=ms())
        return Reply(pid, False, f"PROVIDER_OFFLINE: не удалось связаться с {host_of(url)}: {e.reason}", "PROVIDER_OFFLINE", model, ms=ms())
    except (socket.timeout, TimeoutError):
        return Reply(pid, False, f"PROVIDER_TIMEOUT: {host_of(url)} не ответил вовремя", "PROVIDER_TIMEOUT", model, ms=ms())
    except ValueError:
        return Reply(pid, False, f"PROVIDER_ERROR: {host_of(url)} ответил не JSON", "PROVIDER_ERROR", model, ms=ms())
    except Exception as e:
        return Reply(pid, False, f"PROVIDER_ERROR: {e}", "PROVIDER_ERROR", model, ms=ms())
    if not isinstance(data, dict):
        return Reply(pid, False, "PROVIDER_ERROR: неожиданный формат ответа", "PROVIDER_ERROR", model, ms=ms())
    choices = data.get("choices") or []
    if not choices:
        err = data.get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        text = f"PROVIDER_ERROR: в ответе нет choices{': ' + str(msg)[:400] if msg else ''}"
        kind = "PROVIDER_LIMIT_REACHED" if msg and any(s in str(msg).lower() for s in LIMIT_SIGNALS) else "PROVIDER_ERROR"
        return Reply(pid, False, text.replace("PROVIDER_ERROR", kind, 1), kind, model, ms=ms())
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    reasoning = msg.get("reasoning_content") or msg.get("reasoning") or ""
    text = content.strip() or (f"<thinking>\n{reasoning.strip()}\n</thinking>" if str(reasoning).strip() else "")
    if not text:
        return Reply(pid, False, "PROVIDER_ERROR: пустой ответ модели", "PROVIDER_ERROR", data.get("model") or model, ms=ms())
    return Reply(pid, True, text, None, data.get("model") or model, parse_usage(data.get("usage"), data), ms())


# ---------------------------------------------------------------- Claude Code CLI

def find_claude():
    for c in (os.path.join(HOME, ".local", "bin", "claude.exe"), os.path.join(HOME, ".local", "bin", "claude"),
              shutil.which("claude")):
        if c and os.path.isfile(c):
            return c
    return None


def cli_provider(cfg):
    for pid, p in (cfg.get("providers") or {}).items():
        if p.get("type") == "cli":
            return pid, p
    return "claude", DEFAULT_PROVIDERS["providers"]["claude"]


def validate_work_folder(work_folder):
    if not work_folder or not os.path.isdir(work_folder):
        return None
    return os.path.abspath(work_folder)


# Models behind an HTTP gateway cannot open files, so a request with a project folder carries a map of the
# project, its README and the files the task names. Secrets never leave the computer this way.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next", ".idea", ".vscode",
             "target", ".gradle", "coverage", ".cache", ".pytest_cache", ".mypy_cache", "consilium", ".turbo", "out"}
SECRET_FILE = re.compile(r"(^\.env|\.pem$|\.key$|\.p12$|\.pfx$|^id_rsa|^id_ed25519|credential|secret|oauth|token_usage|"
                         r"\.sqlite$|\.db$|\.kdbx$)", re.I)
TEXT_EXT = re.compile(r"\.(py|js|mjs|cjs|ts|tsx|jsx|json|md|txt|toml|yaml|yml|ini|cfg|html|css|scss|sql|sh|ps1|bat|cmd|"
                      r"go|rs|java|kt|cs|cpp|c|h|hpp|rb|php|swift|vue|svelte|xml|gradle|dockerfile|env\.example)$", re.I)
CONTEXT_BUDGET = int(os.environ.get("AI_BRIDGE_CONTEXT_CHARS", "40000"))


def read_text_file(path, limit):
    try:
        with open(path, "rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    if b"\0" in raw[:2048]:
        return None
    text = raw[:limit].decode("utf-8", errors="replace")
    return text + ("\n…(файл обрезан)" if len(raw) > limit else "")


def project_context(folder, task="", files=None):
    """Tree of the project, README and the files that the task names or that were passed in `files`."""
    root = validate_work_folder(folder)
    if not root:
        return "", []
    real_root = os.path.realpath(root)
    tree, by_name, by_rel = [], {}, {}
    for cur, dirs, names in os.walk(root):
        rel_dir = os.path.relpath(cur, root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")) if depth < 3 else []
        for n in sorted(names):
            rel = n if rel_dir == "." else os.path.join(rel_dir, n)
            by_rel[rel.replace("\\", "/").lower()] = rel
            by_name.setdefault(n.lower(), []).append(rel)
            if len(tree) < 250:
                try:
                    size = os.path.getsize(os.path.join(cur, n))
                except OSError:
                    size = 0
                tree.append(f"{rel.replace(os.sep, '/')}  ({size} Б)")
    wanted = []
    for f in as_list(files):
        wanted.append(by_rel.get(f.replace("\\", "/").lower().lstrip("./")) or f)
    for word in set(re.findall(r"[\w./\\-]+\.[A-Za-z0-9]{1,8}", task or "")):
        w = word.replace("\\", "/").lower().strip("./")
        hit = by_rel.get(w) or (by_name.get(w)[0] if len(by_name.get(w) or []) == 1 else None)
        if hit:
            wanted.append(hit)
    readme = next((by_rel[r] for r in ("readme.md", "readme.txt", "readme") if r in by_rel), None)
    if readme:
        wanted.insert(0, readme)
    parts, used, budget = [], [], CONTEXT_BUDGET
    parts.append(f"Карта проекта {root} (первые {len(tree)} файлов, без служебных папок):\n" + "\n".join(tree))
    budget -= len(parts[0])
    for rel in dict.fromkeys(wanted):
        path = os.path.realpath(os.path.join(root, rel))
        name = os.path.basename(path)
        if not path.startswith(real_root.rstrip(os.sep) + os.sep) or not os.path.isfile(path) or SECRET_FILE.search(name):
            continue
        if not TEXT_EXT.search(name) and name.lower() not in ("readme", "dockerfile", "makefile"):
            continue
        if budget < 500:
            break
        text = read_text_file(path, min(6000 if rel == readme else 20000, budget - 200))
        if text is None:
            continue
        parts.append(f"--- ФАЙЛ {rel.replace(os.sep, '/')} ---\n{text}")
        used.append(rel.replace(os.sep, "/"))
        budget -= len(parts[-1])
    log(f"Project context: {root}: {len(used)} files, {CONTEXT_BUDGET - budget} chars")
    return "\n\n".join(parts), used


def run_claude(work_folder, prompt, tools_arg=None, allow_writes=False, model=None, timeout=None):
    cfg = load_providers()[0]
    pid, p = cli_provider(cfg)
    model = model or prov_model(p) or "opus"
    cwd = validate_work_folder(work_folder)
    if not cwd:
        return Reply(pid, False, f"CLAUDE_ERROR: рабочая папка '{work_folder}' не найдена или не является папкой.", "BAD_FOLDER", model)
    cli = find_claude()
    if not cli:
        return Reply(pid, False, "CLAUDE_ERROR: Claude Code CLI на этом компьютере не найден.", "CLI_MISSING", model)
    via_cmd = cli.lower().endswith((".cmd", ".bat"))
    # long prompts (and anything through cmd.exe) go through stdin: the Windows command line is limited to 32K
    use_stdin = via_cmd or len(prompt) > 6000
    cmd = [cli, "-p", "Выполни задачу из входных данных." if use_stdin else prompt,
           "--model", model, "--output-format", "json"]
    if tools_arg is not None:
        cmd += ["--tools", tools_arg]
    if allow_writes:
        cmd.append("--dangerously-skip-permissions")
    if via_cmd:
        cmd = ["cmd.exe", "/c"] + cmd
    env = os.environ.copy()
    ca = os.path.join(BASE_DIR, "corporate_ca.pem")
    if os.path.isfile(ca):
        env.update(NODE_EXTRA_CA_CERTS=ca, SSL_CERT_FILE=ca, REQUESTS_CA_BUNDLE=ca)
    log(f"Running command in {cwd}: claude -p ... --model {model}")
    io = {"input": prompt} if use_stdin else {"stdin": subprocess.DEVNULL}
    t0 = time.time()
    try:
        res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env=env, timeout=timeout or CLAUDE_TIMEOUT, creationflags=NO_WINDOW, **io)
    except subprocess.TimeoutExpired:
        log(f"Execution timed out ({timeout or CLAUDE_TIMEOUT}s)")
        return Reply(pid, False, f"CLAUDE_ERROR: Claude не ответил за {timeout or CLAUDE_TIMEOUT} с.", "CLAUDE_ERROR", model)
    except Exception as e:
        return Reply(pid, False, f"CLAUDE_ERROR: {e}", "CLAUDE_ERROR", model)
    ms = int((time.time() - t0) * 1000)
    out, err = res.stdout.strip(), res.stderr.strip()
    data = None
    for cand in (out, out.splitlines()[-1] if out else ""):
        try:
            data = json.loads(cand)
            break
        except ValueError:
            continue
    usage, used_model = None, model
    if isinstance(data, dict) and ("result" in data or data.get("type") == "result"):
        text = str(data.get("result") or "").strip()
        failed = bool(data.get("is_error")) or data.get("subtype") not in (None, "success")
        u = data.get("usage") or {}
        prompt_tok = sum(num(u.get(k)) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        compl = num(u.get("output_tokens"))
        if u:
            usage = {"prompt": prompt_tok, "completion": compl, "total": prompt_tok + (compl or 0),
                     "cost": num(data.get("total_cost_usd")), "credit": None}
        mu = data.get("modelUsage")
        if isinstance(mu, dict) and mu:
            used_model = max(mu, key=lambda k: (mu[k] or {}).get("outputTokens", 0) if isinstance(mu[k], dict) else 0)
    else:
        text, failed = out, res.returncode != 0
    if failed or (res.returncode != 0 and not text):
        combined = (text + "\n" + err).lower()
        for sig in LIMIT_SIGNALS:
            if sig in combined:
                log(f"Limit signal detected: {sig}")
                return Reply(pid, False, f"CLAUDE_LIMIT_REACHED: {text or err}", "CLAUDE_LIMIT_REACHED", used_model, ms=ms)
        return Reply(pid, False, f"CLAUDE_ERROR (код {res.returncode}): {text or err or 'без вывода'}", "CLAUDE_ERROR", used_model, ms=ms)
    return Reply(pid, True, text or "Выполнено, текстового ответа нет.", None, used_model, usage, ms)


# ---------------------------------------------------------------- token ledger

def ledger_path():
    return os.path.join(home_dir(), "token_usage.json")


def apply_accounts(led, cfg, persist):
    """Units spent per account (one key can serve several pools) and what is left of a known balance."""
    accounts = cfg.get("accounts") or {}
    provs = cfg.get("providers") or {}
    bp = led.get("by_provider") or {}
    state = led.setdefault("account_state", {}) if persist else dict(led.get("account_state") or {})
    view = {}
    for acc, a in accounts.items():
        members = [pid for pid, p in provs.items() if p.get("account") == acc]
        st = state.setdefault(acc, {"units_used": 0}) if persist else dict(state.get(acc) or {"units_used": 0})
        init = a.get("balance_units")
        ref = [init, a.get("balance_set_at")]
        if init is not None and st.get("balance_ref") != ref:
            # a new balance was entered: spending is counted from now on
            st["balance_ref"], st["units_at_balance"] = ref, st.get("units_used", 0)
        spent = st.get("units_used", 0) - st.get("units_at_balance", 0) if init is not None else None
        no_rate = [pid for pid in members if provs[pid].get("units_per_token") is None and (bp.get(pid) or {}).get("total_tokens")]
        view[acc] = {"title": a.get("title") or acc, "providers": members, "initial_units": init,
                     "units_used": round(st.get("units_used", 0), 2),
                     "remaining_units": round(init - spent, 2) if init is not None else None,
                     "estimate": bool(no_rate),
                     "note": "; ".join(n for n in (
                         f"для {', '.join(no_rate)} курс единиц не задан (units_per_token), считаю 1 единицу за токен" if no_rate else "",
                         "" if init is not None else "начальный баланс не указан в providers.json") if n)}
        for pid in members:
            if pid in bp:
                bp[pid]["account"] = acc
                bp[pid]["remaining_units"] = view[acc]["remaining_units"]
    led["accounts"] = view
    led["initial_balances"] = {acc: a.get("balance_units") for acc, a in accounts.items() if a.get("balance_units") is not None}
    return led


def record(r, agent=None, tool=None):
    """Write one answer (or failure) into token_usage.json. Missing keys are not calls and are not recorded."""
    if r.kind in ("PROVIDER_NO_KEY", "CLI_MISSING", "BAD_FOLDER"):
        return
    if r.ok and r.usage:
        u = r.usage
        log(f"Usage {r.model} @ {r.provider}: prompt={u.get('prompt') or 0} completion={u.get('completion') or 0} total={u.get('total') or 0}")
    path = ledger_path()
    try:
        with LEDGER_LOCK, FileLock(path + ".lock"):
            led = read_json(path) or {}
            cfg = load_providers()[0]
            led["version"] = 3
            apply_accounts(led, cfg, persist=True)  # snapshot a newly entered balance before this spend
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            tot = led.setdefault("total_stats", {})
            for k in ("total_requests", "total_prompt_tokens", "total_completion_tokens", "total_tokens",
                      "requests_without_usage", "errors"):
                tot.setdefault(k, 0)
            tot.setdefault("total_cost_usd", 0.0)
            row = led.setdefault("by_provider", {}).setdefault(r.provider, {})
            for k in ("requests", "prompt_tokens", "completion_tokens", "total_tokens", "requests_without_usage", "errors"):
                row.setdefault(k, 0)
            row.setdefault("cost_usd", 0.0)
            if r.ok:
                u = r.usage or {}
                row["requests"] += 1
                tot["total_requests"] += 1
                row["model"], row["last_ok"], row["last_ms"] = r.model, ts, r.ms
                if r.usage:
                    for src, dst, tdst in (("prompt", "prompt_tokens", "total_prompt_tokens"),
                                           ("completion", "completion_tokens", "total_completion_tokens"),
                                           ("total", "total_tokens", "total_tokens")):
                        row[dst] += u.get(src) or 0
                        tot[tdst] += u.get(src) or 0
                    if u.get("cost") is not None:
                        row["cost_usd"] = round(row["cost_usd"] + u["cost"], 8)
                        tot["total_cost_usd"] = round(tot["total_cost_usd"] + u["cost"], 8)
                    if u.get("credit") is not None:
                        row["last_credit"] = u["credit"]
                    p = (cfg.get("providers") or {}).get(r.provider) or {}
                    acc = p.get("account")
                    if acc:
                        st = led["account_state"].setdefault(acc, {"units_used": 0})
                        rate = p.get("units_per_token")
                        st["units_used"] = round(st.get("units_used", 0) + (u.get("total") or 0) * (1 if rate is None else rate), 4)
                else:
                    row["requests_without_usage"] += 1
                    tot["requests_without_usage"] += 1
                hist = led.setdefault("history", [])
                hist.append({"timestamp": ts, "provider": r.provider, "model": r.model, "agent": agent, "tool": tool,
                             "prompt_tokens": u.get("prompt"), "completion_tokens": u.get("completion"),
                             "total_tokens": u.get("total"), "cost": u.get("cost"), "ms": r.ms})
                del hist[:-HISTORY_KEEP]
            else:
                row["errors"] += 1
                tot["errors"] += 1
                row["last_error"], row["last_error_at"] = (r.text or "")[:300], ts
            apply_accounts(led, cfg, persist=True)
            led["updated"] = ts
            write_json_atomic(path, led)
    except Exception as e:
        log(f"record_usage failed: {e}")


def read_ledger():
    led = read_json(ledger_path()) or {}
    if led:
        apply_accounts(led, load_providers()[0], persist=False)
    return led


# ---------------------------------------------------------------- roles and chains

def run_chain(chain, system_prompt, task, agent=None, tool=None, work_folder=None, allowed_tools="Read,Grep,Glob",
              context=""):
    cfg = load_providers()[0]
    provs = cfg.get("providers") or {}
    tried = []
    chain = [pid for i, pid in enumerate(chain) if pid and pid not in chain[:i]]
    for i, pid in enumerate(chain):
        p = provs.get(pid)
        if not p:
            tried.append((pid, "пула нет в providers.json"))
            continue
        log(f"Agent '{agent or tool}' calling provider '{pid}' ({p.get('type')})...")
        if p.get("type") == "cli":
            r = run_claude(work_folder or os.getcwd(), f"{system_prompt}\n\nПоставленная задача:\n{task}",
                           tools_arg=allowed_tools, model=prov_model(p))
            if r.kind == "CLI_MISSING":
                log("Claude CLI не обнаружен локально. Перехожу к следующему пулу цепочки.")
        elif p.get("type") == "openai_compatible":
            sp = system_prompt + (f"\n\nМатериалы проекта (прочитаны мостом с диска):\n{context}" if context else "")
            r = call_openai(pid, p, [{"role": "system", "content": sp}, {"role": "user", "content": task}])
        else:
            r = Reply(pid, False, f"PROVIDER_ERROR: неизвестный тип пула '{p.get('type')}'", "PROVIDER_ERROR")
        record(r, agent, tool)
        if r.ok:
            log(f"Agent '{agent or tool}' answered via provider '{pid}' ({r.model}) in {(r.ms or 0) / 1000:.1f}s")
            return r, tried
        tried.append((pid, r.short_why()))
        rest = ": запасных пулов больше нет" if i == len(chain) - 1 else "..."
        log(f"Provider '{pid}' {r.short_why()}, switching to fallback (agent '{agent or tool}'){rest}")
    return None, tried


def agent_exec(agent_name, task, skill=None, work_folder=None, tool="agent_run", context=None, files=None):
    data = load_agents()[0]
    agents = data.get("agents") or {}
    agent_name = agent_name or data.get("default_agent") or "developer"
    if agent_name not in agents:
        return {"agent": agent_name, "error": f"AGENT_ERROR: роли '{agent_name}' нет. Есть: {', '.join(agents) or 'нет ни одной'}"}
    a = agents[agent_name]
    role = a.get("name", agent_name)
    names = [skill] if skill else list(a.get("skills") or [])
    skills, text = [], ""
    for s in names:
        content = load_skill(s)
        if content:
            text += f"\n\n--- НАВЫК: {s} ---\n{content}"
            skills.append(s)
    folder = work_folder or os.getcwd()
    system_prompt = (f"Ты — специализированный агент мультимодельной системы: {role}.\n"
                     f"Твоя цель: {a.get('description', '')}\n"
                     f"Рабочая папка проекта: {folder}\n{text}\n\n"
                     "Выполняй задачу по правилам своих навыков. Отвечай по-русски, если задача на русском.")
    chain = [a.get("primary_provider")] + list(a.get("fallback_providers") or [])
    if context is None:
        context = project_context(work_folder, task, files)[0] if work_folder else ""
    r, tried = run_chain(chain, system_prompt, task, agent=agent_name, tool=tool, work_folder=folder,
                         allowed_tools=a.get("allowed_tools", "Read,Grep,Glob"), context=context)
    if not r:
        log(f"Agent '{agent_name}' failed: " + "; ".join(f"{p} ({w})" for p, w in tried))
    return {"agent": agent_name, "role": role, "primary": chain[0], "skills": skills, "reply": r, "tried": tried,
            "error": None if r else "AGENT_ERROR: все пулы роли '{}' не ответили: {}".format(
                agent_name, "; ".join(f"{p} ({w})" for p, w in tried) or "цепочка пуста")}


def agent_header(res):
    r = res["reply"]
    head = f"### [Агент: {res['role']} | Пул: {r.provider} | Модель: {r.model}]"
    if res["skills"]:
        head += f" [Навыки: {', '.join(res['skills'])}]"
    if res["tried"]:
        head += "\n> Сначала пробовал: " + "; ".join(f"{p} ({w})" for p, w in res["tried"])
    return head


def handle_agent_run(agent, task, skill=None, work_folder=None, files=None):
    res = agent_exec(agent, task, skill, work_folder, files=files)
    if res.get("error"):
        return res["error"]
    return agent_header(res) + "\n\n" + res["reply"].text


def as_list(v):
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x) for x in (v or []) if x]


def handle_consilium(task, work_folder=None, members=None, chair=None, files=None):
    data = load_agents()[0]
    agents = data.get("agents") or {}
    conf = data.get("consilium") or {}
    members = as_list(members) or as_list(conf.get("members")) or [a for a in ("architect", "developer", "reviewer") if a in agents]
    chair = chair or conf.get("chair") or (members[0] if members else None)
    missing = [m for m in members + [chair] if m not in agents]
    if not members or missing:
        return f"CONSILIUM_ERROR: нет ролей: {', '.join(missing) or 'участники не заданы'}. Есть: {', '.join(agents)}"
    log(f"Запуск Мультимодельного Консилиума по задаче: {task[:60]}...")
    log(f"Consilium members: {', '.join(members)}; chair: {chair}")
    t0 = time.time()
    context = project_context(work_folder, task, files)[0] if work_folder else ""
    member_task = (f"{task}\n\nТы участник консилиума из нескольких моделей. Дай своё независимое решение: "
                   "ключевые выводы, риски, конкретную рекомендацию. Коротко и по делу.")
    parent = contextvars.copy_context()
    with ThreadPoolExecutor(max_workers=len(members)) as ex:
        results = list(ex.map(lambda m: parent.copy().run(agent_exec, m, member_task, None, work_folder,
                                                          tool="consilium", context=context), members))
    answered = [x for x in results if not x.get("error")]
    if not answered:
        return "CONSILIUM_ERROR: ни один участник не ответил. " + " | ".join(x["error"] for x in results)
    joined = "\n\n".join(f"### {x['role']} (пул {x['reply'].provider}, модель {x['reply'].model})\n{x['reply'].text}" for x in answered)
    verdict_task = (f"Задача консилиума:\n{task}\n\nОтветы участников:\n\n{joined}\n\n"
                    "Ты председатель. Сформируй итог: 1) в чём участники согласны; 2) где расходятся и чья позиция "
                    "сильнее и почему; 3) итоговое решение; 4) план шагов. Опирайся на ответы участников и проверяемые факты, "
                    "не выдумывай того, чего в них нет.")
    log(f"Consilium chair '{chair}' is writing the verdict ({len(answered)} of {len(members)} answered)")
    verdict = agent_exec(chair, verdict_task, None, work_folder, tool="consilium", context="")
    secs = int(time.time() - t0)
    rows = ["| Роль | Ответил пул | Модель | Токены | Сначала пробовал |", "|---|---|---|---|---|"]
    for x in results:
        r = x.get("reply")
        if r:
            tok = (r.usage or {}).get("total")
            rows.append(f"| {x['role']} | {r.provider} | {r.model} | {tok if tok is not None else 'нет данных'} | "
                        f"{'; '.join(f'{p} ({w})' for p, w in x['tried']) or '—'} |")
        else:
            rows.append(f"| {x.get('role', x['agent'])} | не ответил | — | — | {'; '.join(f'{p} ({w})' for p, w in x.get('tried', [])) or '—'} |")
    if verdict.get("error"):
        verdict_text = f"Итог не сформирован: {verdict['error']}"
        verdict_head = f"## Итог (председатель {chair} не ответил)"
    else:
        vr = verdict["reply"]
        verdict_text = vr.text
        verdict_head = f"## Итог (председатель: {verdict['role']}, пул {vr.provider}, модель {vr.model})"
    report = "\n".join([f"# Консилиум: {len(answered)} из {len(members)} участников ответили, {secs} с", "",
                        f"**Задача:** {task}", "", *rows, "", verdict_head, "", verdict_text, "",
                        "## Ответы участников", "", joined])
    path = save_report(report)
    short = "\n\n".join(f"### {x['role']} ({x['reply'].provider})\n{x['reply'].text[:1200]}"
                        + ("\n…(сокращено)" if len(x['reply'].text) > 1200 else "") for x in answered)
    return "\n".join([f"## Консилиум: ответили {len(answered)} из {len(members)}, {secs} с", "", *rows, "",
                      verdict_head, "", verdict_text, "", "## Ответы участников (начало)", "", short, "",
                      f"Полный протокол: {path}" if path else ""]).strip()


def save_report(text):
    try:
        d = os.path.join(home_dir(), "consilium")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, time.strftime("%Y%m%d-%H%M%S") + ".md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        return path
    except OSError as e:
        log(f"Не удалось сохранить протокол консилиума: {e}")
        return None


# ---------------------------------------------------------------- Claude tools

def git_summary(folder):
    try:
        if not os.path.isdir(os.path.join(folder, ".git")):
            return ""
        run = lambda *a: subprocess.run(["git", *a], cwd=folder, capture_output=True, text=True, encoding="utf-8",
                                        errors="replace", timeout=20, creationflags=NO_WINDOW).stdout.strip()
        st, diff = run("status", "--short"), run("diff", "--stat")
        return f"\n\nТекущий git status:\n{st}\nИзменения:\n{diff}" if st or diff else ""
    except Exception:
        return ""


def claude_tool(tool, work_folder, prompt, tools_arg=None, allow_writes=False):
    pid = cli_provider(load_providers()[0])[0]
    log(f"Agent '{tool}' calling provider '{pid}' (cli)...")
    r = run_claude(work_folder, prompt, tools_arg=tools_arg, allow_writes=allow_writes,
                   timeout=max(CLAUDE_TIMEOUT, 600) if allow_writes else None)
    if r.ok:
        log(f"Agent '{tool}' answered via provider '{pid}' ({r.model}) in {(r.ms or 0) / 1000:.1f}s")
    elif r.kind != "CLI_MISSING":
        log(f"Agent '{tool}' failed: {pid} ({r.short_why()})")
    if r.kind == "CLI_MISSING":
        log(f"Provider '{pid}' Claude CLI не найден, switching to fallback (agent '{tool}')...")
        if allow_writes:
            return "CLAUDE_ERROR: claude_implement меняет файлы и работает только через Claude Code CLI, а он на этом компьютере не найден."
        chain = load_providers()[0].get("claude_fallback") or []
        log(f"Claude CLI не обнаружен локально. Перенаправление запроса в шлюз: {', '.join(chain) or 'нет запасных пулов'}")
        fr, tried = run_chain(chain, "Ты старший инженер. Файлы проекта тебе недоступны: опирайся на текст запроса.",
                              prompt, tool=tool)
        if fr:
            return f"### [Claude CLI не найден | Ответил пул {fr.provider} ({fr.model}), без доступа к файлам проекта]\n\n{fr.text}"
        return "CLAUDE_ERROR: Claude CLI не найден, запасные пулы не ответили: " + "; ".join(f"{p} ({w})" for p, w in tried)
    record(r, tool=tool)
    return r.text


def handle_review(work_folder, focus=""):
    prompt = ("Ты старший ревьюер кода. Проверь изменения в проекте.\n"
              f"Рабочая папка: {work_folder}\n"
              f"Фокус проверки: {focus or 'качество кода, логические ошибки, архитектура, безопасность'}"
              f"{git_summary(work_folder) if validate_work_folder(work_folder) else ''}\n\n{load_skill('code-review')}\n\n"
              "ВАЖНО: не изменяй файлы проекта. Верни краткий структурированный список: критические ошибки, "
              "предупреждения, улучшения. Если всё в порядке, подтверди это.")
    return claude_tool("claude_review", work_folder, prompt, tools_arg="Read,Grep,Glob")


def handle_ask(work_folder, question):
    prompt = (f"Вопрос по кодовой базе в {work_folder}:\n{question}\n\n"
              "Ответь развёрнуто и по делу. Не изменяй файлы проекта.")
    return claude_tool("claude_ask", work_folder, prompt, tools_arg="Read,Grep,Glob")


def handle_implement(work_folder, instruction):
    prompt = (f"Задача для реализации в проекте {work_folder}:\n{instruction}\n\n"
              "Внеси нужные изменения аккуратно и точно, затем кратко перечисли, что изменил.")
    return claude_tool("claude_implement", work_folder, prompt, allow_writes=True)


# ---------------------------------------------------------------- direct pools

def handle_model_ask(provider, question, work_folder=None, model=None, files=None):
    cfg = load_providers()[0]
    pid = resolve_provider(provider, cfg)
    if not pid:
        return f"PROVIDER_ERROR: пул '{provider}' не найден. Есть: {', '.join(cfg.get('providers') or {})}"
    p = dict(cfg["providers"][pid])
    if model:
        p["model"], p["model_env"] = model, None
    folder = work_folder or os.getcwd()
    log(f"Routing {prov_model(p)} to provider '{pid}'...")
    if p.get("type") == "cli":
        r = run_claude(folder, question, tools_arg="Read,Grep,Glob", model=prov_model(p))
    else:
        ctx = project_context(work_folder, question, files)[0] if work_folder else ""
        sp = f"Ты опытный инженер. Рабочая папка проекта: {folder}." + (
            f"\n\nМатериалы проекта (прочитаны мостом с диска):\n{ctx}" if ctx else "")
        r = call_openai(pid, p, [{"role": "system", "content": sp}, {"role": "user", "content": question}])
    record(r, tool="model_ask")
    if r.ok:
        log(f"Agent 'model_ask' answered via provider '{pid}' ({r.model}) in {(r.ms or 0) / 1000:.1f}s")
    else:
        log(f"Agent 'model_ask' failed: {pid} ({r.short_why()})")
    return f"### [Пул: {pid} | Модель: {r.model}]\n\n{r.text}" if r.ok else r.text


def probe_models(pid, p):
    """GET /models: is the gateway reachable with this key, and does it list our model? Spends no tokens."""
    state, _ = key_state(p)
    if p.get("type") == "cli":
        return "CLI найден" if find_claude() else "CLI не найден"
    if state == "missing":
        return "нет ключа"
    url = prov_base(p) + "/models"
    try:
        data = http_json(url, None, prov_key(p), timeout=8)
        ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)] if isinstance(data, dict) else []
        if not ids:
            return "отвечает"
        model = prov_model(p)
        if model in ids:
            return f"отвечает, моделей {len(ids)}, {model} есть в списке"
        word = re.split(r"[-_.:/ ]", model.lower())[0]
        like = [i for i in ids if isinstance(i, str) and word and word in i.lower()][:6]
        return f"отвечает, моделей {len(ids)}, {model} НЕТ в списке" + (f"; похожие: {', '.join(like)}" if like else "")
    except urllib.error.HTTPError as e:
        return {401: "ключ не принят (HTTP 401)", 403: "доступ запрещён (HTTP 403)"}.get(e.code, f"HTTP {e.code}")
    except Exception as e:
        return f"недоступен ({type(e).__name__})"


def handle_models_list(check=False):
    cfg = load_providers()[0]
    provs = cfg.get("providers") or {}
    agents = load_agents()[0].get("agents") or {}
    led = read_ledger()
    bp = led.get("by_provider") or {}
    probes = {}
    if check:
        with ThreadPoolExecutor(max_workers=8) as ex:
            probes = dict(zip(provs, ex.map(lambda kv: probe_models(*kv), provs.items())))
    lines = [f"### Пулы моделей ({len(provs)})", "", f"**Пул по умолчанию:** `{cfg.get('default_provider')}`", "",
             "| Пул | Модель | Шлюз | Ключ | Роли | Токены | Последний ответ | " + ("Проверка сейчас |" if check else ""),
             "|---|---|---|---|---|---|---|" + ("---|" if check else "")]
    for pid, p in provs.items():
        st, src = key_state(p)
        key = {"cli": "CLI " + ("найден" if find_claude() else "не найден"), "none": "не нужен",
               "set": f"есть ({src})", "missing": f"нет: {p.get('api_key_env')}"}[st]
        prim = [a for a, x in agents.items() if x.get("primary_provider") == pid]
        fb = [a for a, x in agents.items() if pid in (x.get("fallback_providers") or [])]
        roles = (f"основной: {', '.join(prim)}" if prim else "") + ("; " if prim and fb else "") + (f"запасной у {len(fb)}" if fb else "")
        row = bp.get(pid) or {}
        last = row.get("last_ok") or "—"
        if row.get("last_error_at") and (not row.get("last_ok") or row["last_error_at"] > row["last_ok"]):
            last = f"ошибка {row['last_error_at']}"
        where = p.get("command") if p.get("type") == "cli" else host_of(prov_base(p))
        lines.append(f"| **{pid}** | `{prov_model(p)}` | {where} | {key} | {roles or '—'} | {row.get('total_tokens', 0)} | {last} |"
                     + (f" {probes.get(pid)} |" if check else ""))
    lines += ["", "Ключ «есть» значит, что переменная задана; работает ли ключ, показывает models_list(check=true) "
                  "или `python claude_bridge.py --check`."]
    return "\n".join(lines)


def handle_model_switch(model):
    cfg, path = load_providers()
    if not path:
        return "MODEL_SWITCH_ERROR: providers.json не найден, менять нечего."
    pid = resolve_provider(model, cfg)
    if not pid:
        return f"MODEL_SWITCH_ERROR: не нашёл пул по '{model}'. Есть: {', '.join(cfg.get('providers') or {})}"
    old = cfg.get("default_provider")
    cfg["default_provider"] = pid
    try:
        write_json_atomic(path, cfg)
    except Exception as e:
        return f"MODEL_SWITCH_ERROR: не удалось записать {path}: {e}"
    return (f"Пул по умолчанию: {old} → {pid} ({prov_model(cfg['providers'][pid])}). Он отвечает в model_ask без "
            "указания пула. Цепочки ролей в agents.json не меняются.")


def handle_token_balance():
    led = read_ledger()
    if not led:
        return f"Учёт токенов пуст: файла {ledger_path()} ещё нет. Он появится после первого ответа модели."
    t = led.get("total_stats") or {}
    lines = ["### Учёт токенов", "",
             f"Запросов: {t.get('total_requests', 0)}, токенов: {t.get('total_tokens', 0)} "
             f"(запрос {t.get('total_prompt_tokens', 0)}, ответ {t.get('total_completion_tokens', 0)}), "
             f"стоимость по ответам API: ${t.get('total_cost_usd', 0)}. Ответов без данных о токенах: "
             f"{t.get('requests_without_usage', 0)}, ошибок: {t.get('errors', 0)}.", "",
             "| Пул | Модель | Запросов | Токенов | $ | Остаток счёта | Последняя ошибка |", "|---|---|---|---|---|---|---|"]
    for pid, r in sorted((led.get("by_provider") or {}).items(), key=lambda kv: -(kv[1].get("total_tokens") or 0)):
        rem = r.get("remaining_units")
        lines.append(f"| {pid} | {r.get('model', '—')} | {r.get('requests', 0)} | {r.get('total_tokens', 0)} | "
                     f"{r.get('cost_usd', 0)} | {rem if rem is not None else '—'} | {r.get('last_error_at') or '—'} |")
    accs = [a for a in (led.get("accounts") or {}).values() if a.get("initial_units") is not None]
    if accs:
        lines += ["", "**Остатки на счетах** (один ключ может обслуживать несколько пулов):"]
        for a in accs:
            lines.append(f"- {a.get('title')}: осталось {'≈' if a.get('estimate') else ''}{a.get('remaining_units')} из "
                         f"{a.get('initial_units')} ед." + (f" ({a['note']})" if a.get("note") else ""))
    else:
        lines += ["", "Остатки на счетах не считаются: начальные балансы не указаны (providers.json → accounts → balance_units)."]
    lines += ["", "Остаток считает мост: начальный баланс из providers.json минус расход после его ввода. "
                  "Провайдер свой баланс не присылает, если в ответе нет поля credit. Стоимость Claude CLI: "
                  "то, что сообщил сам Claude Code; при подписке она не списывается отдельно.", f"Файл: {ledger_path()}"]
    return "\n".join(lines)


def handle_agents_list():
    data = load_agents()[0]
    cfg = load_providers()[0]
    provs = cfg.get("providers") or {}
    conf = data.get("consilium") or {}
    lines = [f"### Роли ({len(data.get('agents') or {})})", "",
             "| Роль | Название | Цепочка пулов | Навыки | Назначение |", "|---|---|---|---|---|"]
    for aid, a in (data.get("agents") or {}).items():
        chain = [a.get("primary_provider")] + list(a.get("fallback_providers") or [])
        marks = []
        for pid in chain:
            p = provs.get(pid)
            st = key_state(p)[0] if p else "absent"
            marks.append(f"`{pid}`" + {"missing": " (нет ключа)", "absent": " (нет пула)"}.get(st, ""))
        lines.append(f"| **{aid}**{' *(по умолчанию)*' if aid == data.get('default_agent') else ''} | {a.get('name', aid)} | "
                     f"{' → '.join(marks)} | {', '.join(a.get('skills') or []) or '—'} | {a.get('description', '')} |")
    if conf:
        lines += ["", f"**Консилиум:** участники {', '.join(as_list(conf.get('members')))}; председатель {conf.get('chair')}."]
    return "\n".join(lines)


def handle_skills_list():
    agents = load_agents()[0].get("agents") or {}
    lines = ["### Навыки", "", "| Навык | Название | Роли |", "|---|---|---|"]
    for sid, s in list_skills().items():
        users = [a for a, x in agents.items() if sid in (x.get("skills") or [])]
        lines.append(f"| `{sid}` | {s['title']} | {', '.join(users) or '—'} |")
    return "\n".join(lines)


def handle_soup_recipe(query=""):
    try:
        from soup_cli.recipes.catalog import search_recipes, list_recipes
    except Exception as e:
        return f"SOUP_ERROR: библиотека soup-cli не установлена в этом Python ({e})"
    try:
        found = search_recipes(query) if query else list_recipes()
        if not found:
            return f"Рецептов по запросу '{query}' нет."
        out = [f"Рецептов: {len(found)}" + (" (первые 10)" if len(found) > 10 else "")]
        for r in found[:10]:
            out.append(f"- **{r.model}** ({r.task}, {r.size}): {r.description}")
        return "\n".join(out)
    except Exception as e:
        return f"SOUP_ERROR: {e}"


# ---------------------------------------------------------------- MCP

S = lambda d: {"type": "string", "description": d}
TOOLS = [
    {"name": "claude_review", "description": "Claude Code проверяет изменения в проекте, ничего не меняя. Возвращает замечания и рекомендации.",
     "inputSchema": {"type": "object", "properties": {"work_folder": S("Абсолютный путь к папке проекта"),
                                                      "focus": S("Что проверить в первую очередь")}, "required": ["work_folder"]}},
    {"name": "claude_ask", "description": "Claude Code отвечает на сложный вопрос по проекту, архитектуре или алгоритму, без правок.",
     "inputSchema": {"type": "object", "properties": {"work_folder": S("Абсолютный путь к папке проекта"),
                                                      "question": S("Вопрос")}, "required": ["work_folder", "question"]}},
    {"name": "claude_implement", "description": "Claude Code сам вносит изменения в файлы проекта. Только для крайних случаев.",
     "inputSchema": {"type": "object", "properties": {"work_folder": S("Абсолютный путь к папке проекта"),
                                                      "instruction": S("Что сделать")}, "required": ["work_folder", "instruction"]}},
    {"name": "agent_run", "description": "Отдать задачу роли из agents.json (architect, reviewer, developer, glm_analyst, minimax_architect, "
                                         "kimi_researcher, security_auditor, claude_chief и другие). Роль идёт по своей цепочке моделей: "
                                         "если основной пул не ответил, отвечает запасной.",
     "inputSchema": {"type": "object", "properties": {"agent": S("Роль, список: agents_list"), "task": S("Задача или код"),
                                                      "skill": S("Навык вместо навыков роли (необязательно)"),
                                                      "work_folder": S("Папка проекта: модели получат карту проекта, README и файлы, названные в задаче"),
                                                      "files": S("Файлы проекта через запятую, которые нужно приложить (необязательно)")},
                     "required": ["agent", "task"]}},
    {"name": "agents_list", "description": "Список ролей: модели в цепочке, навыки, назначение, состав консилиума.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "skills_list", "description": "Список навыков (skills) и ролей, которые их используют.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "model_ask", "description": "Спросить конкретный пул напрямую: DeepSeek, GLM, MiniMax, Kimi, Qwen, GPT, Claude. "
                                         "Можно указать id пула, модель или семейство (например 'glm').",
     "inputSchema": {"type": "object", "properties": {"provider": S("Пул, модель или семейство; пусто = пул по умолчанию"),
                                                      "question": S("Вопрос или задача"), "work_folder": S("Папка проекта"),
                                                      "files": S("Файлы проекта через запятую (необязательно)"),
                                                      "model": S("Другая модель того же шлюза (необязательно)")},
                     "required": ["question"]}},
    {"name": "models_list", "description": "Пулы моделей: модель, шлюз, есть ли ключ, какие роли используют, токены и последний ответ. "
                                           "check=true дополнительно проверяет шлюзы (без расхода токенов).",
     "inputSchema": {"type": "object", "properties": {"check": {"type": "boolean", "description": "Проверить шлюзы сейчас"}}}},
    {"name": "consilium", "description": "Консилиум моделей: участники (по умолчанию DeepSeek, GLM, MiniMax и Kimi) отвечают одновременно, "
                                         "председатель (Claude) сводит ответы в итог: согласие, расхождения, решение, план. "
                                         "Для архитектурных решений и новых модулей. Полный протокол сохраняется в файл.",
     "inputSchema": {"type": "object", "properties": {"task": S("Вопрос или задача для консилиума"), "work_folder": S("Папка проекта"),
                                                      "files": S("Файлы проекта через запятую (необязательно)"),
                                                      "members": S("Роли через запятую вместо состава по умолчанию"),
                                                      "chair": S("Роль председателя вместо заданной")}, "required": ["task"]}},
    {"name": "token_balance", "description": "Отчёт по токенам из token_usage.json: расход по пулам и счетам, стоимость, остатки, ошибки.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "model_switch", "description": "Сменить пул по умолчанию для model_ask (deepseek, glm, minimax, kimi, qwen, gpt, claude или id пула).",
     "inputSchema": {"type": "object", "properties": {"model": S("Пул, модель или семейство")}, "required": ["model"]}},
    {"name": "soup_recipe", "description": "Поиск рецептов моделей LLM Soup для локального запуска или дообучения.",
     "inputSchema": {"type": "object", "properties": {"query": S("Запрос, например 'coder' или 'qwen'")}}},
]


def call_tool(name, args):
    wf = args.get("work_folder") or os.getcwd()
    given = args.get("work_folder") or None  # project materials go to gateway models only when a folder was named
    if name == "claude_review":
        return handle_review(wf, args.get("focus", ""))
    if name == "claude_ask":
        return handle_ask(wf, args.get("question", ""))
    if name == "claude_implement":
        return handle_implement(wf, args.get("instruction", ""))
    if name == "agent_run":
        return handle_agent_run(args.get("agent", ""), args.get("task", ""), args.get("skill"), given, args.get("files"))
    if name == "agents_list":
        return handle_agents_list()
    if name == "skills_list":
        return handle_skills_list()
    if name == "model_ask":
        return handle_model_ask(args.get("provider", ""), args.get("question", ""), given, args.get("model"), args.get("files"))
    if name == "models_list":
        return handle_models_list(bool(args.get("check")))
    if name == "consilium":
        return handle_consilium(args.get("task", ""), given, args.get("members"), args.get("chair"), args.get("files"))
    if name == "token_balance":
        return handle_token_balance()
    if name == "model_switch":
        return handle_model_switch(args.get("model", ""))
    if name == "soup_recipe":
        return handle_soup_recipe(args.get("query", ""))
    return f"BRIDGE_ERROR: неизвестный инструмент {name}"


def send(obj):
    with OUT_LOCK:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def serve_tool_call(req_id, params):
    RPC_ID.set(req_id)
    name = params.get("name")
    args = params.get("arguments") or {}
    log(f"Tool call: {name}")
    try:
        text = call_tool(name, args)
    except Exception as e:
        log(f"Error handling tool call: {traceback.format_exc()}")
        text = f"BRIDGE_ERROR: {e}"
    send({"jsonrpc": "2.0", "id": req_id,
          "result": {"content": [{"type": "text", "text": text}], "isError": text.startswith(ERROR_PREFIXES)}})


def main():
    if IS_WIN:
        import io
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        # the log goes to a pipe too (Antigravity, the Council Engine tap): keep it UTF-8, not the ANSI code page
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    log(f"ai-bridge MCP server starting (v{VERSION}, настройки: {home_dir()})...")
    # tool calls run in threads, so a long consilium does not block ping or a second call
    pool = ThreadPoolExecutor(max_workers=4)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            log(f"Failed to parse JSON: {e}")
            continue
        req_id, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                                              "serverInfo": {"name": "ai-bridge", "version": VERSION}}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": req_id, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            pool.submit(serve_tool_call, req_id, params)
        elif req_id is not None and not str(method or "").startswith("notifications/"):
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
    pool.shutdown(wait=True)


# ---------------------------------------------------------------- command line

def cli_status():
    cfg, ppath = load_providers()
    data, apath = load_agents()
    print(f"ai-bridge {VERSION}")
    print(f"providers.json: {ppath or 'не найден, встроенный список'}")
    print(f"agents.json:    {apath or 'не найден'}")
    print(f".env:           {', '.join(f for f in env_files() if os.path.isfile(f)) or 'нет'}")
    print(f"учёт токенов:   {ledger_path()}")
    print(f"Claude CLI:     {find_claude() or 'не найден'}")
    print(f"\nПулы (по умолчанию {cfg.get('default_provider')}):")
    for pid, p in (cfg.get("providers") or {}).items():
        st, src = key_state(p)
        key = {"cli": "CLI", "none": "ключ не нужен", "set": f"ключ есть ({src})",
               "missing": f"НЕТ КЛЮЧА: {p.get('api_key_env')}"}[st]
        where = p.get("command") if p.get("type") == "cli" else host_of(prov_base(p))
        print(f"  {pid:<20} {prov_model(p):<22} {where:<26} {key}")
    print("\nРоли:")
    for aid, a in (data.get("agents") or {}).items():
        print(f"  {aid:<18} {' > '.join([a.get('primary_provider') or '?'] + list(a.get('fallback_providers') or []))}")
    conf = data.get("consilium") or {}
    if conf:
        print(f"\nКонсилиум: {', '.join(as_list(conf.get('members')))}; председатель {conf.get('chair')}")
    t = read_ledger().get("total_stats") or {}
    print(f"\nТокены: {t.get('total_tokens', 0)} за {t.get('total_requests', 0)} запросов, ${t.get('total_cost_usd', 0)}")


def cli_selftest():
    cfg, ppath = load_providers()
    data, apath = load_agents()
    provs, agents = cfg.get("providers") or {}, data.get("agents") or {}
    problems = []
    if not ppath:
        problems.append("providers.json не найден")
    if not apath:
        problems.append("agents.json не найден")
    if cfg.get("default_provider") not in provs:
        problems.append(f"пул по умолчанию '{cfg.get('default_provider')}' не описан")
    for pid in cfg.get("claude_fallback") or []:
        if pid not in provs:
            problems.append(f"claude_fallback: пула '{pid}' нет")
    for pid, p in provs.items():
        if p.get("api_key"):
            problems.append(f"{pid}: ключ записан прямо в providers.json (лучше api_key_env и .env)")
        if p.get("account") and p["account"] not in (cfg.get("accounts") or {}):
            problems.append(f"{pid}: счёт '{p['account']}' не описан в accounts")
    skills = list_skills()
    for aid, a in agents.items():
        for pid in [a.get("primary_provider")] + list(a.get("fallback_providers") or []):
            if pid not in provs:
                problems.append(f"роль {aid}: пула '{pid}' нет")
        for s in a.get("skills") or []:
            if s not in skills:
                problems.append(f"роль {aid}: навыка '{s}' нет в skills")
    conf = data.get("consilium") or {}
    for m in as_list(conf.get("members")) + ([conf["chair"]] if conf.get("chair") else []):
        if m not in agents:
            problems.append(f"консилиум: роли '{m}' нет")
    print(f"ai-bridge {VERSION}: {len(TOOLS)} инструментов, {len(provs)} пулов, {len(agents)} ролей, {len(skills)} навыков")
    missing = sorted({p['api_key_env'] for p in provs.values() if key_state(p)[0] == "missing"})
    print("Нет ключей: " + (", ".join(missing) if missing else "все ключи заданы"))
    for line in problems:
        print("ПРОБЛЕМА: " + line)
    print("Итог: " + ("всё в порядке" if not problems else f"проблем: {len(problems)}"))
    return 1 if problems else 0


def cli_keys(only):
    import getpass
    cfg = load_providers()[0]
    names = {}
    for pid, p in (cfg.get("providers") or {}).items():
        if p.get("api_key_env"):
            names.setdefault(p["api_key_env"], []).append(pid)
    if only:
        names = {k: v for k, v in names.items() if k in only} or {k: [] for k in only}
    path = os.path.join(home_dir(), ".env")
    print(f"Ключи сохраняются в {path}. Ввод не отображается. Enter без текста: оставить как есть.\n")
    new = {}
    for name, pools in names.items():
        state = key_source(name)
        print(f"{name}  (пулы: {', '.join(pools) or '—'})  сейчас: {state or 'не задан'}")
        try:
            value = getpass.getpass("  новый ключ: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nОтменено, ничего не записано.")
            return 1
        if value:
            new[name] = value
    if not new:
        print("\nНичего не изменилось.")
        return 0
    write_env(path, new)
    print(f"\nЗаписано: {', '.join(new)}. Проверка: python \"{os.path.abspath(__file__)}\" --check")
    return 0


def write_env(path, new):
    """Set NAME=value lines in .env, keeping comments and other lines as they are."""
    lines, done = [], set()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
            for line in fh.read().splitlines():
                name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
                if name in new:
                    lines.append(f"{name}={new[name]}")
                    done.add(name)
                else:
                    lines.append(line)
    lines += [f"{k}={v}" for k, v in new.items() if k not in done]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


LEGACY_KEY = re.compile(r"""environ\.get\(\s*["'](\w*(?:API_KEY|KEY|TOKEN))["']\s*,\s*["']([^"'\s]{16,})["']\s*\)""")


def legacy_sources():
    names = ["multillm_bridge.py", "multillm-bridge.py", "aiduo_bridge.py"]
    out = []
    for d in config_dirs():
        for n in names:
            out.append(os.path.join(d, n))
        try:
            out += [os.path.join(d, n) for n in sorted(os.listdir(d)) if n.startswith("claude_bridge.py.bak-")]
        except OSError:
            pass
    seen, files = set(), []
    for f in out:
        k = os.path.normcase(os.path.abspath(f))
        if k not in seen and os.path.isfile(f):
            seen.add(k)
            files.append(f)
    return files


def cli_import_keys(extra, force=False):
    """Older bridges on this PC keep keys as defaults in their code. Move them into .env for the pools
    that use the same gateway (matched by host), so ai-bridge 3 can use them. Prints names, never values."""
    cfg = load_providers()[0]
    by_host = {}
    for pid, p in (cfg.get("providers") or {}).items():
        if p.get("api_key_env") and p.get("base_url"):
            by_host.setdefault(host_of(p["base_url"]), set()).add(p["api_key_env"])
    sources = [f for f in extra if os.path.isfile(f)] + legacy_sources()
    if not sources:
        print("Старых мостов с ключами рядом не нашлось (multillm_bridge.py, claude_bridge.py.bak-*).")
        return 1
    found = {}
    for src in sources:
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for m in LEGACY_KEY.finditer(text):
            name, value = m.group(1), m.group(2)
            if value.lower().startswith(("http", "your", "sk-xxx", "<")):
                continue
            before = text[max(0, m.start() - 700):m.start()]
            urls = re.findall(r"https?://[^\s\"']+", before)
            host = host_of(urls[-1]) if urls else ""
            targets = by_host.get(host) or ({name} if any(p.get("api_key_env") == name for p in (cfg.get("providers") or {}).values()) else set())
            for t in targets:
                found.setdefault(t, (value, src, name, host))
    if not found:
        print("В старых мостах не нашлось ключей для шлюзов из providers.json.")
        return 1
    path = os.path.join(home_dir(), ".env")
    new = {}
    for env, (value, src, name, host) in sorted(found.items()):
        have = key_source(env)
        status = f"уже задан ({have}), оставлен" if have and not force else "перенесён"
        if status == "перенесён":
            new[env] = value
        print(f"  {env:<18} {status}: из {os.path.basename(src)} ({name}, {host or 'тот же шлюз'}), длина {len(value)}")
    if new:
        write_env(path, new)
        print(f"\nЗаписано в {path}: {', '.join(new)}. Проверка: python \"{os.path.abspath(__file__)}\" --check")
    else:
        print("\nНичего не изменилось. Перезаписать: --import-keys --force")
    missing = sorted({p.get("api_key_env") for p in (cfg.get("providers") or {}).values()
                      if p.get("api_key_env") and not key_source(p["api_key_env"]) and p["api_key_env"] not in new})
    if missing:
        print("Без ключа остаются: " + ", ".join(missing) + ". Их можно ввести командой --keys.")
    return 0


def cli_check(only):
    cfg = load_providers()[0]
    provs = {k: v for k, v in (cfg.get("providers") or {}).items() if not only or k in only}
    print(f"Проверка {len(provs)} пулов: короткий запрос к каждому, у кого есть ключ.\n")

    def one(item):
        pid, p = item
        if p.get("type") == "cli":
            cli = find_claude()
            if not cli:
                return pid, "Claude CLI не найден", None
            try:
                v = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30,
                                   creationflags=NO_WINDOW).stdout.strip()
            except Exception as e:
                v = f"не запустился: {e}"
            return pid, f"CLI найден: {v} (запрос не отправлялся, чтобы не тратить подписку)", None
        if key_state(p)[0] == "missing":
            return pid, f"нет ключа {p.get('api_key_env')}", None
        listing = probe_models(pid, p)
        r = call_openai(pid, p, [{"role": "user", "content": "Ответь одним словом: готов"}], max_tokens=200, timeout=60)
        record(r, tool="check")
        if r.ok:
            u = r.usage or {}
            return pid, (f"OK за {r.ms} мс, модель {r.model}, токенов {u.get('total', 'нет данных')}; /models: {listing}"), r
        return pid, f"ОШИБКА: {r.text[:200]}; /models: {listing}", r

    with ThreadPoolExecutor(max_workers=6) as ex:
        for pid, text, _ in ex.map(one, provs.items()):
            print(f"  {pid:<20} {text}")
    print(f"\nРезультаты записаны в {ledger_path()}")
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if "--status" in argv:
        cli_status()
    elif "--selftest" in argv:
        sys.exit(cli_selftest())
    elif "--import-keys" in argv:
        sys.exit(cli_import_keys([a for a in argv if not a.startswith("--")], force="--force" in argv))
    elif "--keys" in argv:
        sys.exit(cli_keys([a for a in argv if not a.startswith("--")]))
    elif "--check" in argv:
        sys.exit(cli_check([a for a in argv if not a.startswith("--")]))
    else:
        main()
