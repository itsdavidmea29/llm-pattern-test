#!/usr/bin/env python3
"""LLM pattern test: sends each context level of a ticket to its own stripped-down Claude Code, all at once.

  python run_test.py run herd-7121                  # every level in tests/herd-7121, at the same time
  python run_test.py run herd-7121 herd-7969 --reps 3
  python run_test.py run herd-7121 --levels some,full      # only these levels
  python run_test.py run herd-7121 --no-logs               # context alone, no log: what the ticket text gives
  python run_test.py run herd-7121 --model sonnet --effort medium   # this run only; config.json is unchanged
  python run_test.py files herd-7121                 # which logs would be sent, which files are skipped
  python run_test.py show herd-7121 --level full     # print exactly what Claude would receive
  python run_test.py upload                          # push runs the sheet doesn't have yet

Attaching the ticket's files (copies into tests/<ticket>/, never changes the originals):
  python run_test.py attach herd-7121 ~/Downloads/phones.txt ~/Downloads/pbx.zip      # as they are
  python run_test.py attach herd-7121 ~/Downloads/pbx.log --grep '0004f2aabbcc' --as one-phone.log
  python run_test.py attach herd-7121 ~/Downloads/pbx.log* --between "2026-02-13 01:10" "2026-02-13 01:20" --as window.log

Adding a test: make tests/<ticket>/ and put in it
  the logs             log files and/or the ticket's archives (.zip, .tar.gz, .tgz, .gz), as downloaded
  <ticket>-some.txt    optional: a little context, such as phone, firmware and the reported symptom
  <ticket>-full.txt    optional: the ticket's history up to before the cause was found, never the resolution
Every file in the folder other than the two context files is sent, so attach = put the file in the folder.
The raw level is the logs alone; some and full put their context in front of the same logs.
The expected answer stays in the sheet, never in this folder.
"""
import argparse
import asyncio
import csv
import gzip
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

import sheet
import sheet_via_claude

HERE = Path(__file__).resolve().parent
TESTS = HERE / "tests"
RESULTS = HERE / "results"
RUNS_FILE = RESULTS / "runs.jsonl"
LEVELS = ["raw", "some", "full"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]  # what `claude --effort` accepts

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "problem_found": {"type": "boolean"},
        "causes": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "cause": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["cause", "confidence"],
                "additionalProperties": False,
            },
        },
        "quoted_lines": {"type": "array", "items": {"type": "string"}},
        "fix": {"type": "string"},
    },
    "required": ["problem_found", "causes", "quoted_lines", "fix"],
    "additionalProperties": False,
}

# Column order of the local CSV and of a new Results tab. The script fills every column up to "Quotes not in log";
# the last four are yours to fill in the sheet.
HEADERS = [
    "Run ID", "Date", "Jira ticket", "Context level", "Repeat", "Prompt", "Model", "Effort",
    "Log lines", "Context chars",
    "Status", "Total time (s)", "Model time (s)", "First token (s)",
    "Input tokens", "Cached input tokens", "Output tokens", "Thinking tokens", "Cost (USD)", "Turns",
    "Problem found", "Cause 1", "Confidence 1", "Cause 2", "Cause 3", "Fix",
    "Quoted lines", "Quoted lines found", "Quotes not in log",
    "Right cause rank", "Fix right?", "Invented a problem?", "Notes",
    "Log files", "Log chars",  # added after Summary was built: kept last so Summary's column letters don't move
]


# ---------- reading a test ----------

def test_dir(name: str) -> Path:
    path = TESTS / name.lower()
    if not path.is_dir():
        sys.exit(f"No folder {path}")
    return path


def context_file(folder: Path, level: str) -> Path:
    return folder / f"{folder.name}-{level}.txt"


def levels_present(folder: Path) -> list:
    return ["raw"] + [lvl for lvl in ("some", "full") if context_file(folder, lvl).is_file()]


