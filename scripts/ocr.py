#!/usr/bin/env python3
"""Étape 1 : texte positionné de chaque page -> <work>/ocr/page-NNN.hocr (+ page-NNN.italic.json).

Deux sources, même format de sortie (hOCR : mots + boîtes englobantes + taille des lignes) :
  * source "ocr"       : rendu de la page (pypdfium2, 300 dpi, niveaux de gris) puis Tesseract
                         (modèle tessdata_best téléchargé une fois dans le cache utilisateur) ;
  * source "textlayer" : couche texte du PDF numérique (pdfplumber), sans OCR.
Italique : mesure de l'inclinaison des traits de chaque mot sur l'image (source ocr) ou nom de la
police (source textlayer).

Usage : ocr.py --work DOSSIER [--jobs N] [--first P] [--last P] [--force]
Les pages déjà traitées sont conservées (cache) sauf avec --force.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

import numpy as np
import pypdfium2 as pdfium

from common import Ctx, Progress, ctx_from_args, ensure_tessdata, find_tesseract, parse_pages, parser

BB = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")

# --------------------------------------------------------------------------- italique (image)
SHEARS = np.arange(-0.10, 0.41, 0.025)
I0 = int(np.argmin(np.abs(SHEARS)))


def shear_score(crop: np.ndarray):
    """Renvoie (cisaillement optimal, gain) : on « redresse » le mot avec différents cisaillements
    et on garde celui qui rend les fûts des lettres les plus verticaux (projection la plus piquée)."""
    if crop.size == 0:
        return None
    ink = (crop < crop.mean() - 0.5 * crop.std()).astype(np.float32)
    h, w = ink.shape
    if ink.sum() < 30 or h < 15:
        return None
    ys, xs = np.nonzero(ink)                         # seuls les pixels d'encre comptent dans la projection
    pad = int(0.45 * h) + 2
    scores = []
    for s in SHEARS:
        cols = xs - np.round(s * (h - 1 - ys)).astype(int) + pad
        proj = np.bincount(cols, minlength=w + 2 * pad).astype(np.float32)
        scores.append(float((proj ** 2).sum()))
    scores = np.array(scores)
    k = int(scores.argmax())
    return float(SHEARS[k]), float(scores[k] / max(scores[I0], 1e-6))


# Fenêtre d'inclinaison par défaut, recalée sur le livre par italiques.py. Hors fenêtre, l'inclinaison ne vient pas
# d'une italique mais des diagonales des lettres (« s’y », « Ayez » : 0,375-0,40 ; italique réelle : 0,225-0,30).
SHEAR_LO, SHEAR_HI, GAIN_MIN = 0.18, 0.34, 1.12


def word_scores(gray: np.ndarray, hocr: str) -> dict[str, tuple[float, float]]:
    """Inclinaison et gain de chaque mot d'au moins 2 caractères : {id du mot: (cisaillement, gain)}."""
    from lxml import etree
    tree = etree.fromstring(hocr.encode("utf-8"), etree.HTMLParser())
    out = {}
    for w in tree.iter("span"):
        if w.get("class") != "ocrx_word":
            continue
        txt = "".join(w.itertext()).strip()
        if len(re.sub(r"\W", "", txt)) < 2:
            continue
        l, t, r, b = map(int, BB.search(w.get("title")).groups())
        res = shear_score(gray[t:b, l:r])
        if res:
            out[w.get("id")] = res
    return out


# --------------------------------------------------------------------------- source OCR
def hocr_complete(f) -> bool:
    """Tesseract écrit l'en-tête dès son lancement : s'il est interrompu, le fichier existe mais sans page ni pied."""
    if not f.exists():
        return False
    t = f.read_text("utf-8", errors="replace")
    return "class='ocr_page'" in t and "</html>" in t


def ocr_pages(ctx: Ctx, pages: list[int], jobs: int, force: bool, prog: Progress):
    exe = find_tesseract()
    if not exe:
        sys.exit("Tesseract introuvable. Installez-le :\n"
                 "  Windows : winget install UB-Mannheim.TesseractOCR\n"
                 "  macOS   : brew install tesseract\n"
                 "  Linux   : sudo apt install tesseract-ocr\n"
                 "ou indiquez son chemin dans la variable TESSERACT_CMD.")
    lang = ctx.cfg["ocr_lang"]
    tessdir = ensure_tessdata(lang)
    dpi, psm = int(ctx.cfg["dpi"]), int(ctx.cfg["psm"])
    doc = pdfium.PdfDocument(str(ctx.pdf))
    lock = threading.Lock()
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)          # Windows uniquement
    done = [0]

    def one(p: int):
        base = ctx.ocr / f"page-{p:03d}"
        hocr_f = base.with_suffix(".hocr")
        if hocr_complete(hocr_f) and not force:
            return
        base.with_suffix(".lost.json").unlink(missing_ok=True)      # calculé sur l'ancien hOCR
        with lock:                                 # pdfium n'est pas thread-safe
            img = doc[p - 1].render(scale=dpi / 72).to_pil().convert("L")
        png = base.with_suffix(".png")
        img.save(png, dpi=(dpi, dpi), compress_level=1)   # fichier temporaire : pas la peine de bien compresser
        # Tesseract (Windows) ne sait pas lire/écrire un chemin accentué : on le lance dans le dossier
        # ocr/ avec des noms de fichiers relatifs (ASCII).
        cmd = [exe, png.name, base.name, "-l", lang, "--psm", str(psm), "--dpi", str(dpi), "--tessdata-dir", str(tessdir),
               "-c", "tessedit_create_hocr=1", "-c", "tessedit_create_txt=0"]
        r = subprocess.run(cmd, capture_output=True, env=env, creationflags=flags, cwd=ctx.ocr)
        if r.returncode != 0 or not hocr_f.exists():
            print(f"[page {p}] échec Tesseract : {r.stderr.decode(errors='replace')[:300]}")
            return
        if ctx.cfg["italics"]:
            scores = word_scores(np.asarray(img, np.float32), hocr_f.read_text("utf-8"))
            base.with_suffix(".scores.json").write_text(json.dumps(scores), "utf-8")   # repris par italiques.py
            ids = [i for i, (s, g) in scores.items() if SHEAR_LO <= s < SHEAR_HI and g >= GAIN_MIN]
            base.with_suffix(".italic.json").write_text(json.dumps(ids), "utf-8")
        png.unlink(missing_ok=True)

    def counted(p: int):
        one(p)
        with lock:
            done[0] += 1
            if done[0] % 10 == 0:
                print(f"  {done[0]}/{len(pages)} pages")
            prog.count(done[0], len(pages), "pages (OCR)")

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(counted, pages))
    bad = [p for p in pages if not hocr_complete(ctx.ocr / f"page-{p:03d}.hocr")]
    if bad:
        sys.exit(f"OCR incomplet pour {len(bad)} page(s) : {bad}. Relancer ocr.py : seules ces pages seront refaites.")


