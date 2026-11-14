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
from pathlib import Path

from coherence import QUOTE, Epub, audit, digest, normalize
from common import (all_paras, book_text, decision_key, hocr_words, iter_prop_mots, near_pages, norm_word, parse_remark,
                    pick_epub, plain, read_decisions, sections, straight_apos)

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
UNREADABLE = "Laissé tel quel : le relecteur n'a pas pu lire le mot sur la page, à vérifier"


def bare(w: str) -> str:
    """Le mot sans sa ponctuation, casse conservée."""
    return straight_apos(w).strip(" .,;:!?«»“”\"()")


def word_rx(form: str) -> str:
    """Regex du mot entier dans le texte livré (« s'Untel » contient « Untel », « Untel's » non)."""
    return r"(?<!\w)" + re.escape(form) + r"(?![\w'])"


def core(w: str) -> str:
    """Forme de comparaison sans l'élision (« d'Untel » et « Untel » sont le même mot)."""
    return re.sub(r"^(?:[dlnmstcj]|qu)'", "", norm_word(w))


def decision(ocr: str, verdict: tuple | None, final: str | None, in_text, chosen: tuple | None = None,
             joined: bool = False) -> dict:
    """Décision pour un mot signalé, d'après le texte livré et pas seulement d'après le verdict.

    verdict : (mot imprimé, verdict) de la vérification sur image, ou None. final : le mot à cet endroit de l'EPUB,
    ou None si l'endroit n'a pas pu être retrouvé. chosen : (forme retenue, raison, …) de decisions.tsv, ou None.
    Renvoie cat, texte, imprime (forme vue sur l'image), epub (forme livrée), attendu, bloquant (livraison refusée),
    verifie (un relecteur ou une décision s'est prononcé sur ce mot)."""
    form = bare(ocr)

    def res(cat, texte, imprime="", attendu="", bloquant=False):
        epub = final or (in_text(attendu) if attendu and in_text(attendu) and not in_text(form) else in_text(form))
        return dict(cat=cat, texte=texte, imprime=imprime, epub=epub, attendu=attendu, bloquant=bloquant,
                    verifie=bool(chosen) or verdict is not None)

    def applied(target):
        if final:
            return core(final) == core(target)
        return in_text(target) and (not in_text(form) or core(form) == core(target))

    seen = ""
    if verdict and not verdict[1].startswith(("illisible", "coquille")):
        seen = bare(verdict[0].split(" (")[0])
    # morceau d'un mot coupé en fin de ligne : jugé identique sur l'image, mais l'EPUB porte le mot entier, recollé
    piece = re.sub(r"^\W+|\W+$", "", core(chosen[0] if chosen else seen))
    if joined and final and piece and piece != core(final) and core(final).startswith(piece) | core(final).endswith(piece):
        return res(CORRIGE, f"Morceau d'un mot coupé en fin de ligne : recollé, l'EPUB contient le mot entier « {final} »", seen)
    if chosen:
        target, why = bare(chosen[0]), chosen[1]
        if core(target) == core(ocr):
            return res(CONSERVE, f"Conservé tel quel, décision motivée : {why}", seen)
        if applied(target):
            return res(CORRIGE, f"Corrigé en « {target} », décision motivée : {why}", seen, target)
        return res(A_VERIFIER, f"NON CORRIGÉ : la correction décidée « {target} » n'est pas dans l'EPUB", seen, target, True)
    if verdict is None:
        return res(A_VERIFIER, "NON VÉRIFIÉ : aucun relecteur n'a lu ce mot sur la page", bloquant=True)
    printed, kind = verdict
    if kind.startswith("illisible"):
        return res(A_VERIFIER, UNREADABLE)
    target = bare(printed.split(" (")[0])
    if kind.startswith("coquille"):
        if applied(target):
            return res(CORRIGE, f"Coquille du livre imprimé, corrigée en « {target} » (écart volontaire au PDF)", form, target)
        return res(A_VERIFIER, f"NON CORRIGÉ : coquille du livre imprimé, à corriger en « {target} »", form, target, True)
    if core(ocr) == core(target):
        if final and core(final) != core(target):
            return res(A_VERIFIER, f"NON CORRIGÉ : le relecteur a lu « {target} » sur la page, l'EPUB contient « {final} »", target, target, True)
        return res(CONSERVE, "Conservé : le relecteur a lu le même mot sur la page", target)
    if applied(target):
        return res(CORRIGE, f"Corrigé en « {target} », lu par le relecteur sur la page", target, target)
    if final or in_text(form):
        return res(A_VERIFIER, f"NON CORRIGÉ : le relecteur a lu « {target} » sur la page, l'EPUB contient « {final or in_text(form)} »", target, target, True)
    return res(A_VERIFIER, f"Corrigé d'après le relecteur (« {target} ») : forme finale à vérifier", target, target)


