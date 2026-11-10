#!/usr/bin/env python3
"""Retrouve les lignes imprimées que Tesseract n'a pas lues (répliques muettes « — … », lignes courtes, ligne entière).

Une ligne absente de l'OCR est invisible pour tous les contrôles sur le texte : on compare donc l'image à l'OCR.
Toute encre du bloc de texte couverte par aucune ligne OCR forme une zone, écrite dans WORK/ocr/page-NNN.lost.json :
  silence   tiret long suivi de trois points : réplique muette, reconnue à sa forme (Tesseract ne la lit jamais)
  texte     zone relue seule (Tesseract, une ligne) : réinjectée dans la page par extract_text.py
  illisible zone assez grande pour être du texte, mais sans mot lisible : signalée dans review.txt

Résultat mis en cache (--force pour refaire). Sans objet pour une source « textlayer ».
Usage : lignes_perdues.py --work DOSSIER [--pages 42,214] [--force]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import numpy as np
import pypdfium2 as pdfium
from lxml import etree

from common import Ctx, Progress, ctx_from_args, ensure_tessdata, find_tesseract, parse_pages, parser

BB = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
INK = 150               # niveau de gris sous lequel un pixel est de l'encre
BOTTOM = 0.95           # au-delà : folio
MIN_CONF = 70           # en dessous, la zone relue n'est pas réinjectée dans le texte


def runs(flags) -> list[tuple[int, int]]:
    """Plages [début, fin[ des valeurs vraies d'un vecteur."""
    idx = np.flatnonzero(np.diff(np.concatenate(([0], np.asarray(flags, np.int8), [0]))))
    return list(zip(idx[::2].tolist(), idx[1::2].tolist()))


def ocr_boxes(hocr) -> list[tuple[int, int, int, int]]:
    tree = etree.parse(str(hocr), etree.HTMLParser(encoding="utf-8"))
    boxes = []
    for ln in tree.iter("span"):
        if ln.get("class") != "ocr_line":
            continue
        if any(w.get("class") == "ocrx_word" and "".join(w.itertext()).strip() for w in ln.iter("span")):
            boxes.append(tuple(map(int, BB.search(ln.get("title")).groups())))
    return sorted(boxes, key=lambda b: b[1])


def is_silence(zone: np.ndarray, s: float) -> bool:
    """Tiret long à gauche, puis exactement trois points (cachés par la ligne suivante si elle est très proche)."""
    cols = runs(zone.any(0))
    if len(cols) == 1:
        a, b = cols[0]
        return 36 * s <= b - a <= 50 * s and zone.shape[0] <= 9 * s
    if len(cols) != 4:
        return False
    a, b = cols[0]
    dash_rows = np.flatnonzero(zone[:, a:b].any(1))
    dots = cols[1:]
    return (30 * s <= b - a <= 120 * s and dash_rows.size <= 9 * s
            and all(y - x <= 12 * s for x, y in dots) and dots[-1][1] - b <= 110 * s)


def zones(g: np.ndarray, boxes, s: float):
    """Zones d'encre hors lignes OCR dans le bloc de texte : (haut, bas, gauche, droite, masque)."""
    H = g.shape[0]
    m = g < INK
    for l, t, r, b in boxes:
        m[max(0, t - int(4 * s)):min(H, b + int(6 * s)), :] = False
    L = max(0, min(b[0] for b in boxes) - int(10 * s))
    R = max(b[2] for b in boxes) + int(10 * s)
    T = max(0, boxes[0][1] - int(80 * s))
    B = int(H * BOTTOM)
    z = np.zeros_like(m)
    z[T:B, L:R] = m[T:B, L:R]
    bands: list[list[int]] = []
    for a, b in runs(z.any(1)):
        if bands and a - bands[-1][1] <= 14 * s:      # le tiret et les points sont à des hauteurs différentes
            bands[-1][1] = b
        else:
            bands.append([a, b])
    for a, b in bands:
        xs = np.flatnonzero(z[a:b].any(0))
        yield a, b, int(xs[0]), int(xs[-1]) + 1, z[a:b, xs[0]:xs[-1] + 1]


def read_zone(ctx: Ctx, g: np.ndarray, top, bot, left, right, exe, tessdir) -> tuple[str, float]:
    """Relit une zone seule : (texte, confiance moyenne). Nom ASCII et cwd : Tesseract (Windows) refuse les chemins accentués."""
    from PIL import Image
    # uniquement l'encre de la zone : un bout de ligne voisine ou l'ombre de marge fausse la lecture
    crop = np.pad(g[top:bot, left:right], 40, constant_values=255)
    png = ctx.ocr / "zone-perdue.png"
    Image.fromarray(crop).save(png, dpi=(ctx.cfg["dpi"], ctx.cfg["dpi"]))
    r = subprocess.run([exe, png.name, "stdout", "-l", ctx.cfg["ocr_lang"], "--psm", "7", "--tessdata-dir", str(tessdir), "-c", "tessedit_create_tsv=1"],
                       capture_output=True, cwd=ctx.ocr, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       env=dict(os.environ, OMP_THREAD_LIMIT="1"))
    png.unlink(missing_ok=True)
    words = [c for c in (l.split("\t") for l in r.stdout.decode("utf-8", "replace").splitlines()[1:])
             if len(c) == 12 and c[11].strip() and float(c[10]) >= 0]
    text = " ".join(c[11].strip() for c in words)
    return text, (sum(float(c[10]) for c in words) / len(words) if words else 0.0)


def page_range(ctx: Ctx) -> list[int]:
    p = ctx.cfg["pages"]
    firsts = [p["body_first"]] + [x["first"] for x in p.get("sections_before_body", [])]
    lasts = [p["body_last"]] + [x["last"] for x in p.get("sections_after_body", [])]
    skip = set(p.get("skip", []))
    return [n for n in range(min(firsts), max(lasts) + 1) if n not in skip]


def main():
    ap = parser(__doc__)
    ap.add_argument("--pages", default=None)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ctx = ctx_from_args(a)
    prog = Progress(ctx.work, "lignes perdues")
    if ctx.cfg["source"] != "ocr":
        prog.done("sans objet : source couche texte")
        return
    exe = find_tesseract()
    if not exe:
        sys.exit("Tesseract introuvable.")
    tessdir = ensure_tessdata(ctx.cfg["ocr_lang"])
    s = ctx.cfg["dpi"] / 300
    doc = pdfium.PdfDocument(str(ctx.pdf))
    pages = parse_pages(a.pages) if a.pages else page_range(ctx)
    total = {"silence": 0, "texte": 0, "illisible": 0}
    for i, n in enumerate(pages, 1):
        prog.count(i, len(pages), "pages (lignes perdues)")
        hocr = ctx.ocr / f"page-{n:03d}.hocr"
        out = hocr.with_suffix(".lost.json")
        if not hocr.exists():
            continue
        if out.exists() and not a.force:
            for z in json.loads(out.read_text("utf-8")):
                total[z["kind"]] += 1
            continue
        boxes = ocr_boxes(hocr)
        found = []
        if len(boxes) >= 3:
            g = np.array(doc[n - 1].render(scale=ctx.cfg["dpi"] / 72).to_pil().convert("L"))
            for top, bot, l, r, mask in zones(g, boxes, s):
                entry = dict(l=l, t=top, r=r, b=bot)
                if is_silence(mask, s):
                    found.append(dict(entry, kind="silence", text="— …"))
                elif bot - top >= 14 * s and mask.sum() >= 200 * s * s:
                    text, conf = read_zone(ctx, g, top, bot, l, r, exe, tessdir)
                    kind = "texte" if conf >= MIN_CONF and re.search(r"[^\W\d_]{2}", text) else "illisible"
                    found.append(dict(entry, kind=kind, text=text))
        out.write_text(json.dumps(found, ensure_ascii=False), "utf-8")
        for z in found:
            total[z["kind"]] += 1
    prog.done(f"{total['silence']} réplique(s) muette(s), {total['texte']} ligne(s) relue(s), {total['illisible']} zone(s) illisible(s)")
    print(f"Lignes perdues : {total}")


if __name__ == "__main__":
    main()
