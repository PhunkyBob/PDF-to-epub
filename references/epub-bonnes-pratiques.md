# Bonnes pratiques EPUB appliquées par `build_epub.py`

Sources : EPUB 3.3 (W3C, https://www.w3.org/TR/epub-33/), EPUB Accessibility 1.1 et ses techniques
(https://www.w3.org/TR/epub-a11y-11/), DAISY Knowledge Base (https://kb.daisy.org/publishing/), EPUBCheck.

## Conteneur (OCF)
- `mimetype` = premier fichier de l'archive, **non compressé**, contenu exact `application/epub+zip`.
- `META-INF/container.xml` pointe vers le document de paquet `OEBPS/content.opf`.
- Archive reproductible : dates fixes, ordre de fichiers stable.

## Paquet (OPF)
- `version="3.0"`, `unique-identifier`, `xml:lang`.
- Obligatoires : `dc:identifier`, `dc:title`, `dc:language`, `dcterms:modified` (UTC, sans fraction de seconde).
- Recommandées : `dc:creator` + rôle MARC `aut` + `file-as` ; `dc:contributor` traducteur (`trl`) ; `dc:publisher`, `dc:date`, `dc:description`.
- Accessibilité (schema.org) : `accessMode`, `accessModeSufficient`, `accessibilityFeature`
  (structuralNavigation, tableOfContents, readingOrder, displayTransformability, alternativeText),
  `accessibilityHazard`, `accessibilitySummary`.
- Couverture : `properties="cover-image"` sur l'image ; page de couverture XHTML avec texte alternatif.
- `spine` = ordre de lecture : couverture, page de titre, mentions, table des matières, liminaires, chapitres, annexes.

## Navigation
- Document de navigation EPUB 3 (`properties="nav"`) avec `nav epub:type="toc"` et `nav epub:type="landmarks"`
  (cover, titlepage, toc, bodymatter). Il est dans le `spine` (sinon EPUBCheck refuse le repère toc).
- `toc.ncx` en plus, pour les liseuses EPUB 2.
- Parties (`parts`) : `epub:type="part"` et `role="doc-part"`, table des matières à deux niveaux (`nav` et `ncx`, `dtb:depth` à 2).

## Contenu XHTML
- XHTML5 bien formé, UTF-8, `lang` et `xml:lang` ; un fichier par chapitre (chargement rapide, saut de page natif).
- Sémantique : `<section epub:type="chapter" role="doc-chapter" aria-labelledby=…>`, `<h1>` pour le titre,
  `<p>` par paragraphe, `<em>` pour l'italique ; liminaires avec `doc-foreword`, `doc-preface`… ; `epub:type`
  `frontmatter` / `bodymatter` / `backmatter` sur `<body>`.
- Illustrations : `<figure class="illustration"><img alt=…/></figure>` entre deux paragraphes, déclarées au manifeste ; `schema:accessMode` `visual` ajouté.
- Aucun retour à la ligne de mise en page, aucun folio ni en-tête courant, aucune césure « en dur » :
  la césure est laissée au lecteur (`hyphens: auto`).

## CSS
- Minimal, unités relatives (`em`, `%`) ; aucune police ni taille de corps imposées (respect des réglages du lecteur).
- Retrait de première ligne plutôt qu'espace entre paragraphes (convention du livre français), pas de retrait après un titre.
- `orphans`/`widows`, `break-after: avoid` sur les titres.

## Typographie française
- Espace insécable avant `: ; ! ?` et à l'intérieur des guillemets « » ; tiret cadratin + insécable pour les dialogues ;
  apostrophe courbe ’ ; points de suspension « … » en un caractère.

## Validation
- EPUBCheck sans erreur ni avertissement. Pour aller plus loin en accessibilité : DAISY Ace (`npx @daisy/ace`).
