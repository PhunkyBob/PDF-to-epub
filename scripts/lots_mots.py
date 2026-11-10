#!/usr/bin/env python3
"""Prépare les tranches de remarques « mot inconnu » / « mot douteux » pour les sous-agents Haiku « mots ».

Lit WORK/build/review.txt et les verdicts déjà rendus (WORK/build/relecture/prop-mots*.tsv), et écrit
WORK/build/relecture/mots-in-N.txt (une remarque par ligne, telle qu'elle figure dans review.txt).
Le sous-agent de la tranche N écrit ses verdicts dans prop-motsN.tsv (prompt C de references/orchestration.md).

  * une remarque déjà vérifiée (même mot, page à ±1 près) n'est pas redemandée : après une relance ou un
    changement de dictionnaire, seules les remarques nouvelles partent en relecture ;
  * un « mot inconnu » est jugé une fois pour toutes (c'est le dictionnaire qui l'ignore, partout) : une seule
    occurrence par mot inconnu est envoyée, aucune s'il a déjà un verdict ; un « mot douteux » (lecture
    incertaine d'une page) reste vérifié page par page ;
  * les apostrophes ’ et ' sont confondues (les sous-agents réécrivent souvent l'une en l'autre).
Les numéros de tranche continuent après les prop-motsN.tsv existants : rien n'est écrasé.

Usage : lots_mots.py --work DOSSIER [--taille 40]
"""
import argparse
import re
from pathlib import Path

from common import iter_prop_mots, near_pages, norm_word, parse_remark


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    ap.add_argument("--taille", type=int, default=40, help="remarques par tranche (40 par défaut)")
    a = ap.parse_args()
    work = Path(a.work)
    rel = work / "build" / "relecture"
    rel.mkdir(parents=True, exist_ok=True)

    used = [0] + [int(m.group(1)) for f in rel.glob("prop-mots*.tsv")
                  if (m := re.fullmatch(r"prop-mots(\d+)\.tsv", f.name))]
    pairs = {(page, norm_word(ocr)) for page, ocr, _, _ in iter_prop_mots(rel)}
    judged = {w for _, w in pairs}                         # déjà jugé quelque part, quel que soit le verdict

    todo, seen = [], set()
    for line in (work / "build" / "review.txt").read_text("utf-8").splitlines():
        r = parse_remark(line)
        if not r or not r[3]:
            continue
        page, _, kind, word = r
        key = (page, norm_word(word))
        unknown = kind.endswith("mot inconnu")
        dedup = key[1] if unknown else key
        if any((p, key[1]) in pairs for p in near_pages(page)) or dedup in seen or (unknown and key[1] in judged):
            continue
        seen.add(dedup)
        todo.append(line)

    for old in rel.glob("mots-in-*.txt"):              # tranches d'un tour précédent non encore relues
        n = re.fullmatch(r"mots-in-(\d+)\.txt", old.name)
        if n and not (rel / f"prop-mots{n.group(1)}.tsv").exists():
            old.unlink()
    first = max(used) + 1
    k = first
    for i in range(0, len(todo), a.taille):
        (rel / f"mots-in-{k}.txt").write_text("\n".join(todo[i:i + a.taille]) + "\n", "utf-8")
        k += 1
    if todo:
        print(f"{len(todo)} remarques sans verdict -> tranches mots-in-{first}.txt à mots-in-{k - 1}.txt "
              f"(verdicts attendus dans prop-mots{first}.tsv à prop-mots{k - 1}.tsv)")
    else:
        print("aucune remarque sans verdict : rien à relire")


if __name__ == "__main__":
    main()
