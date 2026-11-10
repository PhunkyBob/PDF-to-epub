#!/usr/bin/env python3
"""Étape 4 : content.json + cover.jpg -> EPUB 3.3 conforme.

Bonnes pratiques appliquées :
  * conteneur OCF : « mimetype » en premier, non compressé ; META-INF/container.xml ;
  * un XHTML par chapitre (UTF-8, lang/xml:lang, section + epub:type + rôles DPUB-ARIA) ;
  * document de navigation EPUB 3 (toc + landmarks), placé dans l'ordre de lecture, + toc.ncx de compatibilité ;
  * métadonnées Dublin Core (titre, auteur « aut », traducteur « trl », éditeur, date, langue,
    description, dcterms:modified) et métadonnées d'accessibilité schema.org (EPUB Accessibility 1.1) ;
  * CSS minimal en unités relatives, sans police ni taille imposées ; césure automatique ;
  * couverture déclarée (cover-image) avec texte alternatif ; italique en <em> ;
  * parties du livre (option parts) : page de titre propre et table des matières à deux niveaux ;
  * illustrations du livre (champ « figures » posé par illustrations.py) en <figure>, après leur paragraphe ;
  * archive reproductible (dates fixes, ordre stable).
Usage : build_epub.py --work DOSSIER
"""
from __future__ import annotations

import datetime
import itertools
import json
import re
import zipfile
from xml.sax.saxutils import escape

from common import IC, IO, NOTE_RX, SCENE_MARK, SUB_MARK, Ctx, Progress, ctx_from_args, parser, plain

NS = 'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
# types de section qui ont un rôle DPUB-ARIA « doc-<type> »
DPUB_ROLES = {"foreword", "preface", "introduction", "afterword", "epilogue", "prologue", "appendix",
              "conclusion", "endnotes"}

LABELS = {
    "fr": dict(toc="Table des matières", landmarks="Repères", cover="Couverture", titlepage="Page de titre",
               start="Début du texte", legal="Mentions légales", translated="Traduit par", notes="Notes",
               summary="Livre textuel au format réflexible : texte redimensionnable, table des matières "
                       "navigable, ordre de lecture linéaire. La couverture porte un texte alternatif.",
               illustrated=" Les illustrations portent un texte alternatif.", figure="Illustration de l’édition imprimée"),
    "en": dict(toc="Contents", landmarks="Landmarks", cover="Cover", titlepage="Title page",
               start="Start of text", legal="Copyright", translated="Translated by", notes="Notes",
               summary="Reflowable text-only book: resizable text, navigable table of contents, "
                       "linear reading order. The cover image has alternative text.",
               illustrated=" Illustrations have alternative text.", figure="Illustration from the printed edition"),
}