def locator(work: Path, data: dict):
    """Renvoie une fonction (page, mot) -> (mot à cet endroit de l'EPUB, extrait de l'EPUB, extrait lu par l'OCR,
    recollé).

    Le mot et l'extrait de l'EPUB valent None si l'endroit n'est pas certain : il doit être unique, deux mots avant
    et deux mots après identiques à ceux de l'OCR. Dans les extraits, le mot est en gras. recollé : le mot lu n'est
    qu'un morceau (coupure de fin de ligne) du mot de l'EPUB."""
    raw = [t for p in all_paras(data) for t in plain(p).split() if re.search(r"\w", t)]
    norm = [norm_word(t) for t in raw]
    where = defaultdict(list)
    for i, t in enumerate(norm):
        where[t].append(i)
    cache: dict[str, tuple[list[str], list[str]]] = {}

    def page_words(page: str):
        if page not in cache:
            words = hocr_words(work / "ocr" / f"page-{int(page):03d}.hocr") if page.isdigit() else []
            words = [w for w in words if re.search(r"\w", w)]
            cache[page] = (words, [norm_word(w) for w in words])
        return cache[page]

    def cut(words, i, n=6):
        return " ".join(words[max(0, i - n):i] + [f"**{words[i]}**"] + words[i + 1:i + 1 + n])

    def edge(page: str, tail: bool) -> list[str]:
        """Quatre mots de la page voisine, du côté commun, sans le folio : contexte d'un mot en bord de page."""
        if not page.isdigit():
            return []
        ws = [x for x in page_words(str(int(page) + (-1 if tail else 1)))[1] if not x.isdigit()]
        return ws[-4:] if tail else ws[:4]

    def find(page: str, word: str):
        w = norm_word(word)
        found, lu = {}, None
        for pg in near_pages(page):
            seen, cur = page_words(pg)
            before = edge(pg, True)
            ws = before + cur + edge(pg, False)
            for i in range(len(before), len(before) + len(cur)):
                if ws[i] != w:
                    continue
                lu = lu or cut(seen, i - len(before))
                # mot coupé en fin de ligne : le morceau d'avant ou d'après appartient au même mot de l'EPUB, le
                # contexte se prend alors un mot plus loin
                for l in [i - 1] + ([i - 2] if i and ws[i - 1].endswith("-") else []):
                    for r in [i + 1] + ([i + 2] if w.endswith("-") else []):
                        if l < 1 or r + 1 >= len(ws):
                            continue
                        for pos in where.get(ws[l], []):
                            if 0 < pos and pos + 3 < len(norm) and norm[pos - 1] == ws[l - 1] \
                                    and norm[pos + 2] == ws[r] and norm[pos + 3] == ws[r + 1]:
                                found[bare(raw[pos + 1])] = (cut(raw, pos + 1), (l, r) != (i - 1, i + 1))
            if lu:
                break
        if len(found) == 1:
            (final, (extrait, joined)), = found.items()
            return final, extrait, lu, joined
        return None, None, lu, False

    return find


def snippet(text: str, form: str, width: int = 45) -> str | None:
    """Extrait du texte livré autour de la première occurrence de `form` (mot entier), le mot en gras."""
    m = re.search(word_rx(form), text) if form else None
    if not m:
        return None
    a = text.rfind("\n", 0, m.start()) + 1
    b = text.find("\n", m.end())
    b = len(text) if b < 0 else b
    left, right = text[max(a, m.start() - width):m.start()], text[m.end():min(b, m.end() + width)]
    left = left.split(" ", 1)[-1] if m.start() - width > a and " " in left else left
    right = right.rsplit(" ", 1)[0] if m.end() + width < b and " " in right else right
    return f"{left}**{m.group(0)}**{right}".strip()


PAGES = re.compile(r"\bp\.\s*\d+(?:\s*(?:[–-]|à|et|,)\s*(?:p\.\s*)?\d+)*")


