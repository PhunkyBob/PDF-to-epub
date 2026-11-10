# Skill pdf-to-epub

Transforme un livre PDF (scanné ou numérique) en EPUB 3 propre et validé. Mode d'emploi pour l'agent : `SKILL.md`.

Utilisation manuelle :
```
uv run --project . python scripts/inspect_pdf.py "livre.pdf"         # diagnostic + book.json
# éditer "<livre>.epub-work/book.json" (voir references/config.md)
uv run --project . python scripts/run.py --work "<livre>.epub-work"   # OCR -> EPUB -> EPUBCheck
```
Prérequis : uv, Tesseract (PDF scannés), Java (EPUBCheck). Voir `SKILL.md` § 0.
