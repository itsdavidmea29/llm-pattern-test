# llm-pattern-test

This benchmark sends a ticket's logs to Claude three times, with no context, a little context, and the full ticket
history. It records each answer, its time, tokens and cost in the Google Sheet. README.md explains how it works.

Run every command from this folder. The examples use the ticket HERD-1234.

## Add a new test ticket

1. Make the ticket's folder. Use the ticket key in lower case.

   ```bash
   mkdir tests/herd-1234
   ```

2. Write the context in two text files in that folder. Both are optional.

   - `herd-1234-some.txt` holds a little context, such as the phone model, the firmware and the reported symptom.
   - `herd-1234-full.txt` holds the ticket history up to the point before anyone found the cause.

   Never write the cause or the fix in these files. The expected answer goes in the sheet.

3. Attach the files from the ticket. The script copies them into the folder.

   ```bash
   .venv/bin/python run_test.py attach herd-1234 ~/Downloads/phone-logs.zip ~/Downloads/app.log
   ```

   The last line of the output says "Fits" or "Too big". A run can send at most 800,000 characters of logs.

4. If it says "Too big", delete the large file from `tests/herd-1234/` and attach it again with only the lines
   about one phone. `--grep` keeps the lines that match, and `--as` merges the files into one.

   ```bash
   .venv/bin/python run_test.py attach herd-1234 ~/Downloads/server.log* --grep '64167f7d91cb' --as one-phone.log
   ```

   Grep for values that belong to one phone, such as its MAC address. A value that every phone shares, like the
   site's public IP address, keeps the whole log.

5. On the Scenarios tab of the sheet, add a row with the ticket key `HERD-1234`. Without it, Summary leaves the
   ticket out of its per-cause tables.

## Run a test

```bash
.venv/bin/python run_test.py files herd-1234 --level some   # list the files one level will send (free)
.venv/bin/python run_test.py show herd-1234 --level some    # print the exact prompt for one level (free)
.venv/bin/python run_test.py run herd-1234                  # run all three levels at once (paid)
```

`run` starts one Claude session per level and adds a row per level to the Results tab. After it finishes, open
Results and fill in **Right cause rank** on each new row. Write `1` if Claude's first cause is the real one, `2` or
`3` if a later cause is, and `No` if none is.

Each level sends the same attachments. Only the text in front of them changes.

| Level | Text in front of the attachments |
|---|---|
| `raw` | none |
| `some` | `herd-1234-some.txt` |
| `full` | `herd-1234-full.txt` |

Options for `run`:

| Option | What it does |
|---|---|
| `--levels some` | Runs only the levels listed, for example `some` or `some,full` |
| `--model claude-sonnet-5-5` | Uses this model for this run only. Use the full model ID so the sheet records it exactly |
| `--effort medium` | Uses this effort for this run only: `low`, `medium`, `high`, `xhigh` or `max` |
| `--reps 3` | Runs each level 3 times |
| `--no-logs` | Sends the context text without any attachment, and skips `raw` |
| `--no-upload` | Keeps the results on this Mac. Send them later with `run_test.py upload` |

The default model, effort and system prompt are in `config.json`. A one-level run of HERD-7121 on Opus 5.5 at high
effort took about 100 seconds and cost $0.71.

## Rules for Claude in this folder

- Run `run` only when the user asks for it, because every level of every rep is a paid Claude session. `attach`,
  `files` and `show` are free.
- Keep the model and effort the same across runs that will be compared.
- Never reorder the columns of the Results tab, because Summary reads them by column letter. Add new columns at the
  end. The script never writes to the Runs tab.
- The script never writes the four hand-filled Results columns: Right cause rank, Fix right?, Invented a problem?
  and Notes.
- Never print or copy `credentials.json` or `token.json`.
- Results are also saved here. `results/runs.jsonl` and `results/runs.csv` have one row per run, and
  `results/raw/<run id>.json` has the exact prompt and the full reply.
- To try a new system prompt, copy `prompts/p1.txt` to `prompts/p2.txt` and set `"prompt": "p2"` in `config.json`.