# --------------------------------------------------------------------------- source couche texte
ITALIC_FONT = re.compile(r"(italic|oblique|-it\b|\bit$|kursiv|corsivo)", re.I)


def textlayer_pages(ctx: Ctx, pages: list[int], force: bool, prog: Progress):
    import logging
    import pdfplumber
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    k = int(ctx.cfg["dpi"]) / 72.0
    with pdfplumber.open(str(ctx.pdf)) as pdf:
        for k_, p in enumerate(pages, 1):
            prog.count(k_, len(pages), "pages (couche texte)")
            base = ctx.ocr / f"page-{p:03d}"
            if base.with_suffix(".hocr").exists() and not force:
                continue
            page = pdf.pages[p - 1]
            words = page.extract_words(extra_attrs=["fontname", "size"], use_text_flow=False,
                                       keep_blank_chars=False, x_tolerance=1.5)
            W, H = int(page.width * k), int(page.height * k)
            page.close()                                 # libère le cache de la page (sinon tout le livre en mémoire)
            words.sort(key=lambda w: (round(w["top"]), w["x0"]))
            lines: list[list[dict]] = []
            for w in words:
                if lines and abs(w["top"] - lines[-1][0]["top"]) <= 0.45 * w["size"]:
                    lines[-1].append(w)
                else:
                    lines.append([w])
            body, ital, wid = [], [], 0
            for li, ln in enumerate(lines, 1):
                ln.sort(key=lambda w: w["x0"])
                l = int(min(w["x0"] for w in ln) * k); t = int(min(w["top"] for w in ln) * k)
                r = int(max(w["x1"] for w in ln) * k); b = int(max(w["bottom"] for w in ln) * k)
                xs = max(w["size"] for w in ln) * k
                spans = []
                for w in ln:
                    wid += 1
                    i = f"word_1_{wid}"
                    if ITALIC_FONT.search(w.get("fontname", "")):
                        ital.append(i)
                    spans.append(f"<span class='ocrx_word' id='{i}' title='bbox {int(w['x0']*k)} "
                                 f"{int(w['top']*k)} {int(w['x1']*k)} {int(w['bottom']*k)}; x_wconf 99'>"
                                 f"{escape(w['text'])}</span>")
                body.append(f"<span class='ocr_line' id='line_1_{li}' title=\"bbox {l} {t} {r} {b}; "
                            f"x_size {xs:.1f}\">{' '.join(spans)}</span>")
            html = ("<?xml version='1.0' encoding='UTF-8'?>\n<html xmlns='http://www.w3.org/1999/xhtml'>"
                    f"<head><meta charset='utf-8'/></head><body><div class='ocr_page' id='page_1' "
                    f"title='bbox 0 0 {W} {H}'>\n" + "\n".join(body) + "\n</div></body></html>")
            base.with_suffix(".hocr").write_text(html, "utf-8")
            if ctx.cfg["italics"]:
                base.with_suffix(".italic.json").write_text(json.dumps(ital), "utf-8")


def main():
    ap = parser(__doc__)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--first", type=int, default=None)
    ap.add_argument("--last", type=int, default=None)
    ap.add_argument("--pages", default=None, help="liste explicite, ex. 1,13,20-24")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    ctx = ctx_from_args(a)
    n = ctx.page_count()
    if a.pages:
        pages = parse_pages(a.pages)
    else:
        pages = list(range(a.first or 1, (a.last or n) + 1))
    src = ctx.cfg["source"]
    prog = Progress(ctx.work, "ocr")
    cached = sum(hocr_complete(ctx.ocr / f"page-{p:03d}.hocr") for p in pages) if not a.force else 0
    prog.log(f"source « {src} » : {len(pages)} pages, dont {cached} déjà en cache")
    print(f"Source « {src} » : {len(pages)} pages -> {ctx.ocr}")
    if src == "textlayer":
        textlayer_pages(ctx, pages, a.force, prog)
    else:
        ocr_pages(ctx, pages, a.jobs, a.force, prog)
    print(f"Terminé : {len(list(ctx.ocr.glob('page-*.hocr')))} pages disponibles dans {ctx.ocr}")
    prog.done()


if __name__ == "__main__":
    main()
