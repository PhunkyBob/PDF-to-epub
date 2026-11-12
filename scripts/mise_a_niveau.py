#!/usr/bin/env python3
"""Met à niveau un livre déjà livré sans refaire l'extraction : appels de note, italiques vérifiés, options de rendu.

À utiliser quand une reconstruction ne redonne pas la version livrée (corrections manuelles, post-traitements) :
le texte relu est conservé mot pour mot. Tout se passe dans WORK/v2 ; WORK/build et l'EPUB livré ne changent pas.

  prepare  crée WORK/v2 (copie de l'OCR et du texte livré) et lance italiques.py (mots douteux, planches)
  notes    prépare les lots d'appels à relever sur l'image : v2/build/relecture/appels-in-N.txt, images dans pages/
  apply    applique italiques et appels à v2/build/content.json, puis contrôle que les mots n'ont pas changé

Usage : mise_a_niveau.py prepare|notes|apply --work DOSSIER [--taille 20]
Ensuite : run.py --work WORK/v2 --from build, rapport_humain.py et livrer.py sur WORK/v2.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from common import IC, IO, NOTE_BASE, NOTE_RX, SKILL_DIR, plain, slugify
from ocr import SHEAR_HI

HERE = Path(__file__).resolve().parent
WORD = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*")
# « ! » résiduel, aussi après l'espace insécable de la typographie (« mot\xa0!, ») ; devant « » » seulement s'il est collé
RESIDUE = re.compile(r"^(?:[*°¹²³]+\d{0,3}|\d{1,3}|[!?](?=[,.;:)»…])|[\s\xa0 ]!(?=[\s\xa0 ]?[,.;:)]))")


def norm(w: str) -> str:
    return unicodedata.normalize("NFKD", w.lower().replace("’", "'")).encode("ascii", "ignore").decode()


def sections(d):
    return d["front"] + d["chapters"] + d.get("back", [])


def text_words(d):
    return plain(" ".join(p for s in sections(d) for p in s["paras"] + (s.get("notes") or []))).split()


# ---------------------------------------------------------------------- prepare
def prepare(w: Path):
    v = w / "v2"
    (v / "ocr").mkdir(parents=True, exist_ok=True)
    (v / "build" / "relecture").mkdir(parents=True, exist_ok=True)
    old = {}
    for f in sorted((w / "ocr").glob("page-*")):
        if f.suffix == ".hocr" or f.name.endswith(".scores.json"):
            shutil.copy(f, v / "ocr" / f.name)
        elif f.name.endswith(".italic.json"):
            shutil.copy(f, v / "ocr" / f.name)
            old[int(f.name[5:8])] = json.loads(f.read_text("utf-8"))
    if not (v / "italic-avant.json").exists():
        (v / "italic-avant.json").write_text(json.dumps(old), "utf-8")
    for name in ("content.json", "cover.jpg", "review.txt", "qa.txt", "corrections.json"):
        if (w / "build" / name).exists():
            shutil.copy(w / "build" / name, v / "build" / name)
    # sans les décisions, rapport_humain.py refuse le rapport de v2 : les mots tranchés redeviennent « non corrigé »
    if (w / "decisions.tsv").exists() and not (v / "decisions.tsv").exists():
        shutil.copy(w / "decisions.tsv", v / "decisions.tsv")
    if not (v / "build" / "content-avant.json").exists():
        shutil.copy(w / "build" / "content.json", v / "build" / "content-avant.json")
    for f in (w / "build" / "relecture").glob("prop-mots*.tsv"):
        shutil.copy(f, v / "build" / "relecture" / f.name)
    if (w / "rapport.json").exists():
        shutil.copy(w / "rapport.json", v / "rapport-avant.json")
    cfg = json.loads((w / "book.json").read_text("utf-8"))
    pdf = Path(cfg["pdf"])
    cfg["pdf"] = str(pdf if pdf.is_absolute() else (w / pdf).resolve())
    cfg["output"] = f"build/{slugify(cfg['title'])}-v2.epub"
    cfg.pop("post_extract", None)
    if not (v / "book.json").exists():
        (v / "book.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    subprocess.run([sys.executable, str(HERE / "italiques.py"), "--work", str(v)], check=True)


# ---------------------------------------------------------------------- notes
def page_tokens(hocr: Path):
    from lxml import etree
    tree = etree.parse(str(hocr), etree.HTMLParser(encoding="utf-8"))
    return [norm(t) for w in tree.iter("span") if w.get("class") == "ocrx_word"
            for t in WORD.findall("".join(w.itertext()))]


def find_page(note, pages, taken):
    """Page dont le texte contient le mieux les premiers mots de la note (l'OCR peut en avoir mal lu certains).
    Des notes identiques (« En français dans le texte ») prennent chacune la page suivante où le texte figure."""
    head = [norm(t) for t in WORD.findall(plain(note))][:12]
    pairs = list(zip(head, head[1:]))
    if not pairs:
        return None
    scored = []
    for p, toks in pages:
        s = set(zip(toks, toks[1:]))
        scored.append((sum(x in s for x in pairs), p))
    top = max(k for k, _ in scored)
    if top < max(1, len(pairs) // 3):
        return None
    hits = [p for k, p in scored if k == top]
    free = [p for p in hits if (p, tuple(head)) not in taken]
    page = (free or hits)[0]
    taken.add((page, tuple(head)))
    return page


def notes_in(w: Path, size: int):
    import pypdfium2 as pdfium
    v = w / "v2"
    rel = v / "build" / "relecture"
    d = json.loads((v / "build" / "content-avant.json").read_text("utf-8"))
    cfg = json.loads((v / "book.json").read_text("utf-8"))
    pages = [(int(f.stem[5:8]), page_tokens(f)) for f in sorted((v / "ocr").glob("page-*.hocr"))]
    items, missing, taken = [], 0, set()
    for sec in sections(d):
        for k, note in enumerate(sec.get("notes") or []):
            p = find_page(note, pages, taken)
            missing += p is None
            items.append(dict(id=f"{sec['id']}#{k}", page=p, text=plain(note)[:170]))
    (rel / "appels.json").write_text(json.dumps(items, ensure_ascii=False, indent=0), "utf-8")
    known = sorted((i for i in items if i["page"]), key=lambda i: i["page"])
    for f in rel.glob("appels-in-*.txt"):
        f.unlink()
    n = 0
    for s in range(0, len(known), size):
        n += 1
        part = known[s:s + size]
        (rel / f"appels-in-{n}.txt").write_text(
            "".join(f"{i['id']}\t{i['page']}\t{i['text']}\n" for i in part), "utf-8")
    img = rel / "pages"
    img.mkdir(exist_ok=True)
    doc = pdfium.PdfDocument(cfg["pdf"])
    for p in sorted({i["page"] for i in known}):
        pg = doc[p - 1]
        pg.render(scale=1700 / pg.get_height()).to_pil().convert("RGB").save(img / f"p{p:03d}.png")
    print(f"{len(items)} notes, {missing} sans page trouvée, {n} lots")


# ---------------------------------------------------------------------- apply
def locate(text, ctx):
    """Fins possibles du contexte `ctx` (mots imprimés) dans le texte : exactes, sinon à un mot mal lu près."""
    want = [norm(t) for t in WORD.findall(ctx)]
    if not want:
        return []
    toks = [(norm(m.group()), m.end()) for m in WORD.finditer(text)]
    n = len(want)
    exact, near = [], []
    for i in range(len(toks) - n + 1):
        miss = sum(toks[i + j][0] != want[j] for j in range(n))
        if miss == 0:
            exact.append(toks[i + n - 1][1])
        elif miss == 1 and n >= 3 and toks[i + n - 1][0] == want[-1]:
            near.append(toks[i + n - 1][1])
    return exact or near


def insert_call(sec, ctx, mark, after):
    """Pose le marqueur derrière le contexte. `after` = (paragraphe, position) de l'appel précédent de la section :
    les appels se suivent dans le texte, ce qui départage un contexte présent plusieurs fois."""
    spots = []
    for k, para in enumerate(sec["paras"]):
        raw = para.replace(IO, "\ue00f").replace(IC, "\ue00f")   # même longueur : les positions restent valables
        spots += [(k, pos) for pos in locate(raw, ctx)]
    if not spots:
        return "introuvable", after
    later = [s for s in spots if s > after]
    if len(spots) > 1 and not later:
        return f"{len(spots)} occurrences", after
    k, pos = (later or spots)[0]
    if len(later) > 1 and len(WORD.findall(ctx)) < 3:
        return f"{len(later)} occurrences", after
    para = sec["paras"][k]
    # l'exposant resté dans le texte (« k’i* », « mot12 ») disparaît, l'italique se referme avant l'appel
    rest = para[pos:]
    close = IC if rest.startswith(IC) else ""
    tail = rest[len(close):]
    m = RESIDUE.match(tail)
    if m:
        tail = tail[m.end():]
    sec["paras"][k] = para[:pos] + close + mark + tail
    return "ok", (k, pos + len(close))


def italic_flags(para: str) -> list[bool]:
    """Pour chaque caractère du texte sans marqueurs d'italique : est-il en italique ?"""
    flags, cur = [], False
    for ch in para:
        if ch == IO:
            cur = True
        elif ch == IC:
            cur = False
        else:
            flags.append(cur)
    return flags


def set_italic(para: str, start: int, end: int, on: bool) -> str:
    """Met à jour l'italique de la plage [start, end[ du texte sans marqueurs d'italique."""
    chars, flags, cur = [], [], False
    for ch in para:
        if ch == IO:
            cur = True
        elif ch == IC:
            cur = False
        else:
            chars.append(ch)
            flags.append(cur)
    for i in range(start, end):
        flags[i] = on
    out, cur = [], False
    for ch, f in zip(chars, flags):
        if f != cur and (f or not NOTE_RX.match(ch)):
            out.append(IO if f else IC)
            cur = f
        out.append(ch)
    if cur:
        out.append(IC)
    return "".join(out)


def apply_italics(v: Path, d, cfg, log):
    from italiques import page_words
    old = {int(k): set(x) for k, x in json.loads((v / "italic-avant.json").read_text("utf-8")).items()}
    over = {}
    f = v / "italiques.tsv"
    if f.exists():
        for row in f.read_text("utf-8").splitlines():
            c = row.split("\t")
            if len(c) >= 3:
                over[(int(c[0]), c[1])] = c[2] == "i"
    # toutes les sections, avec le texte normalisé mot par mot pour retrouver le contexte du mot
    index = []
    for sec in sections(d):
        for key in ("paras", "notes"):
            for k, para in enumerate(sec.get(key) or []):
                raw = para.replace(IO, "").replace(IC, "")
                toks = [(norm(m.group()), m.start(), m.end()) for m in WORD.finditer(raw)]
                index.append((sec, key, k, toks))
    where = {}                                       # 1er mot -> positions dans le texte livré
    for ip, (_, _, _, toks) in enumerate(index):
        for it, x in enumerate(toks):
            where.setdefault(x[0], []).append((ip, it))
    done, kept, miss, removed = 0, 0, [], []
    for hf in sorted((v / "ocr").glob("page-*.hocr")):
        p = int(hf.stem[5:8])
        # Sans verdict sur l'image, seule la fausse diagonale perd son italique (« s’y », « Ayez » : inclinaison hors
        # fenêtre). Une mesure faible ne prouve rien : un contrôle à l'aveugle en a contredit 35 sur 39.
        scores = json.loads(hf.with_suffix(".scores.json").read_text("utf-8"))
        _, words = page_words(hf)
        txt = {w[0]: w[1] for w in words}
        # le J italique a lui aussi une forte inclinaison : « Je » en italique n'est pas une fausse diagonale
        target = {w: False for w in old.get(p, set())
                  if w in scores and scores[w][0] >= SHEAR_HI and not txt.get(w, "J").startswith("J")}
        target.update({w: on for (pg, w), on in over.items() if pg == p})
        if not target:
            continue
        seq = [(w[0], [norm(t) for t in WORD.findall(w[1])]) for w in words]
        for i, (wid, toks) in enumerate(seq):
            if wid not in target or not toks:
                continue
            if not any(c.isalpha() for c in "".join(toks)):
                continue                             # folio, chiffre : hors du texte
            before = [t for _, x in seq[max(0, i - 3):i] for t in x][-2:]
            after = [t for _, x in seq[i + 1:i + 4] for t in x][:2]
            best, ties = None, 0
            for ip, it in where.get(toks[0], []):
                x = index[ip][3]
                if [y[0] for y in x[it:it + len(toks)]] != toks:
                    continue
                score = sum(it - len(before) + q >= 0 and x[it - len(before) + q][0] == before[q] for q in range(len(before)))
                score += sum(it + len(toks) + q < len(x) and x[it + len(toks) + q][0] == after[q] for q in range(len(after)))
                if best is None or score > best[0]:
                    best, ties = (score, ip, it), 1
                elif score == best[0]:
                    ties += 1
            # deux voisins concordants, ou un seul si la position est unique : sinon le mot n'est pas touché
            hit = (best[1], best[2], best[2] + len(toks) - 1) if best and (best[0] >= 2 or (best[0] == 1 and ties == 1)) else None
            if not hit:
                miss.append(f"p.{p} {'/'.join(toks)}")
                continue
            sec, key, k, t = index[hit[0]]
            if not target[wid] and (p, wid) not in over:
                # mesure seule : un mot au milieu d'un passage en italique y reste (« y », « A » faussent la mesure)
                flags = italic_flags(sec[key][k])
                near = [flags[t[j][1]] for j in (hit[1] - 1, hit[2] + 1) if 0 <= j < len(t)]
                if any(near):
                    kept += 1
                    continue
            sec[key][k] = set_italic(sec[key][k], t[hit[1]][1], t[hit[2]][2], target[wid])
            done += 1
            if (p, wid) not in over:
                removed.append(f"{p}\t{wid}\t{words[i][1]}")
    log.append(f"italique : {done} mots mis à jour, {kept} gardés (passage en italique), {len(miss)} non retrouvés dans le texte livré")
    (v / "build" / "italiques-non-appliques.txt").write_text("\n".join(miss), "utf-8")
    (v / "build" / "italiques-retires-mesure.tsv").write_text("\n".join(removed), "utf-8")


HEADING = re.compile(r"^\W*NOTES?\s*(?:[|Il1]\.\s*|$)")


def apply_calls(v: Path, d, log):
    rel = v / "build" / "relecture"
    props = {}
    for f in sorted(rel.glob("prop-appels*.tsv"), key=lambda f: (f.name.startswith("prop-appels-r"), f.name)):
        for row in f.read_text("utf-8").splitlines():
            c = [x.strip() for x in row.split("\t")]
            if len(c) >= 3 and "#" in c[0] and (c[2].lower() != "aucun" or c[0] not in props):
                props[c[0]] = c                  # une seconde passe complète la première sans l'effacer
    seq, ok, ko, printed = 0, 0, [], {}
    for sec in sections(d):
        sid, notes = sec["id"], sec.get("notes") or []
        new, after = [], (-1, -1)
        for k, note in enumerate(notes):
            mark = chr(NOTE_BASE + seq)
            seq += 1
            text = NOTE_RX.sub("", note)
            if not plain(HEADING.sub("", plain(text))).strip():
                continue                         # simple intitulé « NOTES » pris pour une note
            pl = plain(text)
            if HEADING.match(pl):
                text = text[text.index(pl[HEADING.match(pl).end():][:6]):]
            c = props.get(f"{sid}#{k}")
            ctx = c[2] if c else ""
            res, pos = insert_call(sec, ctx, mark, after) if ctx and ctx.lower() != "aucun" else ("aucun appel relevé", after)
            if res == "ok":
                ok += 1
                after = pos
                new.append(mark + text)
                if c and len(c) > 3 and c[3].isdigit():
                    printed[mark] = int(c[3])
            else:
                ko.append(f"{sid}#{k}\t{res}\t{ctx}\t{plain(text)[:90]}")
                new.append(text)
        if notes:
            sec["notes"] = new
    log.append(f"appels : {ok} posés, {len(ko)} sans appel")
    (v / "build" / "appels-non-poses.txt").write_text("\n".join(ko), "utf-8")


def check_words(before, after, log):
    import difflib
    a, b = text_words(before), text_words(after)
    diffs = []
    if a != b:
        for t, i, j, k, l in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
            if t != "equal":
                diffs.append(f"{' '.join(a[max(0, i - 2):j + 1])}  =>  {' '.join(b[max(0, k - 2):l + 1])}")
    log.append(f"mots modifiés : {len(diffs)} zones (seuls des exposants résiduels sont attendus)")
    return diffs


def apply(w: Path):
    v = w / "v2"
    before = json.loads((v / "build" / "content-avant.json").read_text("utf-8"))
    d = json.loads(json.dumps(before))
    cfg = json.loads((v / "book.json").read_text("utf-8"))
    for sec in sections(d):                  # part toujours du texte livré : l'étape est relançable
        sec["paras"] = [NOTE_RX.sub("", x) for x in sec["paras"]]
    log = []
    apply_italics(v, d, cfg, log)
    apply_calls(v, d, log)
    diffs = check_words(before, d, log)
    (v / "build" / "mots-modifies.txt").write_text("\n".join(diffs), "utf-8")
    (v / "build" / "content.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), "utf-8")
    print(" | ".join(log))
    for x in diffs[:8]:
        print("   ", x[:150])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("prepare", "notes", "apply"))
    ap.add_argument("--work", required=True)
    ap.add_argument("--taille", type=int, default=20)
    a = ap.parse_args()
    w = Path(a.work).resolve()
    if a.action == "prepare":
        prepare(w)
    elif a.action == "notes":
        notes_in(w, a.taille)
    else:
        apply(w)


if __name__ == "__main__":
    main()
