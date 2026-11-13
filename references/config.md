# Référence de `book.json`

Toutes les clés sont facultatives sauf `pdf`, `title` et `pages.body_first`/`body_last`. Les valeurs par
défaut sont dans `scripts/common.py` (`DEFAULTS`). Numéros de page = numéros **du PDF** (ceux des planches
de miniatures), pas ceux imprimés dans le livre.

## Métadonnées

| Clé | Rôle |
|---|---|
| `pdf` | chemin du PDF (absolu, ou relatif au dossier de `book.json`) |
| `title`, `subtitle` | titre et sous-titre |
| `original_title` | titre original (affiché « (…) » sur la page de titre) |
| `author`, `author_sort` | auteur tel qu'imprimé dans le livre ; forme de tri « Nom, Prénom » |
| `translator`, `translator_line` | traducteur (rôle MARC `trl`) ; ligne affichée, ex. « Traduit de l’anglais par … » |
| `publisher`, `published` | éditeur, date ou année d'édition |
| `language` | code BCP 47 : `fr`, `en`… (règles typographiques françaises si `fr`) |
| `description` | résumé court (métadonnée `dc:description`) |
| `identifier` | `urn:uuid:…` ou `urn:isbn:…` ; vide = UUID stable calculé à partir du titre/auteur/date |
| `output` | chemin de l'EPUB (défaut `build/<titre>.epub` dans le dossier de travail) |
| `colophon` | liste de paragraphes pour la page de mentions légales (vide = pas de page) |

## Source du texte

| Clé | Défaut | Rôle |
|---|---|---|
| `source` | `ocr` | `ocr` (Tesseract sur l'image) ou `textlayer` (couche texte d'un PDF numérique) |
| `ocr_lang` | `fra` | modèle(s) Tesseract, ex. `fra+eng` |
| `dpi` | 300 | résolution du rendu pour l'OCR |
| `psm` | 4 | mode de segmentation Tesseract (4 = une colonne de texte de tailles variables) |
| `italics` | true | détection et conservation de l'italique |

## Pages (`pages`)

| Clé | Rôle |
|---|---|
| `cover` | page de couverture (`null` : couverture typographique générée, ou `cover_image`) |
| `body_first`, `body_last` | première page du premier chapitre, dernière page de texte |
| `skip` | pages à ignorer dans les plages (blanches, planches, pubs intercalées…) |
| `sections_before_body` | `[{"id": "avant-propos", "title": "Avant-propos", "type": "foreword", "first": 9, "last": 10}]` |
| `sections_after_body` | même forme ; `type` : `afterword`, `epilogue`, `appendix`, `endnotes`, `conclusion`… |

