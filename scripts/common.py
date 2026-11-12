"""Fonctions partagées : chemins, configuration, outils externes."""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import shutil
import sys
import time
import unicodedata
import uuid
from html import unescape
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("PDF2EPUB_CACHE", Path.home() / ".cache" / "pdf-to-epub"))

# Sorties UTF-8 même sous Windows (console cp1252)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

DEFAULTS: dict = {
    "pdf": "",
    "title": "",
    "subtitle": "",
    "original_title": "",
    "author": "",
    "author_sort": "",
    "translator": "",
    "translator_line": "",
    "publisher": "",
    "published": "",
    "language": "fr",
    "description": "",
    "identifier": "",
    "output": "",

    "source": "ocr",              # "ocr" (scan) ou "textlayer" (PDF numérique avec bon texte)
    "ocr_lang": "fra",            # code(s) Tesseract, ex. "fra" ou "fra+eng"
    "dpi": 300,
    "psm": 4,
    "italics": True,

    "pages": {
        "cover": 1,               # page de couverture (null : couverture générée)
        "body_first": 1,
        "body_last": None,
        "skip": [],               # pages à ignorer dans les plages (blanches, planches, pubs…)
        "sections_before_body": [],
        "sections_after_body": [],
    },
    "running_heads": [],
    "header_band": 0.065,
    "footer_band": 0.90,
    "heading_min_size_ratio": 1.2,
    "footnotes": True,
    "footnote_max_size_ratio": 0.88,
    "note_numbering": "book",        # book : numérotation continue sur le livre ; chapter : reprise à 1 à chaque chapitre
    "note_anchors": [],              # appels forcés : [{"page": 86, "note": 1, "after": "avec"}] (note = rang sur la page)
    "opening_caps": False,           # premier mot de chaque chapitre affiché en majuscules (CSS), comme l'imprimé
    "superscript_abbrevs": False,    # français : M<sup>lle</sup>, M<sup>me</sup>, M<sup>gr</sup>, 1<sup>er</sup> si le livre les imprime ainsi
    "chapter_numbers": "roman",   # roman | arabic | none
    "nbsp_before_high_punct": True,
    "scene_breaks": False,           # blanc vertical entre deux paragraphes -> séparateur « * * * »
    "scene_break_gap": 0.9,          # seuil du blanc, en hauteurs de ligne
    "subheadings": False,            # intertitres à la taille du texte : ligne courte centrée entre deux blancs
    "subheading_gap_before": 2.0,    # blanc minimal au-dessus de l'intertitre (en corps de texte)
    "subheading_gap_after": 1.2,     # blanc minimal au-dessous
    "parts": [],                     # parties du livre : [{"page": 23, "number": "I", "title": "…", "lines": ["…"]}]
    "illustrations": True,           # repère les illustrations du PDF et les replace après le paragraphe en cours
    "cover_image": "",
    "cover_crop": None,
    "colophon": [],
}


IO, IC = "", ""   # marqueurs internes début / fin d'italique (content.json)
SCENE_MARK = "* * *"          # paragraphe séparateur de scène (rendu en <p class="scene">)
SUB_MARK = ""           # préfixe d'un paragraphe qui est un intertitre (rendu en <h2 class="intertitre">)


# appels de note : un caractère de la zone privée par note, posé derrière le mot appelé (et en tête du texte de la note)
NOTE_BASE, NOTE_END = 0xE100, 0xF8FF
NOTE_RX = re.compile(f"[{chr(NOTE_BASE)}-{chr(NOTE_END)}]")


def plain(s: str) -> str:
    """Texte sans les marqueurs internes (italique, intertitre, appels de note)."""
    return NOTE_RX.sub("", s.replace(IO, "").replace(IC, "").replace(SUB_MARK, ""))


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def slugify(s: str) -> str:
    s = s.replace("œ", "oe").replace("Œ", "Oe").replace("æ", "ae").replace("Æ", "Ae")     # sinon « sœur » devient « sur »
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower() or "livre"


