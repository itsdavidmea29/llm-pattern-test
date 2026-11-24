# llm-pattern-test

Sends a phone log to Claude at three levels of context, each in its own stripped-down Claude Code running at the
same time, and records the answer, time, tokens and cost of each one in the "LLM pattern test runs" Google Sheet.

## Setup (once)

```bash
cd ~/Documents/llm-pattern-test
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Add a test

Make a folder under `tests/` named after the ticket, in lower case:

```
tests/herd-7121/
  app.log, phone-logs.zip, ...   the logs: plain files or the ticket's archives, as downloaded (required)
  herd-7121-some.txt             a little context: phone, firmware, reported symptom (optional)
  herd-7121-full.txt             the ticket history up to before the cause was found, never the resolution (optional)
```

Every file that isn't the some or full file counts as a log. Archives (`.zip`, `.tar.gz`, `.tgz`, `.gz`, also inside
one another) are opened in memory, and only text files inside them are sent; images, pcaps and other binaries are
skipped. Run `files` first to see what will be sent and what was skipped.

### Attaching the ticket's files

The context files are text only: the attachments are whatever else is in the folder. `attach` copies files there
for you, trimmed to the lines that matter if the logs are too big, and finishes with the `files` listing and a
"Fits" or "Too big" line against `max_log_chars`:

```bash
.venv/bin/python run_test.py attach herd-7121 ~/Downloads/phones.txt ~/Downloads/pbx.zip   # as they are
.venv/bin/python run_test.py attach herd-7121 ~/Downloads/pbx.log --grep '0004f2aabbcc' --as one-phone.log
.venv/bin/python run_test.py attach herd-7121 ~/Downloads/pbx.log* \
    --between "2026-02-13 01:10" "2026-02-13 01:20" --as window.log
