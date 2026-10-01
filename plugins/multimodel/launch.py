#!/usr/bin/env python3
"""Starts ai-bridge 4 for Claude Code. Finds the installed bridge (C:\\projects\\tools\\claude_bridge.py, ~/.aiduo or
the AI_BRIDGE variable) and, when Council Engine is installed, runs it through council_tap.py so Claude Code's calls
show up in the Council Engine call log like Antigravity's do. stdin/stdout pass straight through to the bridge."""
import os
import subprocess
import sys


def first_file(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def main():
    home = os.path.expanduser("~")
    bridge = first_file([os.environ.get("AI_BRIDGE"), r"C:\projects\tools\claude_bridge.py",
                         os.path.join(home, ".aiduo", "claude_bridge.py")])
    if not bridge:
        sys.stderr.write("[multimodel] ai-bridge не найден: установи его (scripts\\install-multimodel.ps1) "
                         "или укажи путь в переменной AI_BRIDGE\n")
        return 1
    tap = first_file([os.environ.get("COUNCIL_TAP"),
                      os.path.join(os.environ.get("LOCALAPPDATA", ""), "CouncilEngine", "council_tap.py")])
    cmd = [sys.executable, bridge]
    if tap:
        cmd = [sys.executable, tap, "--server", "claude-bridge", "--client", "claude-code", "--"] + cmd
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
