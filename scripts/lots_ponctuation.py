#!/usr/bin/env python3
"""Prépare le contrôle sur image des points suspects : un « . » devant un nom propre est souvent une virgule mal lue.

La chaîne corrige seule le point devant une minuscule. Devant une majuscule, seule l'image tranche (« Eh. Marie ? »).
Noms propres : mots à majuscule rencontrés au moins 3 fois en milieu de phrase. Sortie dans build/relecture :
ponctuation-N.png (planches, 28 cas numérotés, le mot à juger souligné en rouge) et ponctuation-in-N.tsv.

Usage : lots_ponctuation.py --work DOSSIER
"""
from __future__ import annotations

import re
from collections import Counter

import pypdfium2 as pdfium
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

from common import ctx_from_args, parser

BB = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
CAP = re.compile(r"^[A-ZÀ-Ý][a-zà-ÿôûî]{2,}")
ROWS, COLS, CELL_W, CELL_H = 14, 2, 760, 110


def page_words(f):
    tree = etree.parse(str(f), etree.HTMLParser(encoding="utf-8"))
    out = []
    for w in tree.iter("span"):
        if w.get("class") == "ocrx_word":
            t = "".join(w.itertext()).strip()
            if t:
                out.append((t, tuple(map(int, BB.search(w.get("title")).groups()))))
    return out


def main():
    a = parser(__doc__).parse_args()
    ctx = ctx_from_args(a)
    pages = {int(f.name[5:8]): page_words(f) for f in sorted(ctx.ocr.glob("page-*.hocr"))}
    seen = Counter()
    for ws in pages.values():
        for (prev, _), (cur, _) in zip(ws, ws[1:]):
            if re.search(r"[a-zà-ÿ,]$", prev) and CAP.match(cur):
                seen[cur.strip(".,;:!?«»")] += 1
    names = {w for w, k in seen.items() if k >= 3}
    p = ctx.cfg["pages"]
    cases = []
    for n, ws in sorted(pages.items()):
        if not p["body_first"] <= n <= p["body_last"]:
            continue
        for (cur, box), (nxt, _) in zip(ws, ws[1:]):
            if re.fullmatch(r"[^\W\d_’'-]+(?:[’'-][^\W\d_]+)*\.", cur) and nxt.strip(".,;:!?«»") in names:
                cases.append((n, cur, nxt, box))
    out = ctx.build / "relecture"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("ponctuation-*"):
        old.unlink()
    doc = pdfium.PdfDocument(str(ctx.pdf))
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    size = ROWS * COLS
    last_page, g = None, None                      # une seule image de page à la fois : les cas sont triés par page
    for s, start in enumerate(range(0, len(cases), size), 1):
        chunk = cases[start:start + size]
        sheet = Image.new("RGB", (CELL_W * COLS, CELL_H * ROWS), "white")
        d = ImageDraw.Draw(sheet)
        rows = []
        for i, (n, cur, nxt, (l, t, r, b)) in enumerate(chunk):
            if n != last_page:
                g, last_page = doc[n - 1].render(scale=ctx.cfg["dpi"] / 72).to_pil().convert("RGB"), n
            x0, y0 = max(0, l - 150), max(0, t - 14)
            crop = g.crop((x0, y0, l + 500, b + 24))
            ImageDraw.Draw(crop).line((l - x0, b + 14 - y0, r - x0 + 12, b + 14 - y0), fill="red", width=2)
            x, y = (i // ROWS) * CELL_W, (i % ROWS) * CELL_H
            d.text((x + 4, y + 2), str(start + i + 1), fill="red", font=font)
            sheet.paste(crop, (x + 60, y + 6))
            rows.append(f"{start + i + 1}\t{n}\t{cur}\t{nxt}")
        sheet.save(out / f"ponctuation-{s}.png")
        (out / f"ponctuation-in-{s}.tsv").write_text("\n".join(rows) + "\n", "utf-8")
    print(f"{len(cases)} cas, {(len(cases) + size - 1) // size} planche(s) -> {out}")


if __name__ == "__main__":
    main()