def default_work_dir(pdf: Path) -> Path:
    """<livre>.epub-work à côté du PDF. Réutilise un dossier existant dont le nom ne diffère que par la
    normalisation Unicode des accents (NFC/NFD : « é » en un ou deux caractères), sinon on crée des doublons
    selon l'outil qui a écrit le chemin (bash, PowerShell, Python)."""
    parent = pdf.resolve().parent
    name = f"{pdf.stem}.epub-work"
    want = unicodedata.normalize("NFC", name)
    try:
        for d in parent.iterdir():
            if d.is_dir() and unicodedata.normalize("NFC", d.name) == want:
                return d
    except OSError:
        pass
    return parent / name


def output_path(work: Path, cfg: dict, pdf: Path) -> Path:
    """L'EPUB écrit par build_epub : `output` de la configuration, sinon build/<titre ou nom du PDF>.epub."""
    out = cfg.get("output") or f"build/{slugify(cfg.get('title') or pdf.stem)}.epub"
    return Path(out) if Path(out).is_absolute() else work / out


class Ctx:
    """Contexte d'exécution : dossier de travail + configuration fusionnée avec les défauts."""

    def __init__(self, work: Path, config: Path | None = None):
        self.work = work.resolve()
        self.config_path = (config or self.work / "book.json").resolve()
        if not self.config_path.exists():
            sys.exit(f"Configuration introuvable : {self.config_path}\n"
                     f"Lancez d'abord inspect_pdf.py pour la créer.")
        raw = json.loads(self.config_path.read_text("utf-8"))
        self.cfg = deep_merge(DEFAULTS, raw)
        pdf = Path(self.cfg["pdf"])
        self.pdf = pdf if pdf.is_absolute() else (self.config_path.parent / pdf).resolve()
        self.ocr = self.work / "ocr"
        self.build = self.work / "build"
        self.ocr.mkdir(parents=True, exist_ok=True)
        self.build.mkdir(parents=True, exist_ok=True)
        if not self.cfg["identifier"]:
            seed = f"{self.cfg['title']}|{self.cfg['author']}|{self.cfg['published']}"
            self.cfg["identifier"] = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, seed))
        self.output = output_path(self.work, self.cfg, self.pdf)

    def page_count(self) -> int:
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(str(self.pdf)))


