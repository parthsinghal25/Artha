import csv
import io


def render_rows_csv(columns, rows) -> bytes:
    """Genuine structured export: header row, one row per locked entry, blanks for absent values."""
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return buf.getvalue().encode("utf-8")
