#!/usr/bin/env python3
"""Étape 0 : diagnostic du PDF + planches de miniatures + modèle de configuration.

Usage :
  uv run --project <skill> python <skill>/scripts/inspect_pdf.py livre.pdf [--work DOSSIER]

Produit dans le dossier de travail (défaut : <livre>.epub-work à côté du PDF) :
  inspect/report.json        diagnostic (texte présent ? scan ? qualité de la couche texte ?)
  inspect/sheet-*.png        miniatures numérotées (début, fin, échantillon du corps) à examiner
  book.json                  configuration pré-remplie (n'écrase jamais un fichier existant)
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from collections import Counter
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

from common import DEFAULTS, default_work_dir, find_tesseract

LANG_SPELL = {"fr", "en", "es", "de", "it", "pt", "nl", "ru"}       # langues de pyspellchecker


def text_layer_quality(pdf_path: Path, pages: list[int], lang: str) -> tuple[dict, float]:
    """Qualité de la couche texte des pages échantillon, et part de ces pages couvertes par une image pleine page."""
    try:
        from spellchecker import SpellChecker
        spell = SpellChecker(language=lang if lang in LANG_SPELL else "en")
    except Exception:
        spell = None
    words, known, chars, fonts, full_img = 0, 0, 0, Counter(), 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        for p in pages:
            page = pdf.pages[p - 1]
            for c in page.chars:
                fonts[c.get("fontname", "?")] += 1
            txt = page.extract_text() or ""
            chars += len(txt)
            for w in re.findall(r"[^\W\d_]{3,}", txt.lower()):
                words += 1
                if spell is None or w in spell:
                    known += 1
            area = float(page.width * page.height)
            if any((im["x1"] - im["x0"]) * (im["bottom"] - im["top"]) > 0.8 * area for im in page.images):
                full_img += 1
    return {
        "chars_per_page": round(chars / max(1, len(pages))),
        "known_word_ratio": round(known / words, 3) if words else 0.0,
        "fonts": [f for f, _ in fonts.most_common(6)],
    }, full_img / max(1, len(pages))


def sheet(doc, pages: list[int], out: Path, per_row: int = 6, thumb_h: int = 520):
    """Planche de miniatures numérotées (numéro de page PDF en rouge)."""
    try:
        font = ImageFont.load_default(size=34)
    except TypeError:
        font = ImageFont.load_default()
    thumbs = []
    for p in pages:
        page = doc[p - 1]
        scale = thumb_h / page.get_height()
        im = page.render(scale=scale).to_pil().convert("RGB")
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 92, 44], fill="white")
        d.text((6, 4), str(p), fill="red", font=font)
        d.rectangle([0, 0, im.width - 1, im.height - 1], outline="gray")
        thumbs.append(im)
    rows = [thumbs[i:i + per_row] for i in range(0, len(thumbs), per_row)]
    W = max(sum(t.width for t in r) for r in rows)
    H = sum(max(t.height for t in r) for r in rows)
    canvas = Image.new("RGB", (W, H), "white")
    y = 0
    for r in rows:
        x = 0
        for t in r:
            canvas.paste(t, (x, y))
            x += t.width
        y += max(t.height for t in r)
    canvas.save(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--work", default=None)
    ap.add_argument("--lang", default="fr", help="langue du livre (fr, en, …)")
    a = ap.parse_args()

    pdf_path = Path(a.pdf).resolve()
    work = Path(a.work).resolve() if a.work else default_work_dir(pdf_path)
    (work / "inspect").mkdir(parents=True, exist_ok=True)

    doc = pdfium.PdfDocument(str(pdf_path))
    n = len(doc)
    meta = {}
    try:
        meta = {k: v for k, v in doc.get_metadata_dict().items() if v}
    except Exception:
        pass

    if n <= 20:
        sample = list(range(1, n + 1))
    else:                                    # pages du corps : on évite le début et la fin du livre
        step = max(1, (n - 15) // 12)
        sample = list(range(11, n - 4, step))[:14]
    q, img_ratio = text_layer_quality(pdf_path, sample, a.lang)
    producer = " ".join(str(meta.get(k, "")) for k in ("Producer", "Creator", "Keywords"))
    scan_ocr_layer = any("GlyphLess" in f for f in q["fonts"]) or "Internet Archive" in producer

    if q["chars_per_page"] < 200:
        reco, why = "ocr", "pas (ou presque pas) de couche texte : PDF scanné"
    elif scan_ocr_layer or img_ratio > 0.5:
        reco, why = "ocr", "scan avec une couche OCR existante : on refait un OCR de meilleure qualité"
    elif q["known_word_ratio"] < 0.90:
        reco, why = "ocr", f"couche texte de qualité douteuse ({q['known_word_ratio']:.0%} de mots reconnus)"
    else:
        reco, why = "textlayer", "PDF numérique avec une couche texte propre"

    # planches de miniatures
    first = list(range(1, min(n, 18) + 1))
    last = list(range(max(19, n - 11), n + 1))
    mid = sorted(set(range(20, n - 12, max(1, (n - 32) // 6))))[:6]
    sheets = []
    for name, pages in (("debut", first), ("fin", last), ("corps", mid)):
        if pages:
            out = work / "inspect" / f"sheet-{name}.png"
            sheet(doc, pages, out)
            sheets.append(str(out))

    report = {
        "pdf": str(pdf_path), "work": str(work), "pages": n, "metadata": meta,
        "page_size_pt": [round(doc[0].get_width()), round(doc[0].get_height())],
        "text_layer": q, "full_page_image_ratio": round(img_ratio, 2),
        "recommended_source": reco, "reason": why,
        "tesseract": find_tesseract(), "sheets": sheets, "config_created": None,
    }

    cfg_path = work / "book.json"
    if not cfg_path.exists():
        cfg = copy.deepcopy(DEFAULTS)
        cfg.update(pdf=str(pdf_path), title=meta.get("Title", ""), author=meta.get("Author", ""),
                   language=a.lang, source=reco,
                   ocr_lang={"fr": "fra", "en": "eng", "es": "spa", "de": "deu", "it": "ita",
                             "pt": "por", "nl": "nld"}.get(a.lang, "eng"))
        cfg["pages"]["body_last"] = n
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
        report["config_created"] = str(cfg_path)

    text = json.dumps(report, ensure_ascii=False, indent=2)    # report.json = ce qui est affiché
    (work / "inspect" / "report.json").write_text(text, "utf-8")
    print(text)


if __name__ == "__main__":
    main()
