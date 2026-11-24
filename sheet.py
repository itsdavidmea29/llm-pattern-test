"""Writes run results into the Google Sheet with the Sheets API. Plain code: it copies values, nothing decides them."""
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]  # this one spreadsheet API only, no Drive access


def connect(cfg: dict, here: Path):
    import gspread  # imported here so 'show' and 'run --no-upload' work before Google access is set up

    credentials = here / "credentials.json"
    if not credentials.is_file():
        raise SystemExit(f"Missing {credentials}: see 'Google access' in README.md. "
                         "Your runs are saved in results/; push them later with: python run_test.py upload")
    # First call opens a browser to sign in, then keeps the token in token.json for the next runs.
    client = gspread.oauth(scopes=SCOPES, credentials_filename=credentials,
                           authorized_user_filename=here / "token.json")
    return client.open_by_key(cfg["spreadsheet_id"])


def get_worksheet(spreadsheet, title: str, headers: list):
    """Returns the results tab, creating it with the header row the first time."""
    import gspread

    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(headers))
    if not ws.row_values(1):
        ws.update(values=[headers], range_name="A1")
        ws.freeze(rows=1)
    return ws


def rows_to_add(rows: list, sheet_header: list, existing_ids: set) -> list:
    """Lays out the rows the sheet doesn't have yet in the sheet's own column order.

    Matching by header name means you can reorder or add columns in the sheet: unknown columns stay blank, and
    the columns you fill in by hand are never overwritten because the script only ever appends new rows.
    """
    if "Run ID" not in sheet_header:
        raise SystemExit("The results tab has no 'Run ID' column in row 1, so the script can't tell which runs it has.")
    new = [r for r in rows if r["Run ID"] not in existing_ids]
    return [["" if r.get(h) is None else r.get(h, "") for h in sheet_header] for r in new]


def upload(rows: list, headers: list, cfg: dict, here: Path) -> int:
    from gspread.utils import ValueInputOption

    ws = get_worksheet(connect(cfg, here), cfg["worksheet"], headers)
    sheet_header = ws.row_values(1)
    existing_ids = set(ws.col_values(sheet_header.index("Run ID") + 1)[1:]) if "Run ID" in sheet_header else set()
    values = rows_to_add(rows, sheet_header, existing_ids)
    if values:
        # RAW stores text as text: a log line starting with "=" stays a quote and never runs as a formula.
        ws.append_rows(values, value_input_option=ValueInputOption.raw, table_range="A1")
    return len(values)
