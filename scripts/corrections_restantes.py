#!/usr/bin/env python3
"""Liste les corrections vérifiées qui ne sont PAS dans le texte livré. À lancer après chaque run.py --from extract.

Tant que la liste n'est pas vide, rapport_humain.py refuse d'écrire le rapport : le livre ne peut pas être livré.
Écrit WORK/build/relecture/a-corriger.tsv (cas, où, lu, attendu, dans le texte, quoi faire) et l'affiche.

Cas listés, et ce qu'il faut faire pour chacun (references/relecture.md, § 3) :
  * NON VÉRIFIÉ   : mot signalé que personne n'a regardé sur l'image -> lots_mots.py puis sous-agents « mots » ;
  * NON CORRIGÉ   : l'image porte une autre forme que le texte -> règle à la main dans fixes.tsv ;
  * SANS EFFET    : la règle automatique n'a rien changé (elle ne correspond pas au texte)
                    -> apercu.py pour voir le texte, puis règle à la main dans fixes.tsv ;
  * CONTEXTE      : vrai mot, fragment présent plusieurs fois -> règle à la main avec 2 ou 3 mots de contexte ;
  * CONFLIT       : deux relecteurs en désaccord -> regarder l'image (render_pages.py), puis une ligne dans
                    decisions.tsv : forme lue <TAB> forme retenue <TAB> raison ;
  * INCERTAIN     : proposition de confiance basse -> regarder l'image, puis une ligne dans decisions.tsv
                    (fragment lu <TAB> fragment retenu <TAB> raison).
Seule autre sortie : garder la forme lue par une ligne de decisions.tsv (forme lue <TAB> forme lue <TAB> raison),
après l'avoir vue sur l'image. La raison est imprimée dans le rapport.

Usage : corrections_restantes.py --work DOSSIER      (code de sortie 1 s'il reste des corrections)
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from common import book_text, hocr_words, norm_word
from propositions_vers_regles import analyse, norm, spans
from rapport_humain import load_remarks, pages_text, word_rows

TODO = {
    "NON VÉRIFIÉ": "lots_mots.py puis sous-agents « mots » (prompt C)",
    "NON CORRIGÉ": "écrire la règle à la main dans fixes.tsv, la tester avec apercu.py",
    "SANS EFFET": "voir le texte avec apercu.py, écrire la règle à la main dans fixes.tsv",
    "CONTEXTE": "écrire la règle à la main dans fixes.tsv avec 2 ou 3 mots de contexte",
    "CONFLIT": "regarder l'image (render_pages.py), puis écrire la décision dans decisions.tsv",
    "INCERTAIN": "regarder l'image (render_pages.py), puis decisions.tsv : fragment lu, fragment retenu, raison",
}
CASES = {"regle": "SANS EFFET", "contexte": "CONTEXTE", "conflit": "CONFLIT", "incertain": "INCERTAIN"}   # par état d'analyse()


def page_finder(work: Path):
    """(fragment du texte) -> pages du PDF où l'OCR a lu deux mots voisins du fragment, « p. 61 » ou « »."""
    files = {int(f.stem.split("-")[1]): f for f in (work / "ocr").glob("page-*.hocr")}
    texts: dict[int, str] = {}

    def page_text(p: int) -> str:                    # une page n'est lue que si un fragment y est cherché
        if p not in texts:
            texts[p] = " " + " ".join(w for w in map(norm_word, hocr_words(files[p])) if w) + " "
        return texts[p]

    def find(frag: str, first: int = 0, last: int = 10 ** 6) -> str:
        """Pages de [first, last] (celles du chapitre) où le plus de paires de mots du fragment ont été lues."""
        ws = [w for w in (norm_word(t) for t in frag.split()) if w]
        pairs = [f" {' '.join(ws[i:i + 2])} " for i in range(len(ws) - 1)] or [f" {w} " for w in ws]
        score = {p: sum(x in page_text(p) for x in pairs) for p in sorted(files) if first <= p <= last}
        best = max(score.values(), default=0)
        hits = [p for p, n in score.items() if n == best]
        if best and len(hits) <= 3:
            return "p. " + ", ".join(map(str, hits))
        return f"p. {first} à {max(score)}" if first and score else ""     # dernier chapitre : `last` n'est pas une page

    return find