CSS = """\
/* Feuille de style minimale : aucune police ni taille de corps imposée (le lecteur garde la main). */
html { -epub-hyphens: auto; hyphens: auto; }
body { margin: 0; line-height: 1.45; text-align: justify; orphans: 2; widows: 2; }
p { margin: 0; text-indent: 1.3em; }
h1 + p, h2 + p, .sans-retrait { text-indent: 0; }
p.italique { font-style: italic; }
p.scene { text-align: center; text-indent: 0; margin: 1em 0; }
p.scene + p { text-indent: 0; }
h2.intertitre { font-size: 1.05em; font-weight: bold; margin: 1.6em 0 .8em; }
p.italique em { font-style: normal; }
p.signature { text-align: right; text-indent: 0; margin-top: 1.5em; }
h1, h2 { font-weight: normal; text-align: center; hyphens: manual; -epub-hyphens: manual;
         page-break-after: avoid; break-after: avoid; }
h1 { font-size: 1.5em; margin: 3em 0 2em; line-height: 1.2; }
h1 .num { display: block; font-size: .7em; letter-spacing: .2em; margin-bottom: .6em; }
h1 .titre { font-style: italic; }
nav ol { list-style: none; padding: 0; }
nav li { margin: .3em 0; text-indent: 0; }
nav ol ol { padding-left: 1.5em; }
body.cover { text-align: center; }
body.cover img { max-width: 100%; max-height: 100vh; height: auto; }
.titlepage { text-align: center; margin-top: 18%; }
.titlepage p { text-indent: 0; text-align: center; }
.titlepage .auteur { font-size: 1.1em; letter-spacing: .15em; text-transform: uppercase; margin-bottom: 2.5em; }
.titlepage h1 { margin: 0 0 .5em; font-size: 2.2em; letter-spacing: .08em; text-transform: uppercase; }
.titlepage .sous-titre, .titlepage .orig, .titlepage .trad { font-style: italic; margin-top: 1.5em; }
.titlepage .editeur { margin-top: 4em; }
.partie { text-align: center; margin-top: 25%; }
.partie h1 { margin: 0 0 2em; }
.partie h1 .num { font-size: 1em; letter-spacing: 0; margin-bottom: 1.5em; }
.partie h1 .titre { font-style: normal; font-weight: bold; text-transform: uppercase; }
.partie p { text-indent: 0; text-align: center; font-style: italic; }
.colophon p { text-indent: 0; text-align: left; margin: 0 0 1em; font-size: .9em; }
div.notes { margin-top: 2.5em; border-top: 1px solid currentColor; padding-top: .5em; font-size: .9em; }
div.notes h2 { font-size: 1em; text-align: left; margin: 0 0 .5em; }
div.notes p { text-indent: 0; margin: 0 0 .4em; }
sup { font-size: .7em; line-height: 0; vertical-align: super; }
a.appel, a.retour { text-decoration: none; }
.debut { text-transform: uppercase; }
figure.illustration { margin: 1em 0; text-align: center; text-indent: 0; page-break-inside: avoid; break-inside: avoid; }
figure.illustration img { max-width: 100%; max-height: 92vh; height: auto; }
"""


