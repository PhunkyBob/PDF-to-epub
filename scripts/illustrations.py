#!/usr/bin/env python3
"""Illustrations du PDF -> images recadrées, placées à la fin du paragraphe en cours.

L'imprimé pose souvent une gravure sur sa propre page, au milieu d'une phrase. Dans l'EPUB, l'image vient donc après le
paragraphe interrompu : celui qui contient les derniers mots imprimés avant elle.

  1. repérage : encre hors des mots lus par l'OCR, sur toutes les pages du livre, y compris celles de pages.skip.
     Résultat dans WORK/illustrations.json, relu et corrigé par l'agent ; une page déjà vue n'est plus analysée ;
  2. recadrage : build/illustrations/fig-NNN.jpg, en niveaux de gris, fond du papier ramené au blanc ;
  3. placement : champ « figures » des sections de build/content.json (le texte des paragraphes n'est pas touché).

À vérifier : build/illustrations.txt (fin du paragraphe choisi) et build/relecture/illustrations.png (images recadrées).
Une ligne « !! » de illustrations.txt est reprise dans qa.txt.
Usage : illustrations.py --work DOSSIER
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from common import Ctx, Progress, ctx_from_args, parser, plain

WORD = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*")
WORD_BOX = re.compile(r"class=['\"]ocrx_word['\"][^>]*?title=['\"]bbox (\d+) (\d+) (\d+) (\d+)(?:; x_wconf (\d+))?")
PAGE_BOX = re.compile(r"bbox 0 0 (\d+) (\d+)")

SCAN_DPI = 100          # résolution du repérage
INK = 140               # niveau de gris sous lequel un pixel est de l'encre
CELL = 10               # côté, en pixels, d'une case de la grille de regroupement
MIN_WIDTH, MIN_HEIGHT = 0.20, 0.12   # taille minimale d'une illustration (fractions de la page)
MIN_FILL = 0.15         # part minimale de cases encrées dans son rectangle
MAX_BLANK_ROWS = 0.15   # au-delà, ces lignes vides séparent des lignes de texte mal lues : ce n'est pas une image
EDGE = 0.015            # une zone collée au bord de la page est une ombre de marge
MARGIN = 0.008          # marge ajoutée autour de l'encre au recadrage
MIN_WCONF = 50          # un mot moins sûr peut être une lecture fantaisiste de l'image : il ne la masque pas
MAX_HEIGHT = 1600       # hauteur maximale de l'image dans l'EPUB (pixels)
QUALITY = 82
ANCHOR_WORDS = 6        # mots consécutifs qui identifient l'endroit du texte
PAGES_BACK = 20         # pages remontées au plus pour trouver le texte qui précède (cahier de photos)
FAR = 25                # au-delà de ce nombre de mots ignorés en fin de page, le placement est à vérifier


def norm(w: str) -> str:
    return unicodedata.normalize("NFKD", w.lower().replace("’", "'")).encode("ascii", "ignore").decode()


# ---------------------------------------------------------------------- repérage
def word_boxes(hocr: str, min_conf: int = 0):
    """Boîtes (x0, y0, x1, y1) des mots de la page. Une couche texte (sans x_wconf) compte comme sûre."""
    out = []
    for m in WORD_BOX.finditer(hocr):
        if m[5] is None or int(m[5]) >= min_conf:
            out.append(tuple(int(m[i]) for i in range(1, 5)))
    return out


def regions(grid: np.ndarray):
    """Composantes connexes (8 voisins) d'une grille booléenne : listes de (y, x)."""
    seen = np.zeros_like(grid, bool)
    H, W = grid.shape
    for y0, x0 in zip(*np.nonzero(grid)):
        if seen[y0, x0]:
            continue
        seen[y0, x0] = True
        stack, cells = [(int(y0), int(x0))], []
        while stack:
            y, x = stack.pop()
            cells.append((y, x))
            for ny in range(max(0, y - 1), min(H, y + 2)):
                for nx in range(max(0, x - 1), min(W, x + 2)):
                    if grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        yield cells


