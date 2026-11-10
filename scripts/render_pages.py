#!/usr/bin/env python3
"""Rend des pages du PDF en PNG pour les examiner de près.

Usage : render_pages.py livre.pdf --pages 1,7,13-15 [--out DOSSIER] [--height 1100]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pypdfium2 as pdfium

from common import default_work_dir, parse_pages


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--height", type=int, default=1100)
    a = ap.parse_args()
    pdf = Path(a.pdf).resolve()
    out = Path(a.out) if a.out else default_work_dir(pdf) / "inspect"
    out.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    for p in parse_pages(a.pages):
        page = doc[p - 1]
        f = out / f"page-{p:03d}.png"
        page.render(scale=a.height / page.get_height()).to_pil().convert("RGB").save(f)
        print(f)


if __name__ == "__main__":
    main()