def cited_pages(s: str) -> list[str]:
    """Pages citées dans un texte (« p. 18–19 », « p. 14, 48 et 85 ») ; un intervalle court est déroulé."""
    out = []
    for m in PAGES.finditer(s):
        for a, b in re.findall(r"(\d+)(?:\s*(?:[–-]|à)\s*(\d+))?", m[0]):
            a, b = int(a), int(b or a)
            out += [str(p) for p in (range(a, b + 1) if 0 <= b - a <= 20 else (a, b))]
    return list(dict.fromkeys(out))


def cited_extracts(item: dict, fields: tuple, find, text: str) -> list[str]:
    """Extraits de l'EPUB à imprimer avec une décision ou un point : ceux de « dans_epub », puis un extrait trouvé par
    le script pour chaque mot cité entre « » qu'aucun extrait ne contient déjà. Le mot cité est en gras.

    Le mot est cherché aux pages citées (find : locator(), qui retrouve aussi la forme corrigée d'un mot mal lu), sinon
    à sa première occurrence dans le livre. Une citation longue est elle-même un extrait. « dans_epub » vide : les
    citations ne sont pas du texte du livre, aucun extrait n'est cherché."""
    given = item.get("dans_epub")
    if given == []:
        return []
    body = " ".join(str(item.get(k, "")) for k in fields)
    quotes = list(dict.fromkeys(straight_apos(q) for q in QUOTE.findall(body)))
    words = [q for q in quotes if len(q.split()) <= 4 and re.search(r"\w", q)]     # « * * * » n'est pas un mot
    seen = [normalize(q) for q in quotes if len(q.split()) > 4] + [normalize(q) for q in given or []]
    out = []
    if words and given:
        rx = "|".join(r"(?<!\w)" + re.escape(w).replace("'", "['’]") + r"(?![\w'’])"
                      for w in sorted(words, key=len, reverse=True))
        out = [f"« {q if normalize(q) in map(normalize, words) else re.sub(rx, lambda m: f'**{m[0]}**', q)} »"
               for q in given]
    else:
        out = [f"« {q} »" for q in given or []]
    pages = cited_pages(body)
    for w in words:
        if any(normalize(w) in x for x in seen):
            continue
        final, extrait, page = w, None, ""
        if " " not in w:
            for page in pages:
                final, extrait, *_ = find(page, w)
                if extrait:
                    break
        if not extrait:
            final, page = w, ""
            extrait = snippet(text, w) or snippet(text, w[:1].upper() + w[1:])
        if extrait and not any(normalize(final) in x for x in seen):
            seen.append(normalize(extrait.replace("**", "")))
            out.append((f"p. {page} : " if page else "") + f"« … {extrait} … »")
    return out


def load_remarks(work: Path):
    """review.txt -> (autres remarques groupées par genre, [(page, genre, mot)] des mots inconnus ou douteux)."""
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
    return groups, remarks


def word_rows(work: Path, data: dict, remarks: list, text: str | None = None) -> list[dict]:
    """Une ligne par mot signalé et par décision : lu, imprimé, EPUB, décision, pages, extraits.

    Un verdict sur un mot inconnu vaut pour tout le livre (c'est le dictionnaire qui l'ignore) ; pour un mot douteux,
    il vaut à sa page (c'est la lecture de cette page qui était incertaine), sauf si le mot a disparu du texte.
    text : book_text(data), s'il est déjà calculé."""
    if text is None:
        text = book_text(data)
    found: dict[str, str] = {}                       # une recherche sur tout le livre par forme, pas par remarque

    def in_text(form: str) -> str:
        """La forme telle qu'elle est dans le texte livré, « » si elle n'y est pas. La majuscule de début de phrase
        est admise : le relecteur écrit « faites-les », le texte porte « Faites-les »."""
        if form not in found:
            # dict.fromkeys : une forme qui commence déjà par une majuscule n'est cherchée qu'une fois
            found[form] = next((f for f in dict.fromkeys((form, form[:1].upper() + form[1:]))
                                if f and re.search(word_rx(f), text)), "")
        return found[form]

    find = locator(work, data)
    decisions = read_decisions(work)
    by_page, by_word = {}, {}
    for page, ocr, printed, kind in iter_prop_mots(work / "build" / "relecture"):
        w = norm_word(ocr)
        by_page[(page, w)] = (printed, kind.lower())
        by_word.setdefault(w, (printed, kind.lower()))
    rows: dict[tuple, dict] = {}
    for page, kind, word in remarks:
        w = norm_word(word)
        v = next((by_page[(p, w)] for p in near_pages(page) if (p, w) in by_page), None)
        if v is None and w in by_word and (kind.endswith("mot inconnu") or not in_text(bare(word))):
            v = by_word[w]
        final, extrait, lu, joined = find(page, word)
        d = decision(word, v, final, in_text, decisions.get(decision_key(word)), joined)
        r = rows.setdefault((w, d["texte"]), dict(d, mot=bare(word), pages=set(), extraits=[]))
        r["pages"].add(page)
        if len(r["extraits"]) < 2:
            ex = extrait or snippet(text, d["epub"]) or (f"(texte lu) {lu}" if lu else None)
            if ex and all(ex != x[1] for x in r["extraits"]):
                r["extraits"].append((page, ex))
    return sorted(rows.values(), key=lambda r: (not r["bloquant"], r["cat"], r["mot"].lower()))