def roman(n):
    out = ""
    for v, s in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
                 (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += s; n -= v
    return out


CALLS: dict[str, str] = {}     # marqueur d'appel de note -> balisage (rempli par Builder.number_notes)
ABBREV = re.compile(r"\bM(lle|me|gr)\b|\b1(er|re)\b")
SUPERSCRIPTS = [False]         # exposants des abréviations françaises (option superscript_abbrevs)


def finish(html):
    """Appels de note et abréviations en exposant, sur du texte déjà échappé."""
    if SUPERSCRIPTS[0]:
        html = ABBREV.sub(lambda m: f"M<sup>{m[1]}</sup>" if m[1] else f"1<sup>{m[2]}</sup>", html)
    return NOTE_RX.sub(lambda m: CALLS.get(m[0], ""), html)


def inline(text):
    """Échappement XML + italique, appels de note, exposants."""
    return finish(escape(text).replace(IO, "<em>").replace(IC, "</em>"))


def para(text):
    if text == SCENE_MARK:
        return f'<p class="scene">{SCENE_MARK}</p>'
    if text.startswith(SUB_MARK):
        return f'<h2 class="intertitre">{inline(text[1:])}</h2>'
    if text.startswith(IO) and text.endswith(IC) and text.count(IO) == 1:
        return f'<p class="italique">{finish(escape(text[1:-1]))}</p>'
    return f"<p>{inline(text)}</p>"


def is_signature(text):
    """Dernier paragraphe d'une préface ou postface : nom en capitales, sans ponctuation finale."""
    t = plain(text)
    return 0 < len(t) <= 60 and t.isupper() and t[-1].isalpha()


def signature(text):
    return f'<p class="signature">{inline(text)}</p>'


class Builder:
    def __init__(self, ctx: Ctx):
        self.ctx, self.cfg = ctx, ctx.cfg
        self.data = json.loads((ctx.build / "content.json").read_text("utf-8"))
        self.lang = self.cfg["language"]
        self.L = LABELS.get(self.lang.split("-")[0], LABELS["en"])
        self.files, self.manifest, self.spine, self.nav = {}, [], [], []
        self.level = 0                 # niveau des entrées de la table des matières : 1 à l'intérieur d'une partie

    def xhtml(self, title, body, body_attr="", css="../css/style.css"):
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html {NS} lang="{self.lang}" xml:lang="{self.lang}">
<head>
<meta charset="utf-8"/>
<title>{escape(title)}</title>
<link rel="stylesheet" type="text/css" href="{css}"/>
</head>
<body{(' ' + body_attr) if body_attr else ''}>
{body}
</body>
</html>
""".encode("utf-8")

    def add(self, id_, href, data, mtype="application/xhtml+xml", props="", spine=True, nav=None):
        self.files["OEBPS/" + href] = data
        self.manifest.append((id_, href, mtype, props))
        if spine:
            self.spine.append(id_)
        if nav:
            self.nav.append((href, nav, self.level))

    def numlabel(self, n):
        mode = self.cfg["chapter_numbers"]
        if not n or mode == "none":
            return ""
        return roman(n) if mode == "roman" else str(n)

    def build(self):
        self.number_notes()
        c, L = self.cfg, self.L
        title = c["title"] or self.ctx.pdf.stem
        # couverture
        cover = self.ctx.build / "cover.jpg"
        self.add("cover-img", "images/cover.jpg", cover.read_bytes(), "image/jpeg", "cover-image", spine=False)
        alt = f"{L['cover']} : {title}" + (f", {c['author']}" if c["author"] else "")
        self.add("cover", "text/cover.xhtml", self.xhtml(
            title, f'<section epub:type="cover"><img src="../images/cover.jpg" alt="{escape(alt)}"/></section>',
            'class="cover"'))
        # page de titre
        tp = ['<section epub:type="titlepage" class="titlepage">']
        if c["author"]:
            tp.append(f'<p class="auteur">{escape(c["author"])}</p>')
        tp.append(f'<h1>{escape(title)}</h1>')
        if c["subtitle"]:
            tp.append(f'<p class="sous-titre">{escape(c["subtitle"])}</p>')
        if c["original_title"]:
            tp.append(f'<p class="orig">({escape(c["original_title"])})</p>')
        trad = c["translator_line"] or (f'{L["translated"]} {c["translator"]}' if c["translator"] else "")
        if trad:
            tp.append(f'<p class="trad">{escape(trad)}</p>')
        if c["publisher"]:
            tp.append(f'<p class="editeur">{escape(c["publisher"])}</p>')
        tp.append("</section>")
        self.add("titlepage", "text/titlepage.xhtml", self.xhtml(title, "\n".join(tp)))
        # mentions légales
        if c["colophon"]:
            body = ('<section epub:type="copyright-page" class="colophon">\n'
                    + "\n".join(f"<p>{inline(p)}</p>" for p in c["colophon"]) + "\n</section>")
            self.add("colophon", "text/colophon.xhtml", self.xhtml(L["legal"], body))
        self.spine.append("nav")                       # table des matières lisible avant le texte
        # sections liminaires
        for s in self.data["front"]:
            self.section(s, "frontmatter")
        first_body = start = None
        parts = sorted(c.get("parts") or [], key=lambda p: p["page"])
        made = 0
        for ch in self.data["chapters"]:
            while parts and ch["page"] >= parts[0]["page"]:
                made += 1
                href = self.part(parts.pop(0), made)
                start = start or href
            num = self.numlabel(ch["number"])
            h = (f'<h1 id="titre-{ch["id"]}">' + (f'<span class="num">{num}</span>' if num else "")
                 + f'<span class="titre">{inline(ch["title"])}</span></h1>') if (ch["title"] or num) else ""
            lab = " ".join(x for x in (f"{num}." if num else "", plain(ch["title"])) if x) \
                or (f"{ch['number']}" if ch["number"] else "Texte liminaire" if first_body is None else "Sans titre")
            aria = f' aria-labelledby="titre-{ch["id"]}"' if h else ""
            body = (f'<section epub:type="chapter" role="doc-chapter"{aria}>\n{h}\n'
                    + self.paragraphs(ch, lambda i, p: self.opening(para(p)) if i == 0 else para(p))
                    + self.notes(ch) + "\n</section>")
            href = f'text/{ch["id"]}.xhtml'
            self.add(ch["id"], href, self.xhtml(lab, body, 'epub:type="bodymatter"'), nav=lab)
            first_body = first_body or href
        for p in parts:
            print(f"attention : partie « {p['title']} » (p. {p['page']}) sans chapitre après elle, ignorée")
        self.level = 0
        for s in self.data.get("back", []):
            self.section(s, "backmatter")
        self.navigation(title, start or first_body)
        self.add("css", "css/style.css", CSS.encode("utf-8"), "text/css", spine=False)
        self.package(title)
        self.write()

    def part(self, p, n):
        """Page de titre d'une partie ; les chapitres suivants passent au second niveau de la table des matières."""
        pid, num, title = f"partie-{n}", escape(p.get("number", "")), escape(p["title"])
        h = (f'<h1 id="titre-{pid}">' + (f'<span class="num">{num}</span>' if num else "")
             + f'<span class="titre">{title}</span></h1>')
        lines = "".join(f"\n<p>{inline(x)}</p>" for x in p.get("lines", []))
        body = f'<section epub:type="part" role="doc-part" class="partie" aria-labelledby="titre-{pid}">\n{h}{lines}\n</section>'
        label = f"{p.get('number')}. {p['title']}" if p.get("number") else p["title"]
        self.level = 0
        self.add(pid, f"text/{pid}.xhtml", self.xhtml(label, body, 'epub:type="bodymatter"'), nav=label)
        self.level = 1
        return f"text/{pid}.xhtml"

    def nav_tree(self):
        """Entrées de la table des matières : (lien, libellé, sous-entrées)."""
        tree = []
        for href, label, level in self.nav:
            if level and tree:
                tree[-1][2].append((href, label, []))
            else:
                tree.append((href, label, []))
        return tree

    def paragraphs(self, sec, render):
        """Paragraphes de la section, chaque illustration à la suite de celui qu'elle suit."""
        last = len(sec["paras"]) - 1
        figures: dict[int, list] = {}
        for f in sec.get("figures") or []:
            figures.setdefault(min(f["after"], last), []).append(self.figure(f))
        out = []
        for i, p in enumerate(sec["paras"]):
            out.append(render(i, p))
            out += figures.get(i, [])
        return "\n".join(out)

    def figure(self, f):
        name = f["file"]
        self.add(name.rsplit(".", 1)[0], f"images/{name}", (self.ctx.build / "illustrations" / name).read_bytes(),
                 "image/jpeg", spine=False)
        alt = escape(f.get("alt") or self.L["figure"], {'"': "&quot;"})
        return f'<figure class="illustration"><img src="../images/{name}" alt="{alt}"/></figure>'

    def opening(self, html):
        """Premier mot du chapitre affiché en majuscules (option opening_caps) : le texte garde sa casse normale,
        seule la feuille de style le met en capitales (recherche et lecture vocale restent justes)."""
        if not self.cfg.get("opening_caps"):
            return html
        return re.sub(r"^(<p[^>]*>(?:—\u00a0)?(?:<em>)?)([^\W\d_][\w’'-]*)", r'\1<span class="debut">\2</span>', html, count=1)

    def number_notes(self):
        """Numérote les notes dans l'ordre de leurs appels et prépare le balisage des appels."""
        CALLS.clear()
        SUPERSCRIPTS[0] = bool(self.cfg.get("superscript_abbrevs")) and self.lang.startswith("fr")
        n = 0
        for sec in self.data["front"] + self.data["chapters"] + self.data.get("back", []):
            if self.cfg.get("note_numbering") == "chapter":
                n = 0
            notes = sec.get("notes") or []
            by_mark = {x[0]: x for x in notes if NOTE_RX.match(x)}
            ordered = []
            for m in NOTE_RX.findall(" ".join(sec["paras"])):
                if m in by_mark:
                    ordered.append(by_mark.pop(m))
            ordered += [x for x in notes if x not in ordered]    # notes sans appel : en fin de liste
            sec["numbered"] = []
            for x in ordered:
                linked = bool(NOTE_RX.match(x)) and x[0] in NOTE_RX.findall(" ".join(sec["paras"]))
                if not linked:                               # sans appel : pas de numéro, les autres ne se décalent pas
                    sec["numbered"].append((None, None, False, NOTE_RX.sub("", x)))
                    continue
                n += 1
                nid = f"note-{sec['id']}-{n}"
                CALLS[x[0]] = (f'<sup><a class="appel" id="appel-{sec["id"]}-{n}" href="#{nid}" '
                               f'epub:type="noteref" role="doc-noteref">{n}</a></sup>')
                sec["numbered"].append((n, nid, True, NOTE_RX.sub("", x)))

    def notes(self, sec):
        """Notes en fin de chapitre, visibles dans le fil du texte et ouvertes en popup depuis leur appel.

        Le type « footnote » sur un <div> (pas un <aside>, qu'Apple Books masque) donne les deux à la fois."""
        if not sec.get("numbered"):
            return ""
        items = []
        for n, nid, linked, text in sec["numbered"]:
            if not linked:
                items.append(f"<p>{inline(text)}</p>")
                continue
            num = f'<a class="retour" href="#appel-{sec["id"]}-{n}" role="doc-backlink">{n}</a>'
            items.append(f'<div id="{nid}" epub:type="footnote"><p>{num}. {inline(text)}</p></div>')
        return ('\n<div class="notes">\n<h2>' + self.L["notes"] + '</h2>\n' + "\n".join(items) + "\n</div>")

    def section(self, s, matter):
        t = s.get("type", "foreword")
        r = f' role="doc-{t}"' if t in DPUB_ROLES else ""
        body = (f'<section epub:type="{t}"{r} class="{s["id"]}">\n<h1>{escape(s["title"])}</h1>\n'
                + self.paragraphs(s, lambda i, p: signature(p) if i == len(s["paras"]) - 1 and is_signature(p) else para(p))
                + self.notes(s) + "\n</section>")
        self.add(s["id"], f'text/{s["id"]}.xhtml', self.xhtml(s["title"], body, f'epub:type="{matter}"'),
                 nav=s["title"])

    def navigation(self, title, first_body):
        L = self.L
        tree = self.nav_tree()

        def item(h, t, sub):
            inner = "\n<ol>\n" + "\n".join(item(*x) for x in sub) + "\n</ol>\n" if sub else ""
            return f'<li><a href="{h}">{escape(t)}</a>{inner}</li>'

        lis = "\n".join(item(*x) for x in tree)
        lms = [("cover", "text/cover.xhtml", L["cover"]), ("titlepage", "text/titlepage.xhtml", L["titlepage"]),
               ("toc", "nav.xhtml#toc", L["toc"])]
        if first_body:
            lms.append(("bodymatter", first_body, L["start"]))
        lm = "\n".join(f'<li><a epub:type="{t}" href="{h}">{escape(x)}</a></li>' for t, h, x in lms)
        body = (f'<nav epub:type="toc" id="toc" role="doc-toc" aria-labelledby="toc-titre">\n'
                f'<h1 id="toc-titre">{L["toc"]}</h1>\n<ol>\n{lis}\n</ol>\n</nav>\n'
                f'<nav epub:type="landmarks" id="landmarks" hidden="hidden">\n<h2>{L["landmarks"]}</h2>\n'
                f'<ol>\n{lm}\n</ol>\n</nav>')
        self.add("nav", "nav.xhtml", self.xhtml(L["toc"], body, css="css/style.css"), props="nav", spine=False)
        order = itertools.count(1)

        def point(h, t, sub):
            i = next(order)
            inner = "\n" + "\n".join(point(*x) for x in sub) + "\n" if sub else ""
            return (f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
                    f'<content src="{h}"/>{inner}</navPoint>')

        pts = "\n".join(point(*x) for x in tree)
        depth = 2 if any(x[2] for x in tree) else 1
        ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{self.lang}">
<head><meta name="dtb:uid" content="{escape(self.cfg['identifier'])}"/><meta name="dtb:depth" content="{depth}"/>
<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>
<docTitle><text>{escape(title)}</text></docTitle>
<navMap>
{pts}
</navMap>
</ncx>
"""
        self.add("ncx", "toc.ncx", ncx.encode("utf-8"), "application/x-dtbncx+xml", spine=False)

    def package(self, title):
        c = self.cfg
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        md = [f'<dc:identifier id="pub-id">{escape(c["identifier"])}</dc:identifier>',
              f'<dc:title id="title">{escape(title)}</dc:title>',
              '<meta refines="#title" property="title-type">main</meta>']
        if c["subtitle"]:
            md += [f'<dc:title id="subtitle">{escape(c["subtitle"])}</dc:title>',
                   '<meta refines="#subtitle" property="title-type">subtitle</meta>']
        if c["author"]:
            md += [f'<dc:creator id="creator">{escape(c["author"])}</dc:creator>',
                   '<meta refines="#creator" property="role" scheme="marc:relators">aut</meta>',
                   f'<meta refines="#creator" property="file-as">{escape(c["author_sort"] or c["author"])}</meta>']
        if c["translator"]:
            md += [f'<dc:contributor id="translator">{escape(c["translator"])}</dc:contributor>',
                   '<meta refines="#translator" property="role" scheme="marc:relators">trl</meta>']
        if c["publisher"]:
            md.append(f'<dc:publisher>{escape(c["publisher"])}</dc:publisher>')
        if c["published"]:
            md.append(f'<dc:date>{escape(str(c["published"]))}</dc:date>')
        md.append(f'<dc:language>{escape(self.lang)}</dc:language>')
        if c["description"]:
            md.append(f'<dc:description>{escape(c["description"])}</dc:description>')
        illustrated = any(s.get("figures") for s in self.data["front"] + self.data["chapters"] + self.data.get("back", []))
        summary = self.L["summary"] + (self.L["illustrated"] if illustrated else "")
        md += [f'<meta property="dcterms:modified">{now}</meta>',
               '<meta property="schema:accessMode">textual</meta>',
               *(['<meta property="schema:accessMode">visual</meta>'] if illustrated else []),
               '<meta property="schema:accessModeSufficient">textual</meta>',
               '<meta property="schema:accessibilityFeature">structuralNavigation</meta>',
               '<meta property="schema:accessibilityFeature">tableOfContents</meta>',
               '<meta property="schema:accessibilityFeature">readingOrder</meta>',
               '<meta property="schema:accessibilityFeature">displayTransformability</meta>',
               '<meta property="schema:accessibilityFeature">alternativeText</meta>',
               '<meta property="schema:accessibilityHazard">none</meta>',
               f'<meta property="schema:accessibilitySummary">{escape(summary)}</meta>']
        mf = "\n".join(f'<item id="{i}" href="{h}" media-type="{m}"' + (f' properties="{p}"' if p else "") + "/>"
                       for i, h, m, p in self.manifest)
        sp = "\n".join(f'<itemref idref="{i}"/>' for i in self.spine)
        opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id" xml:lang="{self.lang}"
         prefix="schema: http://schema.org/">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
{chr(10).join(md)}
</metadata>
<manifest>
{mf}
</manifest>
<spine toc="ncx">
{sp}
</spine>
</package>
"""
        self.files["OEBPS/content.opf"] = opf.encode("utf-8")
        self.files["META-INF/container.xml"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
"""

    def write(self):
        out = self.ctx.output
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        stamp = (2000, 1, 1, 0, 0, 0)
        with zipfile.ZipFile(tmp, "w") as z:
            zi = zipfile.ZipInfo("mimetype", stamp); zi.compress_type = zipfile.ZIP_STORED
            z.writestr(zi, b"application/epub+zip")
            for name in sorted(self.files, key=lambda n: (n != "META-INF/container.xml", n)):
                zi = zipfile.ZipInfo(name, stamp); zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, self.files[name])
        tmp.replace(out)
        print(f"{out} : {out.stat().st_size // 1024} Ko, {len(self.data['chapters'])} chapitres")


def main():
    a = parser(__doc__).parse_args()
    ctx = ctx_from_args(a)
    prog = Progress(ctx.work, "build")
    prog.log("assemblage de l'EPUB…")
    Builder(ctx).build()
    prog.done(f"{ctx.output.name} écrit")


if __name__ == "__main__":
    main()