class Progress:
    """Journal d'avancement lisible pendant le traitement : <work>/progress.log.

    Réécrit à chaque lancement (par run.py, ou par un script lancé seul). Sous run.py, la variable
    PDF2EPUB_STEP (ex. « 2/5 extract ») donne la position de l'étape dans la chaîne.
    """

    def __init__(self, work: Path, step: str, fresh: bool | None = None):
        self.path = Path(work) / "progress.log"
        self.step = os.environ.get("PDF2EPUB_STEP") or step
        if fresh is None:
            fresh = not os.environ.get("PDF2EPUB_STEP")      # script lancé seul : nouveau journal
        if fresh:
            self.path.write_text("", "utf-8")
        self.t0 = time.time()
        self.last = 0.0

    def log(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')} [étape {self.step}] {msg}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)

    def count(self, done: int, total: int, what: str = "pages", every: float = 5.0):
        """Avancement « 120/282 pages (43 %) », au plus une ligne toutes les `every` secondes."""
        now = time.time()
        if done == total or now - self.last >= every:
            self.last = now
            eta = (now - self.t0) / done * (total - done) if done else 0
            self.log(f"{done}/{total} {what} ({100 * done // max(total, 1)} %)"
                     + (f", reste ~{int(eta)} s" if done < total and done else ""))

    def done(self, msg: str = "terminé"):
        self.log(f"{msg} ({time.time() - self.t0:.0f} s)")


def parser(desc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--work", required=True, help="dossier de travail (créé par inspect_pdf.py)")
    p.add_argument("--config", default=None, help="fichier de config (défaut : <work>/book.json)")
    return p


def ctx_from_args(a: argparse.Namespace) -> Ctx:
    return Ctx(Path(a.work), Path(a.config) if a.config else None)


def parse_pages(spec: str) -> list[int]:
    """« 1,7,13-15 » -> [1, 7, 13, 14, 15]"""
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            first, last = int(a), int(b)
        elif part:
            first = last = int(part)
        else:
            continue
        if first < 1 or last < first:          # la page 0 serait doc[-1] : la dernière page, en silence
            raise ValueError(f"pages invalides : « {part} » (numéros à partir de 1, plage croissante)")
        out += range(first, last + 1)
    return out


def pick_epub(work: Path, cfg: dict) -> Path | None:
    """L'EPUB livré : celui qu'écrit build_epub (`output`, sinon build/<titre>.epub, comme Ctx.output) ;
    à défaut le plus récent de build/, jamais un doublon « Copie » ou « copy »."""
    pdf = Path(cfg.get("pdf") or "")
    built = output_path(work, cfg, pdf if pdf.is_absolute() else (work / pdf).resolve())
    if built.exists():
        return built
    found = sorted((p for p in (work / "build").glob("*.epub")
                    if not re.search(r"copie|copy", p.stem, re.I)), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


# --------------------------------------------------------------------------- relecture (review.txt, prop-mots*.tsv)
EDGE_PUNCT = " .,;:!?«»“”\"()[]"                  # ponctuation de bord d'un mot ou d'un fragment
# bornes de mot d'une règle : l'apostrophe compte comme partie du mot, sinon un fragment comme « aujourd » ou
# « avotte » s'applique à l'intérieur de « aujourd’hui » ou « G’avotte » et duplique le texte
WORD_START, WORD_END = r"(?<![\w'’])", r"(?![\w'’])"


def straight_apos(s: str) -> str:
    """Apostrophes droites : les relecteurs réécrivent souvent ’ en ' (« l’Etat » -> « l'Etat »)."""
    return re.sub(r"[’‘ʼ`´]", "'", s)


def norm_word(w: str) -> str:
    """Forme de comparaison d'un mot."""
    return straight_apos(w.strip().lower()).strip(EDGE_PUNCT)


def lang_code(cfg: dict) -> str:
    """Langue du livre sans la région : « fr-FR » -> « fr »."""
    return (cfg.get("language") or DEFAULTS["language"]).split("-")[0].lower()


def sections(data: dict) -> list[dict]:
    """Toutes les sections de content.json, dans l'ordre du livre."""
    return data.get("front", []) + data.get("chapters", []) + data.get("back", [])


def all_paras(data: dict) -> list[str]:
    """Paragraphes puis notes de chaque section, avec leurs marqueurs internes."""
    return [p for s in sections(data) for p in s["paras"] + (s.get("notes") or [])]


def hocr_words(f: Path) -> list[str]:
    """Mots lus par l'OCR sur une page (fichier hOCR), dans l'ordre ; liste vide si le fichier manque."""
    if not f.exists():
        return []
    spans = re.findall(r"class=['\"]ocrx_word['\"][^>]*>(.*?)</span>", f.read_text("utf-8", errors="replace"), re.S)
    return [unescape(re.sub(r"<[^>]+>", "", w)).strip() for w in spans]


def fix_rules(rows: list[str]) -> list[tuple[str, str]]:
    """(regex, remplacement) des lignes de règle de fixes.tsv ; commentaires et lignes sans tabulation ignorés."""
    return [tuple(r.split("\t", 1)) for r in rows if r.strip() and not r.startswith("#") and "\t" in r]


def parse_remark(line: str):
    """Remarque « p.N: genre … » de review.txt -> (page, texte, genre, mot), ou None.

    `mot` (entre « ») n'est renseigné que pour les « mot inconnu » / « mot douteux »,
    y compris « césure recollée, mot inconnu »."""
    m = re.match(r"p\.(\S+): (.*)", line)
    if not m:
        return None
    page, rest = m.groups()
    kind = re.split(r"[(:«]", rest, maxsplit=1)[0].strip() or "autre"
    w = re.search(r"«\s*(.+?)\s*»", rest)
    word = w.group(1) if w and kind.endswith(("mot inconnu", "mot douteux")) else None
    return page, rest, kind, word


def near_pages(page: str) -> list[str]:
    """Page à ±1 près : la remarque porte la page de la ligne OCR, le relecteur celle où il voit le mot."""
    return [str(int(page) + d) for d in (0, -1, 1)] if page.isdigit() else [page]


def iter_prop_mots(rel: Path):
    """Verdicts des sous-agents « mots » : (page, mot OCR, mot imprimé, verdict) de chaque prop-mots*.tsv.

    Lecture stricte (BOM toléré) : un fichier mal encodé doit échouer, pas perdre ses verdicts en silence."""
    for f in sorted(rel.glob("prop-mots*.tsv")):
        for row in f.read_text("utf-8-sig").splitlines():
            c = [x.strip() for x in row.split("\t")]
            if len(c) >= 4:
                yield c[0].removeprefix("p.").strip(), c[1], c[2], c[3]


def book_text(data: dict) -> str:
    """Tout le texte livré (content.json), un paragraphe par ligne, sans marqueurs internes, apostrophes droites."""
    return straight_apos(plain("\n".join(all_paras(data))))


def decision_key(s: str) -> str:
    """Clé d'une ligne de decisions.tsv : casse, apostrophes, espaces et ponctuation de bord ne comptent pas."""
    s = straight_apos(s).replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip(EDGE_PUNCT).casefold()


def read_decisions(work: Path) -> dict[str, tuple[str, str, str]]:
    """WORK/decisions.tsv : « forme lue <TAB> forme retenue <TAB> raison », écrit par l'orchestrateur.

    Sert à trancher entre deux relecteurs en désaccord et à refuser une proposition (forme retenue = forme lue).
    La raison est obligatoire : elle est imprimée dans le rapport.
    Renvoie {clé de la forme lue: (forme retenue, raison, forme lue telle qu'elle est écrite, casse comprise)}."""
    f = work / "decisions.tsv"
    out = {}
    for n, row in enumerate(f.read_text("utf-8-sig").splitlines() if f.exists() else [], 1):
        if not row.strip() or row.startswith("#"):
            continue
        c = [x.strip() for x in row.split("\t")]
        if len(c) < 3 or not c[0] or not c[1] or not c[2]:
            sys.exit(f"decisions.tsv ligne {n} : il faut 3 champs séparés par une tabulation "
                     "(forme lue, forme retenue, raison) ; la raison est obligatoire.")
        out[decision_key(c[0])] = (c[1], c[2], c[0])
    return out


SHARED = SKILL_DIR / "corrections-ocr.csv"


def read_shared() -> list[tuple[str, str, str]]:
    """Table partagée entre tous les livres : (langue, mot lu par l'OCR, mot corrigé). Voir corrections-ocr.csv."""
    if not SHARED.exists():
        return []
    with SHARED.open(encoding="utf-8-sig", newline="") as f:
        # table éditable à la main : une ligne incomplète (champ manquant : None) est ignorée, elle ne fait pas échouer
        # l'extraction de tous les livres
        rows = [tuple((r.get(k) or "").strip() for k in ("langue", "lu", "corrige")) for r in csv.DictReader(f)]
    return [r for r in rows if r[1] and r[2]]


def write_shared(rows: list[tuple[str, str, str]]):
    """Réécrit la table d'un seul coup (fichier provisoire puis remplacement) : une extraction lancée au même moment
    pour un autre livre ne lit jamais une table à moitié écrite. Ordre stable d'un lancement à l'autre."""
    tmp = SHARED.with_name(SHARED.name + ".part")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["langue", "lu", "corrige"])
            w.writerows(sorted(set(rows), key=lambda r: (r[0], r[1].lower(), r)))
        tmp.replace(SHARED)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------- Tesseract
def find_tesseract() -> str | None:
    cand = [os.environ.get("TESSERACT_CMD"), shutil.which("tesseract")]
    if platform.system() == "Windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            cand.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    else:
        cand += ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract"]
    for c in cand:
        if c and Path(c).exists():
            return c
    return None


TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_best/main/{}.traineddata"


def ensure_tessdata(langs: str) -> Path:
    """Télécharge (une fois) les modèles tessdata_best demandés dans le cache utilisateur."""
    import urllib.request
    d = CACHE / "tessdata"
    d.mkdir(parents=True, exist_ok=True)
    for lang in langs.split("+"):
        f = d / f"{lang}.traineddata"
        if f.exists() and f.stat().st_size > 100_000:
            continue
        print(f"Téléchargement du modèle OCR {lang} (tessdata_best)…")
        tmp = f.with_suffix(".part")
        urllib.request.urlretrieve(TESSDATA_URL.format(lang), tmp)
        tmp.replace(f)
    return d
