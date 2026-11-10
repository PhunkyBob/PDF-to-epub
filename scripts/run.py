#!/usr/bin/env python3
"""Chaîne complète : ocr -> lost -> italics -> extract -> illustrations -> cover -> build -> validate.

Usage :
  run.py --work DOSSIER [--from ETAPE] [--to ETAPE] [--jobs N] [--pages 15-25]
Étapes : ocr, lost, italics, extract, illustrations, cover, build, validate. L'OCR et les lignes perdues sont mis en cache :
après une correction de config ou de fixes.tsv, relancer avec --from extract.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import Progress, parser

SCRIPT = {"ocr": "ocr.py", "lost": "lignes_perdues.py", "italics": "italiques.py", "extract": "extract_text.py",
          "illustrations": "illustrations.py", "cover": "make_cover.py",
          "build": "build_epub.py", "validate": "validate.py"}
STEPS = list(SCRIPT)


def main():
    ap = parser(__doc__)
    ap.add_argument("--from", dest="start", choices=STEPS, default="ocr")
    ap.add_argument("--to", dest="end", choices=STEPS, default="validate")
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--pages", default=None, help="OCR limité à ces pages (essai), ex. 8-9,15-25")
    a = ap.parse_args()
    here = Path(__file__).resolve().parent
    work_args = ["--work", a.work] + (["--config", a.config] if a.config else [])
    steps = STEPS[STEPS.index(a.start): STEPS.index(a.end) + 1]
    cfg_path = Path(a.config) if a.config else Path(a.work) / "book.json"
    post_extract = json.loads(cfg_path.read_text("utf-8")).get("post_extract", "") if cfg_path.exists() else ""
    prog = Progress(Path(a.work), "run", fresh=True)
    prog.log(f"lancement : {' -> '.join(steps)} (journal : {prog.path})")
    for i, step in enumerate(steps, 1):
        print(f"\n===== {step} ({i}/{len(steps)}) =====", flush=True)
        cmd = [sys.executable, str(here / SCRIPT[step])] + work_args
        if step == "ocr":
            cmd += (["--jobs", str(a.jobs)] if a.jobs else []) + (["--pages", a.pages] if a.pages else [])
        prog.step = f"{i}/{len(steps)} {step}"
        prog.log("début")
        t = time.time()
        r = subprocess.run(cmd, env=dict(os.environ, PDF2EPUB_STEP=prog.step))
        if r.returncode != 0:
            prog.log(f"ÉCHEC (code {r.returncode})")
            sys.exit(f"Échec à l'étape « {step} » (code {r.returncode}).")
        prog.log(f"fin ({time.time() - t:.0f} s)")
        if step == "extract" and post_extract:
            # post-traitement propre au livre (ex. titres de chapitre établis à la main) : rejoué à chaque extraction
            hook = Path(a.work) / post_extract
            print(f"\n===== post_extract : {hook.name} =====", flush=True)
            r = subprocess.run([sys.executable, str(hook), "--work", a.work])
            if r.returncode != 0:
                prog.log(f"ÉCHEC du post-traitement (code {r.returncode})")
                sys.exit(f"Échec du post-traitement « {post_extract} » (code {r.returncode}).")
    prog.step = "fin"
    prog.done("chaîne terminée")
    print("\nTerminé.")


if __name__ == "__main__":
    main()