```

`--grep` keeps the log entries matching a regular expression, `--between` keeps those in a time window (compared
as text, so `01:10` means the whole minute), and `--as` merges all the files given into one attachment. A line
without a timestamp, such as a stack trace, stays with the entry before it. `.gz` sources are read directly;
`.zip` and `.tar.gz` can be attached whole but not trimmed. The originals are never changed. To detach a file,
delete it from the folder.

The raw level is the logs alone. Some and full put their context in front of the same logs, so every level gets
exactly the same logs. The expected answer stays in the sheet, never in this folder.

If the logs total more than `max_log_chars` (800,000 characters, roughly 200,000 tokens), `run` stops before
calling Claude and lists the files, so you can take out the ones you don't need.

## Run

```bash
.venv/bin/python run_test.py files herd-7121                # which logs will be sent, costs nothing
.venv/bin/python run_test.py show herd-7121 --level full    # the exact prompt Claude will receive, costs nothing
.venv/bin/python run_test.py run herd-7121                  # one sandbox per level, all at once
.venv/bin/python run_test.py run herd-7121 herd-7969 --reps 3
.venv/bin/python run_test.py run herd-7121 --levels some,full   # only these levels
.venv/bin/python run_test.py run herd-7121 --no-logs        # context alone, no log at all
.venv/bin/python run_test.py upload                         # push any runs the sheet doesn't have yet
```

`--no-logs` sends only the some and full context, so it shows what Claude concludes from the ticket text alone, and
how much the logs add on top. It needs no logs in the folder and skips raw. Those runs are labelled
`some (no logs)` and `full (no logs)` in the Context level column, so Summary's raw/some/full counts and the pass
bar never include them.

`run` uploads at the end unless you pass `--no-upload`. Every run is also kept locally:

- `results/runs.jsonl` and `results/runs.csv`: one row per run.
- `results/raw/<run id>.json`: the exact prompt sent and Claude's full reply.

## What a sandbox is

Each level runs `claude -p` in a new empty folder with `--safe-mode` (no CLAUDE.md, skills, plugins, hooks or MCP
servers), `--tools ""` (no tools at all), and the system prompt from `prompts/<prompt>.txt`. Claude Code still adds
a few lines of its own (your email, the folder, the OS), so "raw" is close to, but not exactly, zero context.

The model replies in fixed fields (`--json-schema`). The script then checks every quoted line against the raw log
itself, with no model involved.

## Settings (`config.json`)

| Key | Meaning |
|---|---|
| `spreadsheet_id` | The Google Sheet to write to |
| `worksheet` | The tab for results; created with its header row on the first upload |
| `model`, `effort` | Defaults for every run; `run --model sonnet --effort medium` overrides them for one run. Keep them the same across runs you want to compare |
| `prompt` | Which file in `prompts/` is the system prompt. Copy `p1.txt` to `p2.txt` to try a new wording |
| `timeout_s` | How long one sandbox may take |
| `max_log_chars` | Largest amount of log text one run may send |
| `upload_via` | `claude` (the default here): upload through Claude's Google Sheets connector. `api`: the Sheets API, see below |
| `sheet_tab_id` | The numeric ID of the Results tab, needed by the `claude` upload |

## Upload through Claude (`upload_via: claude`)

This needs no Google setup: it uses the Google Sheets connector already on your claude.ai account. Python still
decides every value. Claude only carries them, and it can't change them:

1. Python reads the Results header and the Run IDs already there, builds the new rows, and writes the exact tool
   call it wants, 5 rows per call.
2. It starts `claude -p` with your own settings and plugins off, no built-in tools, only the needed Sheets tool
   allowed, and every other tool refused without asking.
3. The prompt tells Claude to copy the call exactly and change nothing.
4. `sheet_guard.py` runs before every tool call (a Claude Code PreToolUse hook) and blocks any call that differs from
   the prepared one by a single character, or any other tool.
5. Afterwards Python reads the Run IDs back from the connector's own reply and checks every new row is there.

Each Claude session waits a few seconds before it gets its prompt, because the claude.ai connectors connect in
the background. An upload takes about a minute, because it runs one read, one write per 5 rows, then one
read to check.

## Google access through the API (`upload_via: api`)

The upload uses the Google Sheets API with your own Google sign-in. It needs an OAuth client file:

1. In the Google Cloud console, pick or create a project and enable the **Google Sheets API**.
2. Under **APIs & Services > Credentials**, create an **OAuth client ID** of type **Desktop app**.
3. Download it and save it here as `credentials.json`.

The first upload opens a browser for you to sign in, then keeps the token in `token.json`. Both files stay on this
Mac (`.gitignore` lists them). 8x8 may control who can create Google Cloud projects or OAuth clients: if you can't,
ask IT. Until then, run with `--no-upload` and push everything later with `upload`.

## The Results tab

The script only ever appends rows, matched to the tab's columns by header name. You can reorder columns or add
your own, and the four columns you fill in by hand (Right cause rank, Fix right?, Invented a problem?, Notes) are
never overwritten.

After a run, fill in **Right cause rank** on each new row: `1` if Claude's first cause is the real one, `2` or `3`
if a later cause is, `No` if none is. The other three hand-filled columns are for your own notes.

## The Summary tab

Summary reads Results and counts, per context level (raw, some, full), the problem-log runs, how many had the right
cause ranked first, answers that quoted a line not in the log, and healthy-log runs where Claude still reported a
problem. The pass bar is 8 of 10 right with no invented quotes and no invented problems; the thresholds are inputs
at the top of Summary. Two checks below the pass bar show runs that failed (timeout or no answer) and runs whose
ticket is not on the Scenarios tab, and two tables break the runs down by cause tested.

Summary knows a run is a healthy log by its ticket: on **Scenarios**, the "Healthy phone" row's **Jira ticket**
cell must hold the folder name of the healthy test, in capitals (a folder `tests/healthy-vvx/` is the ticket
`HEALTHY-VVX`). Any other test also needs a Scenarios row with its ticket, or it shows up in the "not on Scenarios"
check and is missing from the per-cause tables. Summary's formulas use the Results columns in the order the script
creates them, so reorder columns on Results only if you also update Summary. New columns go at the end
(Log files and Log chars, AH and AI, were added that way), so the letters Summary uses never move.
