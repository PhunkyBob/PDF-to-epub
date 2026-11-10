#!/usr/bin/env python3
"""Étape 2 : italique recalé sur le livre, et mots douteux à vérifier sur l'image.

Une seule mesure d'inclinaison avec un seuil fixe se trompe dans les deux sens : les diagonales des lettres
(« s’y », « Ayez ») passent pour de l'italique, et les mots courts, en capitales ou en petit corps (« Wales »,
« Missouri », les libellés de notes) y échappent. Ce script :
  * lit l'inclinaison de chaque mot (<work>/ocr/page-NNN.scores.json, calculé ici si absent, sans refaire l'OCR) ;
  * cale la fenêtre d'inclinaison sur l'italique réelle du livre et réécrit page-NNN.italic.json (mots sûrs) ;
  * liste les mots douteux et les présente sur des planches, pour les sous-agents « italiques » :
    build/relecture/italiques-in-N.tsv + italiques-N.png (une ligne de page par mot, le mot souligné en rouge).
Les verdicts (<work>/italiques.tsv : page, id du mot, i ou r, mot) l'emportent sur la mesure à chaque extraction.

Sans effet pour une source « textlayer » (l'italique vient alors du nom de la police).
Usage : italiques.py --work DOSSIER [--taille 25]
        italiques.py --work DOSSIER --appliquer     (verse les prop-italiques*.tsv dans italiques.tsv)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

from common import Ctx, Progress, ctx_from_args, parser
from ocr import BB, GAIN_MIN, SHEAR_HI, SHEAR_LO, word_scores

WEAK_GAIN = 1.03            # en dessous, la mesure ne dit rien (capitales droites : « Wales », « Yes »)
SHORT = 4                   # mots courts : mesure bruitée
LOW_SHEAR = 0.12            # sous la fenêtre mais déjà une vraie italique pour certains mots : à vérifier sur l'image


def letters(w: str) -> int:
    return len(re.sub(r"\W", "", w))


def _score_page(job):
    pdf, hocr_f, dpi, p = job
    doc = pdfium.PdfDocument(pdf)
    gray = np.asarray(doc[p - 1].render(scale=dpi / 72).to_pil().convert("L"), np.float32)
    return p, word_scores(gray, Path(hocr_f).read_text("utf-8"))


def page_words(hocr_f: Path):
    """Mots de la page dans l'ordre de lecture : (id, texte, boîte du mot, boîte de la ligne)."""
    tree = etree.parse(str(hocr_f), etree.HTMLParser(encoding="utf-8"))
    page = next(e for e in tree.iter("div") if e.get("class") == "ocr_page")
    H = int(BB.search(page.get("title")).group(4))
    out = []
    for ln in tree.iter("span"):
        if ln.get("class") != "ocr_line":
            continue
        lb = tuple(map(int, BB.search(ln.get("title")).groups()))
        for w in ln.iter("span"):
            if w.get("class") == "ocrx_word":
                out.append((w.get("id"), "".join(w.itertext()).strip(), tuple(map(int, BB.search(w.get("title")).groups())), lb))
    return H, out


def calibrate(all_scores):
    """Inclinaison la plus fréquente parmi les mots nettement inclinés : c'est celle de l'italique du livre."""
    hist = Counter(round(s, 3) for s, g in all_scores if g >= 1.2 and SHEAR_LO - 0.06 <= s < SHEAR_HI + 0.02)
    if sum(hist.values()) < 20:
        return SHEAR_LO, SHEAR_HI
    mode = hist.most_common(1)[0][0]
    return max(SHEAR_LO - 0.03, mode - 0.075), min(SHEAR_HI, mode + 0.09)