def load_logs(folder: Path, cfg: dict) -> list:
    """Reads every log in the folder (archives opened), or stops with the list of files if they're too big."""
    logs, skipped = collect_logs(folder)
    if not logs:
        sys.exit(f"No log text found in {folder}. Attach the ticket's files first:\n"
                 f"  python run_test.py attach {folder.name} <files...>\n"
                 "or pass --no-logs to send the context alone.")
    total = sum(len(text) for _, text in logs)
    if total > cfg["max_log_chars"]:
        print_files(logs, skipped)
        sys.exit(f"\nThe logs come to {total:,} characters, over max_log_chars ({cfg['max_log_chars']:,}) in "
                 "config.json. Remove the files you don't need from the folder, or raise the limit.")
    return logs


def build_prompt(folder: Path, level: str, logs: list) -> tuple:
    """Returns (prompt, context text) for one level. Every level gets the same logs in the same order."""
    context = "" if level == "raw" else context_file(folder, level).read_text().strip()
    parts = [f"<context>\n{context}\n</context>"] if context else []
    parts += [f'<log file="{name}">\n{text}\n</log>' for name, text in logs]
    return "\n\n".join(parts), context


# ---------- logs and archives ----------
# Ticket attachments are untrusted: archives are read in memory and never unpacked to disk, every read is capped,
# and only text files are kept. Anything skipped is listed by 'files' with the reason.

MAX_FILE_BYTES = 200 * 1024 * 1024  # per file, after decompressing: stops a zip bomb from filling memory
MAX_DEPTH = 3                       # archives inside archives
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".pcap", ".pcapng", ".cap", ".xlsx", ".xls", ".docx",
              ".pdf", ".mp4", ".mov", ".wav", ".mp3", ".jar", ".class", ".exe", ".bin", ".hprof"}


def collect_logs(folder: Path) -> tuple:
    """Returns ([(name, text)], [(name, reason skipped)]), in file-name order then archive order."""
    logs, skipped = [], []
    context_names = {context_file(folder, lvl).name for lvl in ("some", "full")}
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith(".") or path.name in context_names:
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            skipped.append((path.name, "file over 200 MB"))
            continue
        add_file(path.name, path.read_bytes(), logs, skipped, depth=0)
    return logs, skipped


def read_capped(stream) -> bytes | None:
    data = stream.read(MAX_FILE_BYTES + 1)
    return None if len(data) > MAX_FILE_BYTES else data


def add_file(name: str, data: bytes, logs: list, skipped: list, depth: int) -> None:
    lower = name.lower()
    is_archive = lower.endswith((".zip", ".tar", ".tgz", ".tar.gz", ".gz"))
    if is_archive and depth >= MAX_DEPTH:
        skipped.append((name, f"archive nested more than {MAX_DEPTH} deep"))
        return
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    with zf.open(info) as member:
                        content = read_capped(member)
                    member_name = f"{name}/{info.filename}"
                    if content is None:
                        skipped.append((member_name, "over 200 MB unpacked"))
                    else:
                        add_file(member_name, content, logs, skipped, depth + 1)
        elif lower.endswith((".tar", ".tgz", ".tar.gz")):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    content = read_capped(tf.extractfile(member))
                    member_name = f"{name}/{member.name}"
                    if content is None:
                        skipped.append((member_name, "over 200 MB unpacked"))
                    else:
                        add_file(member_name, content, logs, skipped, depth + 1)
        elif lower.endswith(".gz"):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as gz:
                content = read_capped(gz)
            if content is None:
                skipped.append((name, "over 200 MB unpacked"))
            else:
                add_file(name[:-3], content, logs, skipped, depth + 1)
        elif Path(lower).suffix in BINARY_EXT or b"\x00" in data[:8192]:
            skipped.append((name, "not text"))
        else:
            logs.append((name, data.decode("utf-8", errors="replace")))
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError) as e:
        skipped.append((name, f"can't open: {e}"))


