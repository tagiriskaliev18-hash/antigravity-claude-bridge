"""Council Engine: local desktop dashboard and chat for the Antigravity <-> multi-model bridge.

Shows only what it can actually observe on this computer:
  * running processes of Antigravity, Claude Code, the MCP bridges and their children;
  * MCP servers registered in Antigravity and Claude Code configs;
  * tools each bridge exposes (asked from the bridge itself) and its model pools;
  * every MCP tool call recorded by council_tap.py (time, tool, model, duration, result);
  * the rule files (GEMINI.md, CLAUDE.md, rules, skills) that steer the agents;
  * Antigravity's own steps, read from its local transcripts (~/.gemini/antigravity/brain).

The chat tab sends a request only when the user presses "Отправить": it starts the same
MCP bridge Antigravity uses (through the call log) and calls agent_run, model_ask, consilium
or claude_ask. Everything else is read-only; the app never changes configs from the window.
Wiring the call log is a separate, explicit step:  python council_app.py --wire  (undo: --unwire).

Usage:
  pythonw council_app.py            open the app window (default)
  python  council_app.py --serve    run the server only, print the URL
  python  council_app.py --status   print a text summary and exit
  python  council_app.py --wire     route bridges through council_tap.py (writes backups)
  python  council_app.py --unwire   restore the original bridge commands
"""
import glob
import http.server
import json
import os
import re
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser

APP_NAME = "council-engine"
APP_VERSION = "4.1.0"
PORT = int(os.environ.get("COUNCIL_PORT", "47615"))
APP_DIR = os.path.dirname(os.path.abspath(__file__))
TAP = os.path.join(APP_DIR, "council_tap.py")
IS_WIN = os.name == "nt"
HOME = os.path.expanduser("~")
COUNCIL_HOME = os.environ.get("COUNCIL_HOME") or os.path.join(HOME, ".council")
ACTIVITY_DIR = os.path.join(COUNCIL_HOME, "activity")
NO_WINDOW = 0x08000000 if IS_WIN else 0
TAPPABLE = ("claude-bridge", "multillm-bridge")
KNOWN_SERVER_FILES = {}  # lower-cased script path -> MCP server name, filled from the configs
KEEP_DAYS = 30


def now():
    return time.time()


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except Exception:
        return None


def write_json(path, data):
    tmp = path + ".council-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- config discovery

def antigravity_config_paths():
    cands = [os.path.join(HOME, ".gemini", "config", "mcp_config.json"),
             os.path.join(HOME, ".gemini", "antigravity", "mcp_config.json")]
    return [p for p in cands if os.path.isfile(p)]


def claude_config_path():
    p = os.path.join(HOME, ".claude.json")
    return p if os.path.isfile(p) else None


def is_tapped(entry):
    args = entry.get("args") or []
    return any(str(a).replace("\\", "/").endswith("council_tap.py") for a in args)


def original_command(entry):
    """Return (command, args) the server really runs, looking through the tap."""
    args = [str(a) for a in (entry.get("args") or [])]
    if is_tapped(entry) and "--" in args:
        real = args[args.index("--") + 1:]
        return (real[0] if real else ""), real[1:]
    return str(entry.get("command") or ""), args


def bridge_file(entry):
    _, args = original_command(entry)
    for a in args:
        if a.lower().endswith(".py"):
            return a
    return None


def describe_server(name, entry, client):
    cmd, args = original_command(entry)
    kind = "http" if (entry.get("url") or entry.get("serverUrl")) else "stdio"
    return {
        "name": name,
        "client": client,
        "kind": kind,
        "command": cmd,
        "args": args,
        "url": entry.get("url") or entry.get("serverUrl"),
        "env_keys": sorted((entry.get("env") or {}).keys()),
        "tapped": is_tapped(entry),
        "tappable": kind == "stdio" and name in TAPPABLE,
        "file": bridge_file(entry),
        "disabled": bool(entry.get("disabled")),
        "_env": entry.get("env") or {},
    }


def collect_servers():
    out = []
    for path in antigravity_config_paths():
        data = read_json(path) or {}
        for name, entry in (data.get("mcpServers") or {}).items():
            d = describe_server(name, entry or {}, "antigravity")
            d["config"] = path
            out.append(d)
    cpath = claude_config_path()
    if cpath:
        data = read_json(cpath) or {}
        for name, entry in (data.get("mcpServers") or {}).items():
            d = describe_server(name, entry or {}, "claude-code")
            d["config"] = cpath + " (user)"
            out.append(d)
        for proj, pdata in (data.get("projects") or {}).items():
            for name, entry in ((pdata or {}).get("mcpServers") or {}).items():
                d = describe_server(name, entry or {}, "claude-code")
                d["config"] = cpath + f" (project {proj})"
                out.append(d)
    for s in out:
        if s.get("file"):
            KNOWN_SERVER_FILES[s["file"].lower().replace("\\", "/")] = s["name"]
    return out


def rule_files():
    pats = [
        (".gemini/config/GEMINI.md", "antigravity", "Глобальные правила Antigravity"),
        (".gemini/GEMINI.md", "antigravity", "Глобальные правила Antigravity"),
        (".gemini/config/rules/*.md", "antigravity", "Правило Antigravity"),
        (".gemini/config/skills/*/SKILL.md", "antigravity", "Навык Antigravity"),
        (".claude/CLAUDE.md", "claude-code", "Глобальные правила Claude Code"),
        (".claude/agents/*.md", "claude-code", "Субагент Claude Code"),
        (".claude/skills/*/SKILL.md", "claude-code", "Навык Claude Code"),
    ]
    seen, out = set(), []
    for pat, client, kind in pats:
        for p in sorted(glob.glob(os.path.join(HOME, *pat.split("/")))):
            rp = os.path.realpath(p)
            if rp in seen:
                continue
            seen.add(rp)
            st = os.stat(p)
            out.append({"path": p, "client": client, "kind": kind, "size": st.st_size, "mtime": st.st_mtime})
    for s in collect_servers():
        if s["file"]:
            prompt = os.path.join(os.path.dirname(s["file"]), "COUNCIL_PROMPT.md")
            if os.path.isfile(prompt) and os.path.realpath(prompt) not in seen:
                seen.add(os.path.realpath(prompt))
                st = os.stat(prompt)
                out.append({"path": prompt, "client": "both", "kind": "Промпт консилиума",
                            "size": st.st_size, "mtime": st.st_mtime})
    return out


# ---------------------------------------------------------------- bridge introspection

PROBE = r'''
import importlib.util, json, os, re, sys
path = sys.argv[1]
src = open(path, encoding="utf-8", errors="replace").read()
out = {"hardcoded_keys": len(re.findall(r"['\"]sk-[A-Za-z0-9_\-]{12,}['\"]", src)), "providers": [], "default_model": None}
try:
    spec = importlib.util.spec_from_file_location("bridge_probe", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for p in getattr(m, "PROVIDERS", []) or []:
        out["providers"].append({"name": p.get("name"), "base_url": p.get("base_url"),
            "models": list(p.get("supported_models") or []), "priority": p.get("priority"),
            "has_key": bool(p.get("api_key"))})
    out["default_model"] = getattr(m, "DEFAULT_MODEL", None)
    out["claude_model"] = getattr(m, "MODEL", None)
    out["claude_path"] = getattr(m, "CLAUDE_PATH", None)
    out["tools"] = [{"name": t.get("name"), "description": t.get("description"),
                     "params": sorted(((t.get("inputSchema") or {}).get("properties") or {}).keys())}
                    for t in getattr(m, "TOOLS", [])]
    if hasattr(m, "COUNCIL_MEMBERS"):
        out["council"] = {"members": list(m.COUNCIL_MEMBERS), "judge": getattr(m, "COUNCIL_JUDGE", None),
                          "roles": list(getattr(m, "ROLE_HINTS", []) or []),
                          "cli_member": getattr(m, "CLAUDE_CLI_MEMBER", None)}
    if hasattr(m, "handle_list_models"):
        try:
            out["models_text"] = m.handle_list_models()
        except Exception:
            pass
except Exception as e:
    out["error"] = str(e)
# ASCII only: on Windows a piped stdout uses the ANSI code page, which would garble Cyrillic text
print(json.dumps(out, ensure_ascii=True))
'''


