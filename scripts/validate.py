#!/usr/bin/env python3
"""Étape 5 : validation.

  1. contrôle qualité du texte (content.json) -> <work>/build/qa.txt :
     folios ou en-têtes oubliés, paragraphes coupés en milieu de phrase, césures non recollées,
     paragraphes anormalement longs, chapitres vides, marqueurs d'italique déséquilibrés ;
  2. EPUBCheck (validateur officiel W3C/DAISY, nécessite Java).
Code retour ≠ 0 si EPUBCheck trouve des erreurs.
Usage : validate.py --work DOSSIER
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import IC, IO, NOTE_RX, SUB_MARK, Ctx, Progress, ctx_from_args, parser, plain


def qa(ctx: Ctx) -> list[str]:
    d = json.loads((ctx.build / "content.json").read_text("utf-8"))
    title_up = re.sub(r"\W", "", (ctx.cfg["title"] or "").upper())
    issues = []
    units = [(s["title"], s["paras"]) for s in d["front"]] + \
            [(f"chap {c['number']} « {c['title']} »", c["paras"]) for c in d["chapters"]] + \
            [(s["title"], s["paras"]) for s in d.get("back", [])]
    for name, paras in units:
        if not paras:
            issues.append(f"[{name}] aucun paragraphe")
        for i, p in enumerate(paras):
            t = plain(p)
            if p.count(IO) != p.count(IC):
                issues.append(f"[{name}] §{i + 1} italique déséquilibré")
            if re.fullmatch(r"[\dIVXLCivxlc .-]{1,8}", t):
                issues.append(f"[{name}] §{i + 1} folio isolé ? « {t} »")
            if title_up and len(title_up) > 5 and title_up in re.sub(r"\W", "", t):
                issues.append(f"[{name}] §{i + 1} titre courant oublié ? « {t[:60]} »")
            for m in re.finditer(r"\w+[-\u2010\u00ad] \w+", t):
                if not re.search(r"- (et|ou|and|or)\b", m.group(0)):
                    issues.append(f"[{name}] §{i + 1} césure non recollée ? « {m.group(0)} »")
            if len(t) > 5000:
                issues.append(f"[{name}] §{i + 1} paragraphe très long ({len(t)} car.) : coupures manquées ?")
            if i + 1 < len(paras):
                nxt = plain(paras[i + 1])
                if not re.search(r"[.!?…»:;)\]”\"’—]$", t) and nxt[:1].islower() and not p.startswith(SUB_MARK):
                    issues.append(f"[{name}] §{i + 1}-{i + 2} paragraphe coupé en milieu de phrase ? "
                                  f"« …{t[-30:]} | {nxt[:30]}… »")
    for sec in d["front"] + d["chapters"] + d.get("back", []):
        name = sec["title"] or sec["id"]
        calls = NOTE_RX.findall(" ".join(sec["paras"]))
        notes = sec.get("notes") or []
        heads = [n[0] for n in notes if NOTE_RX.match(n)]
        for n in notes:
            if not NOTE_RX.match(n) or n[0] not in calls:
                issues.append(f"[{name}] note sans appel : « {plain(n)[:60]} »")
        issues += [f"[{name}] appel sans note" for c in calls if c not in heads]
    placed = ctx.build / "illustrations.txt"
    if placed.exists():
        issues += [f"[illustration] {x[3:]}" for x in placed.read_text("utf-8").splitlines() if x.startswith("!! ")]
    return issues


def epubcheck(path: Path) -> int:
    if not shutil.which("java"):
        print("Java introuvable : EPUBCheck ignoré. Installez un JRE :\n"
              "  Windows : winget install EclipseAdoptium.Temurin.21.JRE\n"
              "  macOS   : brew install openjdk      Linux : sudo apt install default-jre-headless")
        return 0
    # Java ne retrouve pas un fichier dont le chemin contient des accents (surtout décomposés) :
    # on valide une copie placée sous un chemin ASCII.
    with tempfile.TemporaryDirectory(prefix="epubcheck-") as tmp:
        copy = Path(tmp) / "book.epub"
        shutil.copyfile(path, copy)
        r = subprocess.run([sys.executable, "-m", "epubcheck", str(copy)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    out = (r.stdout + r.stderr).strip()
    if r.returncode and not out:
        out = "EPUBCheck n'a produit aucun message (échec d'exécution ?)."
    print(out or "EPUBCheck : aucune erreur ni avertissement.")
    return r.returncode


def main():
    a = parser(__doc__).parse_args()
    ctx = ctx_from_args(a)
    prog = Progress(ctx.work, "validate")
    prog.log("contrôle qualité du texte…")
    issues = qa(ctx)
    (ctx.build / "qa.txt").write_text("\n".join(issues) + "\n", "utf-8")
    print(f"Contrôle qualité : {len(issues)} point(s) -> {ctx.build / 'qa.txt'}")
    for line in issues[:15]:
        print("  " + line)
    if not ctx.output.exists():
        sys.exit(f"EPUB introuvable : {ctx.output}")
    prog.log(f"{len(issues)} point(s) de contrôle qualité ; EPUBCheck en cours (Java)…")
    rc = epubcheck(ctx.output)
    prog.done("EPUBCheck OK" if rc == 0 else "EPUBCheck : ERREURS")
    print("EPUBCheck : OK" if rc == 0 else "EPUBCheck : ERREURS")
    sys.exit(rc)


if __name__ == "__main__":
    main()
