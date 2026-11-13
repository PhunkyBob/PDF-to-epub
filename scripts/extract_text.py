#!/usr/bin/env python3
"""Étape 2 : hOCR -> texte structuré (<work>/build/content.json) + rapport de relecture (review.txt).

  * supprime en-têtes courants (titre rappelé en haut de page) et folios (numéros de page) ;
  * reconstitue les vrais paragraphes à partir de la géométrie (retrait de 1re ligne, tiret de
    dialogue, ligne courte), y compris à cheval sur deux pages : plus aucun retour à la ligne de mise en page ;
  * recolle les césures de fin de ligne en gardant les vrais traits d'union ;
  * détecte les titres de chapitre (corps plus gros que le texte courant) ;
  * conserve l'italique (mots ou paragraphes entiers) ;
  * corrige les erreurs d'OCR courantes (dictionnaire + <work>/fixes.tsv) ;
  * applique la typographie (française si language = fr : espaces insécables, guillemets, …).

Usage : extract_text.py --work DOSSIER [--config FICHIER]
"""
from __future__ import annotations

import json
import os
import re
import statistics
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher

from lxml import etree

from common import (IC, IO, NOTE_BASE, NOTE_END, NOTE_RX, SCENE_MARK, SKILL_DIR, SUB_MARK, WORD_END, WORD_START, Ctx,
                    Progress, ctx_from_args, decision_key, fix_rules, lang_code, parser, plain, read_decisions,
                    read_shared, straight_apos)

NB = " "
BB = re.compile(r"bbox (\d+) (\d+) (\d+) (\d+)")
ROMAN = re.compile(r"^[IVXLCivxlc]{1,7}\.?$")
NUM = re.compile(r"^[\dIl|]{1,4}$")
TERMINAL = tuple(".!?…»:;)’\"”")
DASH = re.compile(r"^[—–-]\s?")
WORD = re.compile(r"[^\W\d_]+(?:[-’'][^\W\d_]+)*", re.U)
# appel de note en tête de ligne : « 1. », « 2) », « I. » (1 mal lu), « * », « 1.4 » (espace perdue), « {. » (1 mal lu)
_CALL = r"\d{1,2}\.(?=\d)|(?:\d{1,2}|[IVX]{1,3}|[{}|])\s?[.)]\s+"
NOTE_CALL = re.compile(rf"^[\W_]{{0,2}}(?:{_CALL}|\*{{1,3}}\s*)")
# option note_call_bare_number : appel de note « 12 Texte » sans point (ouvrages collectifs imprimés ainsi)
NOTE_CALL_BARE = re.compile(rf"^[\W_]{{0,2}}(?:{_CALL}|\d{{1,3}}\s+(?=[^\d\s])|\*{{1,3}}\s*)")
# tiret de dialogue mal lu en tête de ligne (« _ Mot », « _— Mot », « —- Mot »)
BAD_DASH = {"_", "__", "—-", "–-", "-—", "_—", "—_", "_-"}
DETACHED = 1.3      # ligne de haut de page détachée du texte : la suivante est à plus de 1,3 interligne
LONE_NUMBER = re.compile(r"^[\W\dIVXLCivxlc|]{1,8}$")   # numéro de chapitre isolé (« 4 », « II », « . 60 13 »)
CHAPTER_LABEL = re.compile(r"^(chapitre|chapter|cap[ií]tulo|kapitel|capitolo)?\s*([IVXLC]+|\d+)\s*\.?$", re.I)
# trait d'union suivi d'une espace (ou d'un guillemet parasite), marqueurs d'italique éventuels autour
_MK, _JUNK = f"[{re.escape(IO + IC)}]*", "[ \t“”‘’\"*°®]"
STRAY_HYPHEN = re.compile(rf"([^\W\d_]+)-(?={_MK}{_JUNK})({_MK})(?:{_JUNK})*({_MK})([^\W\d_]+)")
# appel de note lu par l'OCR : exposant collé au mot (« hakama*de », « Panpan-girl!,arrêtée ») ou isolé (« !, », « 1; », « * »)
CALL_GLUED = re.compile(r"^(.*?[^\W\d_])(?:[*°¹²³]+|\d{1,2}|[:;]!+(?=$)|!+(?=[,.;:)»]|[^\W\d_]))([,.;:)»…]*)([^\W\d_].*)?$")
CALL_ALONE = re.compile(r"^(?:[*°¹²³]+|\d{1,2}|[LlI](?=[!?,.;:…])|[!?*]+(?=[,.;:)»…]))([,.;:)»…!?]*)$")
# Mme, Mlle, Mgr en exposant que l'OCR lit « Me », « Mre »…
ABBREV_SUSPECT = re.compile(r"\bM(?:e|re|ile|lie|ne)\s+[A-ZÉ]")

REVIEW: list[str] = []