def python_for(cmd):
    if cmd and "python" in os.path.basename(cmd).lower():
        found = shutil.which(cmd) or cmd
        return found
    return sys.executable.replace("pythonw.exe", "python.exe")


def probe_bridge(server, env_extra):
    path = server.get("file")
    if not path or not os.path.isfile(path):
        return {"error": "файл моста не найден: %s" % path}
    # run with the env the host passes, so default model and key presence match the real setup
    env = os.environ.copy()
    env.update({k: str(v) for k, v in (env_extra or {}).items()})
    try:
        res = subprocess.run([python_for(server.get("command")), "-c", PROBE, path],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=20, env=env, creationflags=NO_WINDOW)
        lines = res.stdout.strip().splitlines()
        data = json.loads(lines[-1]) if lines else {"error": res.stderr[-400:]}
    except Exception as e:
        data = {"error": str(e)}
    st = os.stat(path)
    data.update({"file": path, "mtime": st.st_mtime, "size": st.st_size})
    return data


SECRET_KEY = re.compile(r"(key|token|secret|password|authorization|bearer)", re.I)


def mask(value, key=""):
    # "api_key_env" and the like hold the NAME of a variable, not its value, so they stay readable
    if SECRET_KEY.search(key or "") and not (key or "").lower().endswith("_env") and isinstance(value, str):
        return "***" if value else ""
    if isinstance(value, dict):
        return {k: mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, key) for v in value]
    if isinstance(value, str) and re.match(r"^(sk-|Bearer )", value):
        return "***"
    return value


SYSTEM_CACHE = {}
SYSTEM_JSON = ("providers.json", "agents.json", "token_usage.json", "pool_health.json", "models.json", "roles.json")


def cached_read(path, reader):
    """Re-read a file only when its mtime or size changes."""
    try:
        st = os.stat(path)
    except OSError:
        SYSTEM_CACHE.pop(path, None)
        return None
    key = (st.st_mtime, st.st_size)
    hit = SYSTEM_CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    val = reader(path)
    SYSTEM_CACHE[path] = (key, val)
    return val


def read_masked_json(path):
    data = read_json(path)
    return {"name": os.path.basename(path), "path": path, "mtime": os.path.getmtime(path),
            "data": mask(data) if data is not None else None,
            "error": None if data is not None else "не удалось прочитать JSON"}


def read_env_names(path):
    """Variable names from .env, split into those with a value and empty ones. Values are never read out."""
    filled, empty = [], []
    try:
        for line in open(path, encoding="utf-8-sig", errors="replace"):
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            (filled if value.strip().strip("\"'") else empty).append(name)
    except OSError:
        pass
    return {"name": ".env", "path": path, "mtime": os.path.getmtime(path), "data": {"variables": filled, "empty": empty}}


def read_skill(path):
    """Skill name, title and one-line description from a markdown skill file."""
    base = os.path.basename(path)
    name = os.path.basename(os.path.dirname(path)) if base.upper() == "SKILL.MD" else os.path.splitext(base)[0]
    title, desc = None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read(20000).splitlines()
    except OSError:
        lines = []
    for line in lines[:80]:
        t = line.strip()
        m = re.match(r"(name|title|description)\s*:\s*(.+)", t, re.I)
        if m and m.group(1).lower() == "description" and not desc:
            desc = m.group(2).strip().strip("\"'")
        elif m and not title:
            title = m.group(2).strip().strip("\"'")
        elif t.startswith("#") and not title:
            title = t.lstrip("#").strip()
        elif t and not t.startswith(("#", "---", "|", "```")) and title and not desc and ":" not in t[:20]:
            desc = t
        if title and desc:
            break
    return {"name": name, "title": title, "description": (desc or "")[:300], "path": path,
            "size": os.path.getsize(path), "mtime": os.path.getmtime(path)}


DIRS_CACHE = {"ts": 0, "dirs": []}


def bridge_dirs():
    # ~/.claude.json can be large, so the folder list is refreshed every 30 s, not on every poll
    if now() - DIRS_CACHE["ts"] < 30:
        return DIRS_CACHE["dirs"]
    dirs = []
    for s in collect_servers():
        f = s.get("file")
        if f and os.path.isfile(f) and os.path.dirname(f) not in dirs:
            dirs.append(os.path.dirname(f))
    DIRS_CACHE.update(ts=now(), dirs=dirs)
    return dirs


def read_system():
    """Model pools, roles, skills and the token ledger that live next to the bridges (secrets masked)."""
    files, skills = [], []
    for folder in bridge_dirs():
        for name in SYSTEM_JSON:
            v = cached_read(os.path.join(folder, name), read_masked_json)
            if v:
                files.append(v)
        v = cached_read(os.path.join(folder, ".env"), read_env_names)
        if v:
            files.append(v)
        for p in sorted(glob.glob(os.path.join(folder, "skills", "*.md")) +
                        glob.glob(os.path.join(folder, "skills", "*", "SKILL.md"))):
            v = cached_read(p, read_skill)
            if v:
                skills.append(v)
    return {"files": files, "skills": skills, "keys": key_states(files)}


def key_states(files):
    """For every api_key_env named in providers.json: where its value is set (.env or the environment), or None."""
    in_env_file = set()
    for f in files:
        if f["name"] == ".env":
            in_env_file.update((f.get("data") or {}).get("variables") or [])
    out = {}
    for f in files:
        if f["name"] != "providers.json" or not isinstance(f.get("data"), dict):
            continue
        for p in (f["data"].get("providers") or {}).values():
            name = isinstance(p, dict) and p.get("api_key_env")
            if isinstance(name, str) and name:
                out[name] = "окружение" if os.environ.get(name) else ".env" if name in in_env_file else None
    return out


def parse_ts(v):
    """Epoch seconds from a number (s or ms) or an ISO-like string; None if unknown."""
    if isinstance(v, (int, float)):
        return v / 1000.0 if v > 1e11 else float(v)
    if not isinstance(v, str) or not v.strip():
        return None
    t = v.strip().replace("Z", "+00:00")
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(t)
        return dt.timestamp() if dt.tzinfo else time.mktime(dt.timetuple()) + dt.microsecond / 1e6
    except ValueError:
        return None


def ledger_matches(system, calls):
    """Attach token_usage.json history entries to the logged calls they happened in (same provider, inside the call)."""
    out = {}
    for f in system["files"]:
        if f["name"] != "token_usage.json" or not isinstance(f.get("data"), dict):
            continue
        for e in f["data"].get("history") or []:
            if not isinstance(e, dict):
                continue
            ts = parse_ts(e.get("timestamp") or e.get("time") or e.get("ts"))
            if ts is None:
                continue
            # the ledger stores local time with one-second precision; prefer a call that used the same pool
            inside = [c for c in calls if c["server"] == "claude-bridge"
                      and c["start"] - 2 <= ts <= (c["end"] or now()) + 5]
            same = [c for c in inside if e.get("provider") in
                    ({r.get("provider") for r in c.get("route") or []} | {c.get("provider")})]
            pick = (same or inside or [None])[0]
            if pick:
                out.setdefault(pick["id"], []).append(e)
    return out


def tier_rules(rules):
    """Lines that define the cost tiers in GEMINI.md, quoted as written."""
    tiers = []
    for r in rules:
        if not r["path"].lower().endswith("gemini.md"):
            continue
        try:
            text = open(r["path"], encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"\s*-\s*\*\*(Tier\s*\d[^*]*)\*\*:?\s*(.*)", line)
            if m:
                tiers.append({"name": m.group(1).strip(), "text": m.group(2).strip(), "source": r["path"]})
            m = re.match(r"\s*-\s*\*\*(Multi-LLM Council[^*]*)\*\*:?\s*(.*)", line)
            if m:
                tiers.append({"name": m.group(1).strip(), "text": m.group(2).strip(), "source": r["path"]})
        if tiers:
            break
    return tiers


