#!/usr/bin/env python3
"""Aperçu rapide du texte final de quelques pages, pour tester une règle de fixes.tsv ou un réglage de
book.json en quelques secondes au lieu de régénérer tout le livre.

N'écrit rien dans le dossier de travail (content.json, review.txt restent intacts) : affiche les paragraphes
et notes des pages demandées, après corrections et typographie. Plusieurs aperçus peuvent tourner en parallèle.

Usage : apercu.py --work DOSSIER --pages 81,120-122 [--grep MOTIF] [--out FICHIER]
  --grep : n'affiche que les paragraphes contenant ce motif (regex, insensible à la casse) ;
  --out  : écrit aussi le résultat dans ce fichier (UTF-8).

Les pages consécutives sont assemblées ensemble, comme dans l'extraction complète (paragraphe à cheval
sur deux pages, règle de fixes.tsv qui franchit un saut de page).
Limite : le vocabulaire (recollage des césures, noms propres épargnés par les corrections automatiques)
n'est calculé que sur ces pages ; un mot coupé en fin de ligne ou un nom propre peut donc être traité
différemment que dans le livre complet.
"""
from __future__ import annotations

import re
from pathlib import Path

import extract_text as et
from common import SCENE_MARK, ctx_from_args, parse_pages, parser, plain


def main():
    ap = parser(__doc__)
    ap.add_argument("--pages", required=True, help="pages du PDF, ex. 81,120-122")
    ap.add_argument("--grep", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    ex = et.Extractor(ctx_from_args(a))
    runs: list[list[int]] = []                     # pages consécutives : [première, dernière]
    for p in parse_pages(a.pages):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    blocks = []
    for first, last in runs:
        blocks += ex.assemble(first, last)
    ex.build_vocab(blocks)
    rx = re.compile(a.grep, re.I) if a.grep else None
    items = []                                     # (page, préfixe, texte) dans l'ordre
    sec = dict(id="apercu", paras=[], notes=[])
    for b in blocks:
        if b["type"] == "h":
            items.append((b["page"], "## ", ex.typography(" ".join(b["lines"]))))
        elif b["type"] == "s":
            items.append((b["page"], "", SCENE_MARK))
        else:
            key, prefix = ("notes", "[note] ") if b["type"] == "n" else ("paras", "### " if b["type"] == "sub" else "")
            sec[key].append(ex.finalize_block(b))
            items.append((b["page"], prefix, (key, len(sec[key]) - 1)))
    ex.strip_note_calls(sec)                       # comme l'extraction complète
    out = []
    for page, prefix, ref in items:
        text = ref if isinstance(ref, str) else plain(sec[ref[0]][ref[1]])
        if rx is None or rx.search(text):
            out.append(f"p.{page}  {prefix}{text}")
    print("\n\n".join(out))
    if a.out:
        Path(a.out).write_text("\n\n".join(out) + "\n", "utf-8")
        print(f"\n-> {a.out} ({len(out)} blocs)")


if __name__ == "__main__":
    main()
