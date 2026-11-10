#!/usr/bin/env python3
"""Exporte le texte extrait en lots pour une relecture parallèle (sous-agents) : <work>/build/relecture/lotN.txt

Chaque ligne est un paragraphe préfixé par son identifiant : [3.42] = chapitre 3, paragraphe 42 ;
[avant-propos.2] = section liminaire ; [3.n1] = note 1 du chapitre 3. Les lots regroupent des chapitres
entiers, d'environ --chars caractères chacun (110 000 par défaut : 2 à 3 chapitres de roman par lot).

Usage : export_review.py --work DOSSIER [--chars 110000]
"""
from __future__ import annotations

import json

from common import ctx_from_args, parser, plain


def section_lines(key, sec):
    out = [f"##### {key} — {sec['title']}"]
    out += [f"[{key}.{k}] {plain(p)}" for k, p in enumerate(sec["paras"], 1)]
    out += [f"[{key}.n{k}] {plain(p)}" for k, p in enumerate(sec.get("notes", []), 1)]
    return out


def main():
    p = parser(__doc__)
    p.add_argument("--chars", type=int, default=110000, help="taille visée d'un lot (caractères)")
    a = p.parse_args()
    ctx = ctx_from_args(a)
    data = json.loads((ctx.build / "content.json").read_text("utf-8"))
    sections = [section_lines(s["id"], s) for s in data["front"]]
    sections += [section_lines(str(c["number"]), c) for c in data["chapters"]]
    sections += [section_lines(s["id"], s) for s in data.get("back", [])]

    lots, cur, size = [], [], 0
    for lines in sections:
        n = sum(len(x) for x in lines)
        if cur and size + n > a.chars:
            lots.append(cur)
            cur, size = [], 0
        cur += lines
        size += n
    if cur:
        lots.append(cur)

    out = ctx.build / "relecture"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("lot*.txt"):
        old.unlink()
    for i, lines in enumerate(lots, 1):
        text = plain("\n".join(lines))
        f = out / f"lot{i}.txt"
        f.write_text(text + "\n", "utf-8")
        print(f"{f} : {len(lines)} lignes, {len(text)} caractères")


if __name__ == "__main__":
    main()
