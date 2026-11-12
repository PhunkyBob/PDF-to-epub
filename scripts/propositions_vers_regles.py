"""Transforme les propositions des sous-agents de relecture en règles de correction, et les verse dans fixes.tsv.

Lit WORK/build/relecture/prop-lot*.tsv     (id <TAB> fragment erroné <TAB> fragment corrigé <TAB> confiance),
                         prop-lettrines.tsv (page <TAB> début OCR <TAB> début imprimé <TAB> identique oui/non)
et                       prop-mots*.tsv     (page <TAB> mot OCR <TAB> mot imprimé ou corrigé <TAB> verdict),
ainsi que WORK/decisions.tsv (forme lue <TAB> forme retenue <TAB> raison : arbitrages et refus de l'orchestrateur).

Écrit WORK/build/relecture/regles-candidates.tsv (même format que fixes.tsv) et regles-ecartees.txt (propositions
non transformées en règle, avec la raison). Avec --appliquer, recopie aussi les règles dans WORK/fixes.tsv, dans une
section délimitée qui est réécrite à chaque lancement ; les règles écrites à la main, hors de cette section, ne sont
jamais touchées et passent avant.

Par défaut une proposition devient une règle. Elle n'est écartée que dans ces cas, décidés par le script :
  * confiance « basse » ; fragment identique ou trop court (< 3 caractères) ;
  * reformulation : des lettres ajoutées ou retirées avec un nombre de mots différent, ou un mot remplacé par un mot
    sans rapport (un mot collé ou coupé, « sielle » -> « si elle », n'est pas une reformulation) ;
  * appel de note (« Marie8 » -> « Marie ») ; accent ajouté sur une capitale (« A » -> « À ») ; apostrophe doublée ;
  * fragment absent du texte (déjà corrigé) ;
  * fragment présent plusieurs fois alors que le mot erroné est un vrai mot du dictionnaire (« la met » -> « la mer »
    casserait les autres « la met ») : règle à écrire à la main avec plus de contexte ;
  * deux relecteurs proposent deux corrections différentes pour le même mot : à trancher dans decisions.tsv.
corrections_restantes.py liste ensuite ce qui n'est pas passé dans le texte.
"""
import argparse
import json
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path

from common import (EDGE_PUNCT, SKILL_DIR, WORD_END, WORD_START, book_text, decision_key, fix_rules, iter_prop_mots,
                    lang_code, read_decisions, read_shared, write_shared)

APOS = "['’‘]"
BEGIN = "# >>> RÈGLES AUTOMATIQUES (propositions_vers_regles.py) : section réécrite à chaque lancement, ne rien y écrire"
END = "# <<< FIN DES RÈGLES AUTOMATIQUES"
CONTEXTE = "# CONTEXTE "         # dans la section : proposition classée « contexte », voir auto_section()