def sorted_pages(s) -> list[str]:
    return sorted(s, key=lambda p: (not p.isdigit(), int(p) if p.isdigit() else 0, p))


def pages_text(s) -> str:
    return ", ".join(sorted_pages(s))


def word_table(rows: list[dict], n_remarks: int) -> list[str]:
    todo = sum(1 for r in rows if r["cat"] == A_VERIFIER)
    cells = [(r["mot"], r["imprime"] or "—", r["epub"] or "?", r["texte"], pages_text(r["pages"]),
              "<br>".join(f"p. {p} : … {x} …" for p, x in r["extraits"]) or "—") for r in rows]
    return [f"### Mots inconnus ou douteux ({len(rows)} lignes, {n_remarks} signalements)", "",
            "Une ligne par mot : ce que l'OCR a lu, ce que le relecteur a lu sur la page du PDF, ce que contient l'EPUB, la décision "
            f"prise, les pages du PDF et un extrait pour retrouver le mot (en gras). {todo} mot(s) restent à vérifier : "
            "ils sont en tête de tableau.", ""] + table(
        cells, ["Lu par l'OCR", "Lu par le relecteur", "Dans l'EPUB", "Décision", "Pages", "Extrait"]) + [""]


def applied_table(work: Path) -> list[str]:
    """Annexe : toutes les corrections de lecture appliquées (build/corrections.json, écrit par l'extraction)."""
    out = ["## Annexe B. Corrections appliquées au texte lu", ""]
    f = work / "build" / "corrections.json"
    if not f.exists():
        return out + ["Liste indisponible : le texte a été extrait avant que les corrections ne soient tracées.", ""]
    log = json.loads(f.read_text("utf-8"))
    groups: dict[tuple, dict] = {}
    for page, lu, new, extrait, *pos in log:
        key = (lu.strip(), new.strip())
        if key not in groups:
            # pos : place de la correction dans l'extrait (un corrections.json plus ancien ne la donne pas). Sans elle,
            # mettre en gras la première occurrence marquerait « f**il**le » pour une correction en « il ».
            a = pos[0] if pos else extrait.find(new)
            b = a + len(new)
            if key[1] and a >= 0:
                extrait = extrait[:a] + extrait[a:b].replace(key[1], f"**{key[1]}**", 1) + extrait[b:]
            groups[key] = dict(n=0, pages=set(), extrait=extrait.strip())
        g = groups[key]
        g["n"] += 1
        g["pages"].add(str(page))
    out += [f"{len(log)} corrections, {len(groups)} formes distinctes. Chaque ligne : ce que l'OCR avait lu, ce qui a été "
            "mis à la place, le nombre de fois, les pages du PDF et un extrait après correction (en gras).", ""]
    rows = []
    for (lu, new), g in sorted(groups.items(), key=lambda kv: kv[0][0].lower()):
        pages = sorted_pages(g["pages"])
        rows.append((lu or "(vide)", new or "(supprimé)", g["n"], ", ".join(pages[:12]) + (" …" if len(pages) > 12 else ""),
                     f"… {g['extrait']} …"))
    return out + table(rows, ["Lu par l'OCR", "Corrigé en", "Fois", "Pages", "Extrait corrigé"]) + [""]


