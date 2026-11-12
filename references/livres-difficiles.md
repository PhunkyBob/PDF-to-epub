# Livres difficiles : cas rencontrés et recettes

Retours d'expérience de conversions réelles (romans, essais, ouvrages collectifs scannés). Chaque cas donne le
symptôme dans `review.txt`, la cause et l'option de `book.json` qui le résout (voir `config.md`).

## Structure des chapitres

| Symptôme | Cause | Recette |
|---|---|---|
| 1 seul « chapitre » ou 59 faux chapitres | Titres à la taille du texte (gras) | `heading_first_on_page: true`, table des matières imprimée dans `running_heads`, `heading_min_size_ratio` 1.8, `chapter_numbers` `none` |
| Quelques chapitres seulement, les autres fondus dans le texte | Chapitres sans titre, juste un petit numéro (souvent non lu) | `chapter_top_gap` (0.09 à 0.3 selon la hauteur du blanc d'ouverture, à mesurer sur 2-3 ouvertures) ; le numéro isolé qui suit est retiré tout seul |
| Numéro romain seul (I à XXV) à la taille du texte, parfois au milieu de la page | Rien ne le distingue d'un titre | `lone_numeral_headings: true` (tolère `IT`, `vV`…) ; combiner avec `chapter_top_gap` sans doublon |
| Le premier chapitre s'appelle « 0 » et tout est décalé d'un cran | Son numéro/titre n'a pas été lu en tête du corps | `first_page_chapter: true` |
| Titres en police script ou ornés, lus « Lc départ », « Fmju » ou absents | OCR impuissant sur la fonte | Relever les titres sur les images (sous-agent Haiku « lettrines »), puis `chapter_titles_by_page`, `chapter_starts` pour un chapitre manquant, `chapter_numbers: none` |
| Faux chapitres résiduels, doublons, pages d'illustrations qui ouvrent un chapitre | Planches, hors-texte | `pages.skip` pour les planches ; sinon un script `post_extract` qui fusionne dans `content.json` |
| Intertitre collé au début du paragraphe suivant (« Le départ Il faisait nuit… »), ou intertitres absents | Intertitres à la taille du texte, centrés entre deux blancs | `subheadings: true` (rendus en intertitres, hors table des matières) ; si des formules ou vers centrés sont pris pour des intertitres, monter `subheading_gap_before` |
| « Prologue » traité comme texte avant le chapitre 1 | Chapitre 0 | `prologue_title` ; la numérotation ignore le chapitre 0 |

## Texte

- **Ombre de marge** d'un scan : caractères parasites en début de ligne (`_`, `‘`, `|`, lettres collées). Compter ~1 000
  remarques en plus dans `review.txt` ; la relecture complète par Haiku est indispensable, et des règles
  générales (début/fin de paragraphe) valent mieux que des cas isolés.
- **Capitales non accentuées** dans le livre imprimé (« A la voix », « Etait ») : ne pas accepter les propositions
  `A → À`. Vérifier sur une page imprimée avant de décider.
- **« Il » lu `II`, `1l`, `11`, `I]`, `H`, `IT`** et point lu à la place d'une virgule devant une minuscule : deux
  règles générales font l'essentiel du travail.
- **Dates** dont le « 1 » est lu « 7 » (`(7912-1950)`), `yin/yang` lus `vin/vang` : règles ciblées après vérification.
- **Appels de note** collés aux mots (`Paris’`, `Rome’`) ou notes restées dans le texte : monter
  `footnote_max_size_ratio` (1.0 pour les petits corps proches du texte), `note_call_bare_number` si l'appel est
  « 12 Texte ». Les appels ne sont pas liés à leur note.
- **Caractères non latins** (grec, idéogrammes, tableaux phonétiques) : perdus ou bruités. Ne jamais laisser un
  sous-agent « restituer » des idéogrammes : ils les inventent. Les lister dans le rapport de relecture.

## Reproductibilité

Tout ce qui est propre au livre (titres relevés à la main, fusion de faux chapitres) va dans un script
`post_extract` du dossier de travail plutôt que dans une manipulation manuelle de `content.json` : un
`run.py --from extract` standard reproduit alors l'EPUB. Documenter le script dans le rapport de relecture.

## Machine et chemins

- **Noms de dossier accentués** : sous Windows, un même nom peut exister en deux normalisations Unicode
  (NFC/NFD). Bash et certains outils ne retrouvent pas le dossier : passer par PowerShell
  (`Get-ChildItem -Filter "*mot*.epub-work"`). `default_work_dir` réutilise désormais un dossier existant.
- **EPUBCheck et Java** ne retrouvent pas un fichier à chemin accentué : `validate.py` valide une copie ASCII.
- **OCR** : ~2 s par page et par cœur ; plusieurs OCR en parallèle ralentissent tout (jusqu'à 3 h pour 330 pages
  sous charge). Un seul OCR à la fois si possible.

## Sous-agents Haiku : limites pratiques

- Limite de **20 sous-agents simultanés** pour toute la session, orchestrateurs de livres compris. Plusieurs
  livres relus en parallèle saturent vite : vagues de 3 Haiku par livre, 2 livres en relecture à la fois.
- Erreur « Concurrent subagent limit » = saturation temporaire : attendre 2-3 minutes (`Start-Sleep`) et relancer
  seulement les sous-agents manquants.
- Chaque sous-agent écrit son `prop-*.tsv` (sorties perdues sinon) ; vérifier qu'aucun lot n'en manque.
- Haiku cite parfois la mauvaise page (±1-2) : vérifier sur l'image avant de corriger.
- `propositions_vers_regles.py --appliquer` verse lui-même les propositions de confiance haute dans la section
  automatique de `fixes.tsv` (boucle de `relecture.md` § 3) ; seules tes règles écrites à la main se testent avec
  `apercu.py` avant la relance.