def print_files(logs: list, skipped: list, limit: int | None = None) -> None:
    if not logs:
        print("No logs yet: attach the phone's log files or the ticket's archives with 'attach'.")
    print("Sent to Claude:")
    for name, text in logs:
        print(f"  {len(text):>12,} chars  {len(text.splitlines()):>9,} lines  {name}")
    total = sum(len(t) for _, t in logs)
    print(f"  {total:>12,} chars in total")
    if skipped:
        print("Skipped:")
        for name, reason in skipped:
            print(f"  {reason:<28} {name}")
    if limit is not None and logs:
        if total > limit:
            print(f"Too big: {total / limit:.1f}x max_log_chars ({limit:,}). Trim with attach --grep or --between, "
                  "or remove files from the folder.")
        else:
            print(f"Fits: {total / limit:.1%} of max_log_chars ({limit:,}).")


# ---------- attaching files ----------
# 'attach' copies a ticket's attachments into the test folder, optionally cut down to the lines that matter, so a
# 50 MB server log can become the few thousand lines about one phone. It never changes the source files.

TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?")
ARCHIVE_EXT = (".zip", ".tar", ".tgz", ".tar.gz")


def read_source(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix.lower() == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


def filter_lines(text: str, pattern, start: str | None, end: str | None) -> str:
    """Keeps the log entries that match the pattern and fall between start and end.

    An entry begins at a line that starts with a timestamp. Lines without one (stack traces, wrapped messages)
    stay with the entry before them, so a kept entry is never cut in half. Times are compared as text, so
    "2026-02-13 01:10" matches every entry in that minute.
    """
    out, keep = [], False
    for line in text.splitlines(keepends=True):
        stamp = TIMESTAMP.match(line.lstrip("[ \t"))
        if stamp:
            when = stamp.group()
            keep = ((not start or when[:len(start)] >= start) and (not end or when[:len(end)] <= end)
                    and (pattern is None or pattern.search(line) is not None))
        elif pattern is not None and pattern.search(line):
            keep = True
        if keep:
            out.append(line)
    return "".join(out)


def cmd_attach(args, cfg):
    folder = TESTS / args.ticket.lower()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        pattern = re.compile(args.grep) if args.grep else None
    except re.error as e:
        sys.exit(f"--grep is not a valid regular expression: {e}")
    start, end = args.between or (None, None)
    trimming = pattern is not None or start is not None
    context_names = {context_file(folder, lvl).name for lvl in ("some", "full")}
    merged = []
    for src in args.files:
        if not src.is_file():
            sys.exit(f"No file {src}")
        if (trimming or args.name) and src.name.lower().endswith(ARCHIVE_EXT):
            sys.exit(f"{src.name}: --grep, --between and --as work on plain and .gz files. Unpack the archive "
                     "first, or attach it on its own without those options.")
        if not trimming and not args.name:
            target = folder / src.name
            if target.name in context_names:
                sys.exit(f"{src.name} is the name of this test's context file; rename it first.")
            shutil.copy2(src, target)
            print(f"Attached {target.name}")
            continue
        text = read_source(src)
        kept = filter_lines(text, pattern, start, end) if trimming else text
        if trimming:
            print(f"{src.name}: kept {len(kept.splitlines()):,} of {len(text.splitlines()):,} lines")
        if args.name:
            merged.append(kept)
        elif not kept.strip():
            print(f"  nothing kept, so {src.name} was not attached")
        else:
            target = folder / (src.name[:-3] if src.suffix.lower() == ".gz" else src.name)
            if target.name in context_names:
                sys.exit(f"{src.name} is the name of this test's context file; use --as to pick another name.")
            target.write_text(kept)
            print(f"Attached {target.name}")
    if args.name:
        target = folder / args.name
        if target.name in context_names:
            sys.exit(f"{args.name} is the name of this test's context file; pick another name.")
        text = "".join(merged)
        if not text.strip():
            sys.exit("Nothing kept, so nothing was attached. Check the pattern or the times against the log.")
        target.write_text(text)
        print(f"Attached {target.name} ({len(args.files)} file{'s' if len(args.files) > 1 else ''} merged)")
    print()
    print_files(*collect_logs(folder), limit=cfg["max_log_chars"])


# ---------- checking the answer (plain code, no model) ----------

def norm(text: str) -> str:
    return " ".join(text.split())


def check_quotes(quotes: list, logs: list) -> list:
    """Returns the quotes that appear nowhere in the logs. A quote spanning several lines must match line by line."""
    log_lines = [norm(line) for _, text in logs for line in text.splitlines()]
    missing = []
    for quote in quotes:
        pieces = [norm(p) for p in quote.splitlines() if p.strip()]
        if not all(any(piece in line for line in log_lines) for piece in pieces):
            missing.append(quote)
    return missing


def seconds(ms):
    return round(ms / 1000, 2) if isinstance(ms, (int, float)) else ""


# ---------- one sandbox ----------

def claude_cmd(cfg: dict, system_prompt: str) -> list:
    # --safe-mode drops CLAUDE.md, skills, plugins, hooks and MCP servers; --tools "" leaves Claude no tools at all.
    return ["claude", "-p", "--safe-mode", "--tools", "",
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
            "--no-session-persistence", "--output-format", "json",
            "--model", cfg["model"], "--effort", cfg["effort"],
            "--system-prompt", system_prompt,
            "--json-schema", json.dumps(ANSWER_SCHEMA)]


def clean_env() -> dict:
    """Drops CLAUDE* variables so a run started from inside Claude Code behaves like one started from a terminal."""
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}


