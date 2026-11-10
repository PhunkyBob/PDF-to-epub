#!/usr/bin/env python3
"""Étape 3 : couverture -> <work>/build/cover.jpg

Ordre de priorité :
  1. « cover_image » (chemin d'une image fournie par l'utilisateur) ;
  2. rendu de la page « pages.cover » du PDF, recadrée par « cover_crop » [x0, y0, x1, y1] en
     fractions (pour écarter étiquette de bibliothèque, code-barres, tampon…) ;
  3. sinon, couverture typographique générée (titre + auteur).
Usage : make_cover.py --work DOSSIER
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from common import Progress, ctx_from_args, parser


def font(size):
    for name in ("DejaVuSerif.ttf", "georgia.ttf", "times.ttf", "Times New Roman.ttf", "LiberationSerif-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def generated(cfg) -> Image.Image:
    W, H = 1200, 1800
    img = Image.new("RGB", (W, H), (245, 240, 228))
    d = ImageDraw.Draw(img)
    d.rectangle([60, 60, W - 60, H - 60], outline=(120, 100, 80), width=4)
    y = 520
    f = font(110)
    for line in textwrap.wrap(cfg["title"] or "Sans titre", 16):
        w = d.textlength(line, font=f)
        d.text(((W - w) / 2, y), line, font=f, fill=(40, 30, 20)); y += 140
    f = font(60)
    a = cfg["author"]
    d.text(((W - d.textlength(a, font=f)) / 2, y + 120), a, font=f, fill=(90, 70, 50))
    return img


def main():
    a = parser(__doc__).parse_args()
    ctx = ctx_from_args(a)
    prog = Progress(ctx.work, "cover")
    cfg = ctx.cfg
    if cfg.get("cover_image"):
        p = Path(cfg["cover_image"])
        img = Image.open(p if p.is_absolute() else ctx.config_path.parent / p).convert("RGB")
        how = "image fournie"
    elif cfg["pages"].get("cover"):
        import pypdfium2 as pdfium
        page = pdfium.PdfDocument(str(ctx.pdf))[cfg["pages"]["cover"] - 1]
        img = page.render(scale=1600 / page.get_height()).to_pil().convert("RGB")
        crop = cfg.get("cover_crop")
        if crop:
            W, H = img.size
            img = img.crop((int(crop[0] * W), int(crop[1] * H), int(crop[2] * W), int(crop[3] * H)))
        how = f"page {cfg['pages']['cover']} du PDF"
    else:
        img = generated(cfg)
        how = "générée"
    if max(img.size) > 2400:
        img.thumbnail((2400, 2400))
    out = ctx.build / "cover.jpg"
    img.save(out, quality=88, optimize=True, progressive=True)
    print(f"{out} {img.size} ({how})")
    prog.done(f"couverture {img.size[0]}x{img.size[1]} ({how})")


if __name__ == "__main__":
    main()