def find_boxes(page, hocr: str) -> list[list[float]]:
    """Rectangles (fractions de la page) des zones d'encre assez grandes qui ne sont pas du texte lu par l'OCR."""
    g = np.asarray(page.render(scale=SCAN_DPI / 72).to_pil().convert("L"))
    H, W = g.shape
    ink = g < INK
    size = PAGE_BOX.search(hocr)
    k = H / int(size[2]) if size else 1
    for x0, y0, x1, y1 in word_boxes(hocr, MIN_WCONF):
        ink[max(0, int(y0 * k) - 2):int(y1 * k) + 3, max(0, int(x0 * k) - 2):int(x1 * k) + 3] = False
    rows, cols = H // CELL, W // CELL
    cells = ink[:rows * CELL, :cols * CELL].reshape(rows, CELL, cols, CELL).sum((1, 3)) >= 3
    near = cells.copy()                                  # relie les traits voisins d'une même image
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, -1), (1, -1), (-1, 1)):
        near |= np.roll(np.roll(cells, dy, 0), dx, 1)
    found = []
    for part in regions(near):
        ys, xs = zip(*part)
        y0, y1, x0, x1 = min(ys), max(ys) + 1, min(xs), max(xs) + 1
        fill = cells[y0:y1, x0:x1].mean()
        box = [x0 / cols, y0 / rows, x1 / cols, y1 / rows]
        if box[2] - box[0] < MIN_WIDTH or box[3] - box[1] < MIN_HEIGHT or fill < MIN_FILL:
            continue
        if box[0] < EDGE or box[1] < EDGE or box[2] > 1 - EDGE or box[3] > 1 - EDGE:
            continue
        sub = ink[y0 * CELL:y1 * CELL, x0 * CELL:x1 * CELL]
        if not sub.any(1).mean() >= 1 - MAX_BLANK_ROWS:
            continue
        py, px = np.nonzero(sub)
        found.append([round(float(v), 4) for v in (
            max(0.0, (x0 * CELL + px.min()) / W - MARGIN), max(0.0, (y0 * CELL + py.min()) / H - MARGIN),
            min(1.0, (x0 * CELL + px.max() + 1) / W + MARGIN), min(1.0, (y0 * CELL + py.max() + 1) / H + MARGIN))])
    return sorted(found, key=lambda b: b[1])


def book_range(ctx: Ctx, count: int) -> list[int]:
    """Pages du livre, de la première section à la dernière, couverture exclue : les planches sont souvent dans skip."""
    p = ctx.cfg["pages"]
    secs = p["sections_before_body"] + p["sections_after_body"]
    first = min([p["body_first"]] + [s["first"] for s in secs])
    last = max([p["body_last"] or count] + [s["last"] for s in secs])
    return [n for n in range(first, min(last, count) + 1) if n != p.get("cover")]


def detect(ctx: Ctx, doc, prog: Progress) -> dict:
    """Complète WORK/illustrations.json avec les pages pas encore analysées : les corrections de l'agent restent."""
    f = ctx.work / "illustrations.json"
    data = json.loads(f.read_text("utf-8")) if f.exists() else {"pages_vues": [], "images": []}
    seen = set(data["pages_vues"])
    todo = [n for n in book_range(ctx, len(doc)) if n not in seen and (ctx.ocr / f"page-{n:03d}.hocr").exists()]
    for i, n in enumerate(todo, 1):
        hocr = (ctx.ocr / f"page-{n:03d}.hocr").read_text("utf-8")
        data["images"] += [dict(page=n, box=b, alt="", apres="") for b in find_boxes(doc[n - 1], hocr)]
        prog.count(i, len(todo))
    data["pages_vues"] = sorted(seen | set(todo))
    data["images"].sort(key=lambda x: (x["page"], x["box"][1]))
    f.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    return data


# ---------------------------------------------------------------------- recadrage
def crop(ctx: Ctx, doc, item: dict, out: Path):
    """Découpe l'image à la résolution de l'OCR, en gris, le fond du papier ramené au blanc."""
    x0, y0, x1, y1 = item["box"]
    im = doc[item["page"] - 1].render(scale=ctx.cfg["dpi"] / 72).to_pil().convert("L")
    W, H = im.size
    a = np.asarray(im.crop((int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H))), np.float32)
    paper = max(float(np.percentile(a, 90)) * 0.97, 1.0)
    fig = Image.fromarray(np.clip(a * 255 / paper, 0, 255).astype(np.uint8))
    if fig.height > MAX_HEIGHT:
        fig = fig.resize((round(fig.width * MAX_HEIGHT / fig.height), MAX_HEIGHT), Image.LANCZOS)
    fig.save(out, "JPEG", quality=QUALITY, optimize=True)


# ---------------------------------------------------------------------- placement
def sections(d):
    return d["front"] + d["chapters"] + d.get("back", [])


def tokens_of(text: str) -> list[str]:
    return [norm(t) for t in WORD.findall(text)]


def page_tokens(ctx: Ctx, page: int, above: float = 2.0) -> list[str]:
    """Mots de la page situés au-dessus de la hauteur `above` (fraction), dans l'ordre de lecture."""
    f = ctx.ocr / f"page-{page:03d}.hocr"
    if not f.exists():
        return []
    from lxml import etree
    tree = etree.parse(str(f), etree.HTMLParser(encoding="utf-8"))
    size = PAGE_BOX.search(f.read_text("utf-8"))
    H = int(size[2]) if size else 1
    out = []
    for w in tree.iter("span"):
        if w.get("class") == "ocrx_word" and int(re.search(r"bbox \d+ \d+ \d+ (\d+)", w.get("title"))[1]) / H <= above:
            out += tokens_of("".join(w.itertext()))
    return out