async def run_sandbox(folder: Path, level: str, rep: int, logs: list, cfg: dict) -> dict:
    system_prompt = (HERE / "prompts" / f"{cfg['prompt']}.txt").read_text()
    prompt, context = build_prompt(folder, level, logs)
    started = datetime.now()
    run_id = f"{started:%Y%m%d-%H%M%S}-{folder.name}-{level}{'' if logs else '-nologs'}-r{rep}"
    row = {
        "Run ID": run_id, "Date": f"{started:%Y-%m-%d %H:%M}", "Jira ticket": folder.name.upper(),
        # A context-only run gets its own label, so Summary's raw/some/full columns never count it.
        "Context level": level if logs else f"{level} (no logs)", "Repeat": rep, "Prompt": cfg["prompt"],
        "Model": cfg["model"], "Effort": cfg["effort"],
        "Log files": len(logs), "Log lines": sum(len(t.splitlines()) for _, t in logs),
        "Log chars": sum(len(t) for _, t in logs), "Context chars": len(context),
    }

    # Each sandbox gets its own empty working folder, so no CLAUDE.md or project file is anywhere near it.
    with tempfile.TemporaryDirectory(prefix="llm-pattern-") as cwd:
        proc = await asyncio.create_subprocess_exec(
            *claude_cmd(cfg, system_prompt), cwd=cwd, env=clean_env(),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), cfg["timeout_s"])
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {**row, "Status": f"timeout after {cfg['timeout_s']} s"}

    raw_dir = RESULTS / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{run_id}.json").write_text(json.dumps(
        {"prompt": prompt, "stdout": stdout.decode(errors="replace"), "stderr": stderr.decode(errors="replace"),
         "exit_code": proc.returncode}, indent=2))

    try:
        out = json.loads(stdout)
    except json.JSONDecodeError:
        return {**row, "Status": f"error: exit {proc.returncode}, see results/raw/{run_id}.json"}

    usage = out.get("usage", {})
    row.update({
        "Status": out.get("subtype", ""),
        "Total time (s)": seconds(out.get("duration_ms")),
        "Model time (s)": seconds(out.get("duration_api_ms")),
        "First token (s)": seconds(out.get("ttft_ms")),
        "Input tokens": sum(usage.get(k, 0) for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
        "Cached input tokens": usage.get("cache_read_input_tokens", 0),
        "Output tokens": usage.get("output_tokens", 0),
        "Thinking tokens": usage.get("output_tokens_details", {}).get("thinking_tokens", 0),
        "Cost (USD)": round(out["total_cost_usd"], 4) if "total_cost_usd" in out else "",
        "Turns": out.get("num_turns", ""),
    })

    answer = out.get("structured_output")
    if out.get("is_error") or not isinstance(answer, dict):
        row["Status"] = f"error: no answer, see results/raw/{run_id}.json"
        return row

    causes = answer.get("causes", [])
    quotes = answer.get("quoted_lines", [])
    missing = check_quotes(quotes, logs)
    cause = lambda i: causes[i]["cause"] if len(causes) > i else ""
    row.update({
        "Problem found": "Yes" if answer.get("problem_found") else "No",
        "Cause 1": cause(0), "Confidence 1": causes[0]["confidence"] if causes else "",
        "Cause 2": cause(1), "Cause 3": cause(2),
        "Fix": answer.get("fix", ""),
        "Quoted lines": len(quotes), "Quoted lines found": len(quotes) - len(missing),
        "Quotes not in log": "\n".join(missing),
    })
    return row


async def run_batch(folder: Path, levels: list, rep: int, logs: list, cfg: dict) -> list:
    """Starts one sandbox per level and waits for all of them, like Promise.all in JavaScript."""
    return await asyncio.gather(*(run_sandbox(folder, level, rep, logs, cfg) for level in levels))


# ---------- results ----------

def load_runs() -> list:
    if not RUNS_FILE.is_file():
        return []
    return [json.loads(line) for line in RUNS_FILE.read_text().splitlines() if line.strip()]


def save_runs(rows: list) -> None:
    RESULTS.mkdir(exist_ok=True)
    with RUNS_FILE.open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    with (RESULTS / "runs.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(load_runs())


def upload(cfg: dict) -> None:
    rows = load_runs()
    if cfg.get("upload_via") == "claude":
        print("Uploading through Claude's Google Sheets connector (guarded: the data can't be changed) ...")
        added = sheet_via_claude.upload(rows, cfg, HERE)
    else:
        added = sheet.upload(rows, HEADERS, cfg, HERE)
    print(f"Sheet: {added} new row(s) added, {len(rows) - added} already there.")


def print_row(row: dict) -> None:
    print(f"  {row['Context level']:<4} r{row['Repeat']}  {row['Status']:<9} {row.get('Total time (s)', '')} s  "
          f"{row.get('Input tokens', '')} in / {row.get('Output tokens', '')} out  ${row.get('Cost (USD)', '')}  "
          f"quotes {row.get('Quoted lines found', '-')}/{row.get('Quoted lines', '-')}  "
          f"{row.get('Cause 1', '')[:70]}")


# ---------- commands ----------

def pick_levels(folder: Path, args) -> list:
    """The levels to run: those asked for (default all), minus raw when no logs are sent, since raw would be empty."""
    present = levels_present(folder)
    wanted = args.levels.split(",") if args.levels else present
    for level in wanted:
        if level not in LEVELS:
            sys.exit(f"Unknown level '{level}'. Use {', '.join(LEVELS)}.")
        if level not in present:
            sys.exit(f"{context_file(folder, level)} doesn't exist")
    levels = [lvl for lvl in wanted if not (args.no_logs and lvl == "raw")]
    if not levels:
        sys.exit(f"Nothing to run for {folder.name}: with --no-logs it needs a some or full file.")
    return levels


def cmd_run(args, cfg):
    # Command-line choices win over config.json for this run only. The rows read cfg, so the sheet records
    # the model and effort that were really used.
    if args.model:
        cfg["model"] = args.model
    if args.effort:
        cfg["effort"] = args.effort
    print(f"Model {cfg['model']}, effort {cfg['effort']}, prompt {cfg['prompt']}", flush=True)
    folders = [test_dir(name) for name in args.tickets]
    # Check every folder before spending anything.
    plans = {folder: (pick_levels(folder, args), [] if args.no_logs else load_logs(folder, cfg)) for folder in folders}
    for folder in folders:
        levels, logs = plans[folder]
        for rep in range(1, args.reps + 1):
            print(f"{folder.name} repeat {rep}: {len(levels)} sandboxes ({', '.join(levels)}), "
                  f"{len(logs)} log file(s) ...", flush=True)
            rows = asyncio.run(run_batch(folder, levels, rep, logs, cfg))
            save_runs(rows)  # saved after every batch, so a crash later loses nothing
            for row in rows:
                print_row(row)
    if not args.no_upload:
        upload(cfg)


def cmd_show(args, cfg):
    folder = test_dir(args.ticket)
    if args.level not in levels_present(folder):
        sys.exit(f"{context_file(folder, args.level)} doesn't exist")
    if args.no_logs and args.level == "raw":
        sys.exit("raw with --no-logs would send nothing")
    logs = [] if args.no_logs else load_logs(folder, cfg)
    print(build_prompt(folder, args.level, logs)[0])


def cmd_files(args, cfg):
    folder = test_dir(args.ticket)
    if args.level:
        if args.level not in levels_present(folder):
            sys.exit(f"{context_file(folder, args.level).name} doesn't exist, so this test has no {args.level} level.")
        if args.level == "raw":
            print("Context: none (raw sends the logs alone)")
        else:
            path = context_file(folder, args.level)
            print(f"Context: {len(path.read_text().strip()):,} chars  {path.name}")
    print_files(*collect_logs(folder), limit=cfg["max_log_chars"])
    print(f"Levels: {', '.join(levels_present(folder))}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run every level of one or more tickets")
    r.add_argument("tickets", nargs="+", help="folder names under tests/, e.g. herd-7121")
    r.add_argument("--levels", help="comma-separated levels to run, e.g. some,full (default: every level present)")
    r.add_argument("--no-logs", action="store_true", help="send the context alone, without any log; skips raw")
    r.add_argument("--reps", type=int, default=1, help="how many times to run each level (default 1)")
    r.add_argument("--no-upload", action="store_true", help="keep results local; push later with 'upload'")
    r.add_argument("--model", help="model for this run: an alias (sonnet, opus, haiku) or a full name such as "
                                   "claude-sonnet-5-5 (default: config.json)")
    r.add_argument("--effort", choices=EFFORTS, help="effort for this run (default: config.json)")
    s = sub.add_parser("show", help="print the prompt for one level without calling Claude")
    s.add_argument("ticket")
    s.add_argument("--level", default="raw", choices=LEVELS)
    s.add_argument("--no-logs", action="store_true", help="show the prompt without any log")
    f = sub.add_parser("files", help="list the logs that would be sent and the files skipped, without calling Claude")
    f.add_argument("ticket")
    f.add_argument("--level", choices=LEVELS, help="also show the context file this level puts in front of the logs")
    a = sub.add_parser("attach", help="copy attachments into a test's folder, trimmed if asked, then list what "
                                      "would be sent")
    a.add_argument("ticket", help="folder name under tests/; created if it doesn't exist")
    a.add_argument("files", nargs="+", type=Path, help="the files to attach, as downloaded from the ticket")
    a.add_argument("--grep", help="keep only log entries matching this regular expression, e.g. a phone's MAC, "
                                  "an extension, or 'ERROR|WARN'")
    a.add_argument("--between", nargs=2, metavar=("FROM", "TO"),
                   help='keep only entries between two times, e.g. "2026-02-13 01:10" "2026-02-13 01:20"')
    a.add_argument("--as", dest="name", metavar="NAME", help="merge every file given into one attachment called NAME")
    sub.add_parser("upload", help="add runs the sheet doesn't have yet")
    args = parser.parse_args()
    cfg = json.loads((HERE / "config.json").read_text())
    commands = {"run": cmd_run, "show": cmd_show, "files": cmd_files, "attach": cmd_attach,
                "upload": lambda a, c: upload(c)}
    commands[args.command](args, cfg)


if __name__ == "__main__":
    main()