def classify(words, scores, lo, hi, band):
    """Renvoie (mots sûrs, {id: raison du doute}) pour une page."""
    def sure_word(wid, txt):
        if wid not in scores:
            return False
        s, g = scores[wid]
        return lo <= s < hi and g >= GAIN_MIN and (letters(txt) >= SHORT or g >= 1.2)

    # Tous les mots comptent : une page sans en-tête a du vrai texte tout en haut. L'en-tête courant, lui, est retiré
    # à l'extraction ; on évite seulement de le soumettre à la vérification.
    sure = [sure_word(w[0], w[1]) for w in words]
    ids, doubts = set(), {}
    for i, (wid, txt, _, line) in enumerate(words):
        n = letters(txt)
        if n < 2 or not any(c.isalpha() for c in txt):       # folio, chiffre
            continue
        prev_on, next_on = i > 0 and sure[i - 1], i + 1 < len(words) and sure[i + 1]
        s, g = scores.get(wid, (0.0, 1.0))
        if sure[i]:
            ids.add(wid)
            if n <= SHORT and txt[:1].islower() and not (prev_on or next_on):
                doubts[wid] = "italique isolé"              # « de », « que » : faux positif possible
        elif prev_on and next_on:
            continue                                        # entre deux italiques : l'extraction le met en italique
        elif line[1] < band and txt.upper() == txt:
            continue                                        # en-tête courant probable
        elif (LOW_SHEAR <= s < hi or (s >= hi and txt[:1] == "J")) and g >= WEAK_GAIN:   # le J italique penche beaucoup
            doubts[wid] = "mesure faible"                   # « Missouri », « romance-seat », « Yukata », « mon » à 0,15
        elif (prev_on or next_on) and txt[:1].isupper() and n >= 3 and g < WEAK_GAIN:
            doubts[wid] = "voisin d'un italique"            # « Yes, all right », « Prince of Wales »
    return ids, doubts


def sheet(rows, doc, dpi, path: Path):
    """Planche : une ligne de page par mot douteux, le mot souligné en rouge, le numéro à gauche."""
    W, rh = 1400, 92
    img = Image.new("RGB", (W, rh * len(rows) + 8), "white")
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default()
    for k, (num, p, box, lb) in enumerate(rows):
        page = doc[p - 1].render(scale=dpi / 72).to_pil().convert("RGB")
        l, t, r, b = lb
        crop = page.crop((max(0, l - 10), max(0, t - 8), r + 10, b + 12))
        dr = ImageDraw.Draw(crop)
        y = box[3] - t + 10
        dr.line((box[0] - l + 10, y, box[2] - l + 10, y), fill=(220, 0, 0), width=3)
        scale = min((W - 70) / crop.width, (rh - 8) / crop.height, 1.0)
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)))
        img.paste(crop, (64, rh * k + 4))
        d.text((6, rh * k + 28), str(num), fill=(200, 0, 0), font=font)
        d.line((0, rh * (k + 1) + 2, W, rh * (k + 1) + 2), fill=(210, 210, 210))
    img.save(path)


def apply_verdicts(ctx: Ctx):
    rel = ctx.build / "relecture"
    over = ctx.work / "italiques.tsv"
    done = {}
    if over.exists():
        for row in over.read_text("utf-8").splitlines():
            f = row.split("\t")
            if len(f) >= 3:
                done[(f[0], f[1])] = row
    asked = {}                                       # numéro de rangée -> (page, id, mot) : la référence
    for f in rel.glob("italiques-in-*.tsv"):
        for row in f.read_text("utf-8").splitlines():
            c = row.split("\t")
            asked[c[0]] = (c[1], c[2], c[3])
    n = 0
    for f in sorted(rel.glob("prop-italiques*.tsv")):
        for row in f.read_text("utf-8").splitlines():
            c = [x.strip() for x in row.split("\t")]
            verdict = c[-1].lower()
            # les sous-agents décalent parfois leurs colonnes : on retrouve la rangée par son numéro et son mot
            hit = next((asked[x] for x in c[:-1] if x in asked and asked[x][2] in c), None)
            if hit and verdict in ("italique", "romain"):
                done[hit[:2]] = "\t".join((hit[0], hit[1], "i" if verdict == "italique" else "r", hit[2]))
                n += 1
    over.write_text("\n".join(done.values()) + "\n", "utf-8")
    print(f"{n} verdicts repris, {len(done)} lignes dans {over}")