class Text:
    """Le texte du livre mot par mot, pour retrouver un groupe de mots dans ses paragraphes."""

    def __init__(self, d):
        self.toks, self.where = [], []
        for s, sec in enumerate(sections(d)):
            for k, para in enumerate(sec["paras"]):
                for t in tokens_of(plain(para)):
                    self.toks.append(t)
                    self.where.append((s, k))
        self.grams: dict[tuple, list[int]] = {}
        for i in range(len(self.toks) - ANCHOR_WORDS + 1):
            self.grams.setdefault(tuple(self.toks[i:i + ANCHOR_WORDS]), []).append(i + ANCHOR_WORDS - 1)

    def after_page_end(self, toks: list[str], start: int):
        """Dernier groupe de mots de `toks` présent dans le texte après `start` : (position, mots ignorés à la fin).

        Folio, note de bas de page ou césure ne se retrouvent pas dans le texte : on remonte jusqu'à un groupe connu."""
        for end in range(len(toks), ANCHOR_WORDS - 1, -1):
            hits = [i for i in self.grams.get(tuple(toks[end - ANCHOR_WORDS:end]), []) if i >= start]
            if hits:
                return hits[0], len(toks) - end
        return None

    def phrase(self, words: list[str], start: int):
        """Position du dernier mot de la phrase imposée par l'agent (« apres »)."""
        for i in range(start, len(self.toks) - len(words) + 1):
            if self.toks[i:i + len(words)] == words:
                return i + len(words) - 1
        return None


def place(ctx: Ctx, d, images: list[dict]) -> list[str]:
    """Écrit les « figures » de chaque section et renvoie le compte rendu du placement."""
    for sec in sections(d):
        sec.pop("figures", None)
    text, secs, start, report, count = Text(d), sections(d), 0, [], {}
    for item in images:
        p = item["page"]
        count[p] = count.get(p, 0) + 1
        name = f"fig-{p:03d}" + (f"-{count[p]}" if count[p] > 1 else "") + ".jpg"
        pos, note = None, ""
        if item.get("apres"):
            pos = text.phrase(tokens_of(item["apres"]), start)
            note = " (position imposée)"
        else:
            for back in range(PAGES_BACK + 1):
                found = text.after_page_end(page_tokens(ctx, p - back, item["box"][1] if back == 0 else 2.0), start)
                if found:
                    pos = found[0]
                    if found[1] > FAR:
                        note = f" (à vérifier : {found[1]} mots ignorés en fin de p.{p - back})"
                    break
        if pos is None:
            report.append(f"!! p.{p} {name} : texte précédent introuvable, image non placée (renseigner « apres »)")
            continue
        s, k = text.where[pos]
        secs[s].setdefault("figures", []).append(dict(after=k, file=name, alt=item.get("alt", ""), page=p, box=item["box"]))
        start = pos
        report.append(f"p.{p} {name} -> {secs[s]['id']} §{k + 1}{note} : « …{plain(secs[s]['paras'][k])[-70:]} »")
    return report


# ---------------------------------------------------------------------- planche de contrôle
def sheet(ctx: Ctx, images: list[tuple[int, Path]]):
    rel = ctx.build / "relecture"
    rel.mkdir(exist_ok=True)
    target = rel / "illustrations.png"
    target.unlink(missing_ok=True)
    if not images:
        return
    thumbs = []
    for page, path in images:
        im = Image.open(path).convert("RGB")
        im.thumbnail((300, 420))
        cell = Image.new("RGB", (310, 450), "white")
        cell.paste(im, (5, 25))
        ImageDraw.Draw(cell).text((5, 5), f"p.{page} {path.name}", fill="red")
        thumbs.append(cell)
    n = min(6, len(thumbs))
    out = Image.new("RGB", (n * 310, (len(thumbs) + n - 1) // n * 450), "white")
    for i, c in enumerate(thumbs):
        out.paste(c, (i % n * 310, i // n * 450))
    out.save(target)


def main():
    a = parser(__doc__).parse_args()
    ctx = ctx_from_args(a)
    prog = Progress(ctx.work, "illustrations")
    content = ctx.build / "content.json"
    d = json.loads(content.read_text("utf-8"))
    out = ctx.build / "illustrations"
    out.mkdir(exist_ok=True)
    for old in out.glob("fig-*.jpg"):
        old.unlink()
    if not ctx.cfg["illustrations"]:
        for sec in sections(d):
            sec.pop("figures", None)
        content.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
        (ctx.build / "illustrations.txt").write_text("", "utf-8")
        prog.done("illustrations désactivées")
        return
    doc = pdfium.PdfDocument(str(ctx.pdf))
    prog.log("repérage des illustrations…")
    images = detect(ctx, doc, prog)["images"]
    report = place(ctx, d, images)
    made = []
    for sec in sections(d):
        for fig in sec.get("figures", []):
            crop(ctx, doc, fig, out / fig["file"])
            made.append((fig["page"], out / fig["file"]))
    content.write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    (ctx.build / "illustrations.txt").write_text("".join(x + "\n" for x in report), "utf-8")
    sheet(ctx, sorted(made))
    print(f"{len(made)} illustration(s) placée(s) sur {len(images)} repérée(s) -> {ctx.build / 'illustrations.txt'}")
    for line in report:
        print("  " + line)
    prog.done(f"{len(made)} illustration(s)")


if __name__ == "__main__":
    main()
