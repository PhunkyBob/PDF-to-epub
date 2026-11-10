"""Transforme les propositions des sous-agents de relecture en règles candidates pour fixes.tsv.

Lit WORK/build/relecture/prop-lot*.tsv  (id <TAB> fragment erroné <TAB> fragment corrigé <TAB> confiance)
et                   prop-mots*.tsv     (page <TAB> mot OCR <TAB> mot imprimé <TAB> verdict),
écrit WORK/build/relecture/regles-candidates.tsv (même format que fixes.tsv) et regles-ecartees.txt
(propositions écartées, avec la raison). N'écrit JAMAIS dans fixes.tsv : relis les candidates, teste-les avec
apercu.py, puis copie celles que tu retiens.

Garde-fous (voir references/relecture.md § 3) : seules les confiances « haute » ; rien qui ajoute, retire
ou réordonne des mots ; pas de « A » -> « À » (le livre n'accentue pas toujours les capitales) ; pas de simple
changement de style d'apostrophe ; un fragment trop court (< 3 caractères) n'est pas une règle sûre. Les règles
s'appliquent au texte BRUT de l'OCR : les fragments avec italique, espace insécable ou typographie corrigée
ensuite peuvent ne jamais correspondre ; vérifier la disparition du motif après relance.
"""
import argparse
import re
from pathlib import Path

import common  # noqa: F401  (sorties UTF-8)

APOS = "['’]"


def norm(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'").replace(" ", " ").replace(" ", " ").strip()


def to_regex(frag: str) -> str:
    frag = norm(frag)
    rx = "".join(APOS if ch == "'" else r"\s+" if ch == " " else re.escape(ch) for ch in frag)
    # bornes de mot : l'apostrophe compte comme partie du mot, sinon un fragment comme « aujourd » ou
    # « avotte » s'applique à l'intérieur de « aujourd’hui » ou « G’avotte » et duplique le texte
    if re.match(r"\w", frag[:1]):
        rx = r"(?<![\w'’])" + rx
    if re.match(r"\w", frag[-1:]):
        rx += r"(?![\w'’])"
    return rx


def repl(s: str) -> str:
    return s.replace("\\", "\\\\")


def check(bad: str, good: str) -> str | None:
    """Raison d'écarter la proposition, ou None si elle est acceptable."""
    bad, good = norm(bad), norm(good)
    if bad == good:
        return "identique (style d'apostrophe ou d'espace)"
    if len(bad) < 3:
        return "fragment trop court"
    if len(bad.split()) != len(good.split()):
        return "nombre de mots différent (ajout, retrait ou fusion)"
    if re.sub(r"[^A-Za-z]", "", bad).lower() == re.sub(r"[^A-Za-z]", "", good).lower() \
            and re.fullmatch(r"[AÀ]\b.*", good) and bad[:1] == "A":
        return "A -> À (capitale non accentuée dans le livre ?)"
    if "''" in good:                                # norm() a déjà ramené ’ à '
        return "apostrophe doublée"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    work = Path(ap.parse_args().work)
    rel = work / "build" / "relecture"
    rules: dict[str, str] = {}
    dropped: list[str] = []
    for f in sorted(rel.glob("prop-lot*.tsv")):
        for line in f.read_text("utf-8", errors="replace").splitlines():
            p = [x.strip() for x in line.split("\t")]
            if len(p) < 3 or not p[1]:
                continue
            if len(p) >= 4 and p[3].lower().startswith("b"):
                dropped.append(f"{f.name}: confiance basse : {p[1]!r} -> {p[2]!r}")
                continue
            why = check(p[1], p[2])
            if why:
                dropped.append(f"{f.name}: {why} : {p[1]!r} -> {p[2]!r}")
            else:
                rules.setdefault(to_regex(p[1]), repl(norm(p[2])))
    for f in sorted(rel.glob("prop-mots*.tsv")):
        for line in f.read_text("utf-8", errors="replace").splitlines():
            p = [x.strip() for x in line.split("\t")]
            if len(p) < 4 or "erreur" not in p[3].lower():
                continue
            bad, good = p[1], p[2]
            if len(norm(bad).split()) != 1 or len(norm(bad)) < 4 or check(bad, good):
                dropped.append(f"{f.name}: écarté : {bad!r} -> {good!r}")
                continue
            rules.setdefault(to_regex(bad), repl(norm(good)))
    out = rel / "regles-candidates.tsv"
    out.write_text("".join(f"{k}\t{v}\n" for k, v in rules.items()), "utf-8")
    (rel / "regles-ecartees.txt").write_text("\n".join(dropped) + "\n", "utf-8")
    print(f"{len(rules)} règles candidates -> regles-candidates.tsv ; "
          f"{len(dropped)} propositions écartées -> regles-ecartees.txt")


if __name__ == "__main__":
    main()