def refused_table(items: list[dict], text: str) -> list[str]:
    """Annexe : propositions des relecteurs (analyse()) non appliquées alors que la forme lue est toujours dans le
    texte livré (book_text)."""
    from propositions_vers_regles import spans
    rows, seen = [], set()
    for e in items:
        # « identique » : raison de check() (simple style d'apostrophe ou d'espace), pas un mot d'une raison de décision
        if e["etat"] not in ("ecarte", "incertain", "decide") or e["mot"] \
                or (e["etat"] == "ecarte" and (e["raison"] or "").startswith("identique")):
            continue
        key = (e["lu"], e["propose"])
        if key in seen or not (m := spans(e["lu"], text, 1)):
            continue
        seen.add(key)
        (a, b), = m
        ex = (text[max(0, a - 40):a] + f"**{text[a:b]}**" + text[b:b + 40]).replace("\n", " / ")
        rows.append((e["ou"], e["lu"], e["propose"], e["raison"], f"… {ex} …"))
    out = ["## Annexe C. Propositions de relecture non appliquées", "",
           "Corrections proposées par la relecture automatique et non appliquées : le texte lu est resté tel quel. "
           "À parcourir : certaines sont peut-être justes.", ""]
    if not rows:
        return out + ["Aucune.", ""]
    return out + table(rows, ["Où", "Texte lu (conservé)", "Proposition", "Pourquoi non appliquée", "Extrait"]) + [""]


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
    if not content.exists():
        sys.exit("build/content.json absent : lancer run.py d'abord.")
    from corrections_restantes import restantes, signaler
    from propositions_vers_regles import analyse
    text = book_text(data)
    groups, remarks = load_remarks(work)
    # calculés une fois : contrôle ci-dessous, puis annexes A et C (noms propres à cet usage : `rows` et `items`
    # servent plus bas aux tableaux des sections 1 et 5)
    mots = word_rows(work, data, remarks, text)
    props = analyse(work, text)
    todo = restantes(work, data, text, mots, props)
    if todo:
        signaler(work, todo)
        sys.exit(f"Rapport refusé : {len(todo)} correction(s) vérifiée(s) ne sont pas dans le texte (liste ci-dessus et "
                 "build/relecture/a-corriger.tsv). Traite-les toutes (references/relecture.md, § 3), relance "
                 "run.py --from extract, puis ce script.")
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
    find = locator(work, data)
    quoted = []                                      # extraits imprimés : texte du livre, hors du contrôle FORBIDDEN
    rows = []
    for d in rap["decisions"]:
        ex = cited_extracts(d, ("decision", "pourquoi"), find, text)
        quoted += ex
        rows.append((d.get("sujet", ""), d.get("decision", ""), d.get("pourquoi", ""), "<br>".join(ex) or "—"))
    out += table(rows, ["Sujet", "Décision", "Pourquoi", "Extrait de l'EPUB"]) + [""]

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
               for s in sections(data) for f in s.get("figures") or []]
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
        ex = cited_extracts(p, ("ou", "constat", "traite", "a_verifier"), find, text)
        quoted += ex
        if ex:
            line += " *Extrait de l'EPUB :* " + " ; ".join(ex)
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

    out += ["## Annexe A. Remarques du contrôle orthographique automatique", "",
            "Le contrôle ne connaît que le dictionnaire : une grande partie de ces remarques sont des noms propres ou "
            "des mots étrangers corrects.", ""]
    if remarks:
        out += word_table(mots, len(remarks))
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        out += [f"### {name} ({len(items)})", ""] + [f"- {x}" for x in items] + [""]
    out += applied_table(work) + refused_table(props, text)
    if rap.get("reproduction"):
        out += ["## Annexe D. Pour reconstruire l'EPUB (technique)", "", rap["reproduction"], ""]

    report = "\n".join(out)
    # les remarques de l'annexe A citent le texte du livre : seules les sections rédigées sont contrôlées
    head = report.split("## Annexe A.")[0]
    for x in quoted:
        head = head.replace(x.replace("|", "/"), "")
    bad = sorted(set(m.group(0) for m in FORBIDDEN.finditer(head)))
    if bad:
        sys.exit("Le rapport cite des fichiers de travail ou des termes pour l'agent : " + ", ".join(bad)
                 + "\nReformule dans rapport.json en langage clair (ni nom de fichier, ni « sous-agent »).")
    target = work / "build" / "rapport-relecture.md"
    target.write_text(report, "utf-8")
    (work / "build" / "rapport-relecture.epub.sha256").write_text(digest(epub), "utf-8")
    print("rapport écrit :", target)


if __name__ == "__main__":
    main()