def restantes(work: Path, data: dict, text: str, rows: list[dict], items: list[dict]) -> list[dict]:
    """Corrections vérifiées absentes du texte livré, d'après word_rows() (mots signalés) et analyse() (propositions).

    data : content.json ; text : book_text(data)."""
    out = []
    for r in rows:
        if r["bloquant"]:
            out.append(dict(cas="NON CORRIGÉ" if r["verifie"] else "NON VÉRIFIÉ", ou="p. " + pages_text(r["pages"]),
                            lu=r["mot"], attendu=r["attendu"] or "?", texte=r["epub"] or "?"))
    seen, page_of = set(), page_finder(work)
    chaps = data.get("chapters", [])
    starts = [c.get("page", 0) for c in chaps]
    span = {str(c.get("number")): (p, nxt) for c, p, nxt in zip(chaps, starts, starts[1:] + [10 ** 6]) if p}
    for e in items:
        key = (e["lu"], e["propose"])
        if e["etat"] not in CASES or e["mot"] or key in seen or not spans(e["lu"], text, 1):
            continue                                  # les mots seuls sont jugés ci-dessus, à leur page
        seen.add(key)
        if norm(e["lu"]) in norm(e["propose"]) and spans(e["propose"], text, 1):
            continue                                  # ajout (« Mais » -> « — Mais ») déjà fait : la forme lue y est encore
        attendu = " / ".join(e["candidats"]) if e["etat"] == "conflit" else e["propose"]
        ou = e["ou"] if e["ou"].startswith("p. ") else ", ".join(
            x for x in (page_of(e["lu"], *span.get(e["ou"].removeprefix("chap. ").split(",")[0], ())), e["ou"]) if x)
        out.append(dict(cas=CASES[e["etat"]], ou=ou, lu=e["lu"], attendu=attendu, texte=e["lu"]))
    for e in out:
        e["faire"] = TODO[e["cas"]]
    return out


def signaler(work: Path, todo: list[dict]):
    """Écrit build/relecture/a-corriger.tsv et affiche la liste."""
    rel = work / "build" / "relecture"
    rel.mkdir(parents=True, exist_ok=True)
    (rel / "a-corriger.tsv").write_text("".join(
        "\t".join(e[k] for k in ("cas", "ou", "lu", "attendu", "texte", "faire")) + "\n" for e in todo), "utf-8")
    for e in todo:
        print(f"{e['cas']:<12} {e['ou']:<28} lu « {e['lu']} »  attendu « {e['attendu']} »  dans le texte « {e['texte']} »"
              f"\n             -> {e['faire']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", required=True)
    work = Path(ap.parse_args().work)
    content = work / "build" / "content.json"
    if not content.exists():
        sys.exit("build/content.json absent : lancer run.py d'abord.")
    data = json.loads(content.read_text("utf-8"))
    text = book_text(data)
    todo = restantes(work, data, text, word_rows(work, data, load_remarks(work)[1], text), analyse(work, text))
    signaler(work, todo)
    if todo:
        n = Counter(e["cas"] for e in todo)
        counts = ", ".join(f"{n[c]} {c}" for c in TODO if n[c])
        sys.exit(f"\n{len(todo)} correction(s) restante(s) ({counts}) -> build/relecture/a-corriger.tsv. "
                 "Traite-les toutes, relance run.py --from extract, puis ce script.")
    print("aucune correction restante : toutes les corrections vérifiées sont dans le texte.")


if __name__ == "__main__":
    main()