# ---------------------------------------------------------------- processes

# UTF-8 output: the default OEM code page would garble Cyrillic folder names in command lines
PS_QUERY = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,CommandLine,"
            "WorkingSetSize,KernelModeTime,UserModeTime,"
            "@{n='Start';e={if($_.CreationDate){[DateTimeOffset]::new($_.CreationDate).ToUnixTimeSeconds()}}}"
            " | ConvertTo-Json -Compress")


def raw_processes():
    """List of dicts: pid, ppid, name, cmd, rss, cpu_s, start (epoch)."""
    try:
        import psutil  # optional
        out = []
        for p in psutil.process_iter(["pid", "ppid", "name", "cmdline", "memory_info", "cpu_times", "create_time"]):
            i = p.info
            ct = i.get("cpu_times")
            out.append({"pid": i["pid"], "ppid": i.get("ppid"), "name": i.get("name") or "",
                        "cmd": " ".join(i.get("cmdline") or []), "rss": getattr(i.get("memory_info"), "rss", 0),
                        "cpu_s": (ct.user + ct.system) if ct else 0, "start": i.get("create_time")})
        return out
    except ImportError:
        pass
    if IS_WIN:
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_QUERY],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30, creationflags=NO_WINDOW)
        rows = json.loads(res.stdout or "[]")
        if isinstance(rows, dict):
            rows = [rows]
        out = []
        for r in rows:
            start = r.get("Start")
            out.append({"pid": r.get("ProcessId"), "ppid": r.get("ParentProcessId"), "name": r.get("Name") or "",
                        "cmd": r.get("CommandLine") or "", "rss": r.get("WorkingSetSize") or 0,
                        "cpu_s": ((r.get("KernelModeTime") or 0) + (r.get("UserModeTime") or 0)) / 1e7,
                        "start": start})
        return out
    res = subprocess.run(["ps", "-eo", "pid=,ppid=,rss=,etimes=,time=,comm=,args="],
                         capture_output=True, text=True)
    out = []
    t = now()
    for line in res.stdout.splitlines():
        parts = line.split(None, 6)
        if len(parts) < 6:
            continue
        pid, ppid, rss, et, cpu, comm = parts[:6]
        hms = [int(x) for x in re.split(r"[:-]", cpu)]
        secs = 0
        for x in hms:
            secs = secs * 60 + x
        out.append({"pid": int(pid), "ppid": int(ppid), "name": comm, "cmd": parts[6] if len(parts) > 6 else comm,
                    "rss": int(rss) * 1024, "cpu_s": secs, "start": t - int(et)})
    return out


def classify(p):
    name = (p["name"] or "").lower()
    cmd = (p["cmd"] or "").lower().replace("\\", "/")
    if "council_app.py" in cmd:
        return "app", "Council Engine (это приложение)"
    if "council_tap.py" in cmd:
        m = re.search(r"--server\s+(\S+)", p["cmd"] or "")
        return "tap", "Журнал вызовов для %s" % (m.group(1) if m else "моста")
    if "claude_bridge.py" in cmd:
        return "bridge", "claude-bridge (ai-bridge: модели, роли, консилиум)"
    if "multillm_bridge.py" in cmd:
        return "bridge", "multillm-bridge (совет моделей)"
    for f, server in list(KNOWN_SERVER_FILES.items()):
        if f in cmd:
            return "bridge", "%s (MCP-сервер из конфига)" % server
    if "newgenmmllm" in cmd and ("uvicorn" in cmd or "main.py" in cmd or "gateway" in cmd):
        return "gateway", "AI Duo шлюз (NewGenMMLLM)"
    if "antigravity" in name or "/antigravity/" in cmd or "antigravity.exe" in cmd:
        return "antigravity", "Antigravity"
    if name in ("claude.exe", "claude") or "@anthropic-ai/claude-code" in cmd or "/claude-code/" in cmd:
        return "claude", "Claude Code"
    return None, None


class ProcessWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.snapshot = {"ts": 0, "items": [], "error": None}
        self.prev_cpu = {}
        self.ncpu = os.cpu_count() or 1

    def run(self):
        while True:
            try:
                self.scan()
            except Exception as e:
                with self.lock:
                    self.snapshot["error"] = str(e)
            time.sleep(4)

    def scan(self):
        t = now()
        procs = raw_processes()
        by_pid = {p["pid"]: p for p in procs}
        picked = {}
        for p in procs:
            group, label = classify(p)
            if group:
                picked[p["pid"]] = dict(p, group=group, label=label)
        # children of bridges (e.g. `claude -p` started by claude-bridge) are part of the picture
        for p in procs:
            parent = picked.get(p["ppid"])
            if parent and parent["group"] in ("bridge", "tap", "bridge-child") and parent["pid"] != p["pid"]:
                g, _ = classify(p)
                if g in ("bridge", "tap"):
                    continue
                label = "Claude выполняет запрос моста" if g == "claude" else "Дочерний процесс моста"
                picked[p["pid"]] = dict(p, group="bridge-child", label=label)
        items = []
        for pid, p in picked.items():
            prev = self.prev_cpu.get(pid)
            cpu = None
            if prev and t > prev[1]:
                cpu = max(0.0, (p["cpu_s"] - prev[0]) / (t - prev[1]) / self.ncpu * 100)
            parent = by_pid.get(p["ppid"])
            items.append({"pid": pid, "ppid": p["ppid"], "group": p["group"], "label": p["label"],
                          "name": p["name"], "cmd": (p["cmd"] or "")[:2000], "rss": p["rss"],
                          "cpu": cpu, "start": p["start"],
                          "parent_name": parent["name"] if parent else None,
                          "parent_in_set": p["ppid"] in picked})
        self.prev_cpu = {pid: (p["cpu_s"], t) for pid, p in picked.items()}
        with self.lock:
            self.snapshot = {"ts": t, "items": items, "error": None}

    def get(self):
        with self.lock:
            return dict(self.snapshot)


# ---------------------------------------------------------------- activity log

USAGE_NUM = {k: re.compile(r"\b%s(?:_tokens)?['\"]?\s*[:=]\s*(\d+)" % k) for k in ("prompt", "completion", "total")}


def parse_usage(line):
    """Token usage printed by a bridge, e.g. 'Usage deepseek-v4-pro @ pool: prompt=12 completion=40 total=52'."""
    if "prompt" not in line or not re.search(r"usage|tokens", line, re.I):
        return None
    vals = {k: (int(m.group(1)) if m else None) for k, rx in USAGE_NUM.items() for m in [rx.search(line)]}
    if vals["prompt"] is None and vals["completion"] is None and vals["total"] is None:
        return None
    if vals["total"] is None:
        vals["total"] = (vals["prompt"] or 0) + (vals["completion"] or 0)
    m = re.search(r"Usage (\S+) @ ([^:]+):", line) or re.search(r"model['\"]?\s*[:=]\s*['\"]?([\w.\-/]+)", line)
    vals["model"] = m.group(1) if m else None
    vals["provider"] = m.group(2).strip() if m and m.lastindex and m.lastindex > 1 else None
    return vals


