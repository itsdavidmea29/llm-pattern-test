#!/usr/bin/env python3
"""PreToolUse hook: lets a tool call run only if it is exactly one of the calls the script prepared.

Claude Code runs this before every tool call in an upload session and passes the call as JSON on stdin.
  argv[1]  a JSON file: {"allow": [{"tool": <tool name>, "input": <exact arguments>}, ...]}
  argv[2]  a log file: one JSON line per decision, read back by the script afterwards
Exit 0 lets the call run. Exit 2 blocks it, and Claude Code shows the reason (stderr) to the model.
"""
import json
import sys


def main() -> int:
    allowed = json.loads(open(sys.argv[1]).read())["allow"]
    call = json.load(sys.stdin)
    tool, args = call.get("tool_name"), call.get("tool_input")
    ok = any(tool == a["tool"] and args == a["input"] for a in allowed)  # dict equality: key order doesn't matter
    with open(sys.argv[2], "a") as log:
        log.write(json.dumps({"tool": tool, "allowed": ok}) + "\n")
    if ok:
        return 0
    print(f"Blocked: {tool} was not called with exactly the prepared arguments. Nothing was changed.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
