"""Livre l'EPUB et le rapport de relecture dans le même dossier que le PDF d'origine.

Le dossier de travail est `<nom du pdf>.epub-work`, créé à côté du PDF : on copie donc build/*.epub et
build/rapport-relecture.md dans le dossier parent. Le rapport est nommé d'après l'EPUB
(`<epub>.rapport-relecture.md`). À relancer après chaque reconstruction ou mise à jour du rapport.
"""
import argparse
import json
import shutil
from pathlib import Path

from coherence import digest
from common import pick_epub


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--malgre-notes", action="store_true",
                    help="livrer malgré des notes sans appel (seulement sur décision explicite de l'utilisateur)")
    a = ap.parse_args()
    work = Path(a.work)
    qa = work / "build" / "qa.txt"
    bad = [l for l in (qa.read_text("utf-8").splitlines() if qa.exists() else []) if "sans appel" in l or "appel sans note" in l]
    if bad and not a.malgre_notes:
        raise SystemExit("livraison refusée, notes non reliées (note_anchors dans config.md) :\n  " + "\n  ".join(bad))
    nap = work / "build" / "italiques-non-appliques.txt"
    if nap.exists() and nap.read_text("utf-8").strip():
        print("attention, verdicts d'italique non appliqués :", " ; ".join(nap.read_text("utf-8").split("\n")))
    # destination = dossier du PDF d'origine (book.json), pas le parent du chemin donné : un lien ou une jonction
    # vers le dossier de travail (ex. C:\Temp\mw) ne doit pas faire livrer ailleurs
    dest = work.resolve().parent
    cfg_f = work / "book.json"
    cfg = json.loads(cfg_f.read_text("utf-8")) if cfg_f.exists() else {}
    pdf = Path(cfg.get("pdf", ""))
    pdf = pdf if pdf.is_absolute() else (work / pdf)
    if pdf.name and pdf.resolve().parent.exists():
        dest = pdf.resolve().parent
    epub = pick_epub(work, cfg)
    if epub is None:
        raise SystemExit("aucun EPUB dans build/ : lancer run.py d'abord")
    rapport = work / "build" / "rapport-relecture.md"
    if not rapport.exists():
        raise SystemExit("pas de rapport : rédiger rapport.json puis lancer rapport_humain.py")
    stamp = work / "build" / "rapport-relecture.epub.sha256"
    if not stamp.exists() or stamp.read_text("utf-8").strip() != digest(epub):
        raise SystemExit("le rapport n'a pas été contrôlé sur cet EPUB : relancer rapport_humain.py avant de livrer")
    shutil.copyfile(epub, dest / epub.name)
    shutil.copyfile(rapport, dest / f"{epub.stem}.rapport-relecture.md")
    print(f"livré : {epub.name} + {epub.stem}.rapport-relecture.md")
    print("dossier :", dest)


if __name__ == "__main__":
    main()