class Extractor:
    def __init__(self, ctx: Ctx):
        self.ctx, self.cfg = ctx, ctx.cfg
        self.lang = lang_code(self.cfg)
        self.fr = self.lang == "fr"
        self.heads = [self.norm_cmp(h) for h in self.cfg.get("running_heads", [])]
        self._top = None                                # relevé des hauts de page du livre (top_profile)
        self.cap_cache: dict[str, str] = {}             # mot à capitale -> forme accentuée (accent_capitals)
        self.note_call = NOTE_CALL_BARE if self.cfg.get("note_call_bare_number") else NOTE_CALL
        self.skip = set(self.cfg["pages"].get("skip", []))
        try:
            from spellchecker import SpellChecker
            self.spell = SpellChecker(language=self.lang)
        except Exception:
            self.spell = None
            REVIEW.append(f"(dictionnaire « {self.lang} » indisponible : contrôle orthographique désactivé)")
        # pyspellchecker n'est qu'une liste de fréquences tirée de sous-titres de films : il ignore « synecdoque »,
        # « hégémonisme », « plénum »… Le vrai lexique (Hunspell, dict/<langue>.aff/.dic : Grammalecte pour fr) décide
        # si un mot existe ; pyspellchecker ne sert plus qu'aux suggestions et variantes d'accent (fréquences).
        self.lexicon, self.lex_cache = None, {}
        dic = SKILL_DIR / "dict" / self.lang
        if dic.with_suffix(".dic").exists():
            try:
                from spylls.hunspell import Dictionary
                self.lexicon = Dictionary.from_files(str(dic))
            except Exception as e:
                REVIEW.append(f"(lexique Hunspell « {self.lang} » illisible : {e} ; seul pyspellchecker est utilisé)")
        self.proper_ok: set[str] = set()
        self.autofix_cache: dict[str, str] = {}         # mot -> correction (vidé par build_vocab, qui fixe proper_ok)
        self.after_label = False                        # ligne précédente : « Chapitre N » (titre juste dessous)
        self.prog = None
        self.unknown: list[tuple[int, str]] = []        # (indice dans REVIEW, mot) : suggestions à calculer
        self.fixes = []
        self.fix_hits = {}                              # index de règle -> pages où elle s'est appliquée
        self.fix_log = []                               # [page, lu, corrigé, extrait, place dans l'extrait] par correction
        self.note_seq = 0                               # rang de la prochaine note (donne son marqueur d'appel)
        self.italic_over = {}                           # verdicts sur l'image : {page: {id du mot: italique ?}}
        over = ctx.work / "italiques.tsv"
        if over.exists():
            for row in over.read_text("utf-8").splitlines():
                f = row.split("\t")
                if len(f) >= 3 and f[0].isdigit() and f[2] in ("i", "r"):
                    self.italic_over.setdefault(int(f[0]), {})[f[1]] = f[2] == "i"
        fx = ctx.work / "fixes.tsv"
        for a, b in fix_rules(fx.read_text("utf-8").splitlines()) if fx.exists() else []:
            # {I} : marqueurs internes éventuels (italique, appel de note)
            a = a.replace("{I}", f"[{IO}{IC}{chr(NOTE_BASE)}-{chr(NOTE_END)}]*")
            self.fixes.append((re.compile(a), b.replace("\\t", "\t")))
        # tables partagées de la skill (corrections-ocr.csv et .perso.csv) : confusions d'OCR valables pour tout livre de cette langue,
        # après les règles du livre ; une ligne de decisions.tsv qui garde la forme lue désactive la confusion
        keep = read_decisions(ctx.work)
        # apostrophe droite ou courbe : l'OCR lit l'une ou l'autre, et le texte final ne porte plus que « ’ »
        self.fixes += [(re.compile(WORD_START + "".join("['’‘]" if ch == "'" else re.escape(ch)
                                                        for ch in straight_apos(lu)) + WORD_END),
                        good.replace("\\", "\\\\"))
                       for lang, lu, good in read_shared() if lang == self.lang and decision_key(lu) not in keep]
        # les mêmes règles, repassées sur le texte final (typographie faite) : une règle écrite d'après le texte final
        # (celui d'apercu.py et des relecteurs) s'applique aussi ; même indice dans les deux listes : fix_hits
        self.fixes_final = [(rx, rep.replace("'", "’")) for rx, rep in self.fixes]
        # les mots écrits par fixes.tsv sont voulus : les corrections automatiques n'y touchent plus
        # (sinon « déroute », absent du dictionnaire, redevient « dérouté ») ; \1 et \g<1> ne sont pas des mots
        self.protected = {w.lower() for _, b in self.fixes for w in WORD.findall(re.sub(r"\\(?:\d|g<\w+>)", " ", b))}

    # ------------------------------------------------------------------ outils
    @staticmethod
    def norm_cmp(s: str) -> str:
        s = unicodedata.normalize("NFKD", s.upper())
        return re.sub(r"[^A-Z]", "", s)

    EXTRA_OK = {"jusqu’à", "jusqu’au", "jusqu’aux", "jusqu’ici", "quelqu’un", "quelqu’une", "aujourd’hui",
                "presqu’île", "presqu’ile", "lorsqu’il", "lorsqu’elle", "lorsqu’on", "puisqu’il", "puisqu’elle"}

    def known(self, w: str, lexicon: bool = True) -> bool:
        """Mot existant ? lexicon=False : liste de fréquences seule, pour les corrections automatiques du texte.

        Hunspell connaît aussi « mème », « membrés »… : s'il décidait des corrections, une mauvaise lecture
        de « même » ne serait plus corrigée. Il ne sert donc qu'à ne pas signaler de faux « mots inconnus ».
        """
        if self.spell is None:
            return True
        wl = w.lower()
        return (wl in self.EXTRA_OK or wl in self.spell or wl.replace("œ", "oe") in self.spell
                or (lexicon and self.in_lexicon(w)))

    CAP_ACCENTS = {"E": "ÉÈÊ", "A": "ÀÂ", "I": "Î", "O": "Ô", "U": "ÙÛ", "C": "Ç"}

    def in_lexicon(self, w: str) -> bool:
        if self.lexicon is None or not w:
            return False
        if w not in self.lex_cache:
            ok = self.lexicon.lookup(w)
            if not ok and self.fr and w[0] in self.CAP_ACCENTS:
                # capitale non accentuée, usuelle à l'imprimerie : « Etat » (État), « Ecole » (École)
                ok = any(self.lexicon.lookup(a + w[1:]) for a in self.CAP_ACCENTS[w[0]])
            self.lex_cache[w] = ok
        return self.lex_cache[w]

    # ------------------------------------------------------------------ lecture d'une page
    def read_page(self, n: int):
        f = self.ctx.ocr / f"page-{n:03d}.hocr"
        if not f.exists():
            return None
        tree = etree.parse(str(f), etree.HTMLParser(encoding="utf-8"))
        page = next(e for e in tree.iter("div") if e.get("class") == "ocr_page")
        W, H = map(int, BB.search(page.get("title")).groups()[2:])
        itf = f.with_suffix(".italic.json")
        ital = set(json.loads(itf.read_text("utf-8"))) if (itf.exists() and self.cfg["italics"]) else set()
        if self.cfg["italics"]:
            for wid, on in self.italic_over.get(n, {}).items():
                (ital.add if on else ital.discard)(wid)
        lines = []
        for ln in tree.iter("span"):
            if ln.get("class") != "ocr_line":
                continue
            l, t, r, b = map(int, BB.search(ln.get("title")).groups())
            xs = re.search(r"x_size ([\d.]+)", ln.get("title"))
            words, confs, flags, wlefts, wrights, wtops, wbots = [], [], [], [], [], [], []
            for w in ln.iter("span"):
                if w.get("class") == "ocrx_word":
                    txt = "".join(w.itertext()).strip()
                    txt = txt.replace("\u2010", "-").replace("\u2011", "-").replace("\u00ac", "-")
                    if txt.endswith("\u00ad"):            # trait d'union conditionnel en fin de ligne
                        txt = txt[:-1] + "-"
                    txt = txt.replace("\u00ad", "")
                    if not txt:
                        continue
                    words.append(txt)
                    c = re.search(r"x_wconf (\d+)", w.get("title") or "")
                    confs.append(int(c.group(1)) if c else 100)
                    flags.append(w.get("id") in ital)
                    bb = BB.search(w.get("title") or "")
                    wlefts.append(int(bb.group(1)) if bb else l)
                    wrights.append(int(bb.group(3)) if bb else r)
                    wtops.append(int(bb.group(2)) if bb else t)
                    wbots.append(int(bb.group(4)) if bb else b)
            if not words:
                continue
            if len(words) > 1 and words[0] in BAD_DASH and words[1][:1].isupper():
                if words[0] == "_":
                    REVIEW.append(f"p.{n}: « _ » lu comme un tiret de dialogue : « {' '.join(words[:6])} »")
                words[0] = "—"
            elif len(words) > 2 and words[0] == "—" and words[1] == "-":
                for lst in (words, confs, flags, wlefts, wrights, wtops, wbots):
                    del lst[1]
            lines.append(dict(l=l, t=t, r=r, b=b, xs=float(xs.group(1)) if xs else 0.0, words=words,
                              wconfs=confs, plain=" ".join(words), text=self.mark_italics(words, flags),
                              wlefts=wlefts, wrights=wrights, wtops=wtops, wbots=wbots, wflags=flags))
        lines += self.lost_lines(f.with_suffix(".lost.json"), n, lines)
        lines.sort(key=lambda d: (d["t"], d["l"]))
        return W, H, lines

    @staticmethod
    def lost_lines(path, n, lines):
        """Lignes imprimées que Tesseract n'a pas lues, retrouvées par lignes_perdues.py."""
        if not path.exists():
            return []
        sizes = sorted(x["xs"] for x in lines) or [0.0]
        out = []
        for z in json.loads(path.read_text("utf-8")):
            if z["kind"] == "illisible":
                REVIEW.append(f"p.{n}: encre non lue par l'OCR (y={z['t']}), à vérifier sur l'image : « {z['text']} »")
                continue
            if z["kind"] == "texte":
                REVIEW.append(f"p.{n}: ligne perdue par l'OCR puis relue, à vérifier : « {z['text']} »")
            words = z["text"].split()
            k = len(words)
            out.append(dict(l=z["l"], t=z["t"], r=z["r"], b=z["b"], xs=sizes[len(sizes) // 2], words=words, wconfs=[100] * k,
                            plain=" ".join(words), text=" ".join(words), wlefts=[z["l"]] * k, wrights=[z["r"]] * k,
                            wtops=[z["t"]] * k, wbots=[z["b"]] * k, wflags=[False] * k, lost=z["kind"]))
        return out

    @staticmethod
    def mark_italics(words, flags):
        flags = list(flags)
        n = len(flags)
        if n and sum(flags) >= 0.75 * n:
            flags = [True] * n                          # ligne entière en italique
        for i in range(1, n - 1):                       # petit mot entre deux mots en italique
            if not flags[i] and flags[i - 1] and flags[i + 1]:
                flags[i] = True
        out, open_ = [], False
        for w, f in zip(words, flags):
            if f and not open_:
                w = IO + w; open_ = True
            elif not f and open_:
                out[-1] += IC; open_ = False
            out.append(w)
        if open_:
            out[-1] += IC
        return " ".join(out)

    # ------------------------------------------------------------------ nettoyage d'une page
    @staticmethod
    def top_numbers(txt: str) -> set[int]:
        """Valeurs possibles d'un numéro seul (« 12 », « IV » = 4, « Il » = 11, 2 ou 49) ; ensemble vide sinon."""
        out = set()
        if NUM.match(txt):
            out.add(int(re.sub(r"[Il|]", "1", txt)))
        if ROMAN.match(txt):
            for t in {txt, txt.replace("l", "I")}:      # « Il », « VIl » : un I lu l
                ds = [{"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}[c] for c in t.rstrip(".").lower()]
                out.add(sum(-d if d < nxt else d for d, nxt in zip(ds, ds[1:] + [0])))
        return out

    def top_lines(self, H, lines) -> list[tuple[dict, bool, bool]]:
        """Lignes de la bande haute (header_band) : (ligne, détachée ?, grand corps ?).

        Détachée : séparée de la ligne suivante par plus qu'un interligne normal, ou sans ligne au-dessous. La
        première ligne du texte ne l'est pas, même quand le texte commence dans la bande. Grand corps : la taille
        d'un titre (heading_min_size_ratio)."""
        hb = self.cfg["header_band"] * H
        band = [ln for ln in lines if ln["t"] < hb and ln.get("lost") != "silence"]   # « — … » : ni en-tête ni folio
        if not band:
            return []
        h = statistics.median(l["b"] - l["t"] for l in lines)
        rows = []                                       # haut de chaque rangée de lignes
        for l in lines:
            if not rows or l["t"] - rows[-1] > 0.5 * h:
                rows.append(l["t"])
        steps = [b - a for a, b in zip(rows, rows[1:])]
        pitch = statistics.median(steps) if len(steps) >= 3 else 1.5 * h      # trop peu de lignes : interligne estimé
        sizes = [l["xs"] for l in lines if l["xs"]]
        big = self.cfg["heading_min_size_ratio"] * statistics.median(sizes) if sizes else 0
        out = []
        for ln in band:
            below = next((t for t in rows if t - ln["t"] > 0.5 * h), None)
            out.append((ln, below is None or below - ln["t"] > DETACHED * pitch, bool(big and ln["xs"] >= big)))
        return out

    def top_profile(self):
        """Ce qui revient en haut des pages du livre, relevé une fois : décalages numéro − page des numéros seuls
        (un folio garde le même d'une page à l'autre), textes détachés de chaque page avec leurs chiffres, et pages
        où chaque texte apparaît, détaché ou non."""
        if self._top is None:
            if self.prog:
                self.prog.log("repérage des en-têtes courants et des folios")
            n0 = len(REVIEW)
            nums, detached, every = {}, {}, {}
            for f in sorted(self.ctx.ocr.glob("page-*.hocr")):
                n = int(f.stem.split("-")[1])
                if n in self.skip:
                    continue
                _, H, lines = self.read_page(n)
                seen = {}                               # texte -> détaché sur cette page ?
                for ln, alone, _ in self.top_lines(H, lines):
                    txt = ln["plain"].strip()
                    vals = self.top_numbers(txt)
                    if vals:
                        nums.setdefault(n, set()).update(v - n for v in vals)
                    elif core := self.norm_cmp(txt):
                        seen[core] = seen.get(core, False) or alone
                        if alone:
                            detached.setdefault(n, {})[core] = re.sub(r"\D", "", txt)
                for c in seen:
                    every.setdefault(c, set()).add(n)
            del REVIEW[n0:]                             # remarques de lecture : refaites quand la page est extraite
            self._top = nums, detached, every
        return self._top

    def top_verdict(self, n: int, txt: str, alone: bool, big: bool) -> str:
        """Sort d'une ligne de la bande haute : « retirer » (en-tête courant ou folio), « texte » (gardée) ou
        « douteux » (gardée et signalée). La position ne suffit pas : le texte de certains livres commence dans la
        bande. Un en-tête se reconnaît à ce qu'il est détaché du texte et revient d'une page à l'autre ; un folio,
        à ce qu'il suit la pagination. Un numéro seul qui ne la suit pas est un numéro de chapitre."""
        nums, detached, every = self.top_profile()
        core = self.norm_cmp(txt)
        if core and any(core == h or (h in core and len(core) - len(h) <= 3) for h in self.heads):
            return "retirer"                            # running_heads
        vals = self.top_numbers(txt)
        if vals:
            if any(nums.get(m, set()) & {v - n for v in vals} for m in range(n - 4, n + 5) if m != n):
                return "retirer"                        # folio
            return "douteux" if alone else "texte"
        if not alone:
            # collée au texte : en-tête seulement si elle revient de page en page (4 pages sur les 13 autour).
            # Une réplique (« — Oui, dit-elle. ») revient aussi en haut de page, mais çà et là : gardée et signalée.
            at = every.get(core, ())
            sure = len(core) >= 8 and not big           # assez longue, au corps du texte : retirée d'office
            if sum(m in at for m in range(n - 6, n + 7)) >= 4:
                return "retirer" if sure else "douteux"     # en-tête court (« NANA ») ou en grand corps : signalé
            # ni lettre ni numéro (folio orné « — 12 — », filet) : pas forcément du texte, signalée
            return "douteux" if not core or (sure and len(at) >= 2) else "texte"
        if not core:
            return "retirer"                            # ni lettre ni numéro : filet, folio orné
        digits = re.sub(r"\D", "", txt)

        def same_number(d: str, m: int) -> bool:
            """Mêmes chiffres, ou folio dans l'en-tête (« 12 LE TITRE » p. 14, « 14 LE TITRE » p. 16)."""
            return d == digits or bool(d and digits and int(d) - m == int(digits) - n)

        # revient, détachée, sur une page voisine (à une erreur de lecture près). Les chiffres comptent : « Lundi 3
        # mai 1915. » et « Lundi 17 mai 1915. » sont deux lignes du texte, pas un en-tête.
        if not big and any((c == core or SequenceMatcher(None, core, c).ratio() >= 0.85) and same_number(d, m)
                           for m in range(n - 6, n + 7) if m != n for c, d in detached.get(m, {}).items()):
            return "retirer"
        letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", txt)
        if len(core) >= 3 and letters.isupper() and len(txt) <= 70:
            return "retirer"                            # ligne de capitales
        return "douteux"

    def clean_page(self, n, H, lines):
        keep = []
        fb = self.cfg["footer_band"] * H
        tops = {id(ln): (alone, big) for ln, alone, big in self.top_lines(H, lines)}
        for ln in lines:
            txt = ln["plain"].strip()
            if id(ln) in tops:
                verdict = self.top_verdict(n, txt, *tops[id(ln)])
                if verdict == "retirer":
                    continue
                if verdict == "douteux":
                    REVIEW.append(f"p.{n}: ligne en haut de page conservée (en-tête ?) : « {txt[:60]} »")
            elif ln.get("lost") != "silence" and ln["t"] > fb \
                    and (NUM.match(txt) or ROMAN.match(txt) or len(txt) <= 3
                         or (LONE_NUMBER.match(txt) and re.search(r"\d", txt))):   # folio mal lu (« . 60 »)
                continue
            keep.append(ln)
        return keep

    def split_notes(self, H, lines):
        """Sépare les notes de bas de page : lignes finales en petit corps, sous un blanc net.

        Renvoie (lignes du texte, notes) ; chaque note est un groupe de lignes commençant par son appel.
        """
        if not self.cfg["footnotes"] or len(lines) < 4:
            return lines, []
        sizes = [l["xs"] for l in lines if l["xs"]]
        if not sizes:
            return lines, []
        xs = statistics.median(sizes)
        ratio = self.cfg["footnote_max_size_ratio"]
        i = len(lines)
        while i > 1 and lines[i - 1]["t"] > 0.6 * H and lines[i - 1]["xs"] and lines[i - 1]["xs"] < ratio * xs:
            i -= 1
        start = None
        for k in range(i, len(lines)):                  # la note commence sous un blanc net
            gap = lines[k]["t"] - lines[k - 1]["b"]
            if gap > 0.5 * xs or (gap > 0.25 * xs and self.note_call.match(lines[k]["plain"])):
                start = k
                break
        if start is None:
            return lines, []
        notes = []
        for ln in lines[start:]:
            if self.note_call.match(ln["plain"]) or not notes:
                notes.append([ln])
            else:
                notes[-1].append(ln)
        return lines[:start], notes

    def note_text(self, lines):
        t = self.join_lines(lines)
        m = self.note_call.match(t)
        if m:
            call = m.group(0)
            t = (IO if call.count(IO) > call.count(IC) else "") + t[m.end():]   # italique ouvert dans l'appel
        return t

    @staticmethod
    def note_labels(p):
        """Mots qu'une note explique : libellés « Mot : définition » ; sinon ce qui précède la 1re virgule
        (« Urashima Taro, héros… »). Renvoie (mots candidats, libellés explicites ?)."""
        labels = re.findall(r"(?:^|;\s*)([^\W\d_][\w’'\- ]{1,30}?)\s*:", p)
        explicit = bool(labels)
        labels = labels or [re.split(r"[,:;.]", p)[0][:40]]
        words = [w for lab in labels for w in re.split(r"\s+(?:et|and)\s+|\s+", lab.strip(" ,")) if len(w) >= 3]
        return words, explicit

    @staticmethod
    def word_matches(tok, label, has_call):
        t = re.sub(r"^\W+|\W+$", "", tok).lower().rstrip("s")
        stem = label.lower().rstrip("s")
        if not t or not stem:
            return False
        if t == stem or (len(stem) >= 4 and t.startswith(stem) and len(t) - len(stem) <= 2):
            return True
        if len(stem) >= 4 and len(t) >= 3 and SequenceMatcher(None, t, stem).ratio() >= 0.8:
            return True                                 # mot mal lu (« mompe », « tatamia »)
        return has_call and len(t) >= 2 and stem.startswith(t) and len(stem) - len(t) <= 1   # « ob ; » pour « obi¹ »

    @staticmethod
    def find_calls(lines):
        """Appels de note visibles sur la page, dans l'ordre de lecture : (ligne, mot portant l'appel, numéro lu ou None).

        Seuls comptent les signes peu ambigus : exposant collé au mot, chiffre isolé nettement plus petit que la ligne,
        astérisque, « !, » ou « ?. ». Un « ! » seul reste une exclamation, un « 9 » de taille normale reste un nombre."""
        calls = []
        for li, ln in enumerate(lines):
            hs = sorted(b - t for t, b in zip(ln["wtops"], ln["wbots"]))
            h_med = hs[len(hs) // 2] if hs else 0
            for wi, tok in enumerate(ln["words"]):
                if NOTE_RX.search(tok):
                    continue
                g = CALL_GLUED.match(tok)
                if g and not re.fullmatch(r"[A-ZÀ-Ý]{1,3}", g.group(1)):
                    digits = re.search(r"\d+", tok[len(g.group(1)):])
                    calls.append((li, wi, int(digits.group()) if digits else None))
                    continue
                if wi and CALL_ALONE.match(tok) and not tok[:1] in "LlI":
                    if tok[:1].isdigit():
                        raised = ln["wbots"][wi] - ln["wtops"][wi] < 0.72 * h_med and ln["wbots"][wi] < ln["b"] - 0.15 * h_med
                        if not raised:
                            continue
                        calls.append((li, wi - 1, int(re.match(r"\d+", tok).group())))
                    else:
                        calls.append((li, wi - 1, None))
        return calls

    def link_notes(self, n, lines, notes):
        """Pose l'appel de chaque note et retire l'exposant mal lu (voir les étapes dans le corps de la méthode).
        Sans preuve, aucun appel : mieux vaut une note signalée qu'un appel posé au hasard (note_anchors la règle).
        Renvoie les marqueurs (un par note ; "" si pas d'appel)."""
        forced = {a["note"]: a["after"] for a in self.cfg.get("note_anchors", []) if a.get("page") == n}
        texts, nums = [], []
        for group in notes:
            raw = " ".join(l["plain"] for l in group)
            m = self.note_call.match(raw)
            d = re.search(r"\d+", m.group()) if m else None
            nums.append(int(d.group()) if d else None)
            text = self.note_call.sub("", raw)                  # le vocabulaire n'est pas encore connu
            texts.append(re.sub(r"(\w)- (?=\w)", r"\1-", text))   # libellé coupé en fin de ligne : « romance- seat »
        marks = [chr(min(NOTE_BASE + self.note_seq + k, NOTE_END)) for k in range(len(notes))]
        self.note_seq += len(notes)
        spots, used = {}, set()                                 # indice de note -> (ligne, mot, comment) ; mots déjà pris

        def by_label(k, only_with_call):
            words, explicit = self.note_labels(texts[k])
            is_forced = k + 1 in forced
            labels = [forced[k + 1]] if is_forced else words
            best, ties = None, 0
            for li, ln in enumerate(lines):
                for wi, tok in enumerate(ln["words"]):
                    if (li, wi) in used:
                        continue
                    nxt = ln["words"][wi + 1] if wi + 1 < len(ln["words"]) else ""
                    after = ln["words"][wi + 2] if wi + 2 < len(ln["words"]) else ""
                    glued = CALL_GLUED.match(tok) if not re.fullmatch(r"[A-ZÀ-Ý]{1,3}\d+\W*", tok) else None
                    alone = (bool(CALL_ALONE.match(nxt)) or (nxt == "!" and after[:1].islower()) or nxt == "!!"
                             or (nxt in ("L", "l", "I") and after[:1] in ("!", "?", ",", ".", ";", ":")))
                    has_call = bool(glued) or alone
                    if only_with_call and not has_call and not is_forced:
                        continue
                    core = glued.group(1) if glued else tok
                    # mot coupé en fin de ligne : « wan-tan- / men »
                    joined = None
                    if tok.endswith("-") and wi == len(ln["words"]) - 1 and li + 1 < len(lines) and lines[li + 1]["words"]:
                        joined = tok + lines[li + 1]["words"][0]
                    for lab in labels:
                        at = None
                        if joined and self.word_matches(joined, lab, False):
                            at = (li + 1, 0)
                        elif self.word_matches(core, lab, has_call):
                            at = (li, wi)
                        if at is None or (not has_call and not explicit and not is_forced):
                            continue                    # note sans libellé : un mot commun ne prouve rien
                        score = (10 if has_call else 0) + (2 if lines[at[0]]["wflags"][at[1]] else 0) + (1 if lab.lower() in tok.lower() else 0)
                        if best is None or score > best[0]:
                            best, ties = (score, at), 1
                        elif score == best[0] and at != best[1]:
                            ties += 1
            if best:
                how = "sûr" if best[0] >= 10 or is_forced else "ambigu, à vérifier" if ties > 1 else "seule occurrence"
                spots[k] = (best[1][0], best[1][1], how)
                used.add(best[1])

        # 1. le mot expliqué suivi d'un exposant : le plus sûr (« Du whisky !, dit-il » n'est pas un appel)
        for k in range(len(notes)):
            by_label(k, True)
        # 2. les exposants restants, dans l'ordre s'ils sont aussi nombreux que les notes, sinon par numéro
        free = [k for k in range(len(notes)) if k not in spots and k + 1 not in forced]
        calls = [c for c in self.find_calls(lines) if (c[0], c[1]) not in used]
        if calls and len(calls) == len(free):
            pairs = list(zip(free, calls))
        else:
            pairs = [(k, c) for c in calls for k in free if c[2] is not None and nums[k] == c[2]]
        for k, c in pairs:
            if k not in spots and (c[0], c[1]) not in used:
                spots[k] = (c[0], c[1], "exposant")
                used.add((c[0], c[1]))
        # 3. le libellé explicite, sans exposant visible
        for k in range(len(notes)):
            if k not in spots:
                by_label(k, False)
        out = []
        # de la fin vers le début : retirer un exposant ne décale pas les positions restant à traiter
        for k in sorted(spots, key=lambda k: spots[k][:2], reverse=True):
            self.place_call(lines[spots[k][0]], spots[k][1], marks[k])
        for k, text in enumerate(texts):
            if k in spots:
                ln = lines[spots[k][0]]
                word = next((w for w in ln["words"] if marks[k] in w), "?")
                REVIEW.append(f"p.{n}: appel de note relié après « {plain(word)} » ({spots[k][2]}) : « {text[:50]} »")
                out.append(marks[k])
            else:
                REVIEW.append(f"!! p.{n}: note sans appel (à placer avec note_anchors) : « {text[:60]} »")
                out.append("")
        return out

    def place_call(self, ln, wi, mark):
        """Écrit le marqueur derrière le mot `wi` et retire de la ligne l'exposant que l'OCR a pu lire."""
        words, flags = ln["words"], ln["wflags"]
        parallel = [words, flags, ln["wconfs"], ln["wlefts"], ln["wrights"], ln["wtops"], ln["wbots"]]
        tok = words[wi]
        glued = CALL_GLUED.match(tok)
        nxt = words[wi + 1] if wi + 1 < len(words) else ""
        after = words[wi + 2] if wi + 2 < len(words) else ""
        if glued:
            head, punct, rest = glued.group(1), glued.group(2) or "", glued.group(3)
            words[wi] = head + mark + punct
            if rest:                                    # « hakama*de » : deux mots
                for lst in parallel:
                    lst.insert(wi + 1, lst[wi])
                words[wi + 1], flags[wi + 1] = rest, False
        else:
            m = re.match(r"^(.*?)([,.;:)»…!?]*)$", tok)
            words[wi] = m.group(1) + mark + m.group(2)
            alone = CALL_ALONE.match(nxt)
            if alone:
                if alone.group(1):
                    words[wi] += alone.group(1)
                for lst in parallel:
                    del lst[wi + 1]
            elif (nxt == "!" and after[:1].islower()) or (nxt in ("L", "l", "I") and after[:1] in ("!", "?", ",", ".", ";", ":")):
                for lst in parallel:
                    del lst[wi + 1]
            elif nxt == "!!":                           # exposant + vraie exclamation
                words[wi + 1] = "!"
        ln["text"] = self.mark_italics(words, flags)
        ln["plain"] = plain(" ".join(words))

    def strip_note_calls(self, sec):
        """Retire l'appel de note (exposant mal lu : « mots !, », « livre *, ») derrière les mots
        expliqués par une note de la même section. « ! » et « ? » ne sont retirés que devant une
        ponctuation, pour ne pas toucher une vraie exclamation (« Un mot ! dit-elle »)."""
        for note in sec.get("notes", []):
            words, explicit = self.note_labels(plain(note))
            # sans libellé explicite, début de phrase : pas de chiffres retirés (« le 9 septembre »)
            call_chars = r"[*¹²³\d]" if explicit else r"[*¹²³]"
            # sans libellé explicite, les mots viennent d'une simple phrase (« des », « dans ») : pas de « ? » retiré
            marks = "[!?]" if explicit else "!"
            for word in words:
                if len(word) < 4 and not explicit:
                    continue
                stem = re.escape(word.lower().removesuffix("s"))
                rx = re.compile(rf"(?i)(\b{stem}s?{IC}?)\s?(?:{marks}(?=\s?[,.;:)…»])|{call_chars}(?=\s?[,.;:)…»]|\s+[a-zà-ÿ]))")
                for k, para in enumerate(sec["paras"]):
                    new = rx.sub(r"\1", para)
                    if new != para:
                        REVIEW.append(f"[{sec.get('title') or sec['id']}] appel de note retiré après « {word} »")
                        sec["paras"][k] = new

    def annotate(self, lines, W, n):
        if not lines:
            return
        rights = sorted(l["r"] for l in lines)
        ref_r = rights[int(0.85 * (len(rights) - 1))]
        sizes = [l["xs"] for l in lines if l["xs"]]
        xs_med = statistics.median(sizes) if sizes else 0
        h_med = statistics.median(l["b"] - l["t"] for l in lines)
        lefts = sorted(l["l"] for l in lines)
        center = (lefts[len(lefts) // 2] + ref_r) / 2
        for i, ln in enumerate(lines):
            ln["gap_after"] = (lines[i + 1]["t"] - ln["b"]) if i + 1 < len(lines) else 10 ** 6
            ln["gap_before"] = (ln["t"] - lines[i - 1]["b"]) if i else 10 ** 6
            ln["centered"] = abs((ln["l"] + ln["r"]) / 2 - center) < 0.06 * W and ln["r"] - ln["l"] < 0.8 * W
            win = lines[max(0, i - 4): i + 5]
            ln["indent"] = ln["l"] - min(w["l"] for w in win) > 0.022 * W
            ln["short"] = ln["r"] < ref_r - 0.05 * W
            ln["xs_med"] = xs_med
            ln["h_med"] = h_med
            ln["page"] = n
            ln["first"] = (i == 0)

    @staticmethod
    def lone_number(ln):
        return bool(LONE_NUMBER.match(ln["plain"]) and re.search(r"[\dIVXLC]", ln["plain"]))

    def is_heading(self, ln, H):
        """Titre : corps nettement plus gros, ou un peu plus gros + centré + suivi d'un grand blanc."""
        p = ln["plain"]
        if not ln["xs_med"] or len(p) > 70 or p.endswith(TERMINAL[:3]) or p.startswith("—") or ln["t"] > 0.5 * H:
            return False
        if any(s in p for s in self.cfg.get("not_headings", [])):
            return False
        if self.cfg.get("heading_first_on_page"):
            # titres à la taille du texte (gras, par exemple) : première ligne centrée de la page, sous un blanc,
            # suivie d'un grand blanc, et qui ressemble à une entrée de la table des matières (placée dans running_heads)
            import difflib
            if not (ln.get("first") and ln["centered"] and ln["t"] > 0.15 * H and ln["gap_after"] > 2.0 * ln["xs_med"]):
                return False
            c = self.norm_cmp(p)
            return any(difflib.SequenceMatcher(None, c, h).ratio() >= 0.85 for h in self.heads)
        ratio = ln["xs"] / ln["xs_med"]
        if ratio >= self.cfg["heading_min_size_ratio"]:
            return True
        if self.after_label and ln["centered"] and ratio >= 1.03:
            return True                                # titre sous « Chapitre N » (corps à peine plus gros)
        return ratio >= 1.08 and ln["centered"] and ln["gap_after"] > 2.5 * ln["xs_med"]

    def is_subheading(self, ln):
        """Intertitre à la taille du texte : ligne courte, centrée, isolée entre deux blancs nets, qui contient
        un vrai mot (pas une formule ni une ligne de capitales) et ne commence pas par une didascalie « ( »."""
        p, xs = ln["plain"].strip(), ln["xs_med"]
        if not self.cfg.get("subheadings") or not xs or ln.get("first") or not ln["centered"]:
            return False
        if len(p) > 70 or len(p) < 3 or p[0] in "(«[—-–" or p.endswith((".", ",", ";", ":")) or not p[0].isupper():
            return False
        if not re.search(r"[a-zà-ÿ]{3,}", p) or self.lone_number(ln):
            return False
        return (ln["gap_before"] > self.cfg.get("subheading_gap_before", 2.0) * xs
                and ln["gap_after"] > self.cfg.get("subheading_gap_after", 1.2) * xs
                and ln["gap_after"] < 10 ** 6)

    def pages(self, first, last, count=False):
        """Pages lues et nettoyées de la plage : (n, W, H, lignes) ; une page sans hOCR est signalée."""
        for n in range(first, last + 1):
            if count and self.prog:
                self.prog.count(n - first + 1, last - first + 1, f"pages lues (p.{first}-{last})")
            if n in self.skip:
                continue
            pg = self.read_page(n)
            if pg is None:
                REVIEW.append(f"p.{n}: pas de hOCR (OCR non fait ?)")
                continue
            W, H, lines = pg
            yield n, W, H, self.clean_page(n, H, lines)

    # ------------------------------------------------------------------ tableau daté (chronologie)
    def assemble_table(self, first, last, title="", left_max=0.395):
        """Tableau à deux colonnes (date à gauche, texte à droite) : un paragraphe « date : texte » par ligne
        du tableau. Les colonnes sont découpées en groupes séparés par un blanc ; le i-ème groupe de dates
        va avec le i-ème groupe de texte ; un texte sans date en tête de page continue l'entrée précédente."""
        def groups(col):
            out = []
            for ln in sorted(col, key=lambda d: d["t"]):
                if out and ln["t"] - out[-1][-1]["b"] <= 0.5 * (ln["b"] - ln["t"]):
                    out[-1].append(ln)
                else:
                    out.append([ln])
            return out

        blocks = []
        for n, W, H, lines in self.pages(first, last):
            lines = [ln for ln in lines if not title or self.norm_cmp(ln["plain"]) != self.norm_cmp(title)]
            # bord gauche de la colonne de texte de cette page : début des mots qui suivent un grand blanc
            # (ou début des lignes sans date), pris à la médiane ; à défaut, left_max
            edges = [ln["wlefts"][i + 1] for ln in lines for i in range(len(ln["words"]) - 1)
                     if ln["wlefts"][i + 1] - ln["wrights"][i] > 0.03 * W and 0.3 * W < ln["wlefts"][i + 1] < 0.5 * W]
            edges += [ln["l"] for ln in lines if 0.3 * W < ln["l"] < 0.5 * W]
            cut = (statistics.median(edges) - 0.012 * W) if edges else left_max * W
            lcol, rcol = [], []
            for ln in lines:                                   # une ligne OCR couvre souvent les deux colonnes
                k = next((i for i, x in enumerate(ln["wlefts"]) if x >= cut), len(ln["words"]))
                for a, z, col in ((0, k, lcol), (k, len(ln["words"]), rcol)):
                    if a < z:
                        ws, fl = ln["words"][a:z], ln["wflags"][a:z]
                        col.append(dict(ln, words=ws, wconfs=ln["wconfs"][a:z], wflags=fl, wlefts=ln["wlefts"][a:z],
                                        plain=" ".join(ws), text=self.mark_italics(ws, fl), l=ln["wlefts"][a]))
            self.annotate(lcol + rcol, W, n)
            left, right = groups(lcol), groups(rcol)
            if len(right) == len(left) + 1 and blocks:
                blocks[-1]["lines"] += right.pop(0)             # suite de l'entrée de la page précédente
            if len(right) != len(left):
                REVIEW.append(f"p.{n}: tableau : {len(left)} dates pour {len(right)} textes (vérifier sur l'image)")
            for d, t in zip(left, right):
                date = re.sub(r"-\s+", "-", " ".join(x["text"] for x in d))
                head = dict(d[0], text=date + " :", plain=re.sub(r"[-]", "", date) + " :")
                blocks.append(dict(type="p", lines=[head] + t, page=n))
        return blocks

    # ------------------------------------------------------------------ assemblage
    def assemble(self, first, last, allow_headings=True):
        blocks, pending, prev = [], [], None

        def heading_on_page(n):
            return blocks and blocks[-1]["type"] == "h" and blocks[-1]["page"] == n

        def open_heading(n, b, text=""):
            """Nouveau chapitre : les notes en attente vont au chapitre précédent."""
            nonlocal prev
            blocks.extend(pending)
            pending.clear()
            blocks.append(dict(type="h", lines=[text], page=n, b=b))
            prev = None

        for n, W, H, lines in self.pages(first, last, count=True):
            lines, notes = self.split_notes(H, lines)
            marks = self.link_notes(n, lines, notes) if notes else []
            self.annotate(lines, W, n)
            self.after_label = False
            # numéros seuls gardés en haut de page par clean_page (numéro de chapitre, trait lu « | ») : ils sont
            # au-dessus du texte. La première ligne de la page, pour heading_first_on_page et chapter_top_gap, est
            # celle qui les suit (`top`).
            hb = self.cfg["header_band"] * H
            top = next((i for i, l in enumerate(lines) if l["t"] >= hb or not self.top_numbers(l["plain"].strip())),
                       len(lines))
            if 0 < top < len(lines):
                lines[top]["first"] = True
            if allow_headings and self.cfg.get("first_page_chapter") and n == first and lines:
                open_heading(n, lines[0]["t"])         # la 1re page du corps ouvre le chapitre 1
            if allow_headings and n in self.cfg.get("chapter_starts", []) and lines and not heading_on_page(n):
                open_heading(n, lines[0]["t"])         # chapitre que la détection manque : page d'ouverture forcée
            gap = self.cfg.get("chapter_top_gap")      # chapitres sans titre lisible : grand blanc en haut de page
            if allow_headings and gap and n != first and top < len(lines) and lines[top]["t"] > gap * H:
                if not heading_on_page(n):             # déjà ouvert par chapter_starts : pas de chapitre vide
                    open_heading(n, lines[top]["t"])
                # le petit numéro de chapitre lu par l'OCR, en haut de page ou sous le blanc : numérotation régénérée
                lines = lines[top + 1:] if self.lone_number(lines[top]) else lines[top:]
                top = 0
            for i, ln in enumerate(lines):
                if allow_headings and self.cfg.get("lone_numeral_headings") \
                        and re.match(r"^[IVXLCivxlT]{1,6}\.?$", ln["plain"].strip()) and ln["centered"] \
                        and ln["gap_after"] > 1.5 * ln["xs_med"]:
                    # chapitre introduit par un numéro romain seul, au milieu de la page : titre vide
                    if not heading_on_page(n):
                        open_heading(n, ln["b"])
                    prev = None
                    continue
                if allow_headings and (i < top or self.lone_number(ln)):
                    nxt = next((x for x in lines[max(i + 1, top):] if not self.lone_number(x)), None)
                    if nxt and self.is_heading(nxt, H):
                        continue                       # numéro de chapitre mal lu au-dessus du titre
                if allow_headings and self.is_heading(ln, H):
                    if CHAPTER_LABEL.match(ln["plain"]):
                        self.after_label = True
                        continue                       # « I », « Chapitre 3 » : numérotation régénérée
                    if heading_on_page(n) and ln["t"] - blocks[-1]["b"] < 3 * ln["xs"]:
                        blocks[-1]["lines"].append(ln["plain"]); blocks[-1]["b"] = ln["b"]
                    else:
                        open_heading(n, ln["b"], ln["plain"])
                    self.after_label = False           # la règle « sous Chapitre N » ne vaut que pour la 1re ligne de titre
                    prev = None
                    continue
                self.after_label = False
                if self.is_subheading(ln):
                    blocks.append(dict(type="sub", lines=[ln], page=n))
                    prev = None
                    continue
                for w, c in zip(ln["words"], ln["wconfs"]):
                    if c < 35 and len(re.sub(r"\W", "", w)) >= 4 and "'" not in w \
                            and not self.known(w.strip(".,;:!?«»—")):
                        REVIEW.append(f"p.{n}: mot douteux (confiance {c}) : « {w} »")
                if self.cfg.get("scene_breaks") and prev is not None and prev["page"] == n \
                        and prev["plain"].endswith(TERMINAL) and prev["gap_after"] < 10 ** 6 \
                        and prev["gap_after"] > self.cfg["scene_break_gap"] * prev["h_med"]:
                    blocks.append(dict(type="s", page=n))      # blanc de scène
                    prev = None
                new = prev is None
                if not new:
                    p_end = prev["plain"].endswith(TERMINAL)
                    first_c = ln["plain"][0]
                    if DASH.match(ln["plain"]) and (p_end or prev["short"]):
                        new = True
                    elif ln["indent"] and not first_c.islower() and (p_end or prev["short"]):
                        new = True
                    elif prev["short"] and p_end:
                        new = True
                if new or not blocks or blocks[-1]["type"] != "p":
                    blocks.append(dict(type="p", lines=[ln], page=n))
                else:
                    blocks[-1]["lines"].append(ln)
                prev = ln
            # les notes ne coupent pas le fil du texte : mises en attente jusqu'au prochain titre
            pending.extend(dict(type="n", lines=g, page=n, mark=m) for g, m in zip(notes, marks))
        return blocks + pending

    # ------------------------------------------------------------------ césures
    KEEP_RIGHT_FR = {"ce", "il", "ils", "elle", "elles", "on", "nous", "vous", "je", "tu", "moi", "toi", "lui",
                     "leur", "eux", "y", "en", "là", "ci", "même", "mêmes", "t"}
    KEEP_LEFT_FR = {"peut", "porte", "grand", "demi", "après", "avant", "quant", "c", "est", "tout", "arc", "chef"}

    def build_vocab(self, blocks):
        """Vocabulaire des blocs : mots avec / sans trait d'union (recollage des césures) et noms propres
        (capitalisés au moins 3 fois) que les corrections automatiques ne touchent pas (« Pétain » ≠ « l’étain »)."""
        plain_c, hyph, caps = Counter(), Counter(), Counter()
        for b in blocks:
            if b["type"] != "p":
                continue
            for ln in b["lines"]:
                for w in WORD.findall(ln["plain"].lower()):
                    (hyph if "-" in w else plain_c)[w] += 1
                caps.update(re.findall(r"\b[A-ZÀ-Ý][a-zà-ÿ]{3,}\b", ln["plain"]))
        self.plain_vocab, self.hyph_vocab = plain_c, hyph
        self.proper_ok.update(w for w, c in caps.items() if c >= 3)
        self.autofix_cache.clear()                      # les corrections dépendent de proper_ok

    def join_lines(self, lines):
        out = lines[0]["text"]
        for ln in lines[1:]:
            nxt = ln["text"]
            if out.endswith("-" + IC) and nxt.startswith(IO):     # mot en italique coupé
                out, nxt = out[:-1], nxt[1:]
            elif out.endswith("-" + IC):                          # italique fermé juste après le trait d'union
                out = out[:-1]                                    # la fermeture passe après le mot recollé
                nxt = re.sub(r"^(\S+)", lambda mo: mo.group(1) + IC, nxt, count=1)
            m = re.search(r"([^\W\d_]+)-$", out)
            r = re.match(r"([^\W\d_]+)", plain(nxt))
            if m and r and plain(nxt)[0].islower():
                left, right = m.group(1).lower(), r.group(1).lower()
                keep = f"{left}-{right}" in self.hyph_vocab
                if self.fr:
                    keep = keep or right in self.KEEP_RIGHT_FR or \
                        (left in self.KEEP_LEFT_FR and f"{left}{right}" not in self.plain_vocab)
                if keep:
                    out = out + nxt
                else:
                    out = out[:-1] + nxt
                    if f"{left}{right}" not in self.plain_vocab and not self.known(left + right) \
                            and self.autofix_words(left + right) == left + right:
                        # le mot avec sa casse : le rapport le cherche tel quel dans le texte livré
                        REVIEW.append(f"p.{ln.get('page', '?')}: césure recollée, mot inconnu : "
                                      f"« {m.group(1)}{r.group(1)} »")
            elif out.endswith("-") and plain(nxt)[:1].isupper():
                out = out + nxt
            else:
                out = out + " " + nxt
        return out

    # ------------------------------------------------------------------ corrections
    GLUED_FIRST_FR = ("cette", "dans", "avec", "pour", "sans", "quand", "comme", "mais", "tout", "tous",
                      "leur", "notre", "votre")

    def accent_capitals(self, text: str) -> str:
        """Accentue les capitales que le livre imprime sans accent (option accent_capitals) : « A » préposition en
        tête de phrase (le verbe y prend un trait d'union : « A-t-il »), et le mot à capitale initiale qui n'existe
        qu'accentué (« Etait », « Ecole »). « Edo », nom propre sans forme accentuée connue, ne change pas."""
        text = re.sub(rf"(^|[.!?…:»]\s+|[—–«]\s*)([{IO}]?)A(?=\s+[^\W\d_])", r"\1\2À", text)
        if self.lexicon is None:
            return text

        def rep(m):
            w = m.group(0)
            if w not in self.cap_cache:
                forms = [a + w[1:] for a in self.CAP_ACCENTS[w[0]] if self.lexicon.lookup(a + w[1:])]
                plain_ok = w.lower() in self.protected or self.lexicon.lookup(w) or self.lexicon.lookup(w.lower())
                self.cap_cache[w] = forms[0] if len(forms) == 1 and not plain_ok else w
            return self.cap_cache[w]
        return re.sub(r"\b[ACEIOU][a-zà-ÿœ]+\b", rep, text)

    def autofix_words(self, text):
        def rep(m):
            w = m.group(0)
            if w not in self.autofix_cache:
                self.autofix_cache[w] = self.autofix_word(w)
            return self.autofix_cache[w]
        return re.sub(r"\b[A-Za-zÀ-ÿŒœ]{4,}\b", rep, text)

    def autofix_word(self, w):
        """Correction d'un mot isolé (mise en cache par autofix_words)."""
        if self.known(w, lexicon=False) or w in self.proper_ok or w.lower() in self.protected:
            return w
        if self.fr and re.match(r"^P[aeiouyéèêhâîô]", w) and self.known(w[1:], lexicon=False):
            return "l’" + w[1:]                         # « Pécole » -> « l’école »
        if self.fr and w.islower() and len(w) >= 8:
            for fw in self.GLUED_FIRST_FR:
                if w.startswith(fw) and len(w) - len(fw) >= 4 and self.known(w[len(fw):], lexicon=False):
                    return fw + " " + w[len(fw):]      # « danslequel » -> « dans lequel »
        fixed = self.accent_variant(w)
        if fixed:
            return fixed
        base = unicodedata.normalize("NFD", w[0]).encode("ascii", "ignore").decode() + w[1:]
        if base != w and base and self.known(base, lexicon=False):
            return base                                 # « Âvec » -> « Avec »
        return w

    ACCENTS = {"e": "eéèêë", "é": "eéèêë", "è": "eéèêë", "ê": "eéèêë", "ë": "eéèêë",
               "a": "aàâ", "à": "aàâ", "â": "aàâ", "i": "iîï", "î": "iîï", "ï": "iîï",
               "o": "oô", "ô": "oô", "u": "uùûü", "ù": "uùûü", "û": "uùûü", "c": "cç", "ç": "cç"}

    def accent_variant(self, w):
        """Mot inconnu dont une variante d'accentuation est connue : « fenétre » -> « fenêtre »."""
        if self.spell is None or not any(ch in "éèêëàâîïôùûüç" for ch in w.lower()):
            return None
        low = w.lower()
        if sum(ch in self.ACCENTS for ch in low) > 5:
            return None
        cands = {""}
        for ch in low:
            opts = self.ACCENTS.get(ch, ch)
            cands = {c + o for c in cands for o in opts}
            if len(cands) > 4000:
                return None
        # Formes justes mais absentes du dictionnaire, à ne pas « corriger » :
        if self.fr and re.search(r"(?:é|ée|és|ées)$", low) and re.sub(r"(?:é|ée|és|ées)$", "er", low) in self.spell:
            return None                                 # participe d'un verbe connu : « bandés » (bander)
        if self.fr and re.search(r"[âîû]t$", low):
            return None                                 # subjonctif imparfait : « parût », « s’aperçût »
        tremas = [i for i, ch in enumerate(low) if ch in "ëïü"]   # l'OCR n'invente pas de tréma : « haït »
        known = [c for c in cands if c != low and c in self.spell and all(c[i] == low[i] for i in tremas)]
        if not known:
            return None
        # le moins de lettres changées, puis le plus fréquent : « déroûte » -> « déroute » (pas « dérouté »)
        best = min(known, key=lambda c: (sum(a != b for a, b in zip(c, low)), -self.spell.word_frequency[c]))
        return best[0].upper() + best[1:] if w[0].isupper() else best

    def resolve_suggestions(self):
        """Complète les remarques « mot inconnu » par une suggestion du dictionnaire.

        La recherche à distance 2 coûte ~0,7 s par mot : on la fait une seule fois par mot distinct, à la fin,
        d'abord à distance 1 (instantané), puis à distance 2 en parallèle sur plusieurs processus, et on garde
        le résultat dans <work>/cache/suggestions.json pour les relances suivantes.
        """
        if self.spell is None or not self.unknown:
            return
        cache_f = self.ctx.work / "cache" / "suggestions.json"
        cache = json.loads(cache_f.read_text("utf-8")) if cache_f.exists() else {}
        words = sorted({w.lower() for _, w in self.unknown if len(w) >= 4} - cache.keys())
        self.prog.log(f"suggestions du dictionnaire : {len({w.lower() for _, w in self.unknown})} mots, "
                      f"{len(words)} à calculer (les autres en cache)")
        far = []
        self.spell.distance = 1
        for w in words:
            c = self.spell.correction(w)
            if c and c != w:
                cache[w] = c
            else:
                far.append(w)
        self.spell.distance = 2
        if far:
            jobs = max(1, min(len(far) // 10 + 1, (os.cpu_count() or 2) - 1))
            chunks = [far[i::jobs] for i in range(jobs)]
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                for k, res in enumerate(pool.map(_suggest_far, [(self.lang, ch) for ch in chunks]), 1):
                    cache.update(res)
                    self.prog.count(k, jobs, "lots de suggestions (distance 2)", every=0)
        cache_f.parent.mkdir(exist_ok=True)
        cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=0), "utf-8")
        for i, w in self.unknown:
            sug = cache.get(w.lower())
            if sug and sug != w.lower():
                REVIEW[i] += f" -> « {sug} » ?"

    def typography(self, t):
        t = t.replace("'", "’").replace("`", "’")
        t = re.sub(r"\.{3,}|\. \. \.", "…", t)
        t = re.sub(r"\s+", " ", t).strip()
        # normalisation des marqueurs d'italique
        t = t.replace(IC + " " + IO, " ").replace(IO + IC, "")
        t = re.sub(" " + IC, IC + " ", t)
        t = re.sub(IO + " ", " " + IO, t)
        t = re.sub(f"({NOTE_RX.pattern})([,.;:…]*){IC}", IC + r"\1\2", t)   # l'appel et sa ponctuation hors de l'italique
        t = re.sub(r" +([,.])", r"\1", t)
        if self.fr:
            t = re.sub(r"\b([Vv])is-(?:a-?)?vis\b", r"\1is-à-vis", t)
            t = re.sub(r"«\s*", "«" + NB, t)
            t = re.sub(r"\s*»", NB + "»", t)
            if self.cfg["nbsp_before_high_punct"]:
                t = re.sub(r"(?<!^)\s*([:;!?])", NB + r"\1", t)
                t = t.replace(NB + "!" + NB + "!", NB + "!!").replace(NB + "?" + NB + "!", NB + "?!")
            t = re.sub(r"^([" + IO + r"]?)[-–—]\s*", r"\1—" + NB, t)
            t = re.sub(r"(\w)—(?=\w)", r"\1 — ", t)
            t = re.sub(r"\s+—\s+", " — ", t)
        else:
            t = re.sub(r"\s+([:;!?])", r"\1", t)
        return t.strip()

    def fix_stray_hyphens(self, text):
        """Césure restée dans le texte : « mon- tagnes », « choi- “sies » (guillemet parasite à la place d'un
        appel de note), « pouvait- on », « anti- Hu », « 1981- 1982 ». Un trait d'union suivi d'une espace n'est
        jamais voulu : on recolle le mot si le résultat est un mot connu, sinon on garde le trait d'union."""
        def fix(m):
            left, m1, m2, right = m.groups()
            if right[0].islower() and self.known((left + right).lower(), lexicon=False):
                return left + m1 + m2 + right
            return f"{left}-{m1}{m2}{right}"

        text = STRAY_HYPHEN.sub(fix, text)
        return re.sub(r"(\d)-[ \t]+(?=\d)", r"\1-", text)

    def finalize(self, text, page):
        italic_par = False
        p = plain(text)
        letters = sum(ch.isalpha() for ch in p) or 1
        it_letters = 0
        depth = False
        for ch in text:
            if ch == IO:
                depth = True
            elif ch == IC:
                depth = False
            elif depth and ch.isalpha():
                it_letters += 1
        if it_letters >= 0.8 * letters:
            italic_par = True                       # paragraphe entier en italique (les appels de note restent)
            text = text.replace(IO, "").replace(IC, "")
        raw_hits: set[int] = set()                      # règles appliquées au texte brut de ce paragraphe
        text = self.apply_fixes(text, page, self.fixes, hits=raw_hits)
        text = self.fix_stray_hyphens(text)
        if self.fr:                                     # « Il » lu « I ! » : « I ! paraît », « I! les contemplait »
            text = re.sub(rf"(^|[—«.!?…] |^— ?)I ?!(?=\s+[{IO}]?[a-zà-ÿ])", r"\1Il", text)
        text = self.autofix_words(text)
        if self.fr and self.cfg["accent_capitals"]:
            text = self.accent_capitals(text)
        text = self.typography(text)
        # second passage sur le texte final : c'est lui que citent les relecteurs et que montre apercu.py
        text = self.apply_fixes(text, page, self.fixes_final, done=raw_hits)
        if self.fr:
            for m in ABBREV_SUSPECT.finditer(plain(text)):
                REVIEW.append(f"p.{page}: abréviation douteuse (Mme, Mlle en exposant ?) : « {m.group(0)} »")
        for junk in re.findall(r"\S*\w[&:|\\#@<>{}\[\]~^_]\w\S*", plain(text)):
            REVIEW.append(f"p.{page}: caractère parasite : « {junk} »")
        for w in WORD.findall(plain(text)):
            parts = [x for x in re.split(r"[-’']", w) if len(x) > 2]
            if len(w) > 3 and w[0].islower() and not self.known(w) and not all(self.known(x) for x in parts):
                self.unknown.append((len(REVIEW), w))          # suggestion ajoutée à la fin (resolve_suggestions)
                REVIEW.append(f"p.{page}: mot inconnu : « {w} »")
        return IO + text + IC if italic_par else text

    def apply_fixes(self, text: str, page, fixes, hits: set | None = None, done=()) -> str:
        """Applique des règles de fixes.tsv [(regex, remplacement)] et trace chaque correction
        (page, lu, corrigé, extrait, place de la correction dans l'extrait) pour le rapport : build/corrections.json.

        hits reçoit l'indice des règles qui ont trouvé leur motif. done : règles déjà appliquées au texte brut de ce
        paragraphe ; au second passage, celle dont le remplacement contient encore son motif (« Mais » -> « — Mais »,
        « dit » -> « dit. ») n'est pas appliquée une seconde fois (« — — Mais », « dit.. »)."""
        for i, (rx, rep) in enumerate(fixes):
            count = [0]

            def trace(m, rx=rx, rep=rep, again=i in done):
                new, s = m.expand(rep), m.string
                if again and rx.search(new):
                    return m.group(0)
                count[0] += 1
                if new != m.group(0):
                    left = plain(s[max(0, m.start() - 45):m.start()])
                    self.fix_log.append([page, plain(m.group(0)), plain(new),
                                         left + plain(new) + plain(s[m.end():m.end() + 45]), len(left)])
                return new
            text = rx.sub(trace, text)
            if count[0]:
                self.fix_hits.setdefault(i, []).extend([page] * count[0])     # tracé dans build/fixes-applied.json
                if hits is not None:
                    hits.add(i)
        return text

    def finalize_block(self, b):
        """Bloc « p » (paragraphe) ou « n » (note) -> texte final."""
        text = self.note_text(b["lines"]) if b["type"] == "n" else self.join_lines(b["lines"])
        return b.get("mark", "") + self.finalize(text, b["page"])

    # ------------------------------------------------------------------ principal
    def run(self):
        self.prog = Progress(self.ctx.work, "extract")
        pg = self.cfg["pages"]
        n = self.ctx.page_count()
        body_last = pg.get("body_last") or n
        before, after, all_blocks = [], [], []
        for lst, key, kind in ((before, "sections_before_body", "front"), (after, "sections_after_body", "back")):
            for sec in pg.get(key, []):
                if sec.get("layout") == "dated_table":
                    b = self.assemble_table(sec["first"], sec["last"], sec["title"])
                else:
                    b = self.assemble(sec["first"], sec["last"], allow_headings=False)
                lst.append(dict(id=sec["id"], title=sec["title"], kind=kind,
                                type=sec.get("type", "foreword" if kind == "front" else "afterword"), blocks=b))
                all_blocks += b
        body = self.assemble(pg["body_first"], body_last)
        all_blocks += body
        self.build_vocab(all_blocks)

        self.prog.log(f"pages assemblées : {len(body)} blocs ; corrections, typographie, contrôle orthographique…")
        chapters, cur = [], None
        for k, b in enumerate(body, 1):
            self.prog.count(k, len(body), "blocs corrigés")
            if b["type"] == "h":
                num = sum(1 for c in chapters if c["number"]) + 1      # le chapitre 0 (prologue) ne compte pas
                cur = dict(id=f"chapitre-{num:02d}", number=num,
                           title=self.typography(" ".join(b["lines"])), page=b["page"], paras=[])
                chapters.append(cur)
            else:
                if cur is None:
                    cur = dict(id="chapitre-00", number=0, title="", page=b["page"], paras=[])
                    chapters.append(cur)
                    REVIEW.append(f"p.{b['page']}: texte avant le premier titre de chapitre "
                                  f"(vérifier body_first ou heading_min_size_ratio)")
                if b["type"] == "s":
                    if cur["paras"] and cur["paras"][-1] != SCENE_MARK:
                        cur["paras"].append(SCENE_MARK)
                elif b["type"] == "n":
                    cur.setdefault("notes", []).append(self.finalize_block(b))
                elif b["type"] == "sub":
                    cur["paras"].append(SUB_MARK + plain(self.finalize_block(b)))
                else:
                    cur["paras"].append(self.finalize_block(b))
        for c in chapters:                              # titres imposés par page d'ouverture (titres illisibles à l'OCR)
            t = (self.cfg.get("chapter_titles_by_page") or {}).get(str(c["page"]))
            if t:
                c["title"] = t
        pt = self.cfg.get("prologue_title")
        if pt and chapters and chapters[0]["number"] == 0:
            chapters[0]["title"] = pt
            if chapters[0]["paras"] and plain(chapters[0]["paras"][0]).strip().lower() == pt.lower():
                chapters[0]["paras"].pop(0)
        for c in chapters:
            self.strip_note_calls(c)

        for c in chapters:
            if c["paras"]:
                REVIEW.append(f"p.{c['page']}: début du chapitre {c['number']} (lettrine ?) : "
                              f"« {plain(c['paras'][0])[:50]} »")
        top: list[str] = []                             # alertes en tête du rapport
        titles = self.cfg.get("chapter_titles") or []
        if titles:
            real = [c for c in chapters if c["number"]]
            if len(titles) == len(real):
                for c, t in zip(real, titles):
                    c["title"] = t
            else:
                top.append(f"!! chapter_titles : {len(titles)} titres fournis pour {len(real)} chapitres détectés")

        for s in before + after:
            s["paras"] = [(SUB_MARK + plain(self.finalize_block(b))) if b["type"] == "sub" else self.finalize_block(b)
                          for b in s["blocks"] if b["type"] in ("p", "sub")]
            s["notes"] = [self.finalize_block(b) for b in s["blocks"] if b["type"] == "n"]
            self.strip_note_calls(s)
            del s["blocks"]

        meta = {k: v for k, v in self.cfg.items() if k != "pages"}
        out = dict(meta=meta, front=before, chapters=chapters, back=after)
        (self.ctx.build / "content.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")

        n_par = sum(len(c["paras"]) for c in chapters) + sum(len(s["paras"]) for s in before + after)
        head = [f"== {len(chapters)} chapitres, {n_par} paragraphes =="]
        head += [f"   chap {c['number']:>2} (p.{c['page']}) {c['title']!r} : {len(c['paras'])} paragraphes"
                 for c in chapters]
        self.resolve_suggestions()
        applied = [dict(rule=rx.pattern, replacement=rep, hits=len(self.fix_hits.get(i, [])),
                        pages=sorted(set(self.fix_hits.get(i, [])), key=str)[:30])
                   for i, (rx, rep) in enumerate(self.fixes)]
        (self.ctx.build / "fixes-applied.json").write_text(json.dumps(applied, ensure_ascii=False, indent=1), "utf-8")
        (self.ctx.build / "corrections.json").write_text(json.dumps(self.fix_log, ensure_ascii=False, indent=0), "utf-8")
        (self.ctx.build / "review.txt").write_text("\n".join(head + top + REVIEW) + "\n", "utf-8")
        print("\n".join(head))
        print(f"-> {self.ctx.build / 'content.json'} ; relecture : {self.ctx.build / 'review.txt'} "
              f"({len(REVIEW)} remarques)")
        self.prog.done(f"{len(chapters)} chapitres, {n_par} paragraphes, {len(REVIEW)} remarques")


def _suggest_far(args):
    """Processus annexe : suggestions à distance 2 pour une liste de mots (mot -> suggestion ou None)."""
    lang, words = args
    from spellchecker import SpellChecker
    sp = SpellChecker(language=lang, distance=2)
    out = {}
    for w in words:
        try:
            out[w] = sp.correction(w)
        except Exception:
            out[w] = None
    return out


def main():
    a = parser(__doc__).parse_args()
    Extractor(ctx_from_args(a)).run()


if __name__ == "__main__":
    main()
