# llm-pattern-test

Sends a phone log to Claude at three levels of context, each in its own stripped-down Claude Code running at the
same time, and records the answer, time, tokens and cost of each one in the Google Sheet.

**Google Sheet:** [LLM pattern test runs](https://docs.google.com/spreadsheets/d/14XcekmtS6C3jRU8Kq6lSZXK50cyDGH7J0PXfO_chwLk/edit)

| Tab | What it holds |
|---|---|
| [Results](https://docs.google.com/spreadsheets/d/14XcekmtS6C3jRU8Kq6lSZXK50cyDGH7J0PXfO_chwLk/edit#gid=777001) | One row per run, appended by the script. You fill in four columns by hand |
| [Summary](https://docs.google.com/spreadsheets/d/14XcekmtS6C3jRU8Kq6lSZXK50cyDGH7J0PXfO_chwLk/edit#gid=1464110643) | Pass bar and per-level counts, computed from Results |
| [Scenarios](https://docs.google.com/spreadsheets/d/14XcekmtS6C3jRU8Kq6lSZXK50cyDGH7J0PXfO_chwLk/edit#gid=1496135137) | One row per cause tested, with the test's ticket |
| Runs, Phones | Not used by the script. It never writes to them |

Every example below uses the lab test `badprod_server`, which is already in `tests/`. It has the logs and a
`badprod_server-some.txt` file but no full file, so its levels are raw and some.

## Setup (once)

```bash
cd ~/Documents/llm-pattern-test
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The `claude` CLI must be on your PATH and signed in. With the default `upload_via: claude`, the Google Sheets
connector must also be connected on your claude.ai account (see "Upload through Claude" below).

## Add a test

Make a folder under `tests/` in lower case. Name it after the Jira ticket, such as `herd-7121`, or give it any
name for a lab test without a ticket, such as `badprod_server`. The sheet records the folder name in capitals as the
ticket (`BADPROD_SERVER`).

```
tests/badprod_server/
  app.log, boot.log, ...        the logs: plain files or the ticket's archives, as downloaded (required)
  badprod_server-some.txt       a little context: phone, firmware, reported symptom (optional)
  badprod_server-full.txt       the ticket history up to before the cause was found, never the resolution (optional)
```

Every file that isn't the some or full file counts as a log. The context files must be named exactly as above,
including `.txt`. A file named `badprod_server-some` is sent as a log, and the some level does not exist. The last
line of `files badprod_server` lists the levels the script found, for example `Levels: raw, some`.

Archives (`.zip`, `.tar`, `.tar.gz`, `.tgz`, `.gz`, also inside one another up to 3 deep) are opened in memory,
and only text files inside them are sent. Images, pcaps and other binaries are skipped, as is any file over 200 MB
unpacked. Run `files` first to see what will be sent and what was skipped.

Last, add a row on the sheet's **Scenarios** tab whose Jira ticket cell is the folder name in capitals, such as
`BADPROD_SERVER`. See "The Summary tab" below for why.

### Attaching the ticket's files

The context files hold text only. The attachments are the other files in the folder. Keep the originals in
`attachments/<test>/`. `attach` copies files from there into the test folder (creating it if needed), trimmed to
the lines that matter if the logs are too big. It finishes with the `files` listing and a "Fits" or "Too big" line
against `max_log_chars`.

```bash
.venv/bin/python run_test.py attach badprod_server attachments/badprod_server/app.log attachments/badprod_server/boot.log
.venv/bin/python run_test.py attach badprod_server attachments/badprod_server/*.log --grep '64167f0082e9' --as one-phone.log
```

`--grep` keeps the log entries matching a regular expression, `--between FROM TO` keeps those in a time window,
and `--as` merges all the files given into one attachment. The originals are never changed. To detach a file,
delete it from the folder.

- When you grep for one phone, use values that belong to it alone, such as its MAC address (`64167f0082e9` in
  `badprod_server`). A value every phone shares, like the site's public IP address, keeps the whole log.
- An entry starts at a line beginning with a `YYYY-MM-DD HH:MM` timestamp. A line without one, such as a stack
  trace, stays with the entry before it, and is kept on its own only if it matches `--grep`.
- `--between` compares those timestamps as text, so `01:10` means the whole minute. It only works on logs that
  carry dates, such as server logs:
  `attach <test> attachments/<test>/server.log --between "2026-02-13 01:10" "2026-02-13 01:20" --as window.log`.
  Phone logs like `badprod_server`'s count seconds since boot (`000016.505|...`), so `--between` keeps nothing
  there. Use `--grep` instead.
- `.gz` sources are read directly. `.zip` and `.tar.gz` can be attached whole but not trimmed or merged.

The raw level is the logs alone. Some and full put their context in front of the same logs, so every level gets
exactly the same logs. The expected answer stays in the sheet, never in this folder.

If the logs total more than `max_log_chars` (800,000 characters, roughly 200,000 tokens), `run` stops before
calling Claude and lists the files, so you can take out the ones you don't need.

## Run

`files`, `show` and `attach` are free. `run` starts one paid Claude session per level and rep.

```bash
.venv/bin/python run_test.py files badprod_server                 # which logs will be sent
.venv/bin/python run_test.py files badprod_server --level some    # the same, plus the context file that level adds
.venv/bin/python run_test.py show badprod_server --level some     # the exact prompt Claude will receive
.venv/bin/python run_test.py run badprod_server                   # one sandbox per level (raw, some), all at once
.venv/bin/python run_test.py run badprod_server goodprov_server --reps 3   # several tests, each level 3 times
.venv/bin/python run_test.py run badprod_server --levels some     # only these levels, e.g. some or raw,some
.venv/bin/python run_test.py run badprod_server --no-logs         # context alone, no log at all
.venv/bin/python run_test.py run badprod_server --model claude-sonnet-5-5 --effort medium   # this run only
.venv/bin/python run_test.py run badprod_server --no-upload       # keep the results on this Mac
.venv/bin/python run_test.py upload                               # push any runs the sheet doesn't have yet
```

`run` checks every test folder before it calls Claude, and its first line shows the model, effort and prompt it
will use. Each level runs in its own sandbox at the same time. Results are saved after every batch, so a crash
later loses nothing, then uploaded at the end unless you pass `--no-upload`.

`--model` takes a model ID: `claude-opus-5-5` (the default in `config.json`), `claude-sonnet-5-5` or
`claude-haiku-5-5`. `--effort` takes `low`, `medium`, `high`, `xhigh` or `max`. Both apply to that run only.
Aliases such as `sonnet` also work, but the sheet then records `sonnet`, which points at a different model when a
newer Sonnet ships. Use the full ID for runs you will compare.

`--no-logs` sends only the some and full context, so it shows what Claude concludes from the ticket text alone, and
how much the logs add on top. It needs no logs in the folder and skips raw. `show --no-logs` prints that prompt.
Those runs are labelled `some (no logs)` and `full (no logs)` in the Context level column, so Summary's
raw/some/full counts and the pass bar never include them.

### Letting Claude read the service's code (`--repo`)

```bash
.venv/bin/python run_test.py run badprod_server --repo ~/code/<service>
```

`--repo` takes a local clone of the repository. The sandbox then gets three read-only tools, Read, Grep and Glob,
with access to that folder only. It still has no shell, no editing tools and no network tools. The system prompt
tells Claude the code is there, and the answer gains a `code_references` field for the code it relied on, written
as path:line. `quoted_lines` stays log lines only, so the quote check still works.

These runs are labelled `raw (repo)`, `some (repo)` and `full (repo)` in the Context level column, so Summary's
counts never include them. The Repo column holds the folder name and commit, such as `my-service@1a2b3c4` (the
commit only when the folder is the top of its own git repository). The Code references column lists what Claude
cited. Check out the same commit for runs you want to compare. Each file Claude opens adds a turn, so expect higher
cost and time than a plain run.

### Results kept on this Mac

- `results/runs.jsonl` and `results/runs.csv`: one row per run.
- `results/raw/<run id>.json`: the exact prompt sent and Claude's full reply.

A run ID looks like `20261124-163514-badprod_server-some-r1`, with `-nologs` or `-repo` added for those runs.
`results/` is in `.gitignore`.

## What a sandbox is

Each level runs `claude -p` in a new empty temporary folder with `--safe-mode` (no CLAUDE.md, skills, plugins,
hooks or MCP servers), `--tools ""` (no tools at all, unless `--repo` is given), no saved session, and the system
prompt from `prompts/<prompt>.txt`. `CLAUDE*` environment variables are dropped, so a run started from inside Claude
Code behaves like one started from a terminal. Claude Code still adds a few lines of its own (your email, the
folder, the OS), so "raw" is close to, but not exactly, zero context.

The model replies in fixed fields (`--json-schema`): `problem_found`, up to three `causes` with a confidence each,
`quoted_lines`, `fix`, and `code_references` on `--repo` runs. The script then checks every quoted line against the
logs itself, with no model involved, and lists any it can't find in "Quotes not in log".

A run that takes longer than `timeout_s` is stopped and recorded as `timeout after 600 s`. A run with no usable
answer is recorded as `error: ...` with the path of its raw file.

## Settings (`config.json`)

| Key | Meaning |
|---|---|
| `spreadsheet_id` | The Google Sheet to write to |
| `worksheet` | The tab for results (`Results`). The `api` upload creates it with its header row if it's missing |
| `sheet_tab_id` | The numeric ID of the Results tab, needed by the `claude` upload |
| `upload_via` | `claude` (the default here): upload through Claude's Google Sheets connector. `api`: the Sheets API, see below |
| `model`, `effort` | Defaults for every run. `run --model claude-sonnet-5-5 --effort medium` overrides them for one run. Keep them the same across runs you want to compare |
| `prompt` | Which file in `prompts/` is the system prompt. Copy `p1.txt` to `p2.txt` and set `"prompt": "p2"` to try a new wording |
| `timeout_s` | How long one sandbox may take, in seconds |
| `max_log_chars` | Largest amount of log text one run may send |

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
the background. If the connector isn't ready it tries again after 12 and then 24 seconds. An upload takes about a
minute, because it runs one read, one write per 5 rows, then one read to check. If the connector never connects,
check it in claude.ai under Settings > Connectors and run `upload` again.

## Google access through the API (`upload_via: api`)

The upload uses the Google Sheets API with your own Google sign-in. It needs an OAuth client file:

1. In the Google Cloud console, pick or create a project and enable the **Google Sheets API**.
2. Under **APIs & Services > Credentials**, create an **OAuth client ID** of type **Desktop app**.
3. Download it and save it here as `credentials.json`.

The first upload opens a browser for you to sign in, then keeps the token in `token.json`. Both files stay on this
Mac (`.gitignore` lists them). 8x8 may control who can create Google Cloud projects or OAuth clients: if you can't,
ask IT. Until then, run with `--no-upload` and push everything later with `upload`.

## The Results tab

The script only ever appends rows, and it matches each value to a column by header name. Text is written as text,
so a log line starting with `=` never becomes a formula. The four columns you fill in by hand (Right cause rank,
Fix right?, Invented a problem?, Notes) are never overwritten.

Summary reads Results by column letter, so don't reorder the columns. Add your own at the end. Columns added after
Summary was built went at the end for the same reason: Log files and Log chars (AH and AI), then Repo and Code
references (AJ and AK).

After a run, fill in **Right cause rank** on each new row: `1` if Claude's first cause is the real one, `2` or `3`
if a later cause is, `No` if none is. The other three hand-filled columns are for your own judgement and notes.

## The Summary tab

Summary reads Results and counts, per context level (raw, some, full), the problem-log runs, how many had the right
cause ranked first, answers that quoted a line not in the log, and healthy-log runs where Claude still reported a
problem. The pass bar is 8 of 10 right with no invented quotes and no invented problems; the thresholds are inputs
at the top of Summary. Two checks below the pass bar show runs that failed (timeout or no answer) and runs whose
ticket is not on the Scenarios tab, and two tables break the runs down by cause tested.

Summary knows a run is a healthy log by its ticket: on **Scenarios**, the "Healthy phone" row's **Jira ticket**
cell must hold the folder name of the healthy test, in capitals (a folder `tests/goodprov_server/` is the ticket
`GOODPROV_SERVER`). Any other test, such as `BADPROD_SERVER`, also needs a Scenarios row with its ticket, or it
shows up in the "not on Scenarios" check and is missing from the per-cause tables.