def main():
    ap = parser(__doc__)
    ap.add_argument("--taille", type=int, default=25, help="mots par planche")
    ap.add_argument("--appliquer", action="store_true", help="verse les prop-italiques*.tsv dans italiques.tsv")
    ap.add_argument("--jobs", type=int, default=None)
    a = ap.parse_args()
    ctx = ctx_from_args(a)
    if a.appliquer:
        return apply_verdicts(ctx)
    if ctx.cfg["source"] != "ocr" or not ctx.cfg["italics"]:
        print("italiques.py : sans objet (source textlayer ou italique désactivé).")
        return
    prog = Progress(ctx.work, "italics")
    files = sorted(ctx.ocr.glob("page-*.hocr"))
    pages = [int(f.stem.split("-")[1]) for f in files]
    todo = [(str(ctx.pdf), str(f), int(ctx.cfg["dpi"]), p) for f, p in zip(files, pages)
            if not f.with_suffix(".scores.json").exists()]
    if todo:
        prog.log(f"mesure de l'inclinaison : {len(todo)} pages (l'OCR n'est pas refait)")
        with ProcessPoolExecutor(max_workers=a.jobs) as pool:
            for k, (p, res) in enumerate(pool.map(_score_page, todo, chunksize=4), 1):
                (ctx.ocr / f"page-{p:03d}.scores.json").write_text(json.dumps(res), "utf-8")
                prog.count(k, len(todo), "pages (inclinaison)")
    scores = {p: json.loads((ctx.ocr / f"page-{p:03d}.scores.json").read_text("utf-8")) for p in pages}
    lo, hi = calibrate([v for sc in scores.values() for v in sc.values()])
    over = set()
    over_f = ctx.work / "italiques.tsv"
    if over_f.exists():
        over = {tuple(r.split("\t")[:2]) for r in over_f.read_text("utf-8").splitlines() if r.count("\t") >= 2}
    pg = ctx.cfg["pages"]
    kept = set(range(pg.get("body_first") or 1, (pg.get("body_last") or max(pages)) + 1))
    for sec in pg.get("sections_before_body", []) + pg.get("sections_after_body", []):
        kept |= set(range(sec["first"], sec["last"] + 1))
    cand, total = [], 0
    for f, p in zip(files, pages):
        H, words = page_words(f)
        ids, doubts = classify(words, scores[p], lo, hi, ctx.cfg["header_band"] * H)
        f.with_suffix(".italic.json").write_text(json.dumps(sorted(ids)), "utf-8")
        total += len(ids)
        by_id = {w[0]: w for w in words}
        cand += [(p, wid, by_id[wid][1], why, by_id[wid][2], by_id[wid][3]) for wid, why in doubts.items()
                 if (str(p), wid) not in over and p in kept]
    rel = ctx.build / "relecture"
    rel.mkdir(parents=True, exist_ok=True)
    for old in list(rel.glob("italiques-in-*.tsv")) + list(rel.glob("italiques-*.png")):
        old.unlink()
    doc = pdfium.PdfDocument(str(ctx.pdf))
    dpi = int(ctx.cfg["dpi"])
    n_sheets = 0
    for k in range(0, len(cand), a.taille):
        n_sheets += 1
        part = cand[k:k + a.taille]
        (rel / f"italiques-in-{n_sheets}.tsv").write_text(
            "".join(f"{k + i + 1}\t{c[0]}\t{c[1]}\t{c[2]}\t{c[3]}\n" for i, c in enumerate(part)), "utf-8")
        sheet([(k + i + 1, c[0], c[4], c[5]) for i, c in enumerate(part)], doc, dpi, rel / f"italiques-{n_sheets}.png")
        prog.count(n_sheets, len(cand) // a.taille + 1, "planches de mots douteux")
    prog.done(f"fenêtre {lo:.3f}-{hi:.3f} ; {total} mots en italique ; {len(cand)} douteux, {n_sheets} planches")


if __name__ == "__main__":
    main()
