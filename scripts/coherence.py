"""Compare ce qu'affirme le rapport de relecture à ce que contient réellement l'EPUB.

Le contrôle lit l'EPUB lui-même, jamais les fichiers intermédiaires. Il vérifie dans rapport.json :
  * résumé et contrôles : les décomptes (« 44 chapitres », « 8 gravures », « 112 notes », « deux parties ») ;
  * points et décisions : les champs « dans_epub » (extraits recopiés de l'EPUB) et « plus_dans_epub » (formes disparues).
Un point qui cite le texte et la décision « Écarts volontaires… » doivent porter « dans_epub » : l'affirmation s'appuie ainsi sur ce
qui a été livré, pas sur ce qui est imprimé. Le texte libre n'est pas compris par le contrôle.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from html import unescape
from pathlib import Path

QUOTE = re.compile(r"«\s*([^«»]+?)\s*»")
COUNT = re.compile(r"\b(\d+|deux|trois|quatre|cinq|six|sept|huit|neuf|dix)\s+"
                   r"(chapitres?|notes?|gravures?|illustrations?|photos?|parties?|textes? liminaires?)\b", re.I)
NUMBERS = dict(deux=2, trois=3, quatre=4, cinq=5, six=6, sept=7, huit=8, neuf=9, dix=10)
KINDS = dict(chapitre="chapitres", note="notes", gravure="figures", illustration="figures", photo="figures",
             partie="parties", texte="liminaires")


def normalize(s: str) -> str:
    """Forme de comparaison : espaces fines et insécables, apostrophes et casse ne comptent pas."""
    s = re.sub(r"[’‘ʼ]", "'", s).replace(" ", " ").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip().casefold()


class Epub:
    """Texte et décomptes lus dans le fichier EPUB."""

    def __init__(self, path: Path):
        pages = {}
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.startswith("OEBPS/text/") and name.endswith(".xhtml"):
                    pages[name] = z.read(name).decode("utf-8")
        raw = "\n".join(pages.values())
        self.counts = dict(chapitres=len(re.findall(r'<section epub:type="chapter"', raw)),
                           notes=len(re.findall(r'epub:type="footnote"', raw)),
                           figures=len(re.findall(r'<figure class="illustration"', raw)),
                           parties=len(re.findall(r'<section epub:type="part"', raw)),
                           liminaires=len(re.findall(r'<body epub:type="frontmatter"', raw)))
        body = re.sub(r"<head>.*?</head>", " ", raw, flags=re.S)
        body = re.sub(r'<sup><a class="appel".*?</sup>', "", body, flags=re.S)     # appels de note : hors du fil du texte
        body = re.sub(r"</(?:p|h1|h2|div|li|section|figure)>|<br\s*/>", " ", body)
        self.text = normalize(unescape(re.sub(r"<[^>]+>", "", body)))

    def has(self, quote: str) -> bool:
        """Mots entiers ; une citation abrégée par « … » est cherchée morceau par morceau."""
        parts = [p for p in (normalize(x) for x in re.split(r"…|\[\.\.\.\]", quote)) if any(c.isalnum() for c in p)]
        return bool(parts) and all(re.search(rf"(?<!\w){re.escape(p)}(?!\w)", self.text) for p in parts)


def audit(rap: dict, epub: Epub) -> tuple[list[str], int]:
    """Contradictions entre rapport.json et l'EPUB, et nombre d'affirmations vérifiées."""
    problems, checked = [], 0
    for field, text in [("résumé", rap.get("resume", ""))] + [("contrôles", c) for c in rap.get("controles", [])]:
        for m in COUNT.finditer(text):
            n = int(m[1]) if m[1].isdigit() else NUMBERS[m[1].lower()]
            real = epub.counts[KINDS[m[2].lower().split()[0].rstrip("s")]]
            checked += 1
            if n != real:
                problems.append(f"{field} : « {m[0]} », mais l'EPUB en contient {real}")
    items = [(f"point « {p.get('ou', '')} »", p, bool(QUOTE.search(p.get("constat", "") + p.get("traite", ""))))
             for p in rap.get("points", [])]
    items += [(f"décision « {d.get('sujet', '')} »", d, d.get("sujet", "").startswith("Écarts volontaires"))
              for d in rap.get("decisions", [])]
    for name, item, required in items:
        if required and "dans_epub" not in item:
            problems.append(f"{name} : cite le texte sans « dans_epub » (recopier ce que dit l'EPUB à cet endroit ; "
                            "liste vide si la citation n'est pas du texte du livre)")
        for q in item.get("dans_epub", []):
            checked += 1
            if not epub.has(q):
                problems.append(f"{name} : « {q} » est absent de l'EPUB")
        for q in item.get("plus_dans_epub", []):
            checked += 1
            if epub.has(q):
                problems.append(f"{name} : « {q} » est encore dans l'EPUB")
    return problems, checked


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