Les pages hors de toutes ces plages (faux-titre, page « Digitized by… », pubs, table des matières imprimée,
achevé d'imprimer, 4e de couverture) sont simplement ignorées.

## Mise en page et détection

| Clé | Défaut | Rôle |
|---|---|---|
| `accent_capitals` | true | français : accentue les capitales que le livre imprime sans accent, « A » préposition en tête de phrase et les mots qui n'existent qu'accentués (« Etait » → « Était », « Ecole » → « École »). C'est un écart voulu au livre imprimé, à dire dans le rapport (décisions). À `false`, les capitales restent comme imprimées et les propositions `A → À` des relecteurs sont écartées |
| `running_heads` | `[]` | textes d'en-têtes courants à supprimer en plus de la règle automatique. Celle-ci retire, en haut de page, une ligne détachée du texte (plus de 1,3 interligne avant la suivante) qui revient sur une page voisine, chiffres compris, ou qui est en capitales, une ligne collée au texte qui revient de page en page, et un numéro qui suit la pagination (folio). Une ligne détachée vue une seule fois, ou un numéro hors pagination (numéro de chapitre), est gardée et signalée |
| `header_band`, `footer_band` | 0.065, 0.90 | zones (fraction de hauteur) où chercher en-têtes et folios. Un texte qui commence dans la zone du haut n'est pas pris pour un en-tête |
| `heading_min_size_ratio` | 1.2 | taille de police (relative au texte courant) au-delà de laquelle une ligne est un titre ; une ligne ≥ 1,08 centrée et suivie d'un grand blanc est aussi un titre |
| `chapter_top_gap` | (absent) | livres dont les chapitres n'ont pas de titre lisible (juste un petit numéro) : une page du corps dont la première ligne commence sous cette fraction de la hauteur de page (ex. 0.3) ouvre un chapitre sans titre |
| `lone_numeral_headings` | false | chapitres introduits par un numéro romain seul (I à XXV), à la taille du texte et parfois au milieu de la page : un numéro romain seul, centré et suivi d'un blanc ouvre un chapitre sans titre (numérotation régénérée) |
| `first_page_chapter` | false | la première page du corps (`body_first`) ouvre le chapitre 1 même si son numéro ou son titre n'a pas été lu (sinon ce texte devient un « chapitre 0 » et toute la numérotation est décalée d'un cran) |
| `prologue_title` | (absent) | titre donné au texte placé avant le premier chapitre (chapitre 0) ; retire le paragraphe qui répète ce titre |
| `heading_first_on_page` | false | titres à la taille du texte (en gras par exemple) : la première ligne centrée d'une page, sous un blanc et suivie d'un grand blanc, est un titre si elle ressemble (similarité ≥ 0,85) à une entrée de la table des matières. Recopier la table des matières imprimée dans `running_heads` ; mettre `heading_min_size_ratio` haut (1.8) et `chapter_numbers` à `none` si les titres suffisent |
| `chapter_starts` | `[]` | pages où un chapitre commence même si la détection ne le voit pas (ex. `[297]`) : ouvre un chapitre sans titre |
| `chapter_titles_by_page` | `{}` | titres imposés par page d'ouverture, ex. `{"297": "Neuf — Le départ"}` : pour les titres en police script ou ornés, illisibles à l'OCR et relevés à la main sur les images |
| `note_call_bare_number` | false | appels de note imprimés « 12 Texte » sans point (ouvrages collectifs) |
| `post_extract` | `""` | script Python propre au livre (chemin relatif au dossier de travail) lancé par `run.py` juste après l'étape `extract`, avec `--work WORK` ; il modifie `build/content.json` (fusion de faux chapitres, titres, nettoyage). Il est rejoué à chaque extraction, donc la reconstruction reste reproductible |
| `footnotes` | true | détecte les notes de bas de page, les retire du texte et les regroupe en fin de chapitre |
| `footnote_max_size_ratio` | 0.88 | corps (relatif au texte courant) en dessous duquel les dernières lignes d'une page, sous un blanc, sont une note |
| `note_numbering` | `book` | `book` : notes numérotées en continu sur le livre ; `chapter` : reprise à 1 à chaque chapitre |
| `note_anchors` | `[]` | appels placés à la main quand la détection échoue : `[{"page": 150, "note": 1, "after": "obi"}]` (`note` = rang de la note sur la page, `after` = mot derrière lequel poser l'appel) |
| `opening_caps` | false | premier mot de chaque chapitre affiché en majuscules, par la feuille de style (`text-transform`), le texte gardant sa casse normale. À activer si le livre imprimé le fait |
| `superscript_abbrevs` | false | français : `Mlle`, `Mme`, `Mgr`, `1er`, `1re` affichés avec leur exposant. À activer seulement si le livre les imprime ainsi |
| `subheadings` | false | intertitres internes aux chapitres, à la taille du texte (souvent en gras ou dans une autre police) : une ligne courte, centrée, qui contient un vrai mot et se trouve entre deux blancs nets devient un intertitre (`<h2 class="intertitre">`, sans entrée dans la table des matières). Sans cette option, l'intertitre est collé au paragraphe suivant (« Le départ Il faisait nuit… ») |
| `subheading_gap_before`, `subheading_gap_after` | 2.0, 1.2 | blancs minimaux au-dessus / au-dessous de l'intertitre, en corps de texte ; les monter si des formules ou des vers centrés sont pris pour des intertitres |
| `chapter_titles` | — | liste de titres pour remplacer ceux détectés (même nombre que les chapitres) |
| `chapter_numbers` | `roman` | numérotation affichée : `roman`, `arabic`, `none` |
| `nbsp_before_high_punct` | true | espace insécable avant `: ; ! ?` (français) |

## Parties (`parts`)

Pour un livre divisé en parties, avec une page de titre imprimée pour chacune. Liste d'objets, un par partie :
`{"page": 23, "number": "I", "title": "Les moines", "lines": ["Traduction de Huang San et Jean Blasse"]}`.

| Clé | Rôle |
|---|---|
| `page` | page du PDF du titre de partie : la partie s'ouvre avant le premier chapitre situé à cette page ou après |
| `number`, `lines` | facultatifs : numéro imprimé tel quel, lignes sous le titre (ex. crédit de traduction) |
| `title` | titre en casse normale ; la feuille de style l'affiche en capitales |

Laisser ces pages hors des plages de texte ou les mettre dans `pages.skip`. Les chapitres d'une partie passent au second
niveau de la table des matières ; les sections liminaires et finales restent au premier niveau.

## Illustrations

| Clé | Défaut | Rôle |
|---|---|---|
| `illustrations` | true | repère les illustrations du PDF et les place à la fin du paragraphe en cours ; `false` : aucune image dans le texte |

Le repérage écrit `WORK/illustrations.json`, que tu peux corriger : une page déjà analysée (`pages_vues`) ne l'est plus,
tes corrections restent donc en place. Chaque entrée de `images` :

| Clé | Rôle |
|---|---|
| `page` | page du PDF |
| `box` | `[x0, y0, x1, y1]` en fractions de la page : le recadrage |
| `alt` | texte alternatif (vide : « Illustration de l’édition imprimée ») |
| `apres` | mots qui figurent dans le paragraphe après lequel placer l'image, quand le placement automatique se trompe |

Supprimer une entrée retire l'image. Une image manquée s'ajoute à la main (mêmes clés).

## Couverture

| Clé | Rôle |
|---|---|
| `cover_image` | image fournie (prioritaire) |
| `cover_crop` | `[x0, y0, x1, y1]` en fractions : recadrage pour écarter étiquette, code-barres, tampon |

## `italiques.tsv` (dans le dossier de travail)

Verdicts sur l'image, prioritaires sur la mesure d'inclinaison : `page<TAB>id du mot<TAB>i ou r<TAB>mot`. Rempli par
`italiques.py --appliquer` à partir des `prop-italiques*.tsv` ; une ligne peut y être ajoutée à la main (l'id est dans
`ocr/page-NNN.hocr`).

## `fixes.tsv` (dans le dossier de travail)

Une règle par ligne : `regex Python<TAB>remplacement`. Lignes vides et `#…` ignorées. Appliquées à chaque
paragraphe (et à chaque note), deux fois : sur le texte brut de l'OCR, avant les corrections automatiques et la
typographie, puis sur le texte final (celui d'`apercu.py`). Dans la regex, `{I}` désigne les marqueurs internes
éventuels (italique, appel de note), à capturer et remettre : `\bob({I})\s*;` → `obi\1`. L'espace avant `! ? ; :`
est absente du texte brut et insécable dans le texte final : écrire `\s*`. Les mots écrits par un remplacement sont
protégés des corrections automatiques (dictionnaire, accents). Tes règles vont **au-dessus** de la ligne
`# >>> RÈGLES AUTOMATIQUES` : la section en dessous est réécrite par `propositions_vers_regles.py --appliquer`.
