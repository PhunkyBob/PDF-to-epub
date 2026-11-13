---
name: pdf-to-epub
description: Convertit un livre PDF (scanné ou numérique) en EPUB 3 propre et validé (OCR, suppression des en-têtes, folios, filigranes et pubs, paragraphes recollés, italique, typographie française, métadonnées). Utiliser quand l'utilisateur veut transformer un PDF en epub ou ebook.
---

# PDF → EPUB propre

Chaîne **déterministe** de scripts Python (gérés par `uv`) ; ton rôle se limite aux décisions qui demandent
du jugement : repérer les pages parasites, remplir la configuration, relire le rapport et ajouter des corrections.

Dans tout ce document, `SKILL` désigne le dossier de cette skill (celui qui contient ce fichier) et `WORK`
le dossier de travail créé à côté du PDF (`<nom du pdf>.epub-work`). Toutes les commandes ont la forme :

```
uv run --project "SKILL" python "SKILL/scripts/<script>.py" ...
```

**Travaille en orchestrateur** : tu décides, tu es le seul à écrire `book.json` / `fixes.tsv` et à lancer
`run.py` ; tout ce qui se découpe (lire les planches, vérifier des lettrines ou des mots sur l'image, relire le
texte par lots) va à des **sous-agents Haiku lancés en parallèle** (`model: "haiku"`, plusieurs appels Agent
dans un même message), qui proposent sans rien modifier. Lance les étapes longues (OCR, livre complet) en
arrière-plan et avance pendant ce temps. Déroulé et prompts : `references/orchestration.md`.

**Suivi** : chaque lancement réécrit `WORK/progress.log` (« étape 2/5 extract », « 120/282 pages (43 %),
reste ~40 s »). Lis-le pour savoir où en est un traitement, et indique-le à l'utilisateur.

## 0. Prérequis (vérifier une fois)

