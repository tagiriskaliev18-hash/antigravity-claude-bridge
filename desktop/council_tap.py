"""Transparent MCP stdio tap.

Runs the real MCP server as a child process and passes every byte through
unchanged. On the side it records server start/exit, tool calls (start, end,
duration, error flag) and the server's stderr lines into a JSONL file, so the
Council Engine app can show what the bridges actually did.

Usage: python council_tap.py --server NAME --client CLIENT -- <command> [args...]
Logging never blocks or alters the proxied traffic: if a log write fails, the
call still goes through.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid

MAX_STR = 400


def council_home():
    base = os.environ.get("COUNCIL_HOME")
    if base:
        return base
    return os.path.join(os.path.expanduser("~"), ".council")


def short(value, limit=MAX_STR):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… (+{len(value) - limit})"
    if isinstance(value, dict):
        return {k: short(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [short(v, limit) for v in value[:20]]
    return value


class Recorder:
    def __init__(self, server, client):
        self.server = server
        self.client = client
        self.lock = threading.Lock()
        self.fh = None
        try:
            folder = os.path.join(council_home(), "activity")
            os.makedirs(folder, exist_ok=True)
            name = f"{server}-{client}-{os.getpid()}.jsonl"
            self.fh = open(os.path.join(folder, name), "a", encoding="utf-8")
        except Exception as e:  # logging is best-effort
            sys.stderr.write(f"[council-tap] cannot open activity log: {e}\n")

    def write(self, event, **fields):
        if not self.fh:
            return
        rec = {"ts": time.time(), "ev": event, "server": self.server,
               "client": self.client, "tap_pid": os.getpid()}
        rec.update(fields)
        try:
            line = json.dumps(rec, ensure_ascii=False)
            with self.lock:
                self.fh.write(line + "\n")
                self.fh.flush()
        except Exception:
            pass


def parse(line):
    try:
        return json.loads(line.decode("utf-8", errors="replace"))
    except Exception:
        return None


def main(argv):
    if "--" not in argv:
        sys.stderr.write("usage: council_tap.py --server NAME --client CLIENT -- cmd args...\n")
        return 2
    sep = argv.index("--")
    opts, cmd = argv[:sep], argv[sep + 1:]
    server = opts[opts.index("--server") + 1] if "--server" in opts else "mcp"
    client = opts[opts.index("--client") + 1] if "--client" in opts else "unknown"
    if not cmd:
        sys.stderr.write("[council-tap] no command given\n")
        return 2

    rec = Recorder(server, client)
    if os.name == "nt":
        # resolve "python" / "npx" etc. the way a shell would (PATHEXT), keeping the config untouched
        import shutil
        cmd[0] = shutil.which(cmd[0]) or cmd[0]
    try:
        child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, bufsize=0)
    except Exception as e:
        rec.write("server_error", error=str(e), cmd=cmd)
        sys.stderr.write(f"[council-tap] failed to start {cmd}: {e}\n")
        return 1
    rec.write("server_start", pid=child.pid, cmd=cmd, parent_pid=os.getppid())

    pending = {}
    plock = threading.Lock()

    def from_host():
        stdin = sys.stdin.buffer
        while True:
            line = stdin.readline()
            if not line:
                break
            msg = parse(line)
            if isinstance(msg, dict):
                method = msg.get("method")
                if method == "initialize":
                    info = (msg.get("params") or {}).get("clientInfo") or {}
                    rec.write("client_info", name=info.get("name"), version=info.get("version"))
                elif method == "tools/call" and "id" in msg:
                    params = msg.get("params") or {}
                    call_id = uuid.uuid4().hex[:12]
                    with plock:
                        pending[json.dumps(msg["id"])] = (call_id, time.time())
                    rec.write("call_start", call=call_id, tool=params.get("name"),
                              args=short(params.get("arguments") or {}))
            try:
                child.stdin.write(line)
                child.stdin.flush()
            except Exception:
                break
        try:
            child.stdin.close()
        except Exception:
            pass

    def from_server():
        out = sys.stdout.buffer
        while True:
            line = child.stdout.readline()
            if not line:
                break
            msg = parse(line)
            if isinstance(msg, dict) and "id" in msg and "method" not in msg:
                with plock:
                    hit = pending.pop(json.dumps(msg["id"]), None)
                if hit:
                    call_id, started = hit
                    result = msg.get("result") or {}
                    text = ""
                    for part in result.get("content") or []:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text += part.get("text", "")
                    is_error = bool(result.get("isError")) or "error" in msg
                    if "error" in msg:
                        text = json.dumps(msg["error"], ensure_ascii=False)
                    rec.write("call_end", call=call_id, ms=int((time.time() - started) * 1000),
                              error=is_error, chars=len(text), preview=short(text))
            out.write(line)
            out.flush()

    def from_stderr():
        err = sys.stderr.buffer
        while True:
            line = child.stderr.readline()
            if not line:
                break
            try:
                err.write(line)
                err.flush()
            except Exception:
                pass
            rec.write("log", line=short(line.decode("utf-8", errors="replace").rstrip(), 300))

    threads = [threading.Thread(target=f, daemon=True) for f in (from_host, from_server, from_stderr)]
    for t in threads:
        t.start()
    code = child.wait()
    threads[1].join(timeout=2)
    threads[2].join(timeout=2)
    with plock:
        left = list(pending.values())
    for call_id, started in left:
        rec.write("call_end", call=call_id, ms=int((time.time() - started) * 1000),
                  error=True, chars=0, preview="сервер завершился, ответа не было")
    rec.write("server_exit", pid=child.pid, code=code)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
