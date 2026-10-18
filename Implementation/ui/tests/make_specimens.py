#!/usr/bin/env python3
"""Generate neutral file-format specimens for viewer tests (stdlib only).

These are protocol/format specimens, not complaint documents: no member,
case narrative or finding content. They exist only under tests/.
"""
import struct
import zipfile
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / "specimens"
OUT.mkdir(exist_ok=True)


def pdf() -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 6 0 R >> >> >>",
        None,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 7 0 R /Resources << /Font << /F1 6 0 R >> >> >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        None,
    ]
    def stream(text):
        body = f"BT /F1 14 Tf 20 120 Td ({text}) Tj ET".encode()
        return b"<< /Length %d >>\nstream\n" % len(body) + body + b"\nendstream"
    objs[3] = stream("Specimen page one")
    objs[6] = stream("Specimen page two")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def png(w=64, h=40) -> bytes:
    raw = b""
    for y in range(h):
        raw += b"\x00" + b"".join(bytes((40 + x * 3, 90 + y * 3, 170)) for x in range(w))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


CSV = 'reference,amount_minor,note\n00123,1250,"line one\nline two"\n00456,-300,plain\n00789,0,"quoted, comma"\n'

XLSX_FILES = {
    "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>',
    "_rels/.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    "xl/workbook.xml": '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Ledger" sheetId="1" r:id="rId1"/><sheet name="Notes" sheetId="2" r:id="rId2"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/></Relationships>',
    "xl/sharedStrings.xml": '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>reference</t></si><si><t>amount_minor</t></si><si><t>00123</t></si><si><t>specimen note</t></si></sst>',
    "xl/worksheets/sheet1.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>1250</v></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>total</t></is></c><c r="B3"><f>SUM(B2:B2)</f><v>1250</v></c></row></sheetData></worksheet>',
    "xl/worksheets/sheet2.xml": '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>3</v></c></row></sheetData></worksheet>',
}

PREVIEW = ('<p id="specimen-body">Specimen sanitised preview body.</p>'
           '<img src="https://example.invalid/tracker.png" alt="remote">'
           '<script>parent.__previewScriptRan = true;</script>'
           '<form action="https://example.invalid/"><input name="x"></form>')

TEXT = "Specimen plain text line one.\nSecond line with the cited words here.\n"


def main():
    (OUT / "specimen.pdf").write_bytes(pdf())
    (OUT / "specimen.png").write_bytes(png())
    (OUT / "specimen.csv").write_bytes(CSV.encode("utf-8"))
    (OUT / "preview.html").write_text(PREVIEW, encoding="utf-8")
    (OUT / "specimen.txt").write_bytes(TEXT.encode("utf-8"))
    with zipfile.ZipFile(OUT / "specimen.xlsx", "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in XLSX_FILES.items():
            z.writestr(name, body)
    print("specimens written to", OUT)


if __name__ == "__main__":
    main()