- `uv --version` ; sinon Windows : `winget install astral-sh.uv` · macOS/Linux : `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Tesseract** (seulement pour les PDF scannés) : `tesseract --version` ; sinon Windows : `winget install UB-Mannheim.TesseractOCR`
  · macOS : `brew install tesseract` · Linux : `sudo apt install tesseract-ocr`. Le script le trouve aussi dans
  `C:\Program Files\Tesseract-OCR\` ou via la variable `TESSERACT_CMD`. Le modèle français `tessdata_best` est
  téléchargé automatiquement dans `~/.cache/pdf-to-epub/tessdata` au premier lancement.
- **Java** (pour EPUBCheck, facultatif mais recommandé) : `java -version` ; sinon Windows : `winget install EclipseAdoptium.Temurin.21.JRE`.
- Le premier `uv run` installe les dépendances Python (voir `pyproject.toml`).

S'il manque un outil, donne la commande d'installation à l'utilisateur plutôt que de contourner.

## 1. Inspecter le PDF

```
uv run --project "SKILL" python "SKILL/scripts/inspect_pdf.py" "livre.pdf" [--lang fr]
```

Sortie : un JSON (affiché + `WORK/inspect/report.json`), des planches de miniatures numérotées
`WORK/inspect/sheet-debut.png`, `sheet-fin.png`, `sheet-corps.png`, et un `WORK/book.json` pré-rempli.

- `recommended_source` : `ocr` (scan, ou couche texte médiocre comme celle de l'Internet Archive, police
  « GlyphLessFont ») ou `textlayer` (PDF numérique propre, pas d'OCR nécessaire).
- **Lance aussitôt l'OCR complet en arrière-plan** (`run.py --work "WORK" --to ocr`, après avoir mis `source`
  à `textlayer` si recommandé) : il ne dépend pas du reste de la configuration et c'est l'étape la plus longue.
- **Regarde les planches** (Read sur les PNG), et en parallèle confie-les à un sous-agent Haiku (prompt A de
  `references/orchestration.md`) pour croiser vos repérages. Pour voir une page de près :
  `uv run --project "SKILL" python "SKILL/scripts/render_pages.py" "livre.pdf" --pages 5,12,240`

À identifier sur les planches :
1. **couverture** (souvent page 1) et ce qui la salit (étiquette code-barres, tampon) → `cover_crop` ;
2. **pages parasites à exclure** : page « Digitized by the Internet Archive » / Google Books, publicités,
   filigranes Z-Library ou autres, pages blanches, faux-titres, achevé d'imprimer, quatrième de couverture,
   table des matières imprimée (elle sera régénérée) ;
3. **pages liminaires à garder** : avant-propos, préface, introduction → `sections_before_body` ;
4. **première page du premier chapitre** (`body_first`) et **dernière page de texte** (`body_last`) ;
5. postface, notes, annexes → `sections_after_body` ;
6. pages à sauter à l'intérieur du corps (planches d'illustrations, pubs intercalées) → `pages.skip` ; une illustration
   ainsi sautée reste reprise dans l'EPUB, seul son texte éventuel est écarté ;
7. pages de titre de **partie** (« I. Les moines ») → `parts` : page de titre propre et table des matières à deux niveaux ;
   à laisser hors des plages de texte ou à mettre dans `pages.skip`, pour ne pas ouvrir de chapitre ;
8. mentions légales utiles (©, traducteur, collection) → `colophon` (texte recomposé, propre).

Les **tampons de bibliothèque, annotations manuscrites, filigranes** des pages gardées n'apparaissent pas
dans le texte final s'ils sont hors des pages du corps ; sinon ils sortent dans `review.txt` et se suppriment
via `fixes.tsv`.

## 2. Remplir `WORK/book.json`

Référence complète des clés : `references/config.md`. Exemple réel : `templates/book.example.json`.
Prends les métadonnées sur la **page de titre du livre** (orthographe de l'auteur comprise), pas dans les
métadonnées du PDF qui sont souvent fausses. Ne demande à l'utilisateur que ce que le livre ne dit pas.
Si un fichier `WORK/fixes.tsv` n'existe pas, copie `templates/fixes.example.tsv`.

## 3. Essai sur un échantillon, puis livre complet

L'OCR prend ~2 s par page et par cœur. S'il tourne déjà en arrière-plan (étape 1), attends-le en suivant
`progress.log` ; sinon **valide d'abord sur un échantillon** (avant-propos, une ouverture de chapitre, quelques
pages courantes ; mets provisoirement `body_last` à la dernière page de l'échantillon) :

```
uv run --project "SKILL" python "SKILL/scripts/run.py" --work "WORK" --pages 8-9,15-25
```

Puis le livre entier (en arrière-plan) :

```
uv run --project "SKILL" python "SKILL/scripts/run.py" --work "WORK"
```

L'OCR est mis en cache (`WORK/ocr/`). Après une modification de `book.json` ou de `fixes.tsv` :
`run.py --work "WORK" --from extract` (plusieurs minutes pour un roman). **Avant**, teste chaque nouvelle règle
en quelques secondes sur les pages concernées, sans rien écraser :

```
uv run --project "SKILL" python "SKILL/scripts/apercu.py" --work "WORK" --pages 81,144 --grep "déroute|exemple"
```

Une seule relance complète, une fois toutes les règles validées à l'aperçu.

Étapes (exécutables séparément) : `ocr.py` → `lignes_perdues.py` → `italiques.py` → `extract_text.py` → `illustrations.py` → `make_cover.py` → `build_epub.py` → `validate.py`.

## 4. Relecture et corrections (boucle)

`WORK/build/review.txt` commence par la **liste des chapitres détectés** : compare-la à la table des matières
imprimée du livre (nombre et titres).
- chapitre manquant : baisser `heading_min_size_ratio` (ex. 1.15) ; titre faux : `chapter_titles` ;
- faux chapitre (ex. en-tête courant pris pour un titre) : ajouter le texte à `running_heads`.

Puis les remarques :
- `début du chapitre N (lettrine ?)` : la grande initiale est souvent mal lue (ex. « a Que » pour « — Qu’en », la grande initiale ayant été remplacée ou perdue) → corriger ;
- `mot inconnu … -> « x » ?` : la suggestion du dictionnaire est **souvent fausse** (mots rares, noms propres,
  mots étrangers). Vérifie sur l'image de la page (`render_pages.py`) avant de corriger ;
- `mot douteux`, `caractère parasite` : à vérifier de même ;
- `ligne en haut de page conservée` : ligne isolée en haut de page, ni en-tête courant ni folio reconnu. Regarde
  l'image : numéro ou titre de chapitre (vérifie qu'un chapitre s'ouvre bien à cette page), en-tête lu une seule
  fois (l'ajouter à `running_heads`), ou vrai texte (rien à faire) ;
- `abréviation douteuse` : Mme ou Mlle en exposant lus « Me », « Mre » : à vérifier de même ;
- `ligne perdue par l'OCR puis relue` / `encre non lue` : ligne imprimée que Tesseract n'a pas lue. Vérifie la relecture
  sur l'image ; une zone `encre non lue` qui est du texte s'ajoute par `fixes.tsv` (ou se note dans le rapport) ;