class Activity:
    def __init__(self):
        self.lock = threading.Lock()
        self.offsets = {}
        self.calls = {}
        self.sessions = {}
        self.cleanup()

    def cleanup(self):
        cutoff = now() - KEEP_DAYS * 86400
        for f in glob.glob(os.path.join(ACTIVITY_DIR, "*.jsonl")):
            try:
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
            except OSError:
                pass

    def refresh(self):
        with self.lock:
            for f in glob.glob(os.path.join(ACTIVITY_DIR, "*.jsonl")):
                off = self.offsets.get(f, 0)
                try:
                    size = os.path.getsize(f)
                    if size <= off:
                        continue
                    with open(f, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(off)
                        chunk = fh.read()
                except OSError:
                    continue
                last_nl = chunk.rfind("\n")
                if last_nl < 0:
                    continue
                self.offsets[f] = off + len(chunk[:last_nl + 1].encode("utf-8"))
                for line in chunk[:last_nl].splitlines():
                    try:
                        self.apply(json.loads(line))
                    except Exception:
                        continue
            if len(self.calls) > 3000:
                for k in sorted(self.calls, key=lambda k: self.calls[k]["start"])[:-2000]:
                    del self.calls[k]

    def apply(self, ev):
        kind = ev.get("ev")
        tap = ev.get("tap_pid")
        sess = self.sessions.setdefault(tap, {"tap_pid": tap, "server": ev.get("server"), "client": ev.get("client"),
                                              "start": ev.get("ts"), "exit": None, "pid": None, "client_info": None,
                                              "logs": [], "current": None, "calls": 0})
        if kind == "server_start":
            sess.update(start=ev["ts"], pid=ev.get("pid"), cmd=ev.get("cmd"))
        elif kind == "server_exit":
            sess.update(exit=ev["ts"], code=ev.get("code"))
        elif kind == "server_error":
            sess.update(exit=ev["ts"], error=ev.get("error"))
        elif kind == "client_info":
            sess["client_info"] = (ev.get("name") or "") + (" " + ev["version"] if ev.get("version") else "")
        elif kind == "call_start":
            args = ev.get("args") or {}
            self.calls[ev["call"]] = {"id": ev["call"], "server": ev.get("server"), "client": ev.get("client"),
                                      "client_info": sess.get("client_info"), "tool": ev.get("tool"),
                                      "args": args, "model": args.get("model") or args.get("judge"),
                                      "agent": args.get("agent"), "provider": args.get("provider"),
                                      "start": ev["ts"], "end": None, "ms": None, "error": None,
                                      "chars": None, "preview": None, "logs": [], "tap_pid": tap,
                                      "consilium": ev.get("tool") == "consilium"}
            sess.setdefault("open", []).append(ev["call"])
            sess["current"] = ev["call"]
            sess["calls"] += 1
        elif kind == "call_end":
            c = self.calls.get(ev.get("call"))
            if c:
                c.update(end=ev["ts"], ms=ev.get("ms"), error=ev.get("error"),
                         chars=ev.get("chars"), preview=ev.get("preview"))
            opened = sess.get("open") or []
            if ev.get("call") in opened:
                opened.remove(ev.get("call"))
            if sess.get("current") == ev.get("call"):
                sess["current"] = opened[-1] if opened else None
        elif kind == "log":
            line = ev.get("line") or ""
            # bridges print "Tool call: <name>" when they pick a request up; bind following lines to it
            m = re.search(r"Tool call: (\S+)", line)
            if m:
                for cid in sess.get("open") or []:
                    c = self.calls.get(cid)
                    if c and c["tool"] == m.group(1) and not c.get("bound"):
                        c["bound"] = True
                        sess["current"] = cid
                        break
            tagged = self.calls.get(ev.get("call") or "")
            cur = tagged or self.calls.get(sess.get("current") or "")
            if cur and (tagged or cur["end"] is None):
                cur["logs"].append({"ts": ev["ts"], "line": line})
                u = parse_usage(line)
                if u:
                    cur.setdefault("usage", []).append(u)
                m = re.search(r"Council member (\S+): (ok|failed) in ([\d.]+)s", line)
                if m:
                    cur.setdefault("members", []).append({"model": m.group(1), "ok": m.group(2) == "ok",
                                                          "s": float(m.group(3))})
                m = re.search(r"Routing (\S+) to provider '([^']+)'", line)
                if m:
                    cur["provider"] = m.group(2)
                    if not cur.get("model"):
                        cur["model"] = m.group(1)
                self.ai_bridge_line(cur, ev["ts"], line)
            else:
                sess["logs"] = (sess["logs"] + [{"ts": ev["ts"], "line": line}])[-40:]

    @staticmethod
    def ai_bridge_line(cur, ts, line):
        """ai-bridge (claude_bridge.py v2) prints which role calls which pool and when it falls back."""
        m = re.search(r"Agent '([^']+)' answered via provider '([^']+)'(?: \(([^)]*)\))? in ([\d.]+)s", line)
        if m:
            step = next((r for r in reversed(cur.get("route") or []) if r["provider"] == m.group(2)
                         and r.get("ok") is None and r.get("agent") in (m.group(1), None)), None)
            if not step:
                step = {"ts": ts, "agent": None if m.group(1) == "model_ask" else m.group(1), "provider": m.group(2)}
                cur.setdefault("route", []).append(step)
            step.update(ok=True, s=float(m.group(4)), model=m.group(3), done=ts)
            return
        m = re.search(r"Agent '([^']+)' failed: (.*)", line)
        if m:
            cur.setdefault("failed_agents", {})[m.group(1)] = m.group(2)[:300]
            who = None if m.group(1) == "model_ask" else m.group(1)
            for pid, why in re.findall(r"(\S+) \(([^)]*)\)", m.group(2)):
                step = next((r for r in reversed(cur.get("route") or []) if r["provider"] == pid
                             and r.get("ok") is None and r.get("agent") == who), None)
                if step:
                    step.update(ok=False, why=why, done=ts)
            return
        m = re.search(r"Consilium members: ([^;]+); chair: ([^;\s]+)(?:; rounds: (\d))?", line)
        if m:
            cur["consilium"] = True
            cur["council"] = {"members": [x.strip() for x in m.group(1).split(",") if x.strip()], "chair": m.group(2),
                              "rounds": int(m.group(3) or 1), "round": 1}
            return
        m = re.search(r"Consilium round (\d): (\d+) members", line)
        if m:
            cur.setdefault("council", {"members": [], "chair": None})
            cur["council"].update(round=int(m.group(1)), **{f"round{m.group(1)}_ts": ts})
            return
        m = re.search(r"Skipping provider '([^']+)' \(agent '([^']+)'\): (.+)", line)
        if m:
            cur.setdefault("route", []).append({"ts": ts, "agent": m.group(2), "provider": m.group(1), "ok": False,
                                                "skipped": True, "why": m.group(3), "done": ts})
            return
        m = re.search(r"Provider '([^']+)' paused until (\S+(?: \S+)?) \((.+)\)$", line)
        if m:
            cur.setdefault("paused", []).append({"provider": m.group(1), "until": m.group(2), "why": m.group(3)})
            return
        m = re.search(r"Auto route: '([^']+)' \((.+)\)$", line)
        if m:
            cur["agent"], cur["auto"] = m.group(1), m.group(2)
            return
        m = re.search(r"Consilium chair '([^']+)' is writing the verdict", line)
        if m:
            cur.setdefault("council", {"members": [], "chair": m.group(1)})["verdict_ts"] = ts
            return
        m = re.search(r"Project context: (.+): (\d+) files, (\d+) chars", line)
        if m:
            cur["project"] = {"folder": m.group(1), "files": int(m.group(2)), "chars": int(m.group(3))}
            return
        m = re.search(r"Routing (\S+) to provider '([^']+)'", line)
        if m:
            cur.setdefault("route", []).append({"ts": ts, "agent": None, "provider": m.group(2), "ok": None})
            return
        m = re.search(r"Agent '([^']+)' calling provider '([^']+)'(?:\s*\(([^)]*)\))?", line)
        if m:
            if not cur.get("consilium"):  # a consilium call has many roles; the route lists them
                cur["agent"] = cur.get("agent") or m.group(1)
            cur["provider"] = m.group(2)
            cur.setdefault("route", []).append({"ts": ts, "agent": m.group(1), "provider": m.group(2),
                                                "type": m.group(3), "ok": None})
            return
        m = re.search(r"Provider '([^']+)' (.+?), switching to fallback", line)
        if m:
            # in a consilium several roles run at once, so match the role too when the line names it
            ma = re.search(r"\(agent '([^']+)'\)", line)
            open_steps = [r for r in reversed(cur.get("route") or []) if r["provider"] == m.group(1) and r.get("ok") is None]
            pick = next((r for r in open_steps if ma and r.get("agent") == ma.group(1)), open_steps[0] if open_steps else None)
            if pick:
                pick.update(ok=False, why=m.group(2), done=ts)
            else:
                cur.setdefault("route", []).append({"ts": ts, "agent": cur.get("agent"), "provider": m.group(1),
                                                    "ok": False, "why": m.group(2)})
            return
        if re.search(r"Консилиум", line):
            cur["consilium"] = True
        elif "Claude CLI" in line and ("не обнаружен" in line or "not found" in line.lower()):
            cur["cli_missing"] = True
        else:
            m = re.search(r"Limit signal detected: (.+)", line)
            if m:
                cur["limit"] = m.group(1).strip()

    def state(self, alive_taps):
        self.refresh()
        with self.lock:
            calls = sorted(self.calls.values(), key=lambda c: c["start"], reverse=True)
            for c in calls:
                # a call without an end whose tap process is gone was cut off (client closed, bridge crashed)
                c["orphan"] = c["end"] is None and c["tap_pid"] not in alive_taps
            sessions = []
            for s in self.sessions.values():
                d = {k: v for k, v in s.items() if k not in ("current", "open")}
                d["alive"] = s["exit"] is None and s["tap_pid"] in alive_taps
                sessions.append(d)
        day0 = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        tokens = {"by_model": {}, "by_provider": {}, "by_day": {}, "today": 0, "total": 0,
                  "calls_with_usage": 0, "calls_without_usage": 0}
        for c in calls:
            us = c.get("usage") or []
            if c["end"] is not None and c["server"] != "claude-bridge":
                tokens["calls_with_usage" if us else "calls_without_usage"] += 1
            for u in us:
                t = u.get("total") or 0
                day = time.strftime("%Y-%m-%d", time.localtime(c["start"]))
                tokens["by_day"][day] = tokens["by_day"].get(day, 0) + t
                tokens["total"] += t
                if c["start"] >= day0:
                    tokens["today"] += t
                for key, name in (("by_model", u.get("model") or c.get("model") or "?"),
                                  ("by_provider", u.get("provider") or c.get("provider") or "?")):
                    row = tokens[key].setdefault(name, {"prompt": 0, "completion": 0, "total": 0, "replies": 0})
                    row["prompt"] += u.get("prompt") or 0
                    row["completion"] += u.get("completion") or 0
                    row["total"] += t
                    row["replies"] += 1
        today = [c for c in calls if c["start"] >= day0]
        return {"calls": calls[:300], "sessions": sorted(sessions, key=lambda s: s["start"] or 0, reverse=True)[:60],
                "today": {"count": len(today), "errors": sum(1 for c in today if c["error"]),
                          "running": sum(1 for c in calls if c["end"] is None and c["tap_pid"] in alive_taps)},
                "tokens": tokens, "log_dir": ACTIVITY_DIR, "has_log": bool(self.offsets)}


# ---------------------------------------------------------------- wiring

def bundled_claude():
    """The Claude desktop app keeps its own copy of the Claude Code CLI in %APPDATA%\\Claude\\claude-code\\<version>\\;
    it is not on PATH, so look there too and take the newest version."""
    root = os.path.join(os.environ.get("APPDATA") or os.path.join(HOME, "AppData", "Roaming"), "Claude", "claude-code")
    try:
        vers = [d for d in os.listdir(root) if os.path.isfile(os.path.join(root, d, "claude.exe"))]
    except OSError:
        return None
    key = lambda v: [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v)]
    return os.path.join(root, max(vers, key=key), "claude.exe") if vers else None


