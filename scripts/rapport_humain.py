"""Génère le rapport de relecture pour un humain : WORK/build/rapport-relecture.md.

Le rapport se lit seul : il ne renvoie à aucun fichier de travail et ne contient aucune section destinée à
l'agent. Il rassemble (1) le livre et le fichier livré, (2) ce qui a été fait et contrôlé, (3) toutes les
décisions prises, (4) la structure de l'EPUB, (5) TOUS les points à vérifier à la main, (6) les limites,
(7) la validation, et en annexe la liste exhaustive des remarques du contrôle orthographique automatique.

Sources : book.json, build/content.json, build/review.txt, build/qa.txt (automatique) et WORK/rapport.json
(rédigé par l'agent, en langage clair : voir references/rapport-humain.md pour le format).
Le script refuse de produire un rapport vide, qui cite des fichiers de travail ou qui contredit l'EPUB (coherence.py).
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from html import unescape
from pathlib import Path

from coherence import Epub, audit, digest
from common import iter_prop_mots, near_pages, norm_word, parse_remark, pick_epub, plain

LABELS = [
    ("mot inconnu", "Mots absents du dictionnaire (souvent des noms propres ou des mots étrangers corrects)"),
    ("mot douteux", "Mots lus avec une faible confiance par l'OCR"),
    ("début du chapitre", "Débuts de chapitre (la grande initiale a pu être mal lue)"),
    ("note de bas de page", "Notes de bas de page détectées"),
    ("appel de note retiré", "Appels de note retirés du texte"),
    ("ligne en haut de page conservée", "Lignes conservées en haut de page (en-tête possible)"),
    ("caractère parasite", "Caractères parasites"),
    ("texte avant le premier titre de chapitre", "Texte placé avant le premier titre de chapitre"),
]
FORBIDDEN = re.compile(r"\b[\w-]+\.(?:txt|tsv|json|py)\b|À compléter par l'agent|sous-agent|build/|fixes\b|review\b", re.I)


def read(p: Path) -> str:
    return p.read_text("utf-8") if p.exists() else ""


def label(kind: str) -> str:
    for k, v in LABELS:
        if kind.startswith(k):
            return v
    return kind[:1].upper() + kind[1:]


A_VERIFIER, CORRIGE, CONSERVE = 0, 1, 2
KEEP_OK = "Conservé tel quel : vérifié sur l'image"
KEEP_UNSEEN = "Conservé tel quel : non vérifié sur l'image, à vérifier"
CHANGED_UNSEEN = "Modifié par la correction automatique : forme finale à vérifier"
UNREADABLE = "Laissé tel quel : illisible sur l'image, à vérifier"


def bare(w: str) -> str:
    """Le mot sans sa ponctuation, casse conservée."""
    return re.sub(r"[’‘ʼ`´]", "'", w).strip(" .,;:!?«»“”\"()")


def core(w: str) -> str:
    """Forme de comparaison sans l'élision (« d'Untel » et « Untel » sont le même mot)."""
    return re.sub(r"^(?:[dlnmstcj]|qu)'", "", norm_word(w))


def decision(ocr: str, verdict: tuple | None, final: str | None, in_text) -> tuple[int, str]:
    """(catégorie, décision) pour un mot signalé. La décision décrit le texte livré, pas seulement le verdict.

    verdict : (mot imprimé, verdict) de la vérification sur image, ou None. final : le mot à cet endroit de l'EPUB,
    ou None si l'endroit n'a pas pu être retrouvé."""
    form = bare(ocr)
    if verdict is None:
        if final and core(final) != core(ocr):
            return A_VERIFIER, f"Remplacé par « {final} » par la correction automatique : non vérifié sur l'image, à vérifier"
        return (A_VERIFIER, KEEP_UNSEEN) if final or in_text(form) else (A_VERIFIER, CHANGED_UNSEEN)
    printed, kind = verdict
    target = bare(printed.split(" (")[0])
    if kind.startswith("illisible"):
        return A_VERIFIER, UNREADABLE
    if final and core(final) != core(target):
        if core(final) == core(ocr):
            return A_VERIFIER, f"Forme lue conservée, alors que l'imprimé porte « {target} » : à vérifier"
        return A_VERIFIER, f"Remplacé par « {final} » dans l'EPUB, alors que l'imprimé porte « {target} » : à vérifier"
    if core(ocr) == core(target) and (kind.startswith("correct") or form == bare(printed.split(" (")[0])):
        return CONSERVE, KEEP_OK
    if final or (in_text(target) and not in_text(form)) or core(ocr) == core(target):
        return CORRIGE, f"Corrigé en « {target} » : vérifié sur l'image"
    if in_text(form):
        return A_VERIFIER, f"Forme lue conservée, alors que l'imprimé porte « {target} » : à vérifier"
    return A_VERIFIER, f"Corrigé d'après l'image (« {target} ») : forme finale à vérifier"


def locator(work: Path, data: dict):
    """Renvoie une fonction (page, mot) -> mot à cet endroit de l'EPUB, ou None si l'endroit n'est pas certain.

    L'endroit doit être unique : deux mots avant et deux mots après identiques à ceux de l'OCR."""
    raw = [t for s in data["front"] + data["chapters"] + data.get("back", [])
           for p in s["paras"] + (s.get("notes") or []) for t in plain(p).split() if re.search(r"\w", t)]
    norm = [norm_word(t) for t in raw]
    where = defaultdict(list)
    for i, t in enumerate(norm):
        where[t].append(i)
    cache: dict[str, list[str]] = {}

    def page_words(page: str) -> list[str]:
        if page not in cache:
            f = work / "ocr" / f"page-{int(page):03d}.hocr" if page.isdigit() else None
            html = read(f) if f else ""
            words = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"class=['\"]ocrx_word['\"][^>]*>(.*?)</span>", html, re.S)]
            cache[page] = [norm_word(unescape(w)) for w in words if re.search(r"\w", w)]
        return cache[page]

    def final(page: str, word: str) -> str | None:
        ws, w = page_words(page), norm_word(word)
        found = set()
        for i in range(2, len(ws) - 2):
            if ws[i] == w:
                for pos in where.get(ws[i - 1], []):
                    if 0 < pos and pos + 3 < len(norm) and norm[pos - 1] == ws[i - 2] \
                            and norm[pos + 2] == ws[i + 1] and norm[pos + 3] == ws[i + 2]:
                        found.add(bare(raw[pos + 1]))
        return found.pop() if len(found) == 1 else None

    return final


def word_table(work: Path, data: dict, remarks: list) -> list[str]:
    """Annexe des mots signalés : une ligne par mot, avec toutes ses pages et la décision prise pour l'EPUB.

    Un verdict sur un mot inconnu vaut pour tout le livre (c'est le dictionnaire qui l'ignore) ; pour un mot douteux,
    il vaut à sa page (c'est la lecture de cette page qui était incertaine), sauf si le mot a disparu du texte."""
    text = re.sub(r"[’‘ʼ`´]", "'", plain(json.dumps(data, ensure_ascii=False)))

    def in_text(form: str) -> bool:
        return bool(form) and re.search(r"(?<!\w)" + re.escape(form) + r"(?![\w'])", text) is not None   # « s'Untel » contient « Untel »

    final = locator(work, data)
    by_page, by_word = {}, {}
    for page, ocr, printed, kind in iter_prop_mots(work / "build" / "relecture"):
        w = norm_word(ocr)
        by_page[(page, w)] = (printed, kind.lower())
        by_word.setdefault(w, (printed, kind.lower()))
    entries: dict[str, dict] = {}
    for page, kind, word in remarks:
        w = norm_word(word)
        e = entries.setdefault(w, dict(word=bare(word), pages=set(), kinds=set(), by=defaultdict(set)))
        e["pages"].add(page)
        e["kinds"].add("césure" if kind.startswith("césure") else kind.removeprefix("mot "))
        v = next((by_page[(p, w)] for p in near_pages(page) if (p, w) in by_page), None)
        if v is None and w in by_word and (kind.endswith("mot inconnu") or not in_text(bare(word))):
            v = by_word[w]
        e["by"][decision(word, v, final(page, word), in_text)].add(page)

    def pages(s):
        return ", ".join(sorted(s, key=lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p)))

    rows = []
    for e in entries.values():
        items = sorted(e["by"].items())
        text_dec = items[0][0][1] if len(items) == 1 else " ; ".join(f"{d} (p. {pages(s)})" for (_, d), s in items)
        rows.append((items[0][0][0], e["word"].lower(), e["word"], pages(e["pages"]), ", ".join(sorted(e["kinds"])), text_dec))
    rows.sort(key=lambda r: r[:2])
    todo = sum(1 for r in rows if r[0] == A_VERIFIER)
    return [f"### Mots inconnus ou douteux ({len(rows)} mots distincts, {len(remarks)} signalements)", "",
            f"Une ligne par mot, avec toutes ses pages et la décision prise pour l'EPUB. {todo} mot(s) restent à vérifier : "
            "ils sont en tête de tableau.", ""] + table([r[2:] for r in rows], ["Mot lu", "Pages", "Signalé comme", "Décision pour l'EPUB"]) + [""]