def norm(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'").replace(" ", " ").replace(" ", " ").strip()


def to_regex(frag: str) -> str:
    frag = norm(frag)
    rx = "".join(APOS if ch == "'" else r"\s+" if ch == " " else re.escape(ch) for ch in frag)
    if re.match(r"\w", frag[:1]):
        rx = WORD_START + rx
    if re.match(r"\w", frag[-1:]):
        rx += WORD_END
    return rx


def spans(frag: str, text: str, limit: int = 0) -> list[tuple[int, int]]:
    """(début, fin) des occurrences du fragment dans le texte : celles de re.finditer(to_regex(frag), text), au plus
    `limit` si donné. La borne de début est vérifiée ici et non par la regex : commencer par une assertion interdit
    au moteur de sauter d'une occurrence du premier caractère à la suivante, et coûte vingt fois plus sur un livre."""
    rx = to_regex(frag)
    bounded = rx.startswith(WORD_START)
    pat = re.compile(rx.removeprefix(WORD_START))
    out, pos = [], 0
    while (not limit or len(out) < limit) and (m := pat.search(text, pos)):
        if bounded and m.start() and re.match(r"[\w'’]", text[m.start() - 1]):
            pos = m.start() + 1
            continue
        out.append(m.span())
        pos = max(m.end(), m.start() + 1)
    return out


def repl(s: str) -> str:
    return s.replace("\\", "\\\\")


def to_rule(bad: str, good: str) -> tuple[str, str]:
    """Règle de fixes.tsv. À nombre de mots égal, ce qui se trouve entre les mots et devant leur ponctuation est
    capturé et remis tel quel (espace insécable, marqueur d'italique, appel de note : {I}) : seuls les mots changent."""
    b, g = norm(bad).split(), norm(good).split()
    if len(b) != len(g) or len(b) < 2:
        return to_regex(bad), repl(norm(good))

    def esc(t):
        return "".join(APOS if ch == "'" else re.escape(ch) for ch in t)

    def cut(t):                                      # « Kansai. » -> (« Kansai », « . »)
        return re.fullmatch(r"(.*?)([.,;:!?…]*)", t).groups()

    rx, rep, n = [], [], 0
    for i, (x, y) in enumerate(zip(b, g)):
        (xw, xp), (yw, yp) = cut(x), cut(y)
        n += 1
        rx.append(esc(xw) + "({I})" + esc(xp))
        rep.append(repl(yw) + rf"\g<{n}>" + repl(yp))
        if i < len(b) - 1:
            n += 1
            rx.append(r"(\s+{I})")
            rep.append(rf"\g<{n}>")
    rx = "".join(rx)
    if re.match(r"\w", b[0][:1]):
        rx = WORD_START + rx
    return rx + WORD_END, "".join(rep)


def letters(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum()).casefold()


def token_pairs(bad: str, good: str) -> list[tuple[str, str]]:
    """Mots qui changent entre les deux fragments (même nombre de mots), ou le fragment entier sinon."""
    b, g = norm(bad).split(), norm(good).split()
    if len(b) != len(g):
        return [(norm(bad), norm(good))]
    return [(x, y) for x, y in zip(b, g) if x != y]


def check(bad: str, good: str) -> str | None:
    """Raison d'écarter la proposition, ou None si elle est acceptable."""
    bad, good = norm(bad), norm(good)
    if bad == good:
        return "identique (style d'apostrophe ou d'espace)"
    if len(bad) < 3:
        return "fragment trop court"
    if re.sub(r"\d+$", "", bad) == good or re.sub(r"\d+$", "", good) == bad:
        return "appel de note (placé par la chaîne, voir note_anchors)"
    if len(bad.split()) != len(good.split()) and letters(bad) != letters(good):
        return "reformulation (mots ajoutés ou retirés)"
    if len(bad) == len(good) and all(x == y or (x.isupper() and unicodedata.normalize("NFD", y)[:1] == x)
                                     for x, y in zip(bad, good)):
        return "capitale accentuée (« A » -> « À ») : le livre n'accentue peut-être pas les capitales"
    if "''" in good:                                # norm() a déjà ramené ’ à '
        return "apostrophe doublée"
    for x, y in token_pairs(bad, good):
        if x.isalpha() and len(x) > 3 and SequenceMatcher(None, x.lower(), y.lower()).ratio() < 0.4:
            return f"mot remplacé par un mot sans rapport (« {x} » -> « {y} ») : reformulation"
    return None


@cache
def lexicon(lang: str):
    """Lexique Hunspell de la langue du livre (dict/<langue>.dic), ou None s'il n'y en a pas."""
    try:
        from spylls.hunspell import Dictionary
        return Dictionary.from_files(str(SKILL_DIR / "dict" / lang))
    except Exception:
        return None


WORD_FORM = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")      # mot, élision et trait d'union compris


def is_word(w: str, lang: str, default: bool = True) -> bool:
    """Vrai mot du dictionnaire ? Sans lexique pour cette langue, on répond `default`.

    « s'est », « qu'il », « dit-il » sont de vrais mots (le lexique les connaît) : sans eux, « s'est fait » ->
    « c'est fait » deviendrait une règle appliquée à tous les « s'est fait » du livre, et « qu'il » -> « qui » entrerait
    dans la table partagée."""
    w = w.strip(EDGE_PUNCT + "—-")
    if not WORD_FORM.fullmatch(w):
        return False
    lex = lexicon(lang)
    return default if lex is None else bool(lex.lookup(w) or lex.lookup(w.lower()))


def book_lang(work: Path) -> str:
    f = work / "book.json"
    cfg = json.loads(f.read_text("utf-8")) if f.exists() else {}
    return lang_code(cfg)


def auto_section(fixes: Path) -> tuple[set[str], dict[str, int]]:
    """Mémoire de la section automatique de fixes.tsv : regex des règles déjà écrites, et propositions déjà classées
    « contexte » (lignes « # CONTEXTE n <TAB> regex » : regex -> nombre d'occurrences du fragment à ce moment-là)."""
    rows = fixes.read_text("utf-8").splitlines() if fixes.exists() else []
    if BEGIN not in rows:
        return set(), {}
    rows = rows[rows.index(BEGIN):]
    seen = {}
    for r in rows:
        if r.startswith(CONTEXTE):
            n, _, rx = r[len(CONTEXTE):].partition("\t")
            if n.isdigit() and rx:
                seen[rx] = int(n)
    return {rx for rx, _ in fix_rules(rows)}, seen


def proposals(rel: Path):
    """(source, où, fragment lu, fragment proposé, confiance haute ?) de chaque proposition de relecture de texte."""
    def num(f):
        return int(re.sub(r"\D", "", f.stem) or 0)
    for f in sorted(rel.glob("prop-lot*.tsv"), key=num):
        for line in f.read_text("utf-8-sig", errors="replace").splitlines():
            p = [x.strip() for x in line.split("\t")]
            if len(p) >= 3 and p[1]:
                ident = p[0].strip("[]")
                # [3.42] : chapitre 3, paragraphe 42 ; [3.n1] : note 1 du chapitre 3 (export_review.py)
                where = re.sub(r"^(\d+)\.(n?)(\d+)$", lambda m: f"chap. {m[1]}, {'note' if m[2] else '§'} {m[3]}", ident)
                yield f.name, where, p[1], p[2], not (len(p) >= 4 and p[3].lower().startswith("b"))
    f = rel / "prop-lettrines.tsv"
    for line in f.read_text("utf-8-sig", errors="replace").splitlines() if f.exists() else []:
        p = [x.strip() for x in line.split("\t")]
        if len(p) >= 4 and p[3].lower().startswith("n") and p[1] and p[2]:
            yield f.name, f"p. {p[0].removeprefix('p.').strip()} (début de chapitre)", p[1], p[2], True


def analyse(work: Path, text: str | None = None) -> list[dict]:
    """Une entrée par proposition : source, ou, lu, propose, etat, raison, regle (regex, remplacement) ou None.

    etat : « regle » (devient une règle), « ecarte » (raison donnée), « incertain » (confiance basse : à voir sur
    l'image), « absent » (le fragment n'est plus dans le texte), « conflit » (deux corrections différentes pour le
    même mot : elles sont dans `candidats`), « decide » (tranché dans decisions.tsv, forme lue gardée), « contexte »
    (vrai mot, fragment présent plusieurs fois : règle à écrire à la main), « manuel » (ancien « contexte » dont une
    occurrence a été corrigée par une règle à la main : les autres sont justes, plus rien à faire).
    `contexte` (états « contexte » et « manuel ») : (regex de la règle, occurrences au premier classement), gardé
    dans la section automatique de fixes.tsv par write_fixes().
    text : le texte livré (book_text), s'il est déjà calculé."""
    rel = work / "build" / "relecture"
    content = work / "build" / "content.json"
    if text is None:
        text = book_text(json.loads(content.read_text("utf-8"))) if content.exists() else ""
    decisions = read_decisions(work)
    lang = book_lang(work)
    auto, ctx_seen = auto_section(work / "fixes.tsv")
    counts: dict[str, int] = {}

    def count(lu: str) -> int:
        """Occurrences du fragment dans le texte (une recherche sur tout le livre : faite au besoin, une fois)."""
        if not text:
            return 1
        if lu not in counts:
            counts[lu] = len(spans(lu, text))
        return counts[lu]

    out = []
    for src, where, bad, good, sure in proposals(rel):
        out.append(dict(source=src, ou=where, lu=bad, propose=good, sure=sure, mot=False))
    for page, bad, good, verdict in iter_prop_mots(rel):
        v = verdict.lower()
        if "erreur" in v or v.startswith("coquille"):
            out.append(dict(source="mots", ou=f"p. {page}", lu=bad, propose=good.split(" (")[0], sure=True, mot=True,
                            coquille=v.startswith("coquille")))

    # même mot erroné, corrections différentes -> conflit (sauf décision de l'orchestrateur)
    goods: dict[str, dict[str, str]] = {}            # clé du mot lu -> {clé de la correction: correction proposée}
    for e in out:
        e["raison"] = check(e["lu"], e["propose"])
        e["ecart"] = "ecarte"
        if not e["sure"] and not e["raison"]:                    # à voir sur l'image : corrections_restantes.py
            e["ecart"], e["raison"] = "incertain", "confiance basse : à vérifier sur l'image"
        if e["mot"] and not e["raison"] and (len(norm(e["lu"]).split()) != 1 or len(norm(e["lu"])) < 4):
            e["raison"] = "mot trop court pour une règle générale : écrire une règle avec son contexte"
        if not e["raison"]:
            for x, y in token_pairs(e["lu"], e["propose"]):
                if decision_key(x) != decision_key(y):          # ponctuation ou casse seule : pas un désaccord
                    goods.setdefault(decision_key(x), {}).setdefault(decision_key(y), y)
    places: dict[str, set[str]] = {}                 # regex -> endroits où un relecteur dit ce fragment fautif
    for e in out:
        if not e["mot"] and not e["raison"]:
            places.setdefault(to_rule(e["lu"], e["propose"])[0], set()).add(e["ou"])
    for e in out:
        e["regle"] = None
        pairs = token_pairs(e["lu"], e["propose"])
        keys = [decision_key(e["lu"])] + [decision_key(x) for x, _ in pairs]
        chosen = next((decisions[k] for k in keys if k in decisions), None)
        whole = decisions.get(keys[0])               # décision sur le fragment entier : elle l'emporte sur tout
        if whole and decision_key(whole[0]) != keys[0]:
            e["etat"], e["propose"], e["raison"] = "regle", whole[0], f"décision : {whole[1]}"
        elif whole:
            e["etat"], e["raison"] = "decide", f"forme lue gardée sur décision : {whole[1]}"
        elif e["raison"]:
            e["etat"] = e["ecart"]
        elif chosen and decision_key(chosen[0]) in keys:
            e["etat"], e["raison"] = "decide", f"forme lue gardée sur décision : {chosen[1]}"
        elif chosen and len(norm(e["lu"]).split()) == 1:
            e["etat"], e["propose"], e["raison"] = "regle", chosen[0], f"décision : {chosen[1]}"
        elif chosen:                                 # la décision porte sur un mot du fragment : sa propre règle suffit
            e["etat"], e["raison"] = "decide", f"tranché mot par mot : {chosen[1]}"
        elif any(len(goods.get(k, ())) > 1 for k in keys[1:] or keys):
            e["etat"] = "conflit"                    # corrections telles que proposées (casse gardée) : à recopier
            e["candidats"] = sorted({g for k in keys for g in goods.get(k, {}).values()})
            e["raison"] = "relecteurs en désaccord : " + " / ".join(e["candidats"])
        else:
            rx, n = to_rule(e["lu"], e["propose"])[0], count(e["lu"])
            n0 = ctx_seen.get(rx, 0)                 # occurrences quand la proposition a été classée « contexte »
            if n == 0 and rx in auto:
                e["etat"] = "regle"                  # déjà appliquée : la règle reste, sinon l'erreur reviendrait
            elif n == 0:
                e["etat"], e["raison"] = "absent", "fragment absent du texte (déjà corrigé)"
            elif n <= n0 - max(1, len(places.get(rx, ()))):
                # il reste moins d'occurrences qu'au classement : la fautive a été corrigée par une règle à la main
                # (une par endroit signalé : tant qu'il en manque, la proposition reste « contexte »).
                # Sans cette mémoire, la proposition redeviendrait une règle et casserait les occurrences justes.
                e["etat"], e["contexte"] = "manuel", (rx, n0)
                e["raison"] = f"corrigée par une règle à la main avec contexte ; {n} autre(s) occurrence(s) conservée(s)"
            elif n > 1 and all(is_word(x, lang) for x, _ in pairs):
                e["etat"], e["contexte"] = "contexte", (rx, max(n, n0))
                e["raison"] = f"vrai mot et fragment présent {n} fois : règle à écrire avec plus de contexte"
            else:
                e["etat"] = "regle"
        del e["ecart"]
        if e["etat"] == "regle":
            e["regle"] = to_rule(e["lu"], e["propose"])
    # décisions de l'orchestrateur sur un mot sans proposition exploitable : règle directe
    seen = {decision_key(e["lu"]) for e in out if e["etat"] == "regle"}
    first_lu: dict[str, str] = {}
    for e in out:
        first_lu.setdefault(decision_key(e["lu"]), e["lu"])
    for k, (kept, why, typed) in decisions.items():
        if k not in seen and decision_key(kept) != k and len(k) >= 3:
            # la forme telle qu'elle est écrite, pas la clé : la clé est en minuscules et la règle ne trouverait
            # jamais « Untel » ni un début de phrase
            lu = first_lu.get(k) or typed.strip(EDGE_PUNCT)
            out.append(dict(source="decisions", ou="", lu=lu, propose=kept, sure=True, mot=False, etat="regle",
                            raison=f"décision : {why}", regle=to_rule(lu, kept)))
    return out


def enrichir(work: Path) -> int:
    """Ajoute à la table partagée de la skill (corrections-ocr.csv) les confusions d'OCR de ce livre qui valent pour
    tout livre de la même langue : un mot lu qui n'existe pas, corrigé en un mot du dictionnaire. Les noms propres et
    les mots du livre (absents du dictionnaire) n'y entrent pas : ils restent dans le fixes.tsv du livre."""
    lang = book_lang(work)
    if lexicon(lang) is None:
        return 0
    table = read_shared()
    known = {(l, lu) for l, lu, _ in table}
    new = []
    edge = EDGE_PUNCT + "—"
    seen = Counter((x.strip(edge), y.strip(edge).replace("'", "’"))
                   for e in analyse(work) if e["etat"] == "regle" and not e.get("coquille")
                   for x, y in token_pairs(e["lu"], e["propose"]))
    fixes_of = Counter(x for x, _ in seen)
    for (x, y), n in seen.items():
        # relevée au moins deux fois dans ce livre, et une seule correction connue pour ce mot : sinon trop incertain
        if n >= 2 and fixes_of[x] == 1 and len(x) >= 4 and re.fullmatch(r"[\w'’-]+", x) \
                and (lang, x) not in known and not is_word(x, lang) and is_word(y, lang, False) and x.lower() != y.lower():
            known.add((lang, x))
            new.append((lang, x, y))
    if new:
        write_shared(table + new)
    return len(new)


def write_fixes(fixes: Path, rules: dict[str, str], contexte: dict[str, int] | None = None) -> int:
    """Réécrit la section automatique de fixes.tsv ; les règles écrites à la main restent devant et l'emportent.

    contexte : propositions classées « contexte » ou « manuel » (regex -> occurrences au premier classement), notées
    en commentaire dans la section pour le tour suivant (auto_section)."""
    old = fixes.read_text("utf-8").splitlines() if fixes.exists() else []
    if BEGIN in old:
        a = old.index(BEGIN)
        b = old.index(END, a) if END in old[a:] else len(old) - 1
        old = old[:a] + old[b + 1:]
    manual = {rx for rx, _ in fix_rules(old)}
    auto = [f"{k}\t{v}" for k, v in rules.items() if k not in manual]
    notes = [f"{CONTEXTE}{n}\t{k}" for k, n in (contexte or {}).items()]
    while old and not old[-1].strip():
        old.pop()
    fixes.write_text("\n".join(old + [BEGIN] + auto + notes + [END]) + "\n", "utf-8")
    return len(auto)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--appliquer", action="store_true", help="recopie les règles dans la section automatique de fixes.tsv")
    a = ap.parse_args()
    work = Path(a.work)
    rel = work / "build" / "relecture"
    items = analyse(work)
    rules: dict[str, str] = {}
    # les fragments (avec contexte) d'abord, les mots seuls ensuite ; les plus longs en premier
    for e in sorted((e for e in items if e["regle"]), key=lambda e: (e["mot"], -len(e["lu"]))):
        rules.setdefault(*e["regle"])
    dropped = [f"{e['source']} {e['ou']}: {e['raison']} : {e['lu']!r} -> {e['propose']!r}"
               for e in items if e["etat"] != "regle"]
    (rel / "regles-candidates.tsv").write_text("".join(f"{k}\t{v}\n" for k, v in rules.items()), "utf-8")
    (rel / "regles-ecartees.txt").write_text("\n".join(dropped) + "\n", "utf-8")
    print(f"{len(rules)} règles -> regles-candidates.tsv ; {len(dropped)} propositions sans règle -> regles-ecartees.txt")
    todo = [e for e in items if e["etat"] in ("conflit", "contexte")]
    if todo:
        print(f"{len(todo)} à traiter à la main (conflit : decisions.tsv ; contexte : règle dans fixes.tsv) :")
        for e in todo[:40]:
            print(f"  {e['ou']}  « {e['lu']} » -> « {e['propose']} »  [{e['raison']}]")
    if a.appliquer:
        n = write_fixes(work / "fixes.tsv", rules, dict(e["contexte"] for e in items if e.get("contexte")))
        print(f"{n} règles écrites dans la section automatique de fixes.tsv. Suite : run.py --from extract, "
              "puis corrections_restantes.py.")


if __name__ == "__main__":
    main()
