"""Portal-screenshot style JPEG for the upload receipt. Returns bytes plus the text actually drawn,
so checks can verify locked values without OCR."""
import io
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from .textfmt import MONTHS, clock12
from .tz import melbourne_abbr

FONT_CANDIDATES = {
    "regular": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def _font(kind, size):
    for p in FONT_CANDIDATES[kind]:
        try:
            return ImageFont.truetype(p, size), p
        except OSError:
            continue
    return ImageFont.load_default(), "pillow-default"


def render_upload_receipt(asset: dict, rows: list, fields: dict):
    W, H = 1080, 1560
    img = Image.new("RGB", (W, H), "#f3f5f7")
    d = ImageDraw.Draw(img)
    drawn = []
    fonts_used = set()

    def text(xy, s, kind="regular", size=34, fill="#1f2933", anchor="la"):
        f, p = _font(kind, size)
        fonts_used.add(p)
        d.text(xy, s, font=f, fill=fill, anchor=anchor)
        drawn.append(s)

    cap = datetime.fromisoformat(asset["created_at"])
    status = f"{clock12(cap)}"
    # phone status bar carries the capture moment
    d.rectangle([0, 0, W, 96], fill="#ffffff")
    text((48, 48), f"{status}  {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][cap.weekday()]} {cap.day} {MONTHS[cap.month-1][:3]}",
         "bold", 34, anchor="lm")
    text((W - 48, 48), "5G  84%", "regular", 30, "#5b6770", anchor="rm")
    # address bar
    d.rounded_rectangle([40, 112, W - 40, 184], radius=30, fill="#e4e8ec")
    text((W // 2, 148), "secure.examplesuper.example", "regular", 32, "#3d4852", anchor="mm")
    # page header
    d.rectangle([0, 210, W, 330], fill="#17324d")
    text((56, 270), "Example Super", "bold", 48, "#ffffff", anchor="lm")
    text((W - 56, 270), "Secure upload", "regular", 34, "#c9d6e2", anchor="rm")
    # receipt card
    d.rounded_rectangle([40, 370, W - 40, 1440], radius=24, fill="#ffffff", outline="#d3dae0", width=3)
    text((88, 440), "Upload receipt", "bold", 52, anchor="lm")
    d.line([88, 490, W - 88, 490], fill="#d3dae0", width=3)
    up = datetime.fromisoformat(fields["uploaded_at"])
    up_txt = f"{up.day} {MONTHS[up.month-1][:3]} {up.year}, {clock12(up)} {melbourne_abbr(fields['uploaded_at'])}"
    y = 540
    for lab, val in [("Upload reference", fields["upload_reference"]), ("Uploaded", up_txt),
                     ("Member account number", fields["member_account_number"])]:
        text((88, y), lab, "regular", 30, "#5b6770")
        text((88, y + 40), val, "bold", 38)
        y += 128
    d.line([88, y + 8, W - 88, y + 8], fill="#d3dae0", width=3)
    y += 56
    text((88, y), "Submitted files", "bold", 40, anchor="lm")
    y += 56
    d.rectangle([88, y, W - 88, y + 64], fill="#eef2f5")
    text((108, y + 32), "File", "bold", 30, "#3d4852", anchor="lm")
    text((W - 108, y + 32), "Status", "bold", 30, "#3d4852", anchor="rm")
    y += 64
    for r in rows:
        d.line([88, y, W - 88, y], fill="#d3dae0", width=2)
        text((108, y + 44), r["file"], "regular", 34, anchor="lm")
        text((W - 108, y + 44), r["receipt_state"].capitalize(), "bold", 34, "#1d6b44", anchor="rm")
        y += 88
    d.line([88, y, W - 88, y], fill="#d3dae0", width=2)
    # footer
    text((W // 2, 1500), "Example Super secure upload portal", "regular", 28, "#7b8791", anchor="mm")

    exif = Image.Exif()
    stamp = cap.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0132] = stamp
    exif_ifd = exif.get_ifd(0x8769)
    exif_ifd[0x9003] = stamp
    exif_ifd[0x9011] = cap.strftime("%z")[:3] + ":" + cap.strftime("%z")[3:]
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, optimize=False, exif=exif, subsampling=0)
    return buf.getvalue(), {"drawn_text": drawn, "fonts": sorted(fonts_used)}