def table(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |" for r in rows]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    work = Path(ap.parse_args().work)
    cfg = json.loads(read(work / "book.json") or "{}")
    data = json.loads(read(work / "build" / "content.json") or "{}")
    rj = work / "rapport.json"
    if not rj.exists():
        sys.exit("rapport.json absent : l'agent doit le rédiger (références/rapport-humain.md) avant de générer le rapport.")
    rap = json.loads(rj.read_text("utf-8"))
    for key in ("resume", "decisions", "points", "limites", "relecture"):
        if not rap.get(key):
            sys.exit(f"rapport.json : la clé « {key} » est vide ou absente ; le rapport serait incomplet.")

    title = cfg.get("title") or work.name
    pdf = Path(cfg.get("pdf", "")).name
    epub = pick_epub(work, cfg)
    if epub is None:
        sys.exit("aucun EPUB dans build/ : le rapport se contrôle sur l'EPUB livré, construire le livre d'abord.")
    content = work / "build" / "content.json"
    if content.exists() and content.stat().st_mtime > epub.stat().st_mtime:
        sys.exit("le texte a changé depuis la construction de l'EPUB : relancer run.py --from build, puis ce script.")
    problems, checked = audit(rap, Epub(epub))
    if problems:
        sys.exit(f"Le rapport contredit l'EPUB ({len(problems)}) :\n  " + "\n  ".join(problems)
                 + "\nCorrige rapport.json, ou le livre si c'est lui qui se trompe. Un nombre qui ne désigne pas le total "
                   "du livre (« 96 notes sorties du texte ») se reformule (« 96 des notes »).")
    off = rap.get("decalage_pages")
    out = [f"# Rapport de relecture : {title}" + (f" ({cfg['author']})" if cfg.get("author") else ""), ""]
    out += ["Ce rapport est destiné à la personne qui validera l'EPUB. Il dit ce qui a été fait, les décisions "
            "prises et tout ce qu'il reste à vérifier à la main. "
            + ("Les numéros de page « p. » sont ceux du PDF d'origine"
               + (f" (page imprimée = page du PDF − {off})." if off else ".")), ""]

    out += ["## 1. Le livre et le fichier livré", ""]
    rows = [("Titre", title), ("Sous-titre", cfg.get("subtitle", "")), ("Titre original", cfg.get("original_title", "")),
            ("Auteur", cfg.get("author", "")), ("Traducteur", cfg.get("translator", "")),
            ("Éditeur", cfg.get("publisher", "")), ("Année", cfg.get("published", "")),
            ("Langue", cfg.get("language", "")), ("PDF d'origine", pdf), ("EPUB livré", epub.name if epub else "")]
    out += table([r for r in rows if r[1]], ["", ""]) + [""]

    out += ["## 2. Résumé", "", rap["resume"], ""]

    out += ["## 3. Contrôles effectués", ""]
    out += [f"- {c}" for c in rap.get("controles", [])]
    out += [f"- Cohérence du rapport : {checked} décomptes et citations comparés au texte de l'EPUB livré, sans contradiction"]
    out += ["", "**Relecture du texte.** " + rap["relecture"], ""]

    out += ["## 4. Décisions prises", "",
            "Chaque décision de structure, d'exclusion ou de correction qui engage le résultat final.", ""]
    out += table([(d.get("sujet", ""), d.get("decision", ""), d.get("pourquoi", "")) for d in rap["decisions"]],
                 ["Sujet", "Décision", "Pourquoi"]) + [""]

    out += ["## 5. Structure de l'EPUB", ""]
    for kind, key in (("Avant le corps", "front"), ("Corps du livre", "chapters"), ("Après le corps", "back")):
        items = data.get(key, [])
        if not items:
            continue
        out += [f"**{kind}**", ""]
        parts = sorted(cfg.get("parts") or [], key=lambda p: p["page"]) if key == "chapters" else []
        rows = []
        for c in items:
            while parts and c.get("page", 0) >= parts[0]["page"]:
                p = parts.pop(0)
                name = " ".join(x for x in (p.get("number", ""), p["title"]) if x)
                rows.append(("", f"**Partie {name}** (page de titre)", p["page"], ""))
            rows.append(((c.get("number") or "") if key == "chapters" else "", c.get("title") or "(sans titre)",
                         c.get("page", ""), len(c.get("paras", []))))
        out += table(rows, ["N°", "Titre", "Page du PDF", "Paragraphes"]) + [""]
    figures = [(f["page"], s.get("title") or s["id"], f["after"] + 1, "…" + plain(s["paras"][f["after"]])[-60:])
               for s in data.get("front", []) + data.get("chapters", []) + data.get("back", []) for f in s.get("figures") or []]
    if figures:
        out += ["**Illustrations** (chacune à la fin du paragraphe indiqué)", ""]
        out += table(figures, ["Page du PDF", "Section", "Paragraphe", "Fin du paragraphe"]) + [""]
    out += ["À comparer avec la table des matières imprimée : nombre de chapitres, titres, ordre.", ""]

    out += ["## 6. Points à vérifier à la main", "",
            "Liste complète. Chaque point indique où regarder, ce qui a été constaté, ce qui a été fait et ce qui "
            "reste à trancher.", ""]
    for p in rap["points"]:
        line = f"- [ ] **{p.get('ou', '')}** — {p.get('constat', '')}"
        if p.get("traite"):
            line += f" *Traitement :* {p['traite']}"
        if p.get("a_verifier"):
            line += f" *À vérifier :* {p['a_verifier']}"
        out.append(line)
    qa = [l.strip() for l in read(work / "build" / "qa.txt").splitlines() if l.strip()]
    if qa:
        out += ["", f"**Incohérences relevées dans le texte par le contrôle automatique ({len(qa)})** : "
                "folios ou en-têtes oubliés, paragraphes coupés, césures non recollées.", ""]
        out += [f"- [ ] {l}" for l in qa]
    out.append("")

    out += ["## 7. Limites connues", ""] + [f"- {l}" for l in rap["limites"]] + [""]

    out += ["## 8. Validation humaine", "",
            "- [ ] Table des matières comparée à celle du livre imprimé",
            "- [ ] Tous les points de la section 6 vérifiés",
            "- [ ] Lecture par sondage d'au moins quelques chapitres complets (indiquer lesquels) : …",
            "- [ ] Appartenance des notes, italiques et couverture contrôlés dans une liseuse",
            "- [ ] EPUB accepté", ""]

    groups, remarks = defaultdict(list), []
    for l in read(work / "build" / "review.txt").splitlines():
        r = parse_remark(l)
        if not r:
            continue
        page, rest, kind, word = r
        if word:
            remarks.append((page, kind, word))
        else:
            groups[label(kind)].append(f"p. {page} — {rest}")
    out += ["## Annexe A. Remarques du contrôle orthographique automatique", "",
            "Le contrôle ne connaît que le dictionnaire : une grande partie de ces remarques sont des noms propres ou "
            "des mots étrangers corrects.", ""]
    if remarks:
        out += word_table(work, data, remarks)
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out += [f"### {name} ({len(items)})", ""] + [f"- {x}" for x in items] + [""]
    if rap.get("reproduction"):
        out += ["## Annexe B. Pour reconstruire l'EPUB (technique)", "", rap["reproduction"], ""]

    text = "\n".join(out)
    # les remarques de l'annexe A citent le texte du livre : seules les sections rédigées sont contrôlées
    head = text.split("## Annexe A.")[0]
    bad = sorted(set(m.group(0) for m in FORBIDDEN.finditer(head)))
    if bad:
        sys.exit("Le rapport cite des fichiers de travail ou des termes pour l'agent : " + ", ".join(bad)
                 + "\nReformule dans rapport.json en langage clair (ni nom de fichier, ni « sous-agent »).")
    target = work / "build" / "rapport-relecture.md"
    target.write_text(text, "utf-8")
    (work / "build" / "rapport-relecture.epub.sha256").write_text(digest(epub), "utf-8")
    print("rapport écrit :", target)


if __name__ == "__main__":
    main()