def find_claude_cli():
    for c in (shutil.which("claude"), os.path.join(HOME, ".local", "bin", "claude.exe"),
              os.path.join(HOME, ".local", "bin", "claude"), bundled_claude()):
        if c and os.path.isfile(c):
            return c
    return None


def tap_python():
    exe = sys.executable
    if IS_WIN and exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def wrap_entry(name, entry, client):
    cmd = entry.get("command")
    args = list(entry.get("args") or [])
    new = dict(entry)
    new["command"] = tap_python()
    new["args"] = [TAP, "--server", name, "--client", client, "--", cmd] + args
    return new


def unwrap_entry(entry):
    cmd, args = original_command(entry)
    new = dict(entry)
    new["command"] = cmd
    new["args"] = args
    return new


def wire(enable=True):
    report = []
    for path in antigravity_config_paths():
        data = read_json(path)
        if data is None:
            report.append(f"[!] {path}: не удалось прочитать JSON, пропущено")
            continue
        servers = data.get("mcpServers") or {}
        changed = False
        for name, entry in servers.items():
            if name not in TAPPABLE or not entry.get("command"):
                continue
            if enable and not is_tapped(entry):
                servers[name] = wrap_entry(name, entry, "antigravity")
                changed = True
                report.append(f"Antigravity: {name} теперь пишет журнал")
            elif not enable and is_tapped(entry):
                servers[name] = unwrap_entry(entry)
                changed = True
                report.append(f"Antigravity: {name} возвращён к исходной команде")
        if changed:
            backup = path + ".council-backup"
            if not os.path.exists(backup):
                shutil.copy2(path, backup)
            write_json(path, data)
            report.append(f"  сохранено: {path} (резервная копия: {backup})")
    cpath = claude_config_path()
    claude = find_claude_cli()
    if cpath:
        data = read_json(cpath) or {}
        for name, entry in (data.get("mcpServers") or {}).items():
            if name not in TAPPABLE or not entry.get("command"):
                continue
            if enable == is_tapped(entry):
                continue
            new = wrap_entry(name, entry, "claude-code") if enable else unwrap_entry(entry)
            new.setdefault("type", "stdio")
            if not claude:
                report.append(f"[!] Claude Code: CLI не найден, {name} не изменён")
                continue
            backup = cpath + ".council-backup"
            if not os.path.exists(backup):
                shutil.copy2(cpath, backup)
            r1 = subprocess.run([claude, "mcp", "remove", "-s", "user", name], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
            r2 = subprocess.run([claude, "mcp", "add-json", "-s", "user", name, json.dumps(new)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                creationflags=NO_WINDOW)
            if r2.returncode == 0:
                report.append(f"Claude Code: {name} " + ("теперь пишет журнал" if enable else "возвращён к исходной команде"))
            else:
                report.append(f"[!] Claude Code: {name} не удалось обновить: {(r2.stderr or r2.stdout).strip()[:300]}")
                if r1.returncode == 0:
                    r3 = subprocess.run([claude, "mcp", "add-json", "-s", "user", name, json.dumps(entry)],
                                        capture_output=True, text=True, creationflags=NO_WINDOW)
                    report.append("    исходная запись восстановлена" if r3.returncode == 0 else
                                  f"    [!] восстановите вручную из {backup}")
    if not report:
        report.append("Изменений нет: всё уже в нужном состоянии или мосты не найдены в настройках.")
    report.append("Перезапустите Antigravity и откройте новый чат Claude Code, чтобы изменения вступили в силу.")
    return report


# ---------------------------------------------------------------- http server

# ---------------------------------------------------------------- Antigravity's own activity (read-only)

AG_HOME = os.path.join(HOME, ".gemini")
AG_CACHE = {}


def uri_to_path(uri):
    if not isinstance(uri, str):
        return None
    if uri.startswith("file://"):
        p = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
        if IS_WIN and re.match(r"^/[A-Za-z]:", p):
            p = p[1:]
        return os.path.normpath(p)
    return os.path.normpath(uri)


def antigravity_projects():
    out = []
    for f in sorted(glob.glob(os.path.join(AG_HOME, "config", "projects", "*.json"))):
        d = read_json(f) or {}
        folders = []
        for r in ((d.get("projectResources") or {}).get("resources") or []):
            g = (r or {}).get("gitFolder") or (r or {}).get("folder") or {}
            path = uri_to_path(g.get("folderUri") or g.get("uri"))
            if path:
                folders.append(path)
        out.append({"id": d.get("id") or os.path.basename(f)[:-5], "name": d.get("name") or "", "folders": folders,
                    "settings": {k: v for k, v in (d.get("settings") or {}).items() if isinstance(v, (str, bool, int))}})
    return out


def ag_model_code():
    path = os.path.join(AG_HOME, "antigravity", "antigravity_state.pbtxt")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            m = re.search(r"last_selected_agent_model:\s*(\S+)", fh.read(20000))
        return m.group(1) if m else None
    except OSError:
        return None


AG_TAG = re.compile(r"</?[A-Z][A-Z0-9_]*>")
AG_RESET = re.compile(r"[Rr]esets in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")


def ag_user_text(content):
    """The person's own words: Antigravity wraps them in tags such as <USER_REQUEST>."""
    text = str(content or "")
    m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", text, re.S)
    lines = [ln.strip() for ln in AG_TAG.sub("\n", m.group(1) if m else text).splitlines()]
    return "\n".join(ln for ln in lines if ln)


def ag_step(d):
    """One transcript line, reduced to what the app shows: type, status, time, tool names and their summaries."""
    step = {"i": d.get("step_index"), "type": d.get("type"), "source": d.get("source"), "status": d.get("status"),
            "ts": parse_ts(d.get("created_at"))}
    tools = []
    for t in d.get("tool_calls") or []:
        a = (t or {}).get("args") or {}
        item = {"name": t.get("name"), "summary": str(a.get("toolSummary") or "")[:160],
                "action": str(a.get("toolAction") or "")[:60]}
        if t.get("name") == "call_mcp_tool":
            item["server"], item["tool"] = a.get("ServerName"), a.get("ToolName")
        tools.append(item)
    if tools:
        step["tools"] = tools
    if d.get("type") == "USER_INPUT":
        step["text"] = ag_user_text(d.get("content"))[:300]
    elif d.get("type") == "ERROR_MESSAGE":
        err = str(d.get("error") or "")
        step["text"] = err[:300]
        # quota errors say "Resets in 2h6m32s", counted from the moment of the error
        m = AG_RESET.search(err)
        if m and step["ts"] and any(m.groups()):
            h, mi, se = (int(x or 0) for x in m.groups())
            step["reset_at"] = step["ts"] + h*3600 + mi*60 + se
        if "RESOURCE_EXHAUSTED" in err or "quota" in err.lower():
            step["quota"] = True
    return step


def ag_conversation(folder):
    logs = os.path.join(folder, ".system_generated", "logs", "transcript.jsonl")
    try:
        st = os.stat(logs)
    except OSError:
        return None
    key = (st.st_mtime, st.st_size)
    hit = AG_CACHE.get(logs)
    if hit and hit[0] == key:
        return hit[1]
    title, steps, errors, count = None, [], 0, 0
    try:
        with open(logs, "rb") as fh:
            head = fh.read(65536).decode("utf-8", errors="replace")
            for line in head.splitlines():
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("type") == "USER_INPUT":
                    text = ag_user_text(d.get("content"))
                    if text:
                        title = text.splitlines()[0][:140]
                        break
            fh.seek(max(0, st.st_size - 262144))
            tail = fh.read().decode("utf-8", errors="replace").splitlines()
        if st.st_size > 262144:
            tail = tail[1:]
        for line in tail:
            try:
                d = json.loads(line)
            except Exception:
                continue
            steps.append(ag_step(d))
        errors = sum(1 for x in steps if x["type"] == "ERROR_MESSAGE")
        count = (steps[-1]["i"] + 1) if steps and isinstance(steps[-1]["i"], int) else len(steps)
    except OSError:
        return None
    last = steps[-1] if steps else {}
    quota = next((x for x in reversed(steps) if x.get("quota")), None)
    conv = {"id": os.path.basename(folder), "title": title, "mtime": st.st_mtime, "size": st.st_size, "steps": count,
            "errors_recent": errors, "last": last, "tail": steps[-40:],
            "marks": [[x["ts"], x["type"], x["status"]] for x in steps[-200:] if x.get("ts")],
            "running_flag": any(x.get("status") == "RUNNING" for x in steps[-3:]),
            "quota": {"ts": quota["ts"], "reset_at": quota.get("reset_at")} if quota else None}
    AG_CACHE[logs] = (key, conv)
    return conv


def antigravity_state(full=False):
    convs = []
    for d in glob.glob(os.path.join(AG_HOME, "antigravity", "brain", "*")):
        if os.path.isdir(d):
            try:
                convs.append((os.path.getmtime(os.path.join(d, ".system_generated", "logs", "transcript.jsonl")), d))
            except OSError:
                continue
    convs.sort(reverse=True)
    out = []
    for _, d in convs[:6 if full else 1]:
        c = ag_conversation(d)
        if c:
            c = dict(c)
            # RUNNING on the last lines means a step is in progress; a stale file means Antigravity stopped mid-step
            c["active"] = c["running_flag"] and now() - c["mtime"] < 180
            c["recent"] = now() - c["mtime"] < 120
            if not full:
                c.pop("tail", None)
            out.append(c)
    return {"home": AG_HOME, "found": os.path.isdir(os.path.join(AG_HOME, "antigravity")),
            "model_code": ag_model_code(), "conversations": out, "projects": antigravity_projects() if full else None,
            "exe": antigravity_exe()}


def antigravity_exe():
    if not IS_WIN:
        return None
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles")):
        if root:
            p = os.path.join(root, "Programs", "antigravity", "Antigravity.exe") if root == os.environ.get("LOCALAPPDATA") \
                else os.path.join(root, "Antigravity", "Antigravity.exe")
            if os.path.isfile(p):
                return p
    return None


# ---------------------------------------------------------------- projects the chat can work with

def project_roots():
    env = os.environ.get("COUNCIL_PROJECT_ROOTS")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return [r"C:\projects"] if IS_WIN else [os.path.join(HOME, "projects")]


def list_projects():
    seen, out = set(), []

    def add(path, name, source):
        if not path or not os.path.isdir(path):
            return
        k = os.path.normcase(os.path.abspath(path))
        if k in seen:
            for x in out:
                if os.path.normcase(os.path.abspath(x["path"])) == k and source not in x["source"]:
                    x["source"].append(source)
            return
        seen.add(k)
        out.append({"path": os.path.abspath(path), "name": name or os.path.basename(path.rstrip("\\/")),
                    "source": [source], "git": os.path.isdir(os.path.join(path, ".git")),
                    "mtime": os.path.getmtime(path)})
    for p in antigravity_projects():
        for f in p["folders"]:
            add(f, p["name"], "Antigravity")
    for root in project_roots():
        try:
            for n in sorted(os.listdir(root)):
                if not n.startswith(".") and os.path.isdir(os.path.join(root, n)):
                    add(os.path.join(root, n), n, root)
        except OSError:
            continue
    for j in CHAT.history[-50:]:
        add(j.get("folder"), None, "чат")
    return out


# ---------------------------------------------------------------- chat: requests through the same MCP bridge

CHAT_MODES = {
    "auto": ("auto_run", "task"),
    "role": ("agent_run", "task"),
    "model": ("model_ask", "question"),
    "consilium": ("consilium", "task"),
    "claude": ("claude_ask", "question"),
}


class Chat:
    """Keeps one bridge process (through council_tap.py, so every request lands in the call log) and the dialogue."""

    def __init__(self):
        self.lock = threading.RLock()
        self.proc = None
        self.server = None
        self.rpc = 0
        self.pending = {}
        self.jobs = []
        self.history = []
        self.folder = os.path.join(COUNCIL_HOME, "chat")
        self.file = os.path.join(self.folder, "history.jsonl")
        self.load()

    def load(self):
        try:
            with open(self.file, "r", encoding="utf-8") as fh:
                for line in fh.readlines()[-200:]:
                    try:
                        self.history.append(json.loads(line))
                    except Exception:
                        pass
        except OSError:
            pass
        self.jobs = list(self.history[-100:])

    def save(self, job):
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(self.file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({k: v for k, v in job.items() if not k.startswith("_")}, ensure_ascii=False) + "\n")
        except OSError:
            pass
        self.history.append(job)

    def core_server(self):
        servers = [s for s in collect_servers() if s.get("kind") == "stdio" and s.get("file") and os.path.isfile(s["file"])]
        with_cfg = [s for s in servers if os.path.isfile(os.path.join(os.path.dirname(s["file"]), "providers.json"))]
        for pool in (with_cfg, [s for s in servers if s["name"] == "claude-bridge"]):
            pool = sorted(pool, key=lambda s: (s["client"] != "antigravity", s["name"] != "claude-bridge"))
            if pool:
                return pool[0]
        return None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        s = self.core_server()
        if not s:
            raise RuntimeError("мост с providers.json не найден в настройках Antigravity и Claude Code")
        env = os.environ.copy()
        env.update({k: str(v) for k, v in (s.get("_env") or {}).items()})
        cmd = [tap_python(), TAP, "--server", s["name"], "--client", "council-engine", "--",
               python_for(s["command"]) if "python" in os.path.basename(s["command"] or "").lower() else s["command"]] + s["args"]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     cwd=os.path.dirname(s["file"]), env=env, creationflags=NO_WINDOW)
        self.server = s["name"]
        threading.Thread(target=self.reader, args=(self.proc,), daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "council-engine", "version": APP_VERSION}})
        self.write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def write(self, msg):
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def request(self, method, params, job=None):
        with self.lock:
            self.rpc += 1
            rid = self.rpc
            self.pending[rid] = job
        self.write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def reader(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if not isinstance(msg, dict) or "id" not in msg or "method" in msg:
                continue
            with self.lock:
                job = self.pending.pop(msg["id"], None)
            if not job:
                continue
            res = msg.get("result") or {}
            text = "".join(p.get("text", "") for p in res.get("content") or [] if isinstance(p, dict))
            if "error" in msg:
                text = "BRIDGE_ERROR: " + json.dumps(msg["error"], ensure_ascii=False)
            job.update(status="error" if (res.get("isError") or "error" in msg) else "done", answer=text,
                       end=now(), ms=int((now() - job["ts"]) * 1000))
            self.save(job)
        # the bridge process ended: whatever was still waiting will not get an answer
        with self.lock:
            left = [j for j in self.pending.values() if j]
            self.pending.clear()
        for job in left:
            if job["status"] == "running":
                job.update(status="error", answer="Мост завершился, ответа не было.", end=now(),
                           ms=int((now() - job["ts"]) * 1000))
                self.save(job)

    def send(self, body):
        mode = body.get("mode") or "role"
        if mode not in CHAT_MODES:
            raise ValueError("неизвестный режим")
        text = str(body.get("text") or "").strip()
        if not text:
            raise ValueError("пустое сообщение")
        folder = str(body.get("folder") or "").strip() or None
        if folder and not os.path.isdir(folder):
            raise ValueError("папка проекта не найдена: " + folder)
        if mode == "claude" and not folder:
            raise ValueError("для Claude Code нужна папка проекта")
        tool, field = CHAT_MODES[mode]
        task = text
        if body.get("follow") and self.history:
            prev = [j for j in self.history if j.get("status") == "done"][-2:]
            if prev:
                ctx = "\n\n".join(f"Вопрос: {j['text'][:1500]}\nОтвет: {j['answer'][:3000]}" for j in prev)
                task = f"Предыдущие сообщения этого разговора:\n{ctx}\n\nНовое сообщение:\n{text}"
        args = {field: task}
        if folder:
            args["work_folder"] = folder
        if mode == "role":
            args["agent"] = str(body.get("agent") or "")
        if mode == "model":
            args["provider"] = str(body.get("provider") or "")
        files = str(body.get("files") or "").strip()
        if files and mode != "claude":
            args["files"] = files
        job = {"id": "%x" % int(now() * 1000), "ts": now(), "mode": mode, "tool": tool, "agent": args.get("agent"),
               "provider": args.get("provider"), "folder": folder, "text": text, "follow": bool(body.get("follow")) and task != text,
               "status": "running", "answer": None, "end": None, "ms": None, "call": None}
        with self.lock:
            if not self.alive():
                self.start()
            tap = self.proc.pid
        job["tap_pid"] = tap
        self.jobs.append(job)
        self.jobs = self.jobs[-100:]
        self.request("tools/call", {"name": tool, "arguments": args}, job)
        return job

    def stop(self):
        with self.lock:
            p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def link(self, calls):
        """Tie each chat request to its entry in the call log: same tap process, same tool, started right after."""
        taken = {j.get("call") for j in self.jobs if j.get("call")}
        for j in self.jobs:
            if j.get("call") or not j.get("tap_pid"):
                continue
            for c in sorted(calls, key=lambda c: c["start"]):
                if c["tap_pid"] == j["tap_pid"] and c["tool"] == j["tool"] and c["id"] not in taken \
                        and j["ts"] - 2 <= c["start"] <= j["ts"] + 30:
                    j["call"] = c["id"]
                    taken.add(c["id"])
                    break

    def state(self, calls):
        self.link(calls)
        return {"jobs": [{k: v for k, v in j.items() if not k.startswith("_")} for j in self.jobs[-60:]],
                "bridge": {"alive": self.alive(), "pid": self.proc.pid if self.alive() else None, "server": self.server}}


CHAT = Chat()


WATCHER = ProcessWatcher()
ACTIVITY = Activity()
LAST_BEAT = [None]
CONFIG_CACHE = {"ts": 0, "data": None}


def build_config(force=False):
    if not force and CONFIG_CACHE["data"] and now() - CONFIG_CACHE["ts"] < 300:
        return CONFIG_CACHE["data"]
    servers = collect_servers()
    bridges = {}
    for s in servers:
        f = s.get("file")
        # only the model bridges are imported for introspection; other MCP servers are left alone
        if f and f not in bridges and s["name"] in TAPPABLE:
            bridges[f] = probe_bridge(s, s["_env"])
    servers = [{k: v for k, v in s.items() if k != "_env"} for s in servers]
    rules = rule_files()
    data = {"servers": servers, "bridges": bridges, "rules": rules, "tiers": tier_rules(rules),
            "host": socket.gethostname(),
            "antigravity_configs": antigravity_config_paths(), "claude_config": claude_config_path(),
            "claude_cli": find_claude_cli(), "activity_dir": ACTIVITY_DIR, "home": HOME,
            "app": {"version": APP_VERSION, "dir": APP_DIR, "python": sys.executable}, "ts": now()}
    CONFIG_CACHE.update(ts=now(), data=data)
    return data


AG_STATE = {"ts": 0, "data": None}


def build_state():
    procs = WATCHER.get()
    alive_taps = {p["pid"] for p in procs["items"] if p["group"] == "tap"}
    if CHAT.alive():
        alive_taps.add(CHAT.proc.pid)  # the process list refreshes every 4 s; the chat's own bridge is known now
    activity = ACTIVITY.state(alive_taps)
    system = read_system()
    activity["ledger_calls"] = ledger_matches(system, activity["calls"])
    if now() - AG_STATE["ts"] > 2:
        try:
            AG_STATE.update(ts=now(), data=antigravity_state(full=False))
        except Exception as e:
            AG_STATE.update(ts=now(), data={"error": str(e)})
    return {"ts": now(), "processes": procs, "activity": activity, "system": system,
            "chat": CHAT.state(activity["calls"]), "antigravity": AG_STATE["data"]}


STATIC = {".js": "text/javascript; charset=utf-8", ".woff2": "font/woff2", ".css": "text/css; charset=utf-8",
          ".txt": "text/plain; charset=utf-8"}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", cache="no-store"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def do_GET(self):
        if not self.local():
            return self.send(403, {"error": "local only"})
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            with open(os.path.join(APP_DIR, "ui", "index.html"), "rb") as fh:
                return self.send(200, fh.read(), "text/html; charset=utf-8")
        if u.path.startswith(("/vendor/", "/fonts/")):
            # only files shipped with the app: three.js and the two typefaces
            root = os.path.realpath(os.path.join(APP_DIR, "ui"))
            path = os.path.realpath(os.path.join(root, *urllib.parse.unquote(u.path).strip("/").split("/")))
            ext = os.path.splitext(path)[1].lower()
            if not path.startswith(root + os.sep) or ext not in STATIC or not os.path.isfile(path):
                return self.send(404, {"error": "not found"})
            with open(path, "rb") as fh:
                return self.send(200, fh.read(), STATIC[ext], cache="max-age=3600")
        if u.path == "/api/antigravity":
            return self.send(200, antigravity_state(full=True))
        if u.path == "/api/projects":
            return self.send(200, {"projects": list_projects(), "roots": project_roots()})
        if u.path == "/api/ping":
            return self.send(200, {"app": APP_NAME, "version": APP_VERSION})
        if u.path == "/api/state":
            LAST_BEAT[0] = now()
            return self.send(200, build_state())
        if u.path == "/api/config":
            return self.send(200, build_config(force=q.get("refresh") == ["1"]))
        if u.path == "/api/file":
            path = (q.get("path") or [""])[0]
            allowed = {r["path"] for r in build_config()["rules"]} | {k["path"] for k in read_system()["skills"]}
            if path not in allowed:
                return self.send(403, {"error": "этот файл не входит в список правил"})
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return self.send(200, {"path": path, "text": fh.read(200000)})
        return self.send(404, {"error": "not found"})

    def do_POST(self):
        # requests that spend tokens or open programs: same origin only, JSON only, with the app's own header
        origin = self.headers.get("Origin")
        if not self.local() or self.headers.get("X-Council") != "1" or \
                (origin and urllib.parse.urlparse(origin).hostname not in ("127.0.0.1", "localhost")):
            return self.send(403, {"error": "local only"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(min(n, 2_000_000)).decode("utf-8")) if n else {}
        except Exception:
            return self.send(400, {"error": "bad json"})
        u = urllib.parse.urlparse(self.path)
        try:
            if u.path == "/api/chat":
                job = CHAT.send(body)
                return self.send(200, {k: v for k, v in job.items() if not k.startswith("_")})
            if u.path == "/api/chat/stop":
                CHAT.stop()
                return self.send(200, {"ok": True})
            if u.path == "/api/antigravity/open":
                exe = antigravity_exe()
                folder = str(body.get("folder") or "")
                known = {os.path.normcase(p["path"]) for p in list_projects()}
                if not exe:
                    return self.send(404, {"error": "Antigravity.exe не найден"})
                if os.path.normcase(os.path.abspath(folder)) not in known:
                    return self.send(403, {"error": "папки нет в списке проектов"})
                subprocess.Popen([exe, folder], creationflags=NO_WINDOW, close_fds=True)
                return self.send(200, {"ok": True})
        except (ValueError, RuntimeError) as e:
            return self.send(400, {"error": str(e)})
        except Exception as e:
            return self.send(500, {"error": str(e)})
        return self.send(404, {"error": "not found"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False


def already_running():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/ping", timeout=1.5) as r:
            return json.loads(r.read()).get("app") == APP_NAME
    except Exception:
        return False


def browser_exe():
    if not IS_WIN:
        return None
    roots = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")]
    rels = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
    for rel in rels:
        for root in roots:
            if root and os.path.isfile(os.path.join(root, rel)):
                return os.path.join(root, rel)
    return None


def open_window():
    url = f"http://127.0.0.1:{PORT}/"
    exe = browser_exe()
    if exe:
        profile = os.path.join(COUNCIL_HOME, "window-profile")
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1440,900",
                          "--no-first-run", "--no-default-browser-check",
                          "--disable-background-timer-throttling", "--disable-renderer-backgrounding"], creationflags=NO_WINDOW)
    else:
        webbrowser.open(url)


def serve(open_ui):
    if already_running():
        if open_ui:
            open_window()
        print(f"Council Engine уже запущен: http://127.0.0.1:{PORT}/")
        return
    httpd = Server(("127.0.0.1", PORT), Handler)
    WATCHER.start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Council Engine: http://127.0.0.1:{PORT}/", flush=True)
    started = now()
    if open_ui:
        open_window()
    while True:
        time.sleep(5)
        if not open_ui:
            continue
        beat = LAST_BEAT[0]
        # the window polls every 2 s; when it has been closed for a while, the app exits on its own
        if (beat and now() - beat > 150) or (not beat and now() - started > 180):
            CHAT.stop()
            httpd.shutdown()
            return


def status():
    WATCHER.scan()
    st = build_state()
    cfg = build_config(force=True)
    print(f"Council Engine {APP_VERSION}  ({APP_DIR})")
    print("\nMCP-серверы:")
    for s in cfg["servers"]:
        print(f"  [{s['client']}] {s['name']}: {s['command']} {' '.join(s['args'])}  "
              f"журнал={'да' if s['tapped'] else 'нет'}  ({s['config']})")
    print("\nМосты:")
    for f, b in cfg["bridges"].items():
        tools = ", ".join(t["name"] for t in b.get("tools") or [])
        print(f"  {f}: инструменты [{tools}]" + (f"  ошибка: {b['error']}" if b.get("error") else ""))
        for p in b.get("providers") or []:
            print(f"     пул {p['name']}: {len(p['models'])} моделей, ключ {'есть' if p['has_key'] else 'нет'}")
        if b.get("hardcoded_keys"):
            print(f"     [!] в файле зашито ключей: {b['hardcoded_keys']}")
    files = {f["name"]: f for f in st["system"]["files"]}
    pj = (files.get("providers.json") or {}).get("data") or {}
    aj = (files.get("agents.json") or {}).get("data") or {}
    tu = (files.get("token_usage.json") or {}).get("data") or {}
    if pj or aj or tu:
        print("\nМультимодель:")
        keys = st["system"].get("keys") or {}
        for pid, prov in (pj.get("providers") or {}).items():
            env = prov.get("api_key_env")
            key = "CLI" if prov.get("type") == "cli" else (f"ключ есть ({keys[env]})" if keys.get(env) else f"НЕТ КЛЮЧА {env}") if env else "ключ не нужен"
            print(f"  пул {pid}: {prov.get('model')}  {prov.get('base_url') or prov.get('command') or ''}  {key}")
        for aid, ag in (aj.get("agents") or {}).items():
            chain = [ag.get("primary_provider")] + list(ag.get("fallback_providers") or [])
            print(f"  роль {aid}: {' > '.join(str(c) for c in chain if c)}")
        ts = tu.get("total_stats") or {}
        if ts:
            print(f"  токены: {ts.get('total_tokens')} за {ts.get('total_requests')} запросов, ${ts.get('total_cost_usd')}")
    print("\nПроцессы:")
    for p in st["processes"]["items"]:
        print(f"  {p['pid']:>7}  {p['label']:<40} {p['rss'] / 1048576:7.1f} МБ")
    a = st["activity"]
    print(f"\nВызовов сегодня: {a['today']['count']}, ошибок: {a['today']['errors']}, журнал: {a['log_dir']}")
    print("\nФайлы правил:")
    for r in cfg["rules"]:
        print(f"  {r['kind']}: {r['path']}")


def main(argv):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    if "--status" in argv:
        return status()
    if "--wire" in argv or "--unwire" in argv:
        for line in wire(enable="--wire" in argv):
            print(line)
        return
    serve(open_ui="--serve" not in argv)


if __name__ == "__main__":
    if IS_WIN and sys.executable.lower().endswith("pythonw.exe"):
        logf = os.path.join(COUNCIL_HOME, "app.log")
        os.makedirs(COUNCIL_HOME, exist_ok=True)
        sys.stdout = sys.stderr = open(logf, "a", encoding="utf-8")
    main(sys.argv[1:])