- `appel de note relié` : chaque note (petit corps sous un blanc en bas de page) est retirée du fil du texte,
  placée en fin de chapitre et reliée à son appel, qui ouvre un popup et reste visible en fin de chapitre. Le
  programme place l'appel derrière le mot expliqué. Vérifie les lignes `ambigu, à vérifier` ; `!! note sans appel`
  ou un mot mal placé se règle par `note_anchors` (`config.md`) : `qa.txt` doit être vide sur ce point. Note non détectée (elle reste dans le texte, souvent « 1. Mot :
  définition ») : monter `footnote_max_size_ratio` (ex. 0.92) ; texte courant pris pour une note : le baisser.

Les corrections vont dans `WORK/fixes.tsv` (`regex<TAB>remplacement`, une règle par ligne). Les règles tirées des
relecteurs y sont écrites par le script (boucle plus bas) ; tu n'écris à la main que celles que la boucle te demande, **au-dessus**
de la ligne `# >>> RÈGLES AUTOMATIQUES`. Chaque règle passe sur le texte brut de l'OCR puis sur le texte final (celui
d'`apercu.py`). Apostrophe droite ou courbe : `['’]` ; espace avant `! ? ; :` : `\s*` ; mot en italique ou suivi d'un appel
de note : `{I}` là où un marqueur peut se trouver, capturé et remis. Exemples :
```
^a Que dites-vous	— Qu’en dites-vous
\bmaisou\b	maison
\bob({I})\s*;	obi\1
```
### Règle de correction (sans exception)

**But : corriger le plus possible d'erreurs de lecture (OCR) et de coquilles.** Une erreur vérifiée se corrige :
tu ne choisis pas de la laisser dans le texte pour la signaler dans le rapport. Pour chaque mot ou passage :

| Ce que tu constates | Ce que tu fais |
|---|---|
| L'image de la page porte une autre forme que le texte | **Corriger** d'après l'image |
| Coquille évidente du livre imprimé, une seule correction possible : mot qui n'existe pas (« avce » → « avec ») ou faute d'accord flagrante (« un telle impolitesse » → « une telle impolitesse ») | **Corriger** (verdict « coquille » des sous-agents « mots », ou règle à la main) ; le rapport la liste comme écart au PDF |
| L'image porte la même forme, et elle est correcte (nom propre, mot étranger, mot rare, graphie d'époque) | Conserver |
| Illisible sur l'image | Laisser ; le rapport le signale |
| Deux relecteurs proposent deux corrections différentes | Regarder l'image, puis écrire la décision dans `decisions.tsv` |
| La proposition change le sens, le style, un fait, un nom ou une translittération que le livre imprime ainsi | Refuser, avec la raison, dans `decisions.tsv` |

Ce que tu ne fais jamais : reformuler, ajouter ou retirer des mots de l'auteur, corriger un fait ou un nom de lieu,
ajouter des ō ou des ô que le livre n'imprime pas (ni dans le texte ni dans `original_title`). Le `colophon` recopie la
graphie imprimée.

`WORK/decisions.tsv` (une ligne par décision : `forme lue<TAB>forme retenue<TAB>raison`) sert à trancher et à refuser.
Pour garder la forme lue, écris-la deux fois. La raison est obligatoire et elle est imprimée dans le rapport :
```
omée	ornée	image p. 317 : « ornée d'une perle »
droitistes	droitistes	mot voulu par l'auteur, imprimé ainsi p. 88
```

**Retours d'un relecteur humain** : vérifie chaque remarque sur l'image, sépare les erreurs de la chaîne (à corriger
aussi dans la skill) de celles du livre, et applique ses corrections éditoriales dans une section à part de
`fixes.tsv`, en les listant dans le rapport comme écarts volontaires au PDF. Une remarque du type « c'est bizarre
mais c'est dans le PDF » n'appelle aucune correction. Une erreur signalée une fois se cherche ensuite dans tout le
livre : le relecteur n'en relève qu'une partie. Sur un livre déjà livré, corrige une copie du texte livré
(`mise_a_niveau.py`) plutôt que de relancer l'extraction.

**Illustrations** : gravures et photos sont reprises et placées à la **fin du paragraphe en cours**, jamais au milieu d'une
phrase comme sur l'imprimé. Après l'étape `illustrations`, regarde `build/relecture/illustrations.png` (images recadrées) et
lis `build/illustrations.txt` (fin du paragraphe choisi), puis corrige si besoin `WORK/illustrations.json` (`config.md`) et
relance `run.py --from illustrations` : retirer une fausse détection, corriger un recadrage (`box`), imposer un endroit (`apres`).
Une ligne `!!` (image non placée) ou « à vérifier » se règle ainsi. Sur un livre déjà livré, le texte relu ne change pas :
`illustrations.py --work "WORK/vN"` puis `run.py --work "WORK/vN" --from build`.

**Livre difficile** (titres illisibles, chapitres sans titre, scan à ombre de marge, ouvrage collectif) : lis
`references/livres-difficiles.md`, qui donne l'option de `book.json` adaptée à chaque symptôme. Un traitement
propre au livre se fait dans un script `post_extract` (voir `config.md`) pour rester reproductible.

### Vérifications et relecture par sous-agents Haiku (en parallèle)

**La relecture est complète et obligatoire** : *tous* les lots de `build/relecture/` sont relus (aucun lot
sauté ni échantillonné), toutes les remarques `mot inconnu` / `mot douteux` sont vérifiées sur l'image, et tous
les débuts de chapitre aussi. Chaque sous-agent **écrit ses propositions dans un fichier**
(`build/relecture/prop-lotN.tsv`, `prop-motsN.tsv`, `prop-lettrines.tsv`) et répond seulement « fini, N lignes » :
ses sorties ne se perdent pas. Tu lis ensuite ces fichiers pour trier. Contrôle que chaque lot a bien son
fichier de propositions avant de passer à la suite ; relance un sous-agent pour tout lot manquant. La note de
relecture humaine doit indiquer « relecture complète : oui » seulement si c'est vrai.

Une fois la structure correcte (chapitres, notes, `qa.txt`), ne vérifie pas toi-même les remarques une à
une : exporte le texte en lots

```
uv run --project "SKILL" python "SKILL/scripts/export_review.py" --work "WORK"
```

puis lance **dans un même message** (prompts dans `references/orchestration.md` et `references/relecture.md`) :
- un sous-agent Haiku « lettrines » (débuts de chapitre lus sur l'image) ;
- des sous-agents Haiku « italiques », un par planche `build/relecture/italiques-N.png` (prompt D) ; verse ensuite
  leurs verdicts avec `italiques.py --work "WORK" --appliquer` : la mesure d'inclinaison seule ne suffit pas ;
- un sous-agent Haiku « notes » : chaque appel, le texte et l'italique de chaque note, sur l'image (prompt E) ;
- des sous-agents Haiku « ponctuation », sur les planches de `lots_ponctuation.py --work "WORK"` (prompt G) : un point
  devant un nom propre est souvent une virgule mal lue, et seule l'image tranche. Leurs verdicts
  (`prop-ponctuationK.tsv`) sont versés dans `fixes.tsv` par la boucle ci-dessous, comme les autres propositions ;
- des sous-agents Haiku « mots », un par tranche de 40 remarques `mot inconnu` / `mot douteux`, préparées par
  `lots_mots.py --work "WORK"` (`build/relecture/mots-in-N.txt` → `prop-motsN.tsv`) : seules les remarques
  sans verdict partent, une seule occurrence par mot inconnu ;
- un sous-agent Haiku « relecture » par lot : `review.txt` ne voit que les mots absents du dictionnaire, les
  erreurs qui forment un vrai mot (« Pai » pour « J'ai », « II » pour « Il », un nom propre mal lu) demandent
  une lecture.

**Applique ensuite toutes les corrections par cette boucle** (détail des cas : `references/relecture.md` § 3). Tu ne
recopies aucune règle à la main : le script le fait.

1. Verse les propositions dans `fixes.tsv` (section automatique, réécrite à chaque lancement) :
   `uv run --project "SKILL" python "SKILL/scripts/propositions_vers_regles.py" --work "WORK" --appliquer`
2. Reconstruis : `run.py --work "WORK" --from extract`
3. Liste ce qui n'est pas passé dans le texte :
   `uv run --project "SKILL" python "SKILL/scripts/corrections_restantes.py" --work "WORK"`
4. Chaque ligne affichée dit quoi faire (`NON VÉRIFIÉ`, `NON CORRIGÉ`, `SANS EFFET`, `CONTEXTE`, `CONFLIT`, `INCERTAIN`). Traite-les
   **toutes**, puis reprends à l'étape 1.
5. Tu as fini quand l'étape 3 affiche « aucune correction restante ». Tant que ce n'est pas le cas, le rapport et la
   livraison sont refusés.

**Dernier recours, seulement après trois tours complets** : si une correction ne passe toujours pas, écris une ligne de
`decisions.tsv` qui garde la forme lue, avec la raison « correction impossible : il faut « x », à corriger à la main
p. 212 ». C'est la seule exception à la règle de correction ; le rapport l'affiche pour le lecteur humain, qui corrigera.

`WORK/build/qa.txt` (créé par `validate.py`) signale les problèmes structurels restants : folio ou en-tête
oublié, paragraphe coupé en milieu de phrase, césure non recollée, paragraphe anormalement long. Corrige via la
config ou `fixes.tsv`, relance `--from extract`, jusqu'à ce que `qa.txt` soit vide (ou que chaque point restant soit justifié)
et qu'EPUBCheck soit OK.

## 5. Livrer

L'EPUB est dans `WORK/build/<titre>.epub` (ou `output` de la config). **Chaque livre est livré avec son
« rapport de relecture » pour un humain** : un document qui se lit seul et liste *tous* les points à vérifier
à la main et *toutes* les décisions prises. Il ne renvoie à aucun fichier de travail (`qa.txt`, `fixes.tsv`…)
et n'a aucune section destinée à l'agent. Format et règles d'écriture : `references/rapport-humain.md`.

1. Rédige `WORK/rapport.json` en langage clair (résumé, contrôles, relecture réellement faite, décisions, points
   à vérifier avec leur page, limites). Recopie-y tout ce qui est utile : le lecteur n'ouvrira pas tes fichiers.
2. Génère le rapport (le script refuse un rapport vide, qui cite des fichiers de travail ou qui contredit l'EPUB :
   `references/rapport-humain.md`, § Contrôle de cohérence) :

```
uv run --project "SKILL" python "SKILL/scripts/rapport_humain.py" --work "WORK"
```

Il produit `WORK/build/rapport-relecture.md` : livre, résumé, contrôles, décisions, structure (chapitres et
pages), points à vérifier en cases à cocher, limites, validation humaine (que tu ne coches jamais), et trois annexes
remplies par le script : A. mots inconnus ou douteux (ce que l'OCR a lu, ce que le relecteur a lu sur la page, ce que contient l'EPUB,
la décision, les pages, un extrait de la phrase) ; B. toutes les corrections appliquées (lu → corrigé, pages, extrait) ;
C. propositions de relecture non appliquées et pourquoi. Il refuse d'écrire le rapport tant que
`corrections_restantes.py` liste quelque chose.

**Livraison : l'EPUB et le rapport doivent se trouver dans le même dossier que le PDF d'origine.**

```
uv run --project "SKILL" python "SKILL/scripts/livrer.py" --work "WORK"
```

Il copie `build/<titre>.epub` et le rapport (`<titre>.rapport-relecture.md`) à côté du PDF. À relancer après toute
reconstruction ou mise à jour du rapport. Il refuse un EPUB sur lequel le rapport n'a pas été contrôlé, et de livrer tant qu'une note reste sans appel : relie-la avec
`note_anchors` ; `--malgre-notes` n'existe que pour une décision explicite de l'utilisateur. Indique à l'utilisateur : nombre de chapitres, résultat d'EPUBCheck,
corrections faites, **chemins de l'EPUB et du rapport**, et un résumé du rapport. Si la relecture n'a pas été
complète, dis-le clairement : ne présente pas l'EPUB comme relu.

## Ce que font les scripts (pour expliquer ou déboguer)

- **ocr.py** : rendu 300 dpi (pypdfium2) + Tesseract hOCR (mots + boîtes) ; inclinaison mesurée sur chaque mot
  (`page-NNN.scores.json`) ; source `textlayer` : couche texte du PDF (pdfplumber), italique par nom de police.
- **lignes_perdues.py** : compare l'image à l'OCR. Les réponses muettes (« — … », jamais lues par Tesseract) sont reconnues
  à leur forme, les autres lignes manquantes sont relues seules et réinjectées si la lecture est sûre (confiance ≥ 70),
  sinon signalées. Résultat en cache (`page-NNN.lost.json`) ; pour un livre déjà passé par l'OCR, lancer ce script seul.
- **lots_ponctuation.py** : planches des points placés devant un nom propre, à juger sur l'image (prompt G). Chaque
  verdict « virgule » ou « suspension » devient une règle limitée à son endroit (`propositions_vers_regles.py`).
- **italiques.py** : cale la fenêtre d'inclinaison sur l'italique réelle du livre, classe les mots en sûrs et
  douteux, et prépare les planches des douteux. Les verdicts de `WORK/italiques.tsv` l'emportent sur la mesure.
- **illustrations.py** : repère l'encre hors des mots lus (pages `skip` comprises), recadre les images (gris, fond blanchi) et les
  rattache au paragraphe qui contient les derniers mots imprimés avant elles (`figures` dans `content.json`, texte inchangé).
- **extract_text.py** : supprime les en-têtes courants (ligne détachée du texte en haut de page, qui revient d'une
  page à l'autre ou en capitales ; `running_heads`), les folios (numéro qui suit la pagination) et les numéros de
  chapitre isolés au-dessus du titre ; sépare les notes de bas de page (petit corps) ; recolle les
  lignes en paragraphes (retrait, tiret de dialogue, ligne courte), y compris entre deux pages ; recolle les
  césures en gardant les vrais traits d'union (vocabulaire du livre + règles du français), y compris celles que
  l'OCR a laissées (« mon- tagnes », guillemet parasite à la place d'un appel de note, italique fermé juste après
  le trait d'union : inutile d'écrire une règle `fixes.tsv` pour ça) ; détecte les titres
  (taille de police, centrage, blanc) ; corrige l'OCR (dictionnaire : « Pécole »→« l’école », variantes
  d'accents « fenétre »→« fenêtre », mots collés) ; typographie française (espaces insécables, « », …, —).
- **build_epub.py** : EPUB 3.3, un XHTML par chapitre et par page de partie, nav + landmarks + ncx, métadonnées DC et accessibilité,
  CSS minimal. Voir `references/epub-bonnes-pratiques.md`.
- **validate.py** : contrôle qualité du texte + EPUBCheck.
- **mise_a_niveau.py** : met à niveau un livre déjà livré quand une reconstruction ne redonne pas la version relue
  (corrections manuelles, post-traitements) : appels de note et italiques vérifiés, mots conservés. Tout se passe dans
  `WORK/v2`. Avant de relancer `--from extract` sur un livre déjà livré, compare le résultat au texte livré sur une copie.
- **lots_mots.py** : tranches `mots-in-N.txt` des remarques `mot inconnu` / `mot douteux` encore sans verdict,
  pour les sous-agents « mots » (une occurrence par mot inconnu ; relançable après un nouveau contrôle).
- **export_review.py** : texte en lots `build/relecture/lotN.txt` pour la relecture par sous-agents.
- **propositions_vers_regles.py** : transforme les `prop-*.tsv` de confiance haute en règles ; avec `--appliquer`, les
  écrit dans la section automatique de `fixes.tsv` (tes règles à la main, hors de cette section, ne sont jamais touchées).
  Les règles automatiques ne changent que les mots fautifs.
- **corrections_restantes.py** : liste les corrections vérifiées absentes du texte, avec ce qu'il faut faire pour chacune.
- **corrections-ocr.csv** et **corrections-ocr.perso.csv** (dans `SKILL`) : tables partagées entre tous les livres
  (`langue,lu,corrige`) des confusions d'OCR qui valent partout : un mot qui n'existe pas, corrigé en un mot du
  dictionnaire. Les deux sont appliquées à chaque extraction. La première est la table globale, livrée avec la skill :
  aucun script ne l'écrit. La seconde est la table personnelle, absente du dépôt (ignorée par git) : `livrer.py`
  l'enrichit à chaque livraison (confusion relevée au moins deux fois dans le livre), et c'est là que tu ajoutes une
  ligne à la main ; pour un même mot lu, elle l'emporte sur la globale. Rien de propre à un livre n'y entre (noms
  propres, mots du livre) : cela reste dans le `fixes.tsv` du livre. Une ligne de `decisions.tsv` qui garde la forme
  lue désactive une confusion pour un livre.
- **livrer.py** : copie l'EPUB et le rapport de relecture dans le dossier du PDF d'origine.
- **rapport_humain.py** : écrit le rapport de relecture pour un humain (`build/rapport-relecture.md`) à partir de
  `rapport.json` (rédigé par l'agent) et des contrôles automatiques.
- **coherence.py** : compare les décomptes et citations de `rapport.json` à l'EPUB lui-même.
- **apercu.py** : texte final de quelques pages en quelques secondes (test d'une règle), sans rien écrire.
- Tous les scripts tiennent `WORK/progress.log` à jour (étape X/Y, pages traitées, temps restant estimé).
- **ocr.py** lance Tesseract dans `WORK/ocr` avec des noms relatifs : un dossier de travail au chemin accentué
  (ex. `_Sélection`) ne pose pas de problème.

Limites : la pose automatique des appels est fiable sur les notes de glossaire (« Mot : définition »). Sur des notes
de référence (« Cf. … », « Idem. »), beaucoup restent sans appel et les appels attribués à un exposant peuvent être
faux : dans ce cas, le prompt E de `references/orchestration.md` est indispensable, et `note_anchors` place les appels.
Le texte contenu dans une image reste dans l'image (légende comprise si elle touche l'image), il n'est pas transcrit. Le popup des notes dépend de la
liseuse : il est prévu par Apple Books (`<div epub:type="footnote">`, visible aussi en fin de chapitre) ; ailleurs, le
lien mène à la note et le numéro de la note ramène au texte.

