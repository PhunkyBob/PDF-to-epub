# Relecture du texte par sous-agents

Le rapport `review.txt` ne voit que les mots inconnus du dictionnaire. Les erreurs d'OCR qui forment un mot
existant (« Pai » pour « J'ai », « II » pour « Il », « Sacko » pour « Saeko », « la met » pour « la mer »,
virgule lue comme un point…) ne sont repérées que par une lecture du texte. Cette lecture est déléguée à des
sous-agents **Haiku** (rapides et peu coûteux), un par lot, lancés **en parallèle** (plusieurs appels Agent dans
un même message, `model: "haiku"`).

## 1. Exporter les lots

```
uv run --project "SKILL" python "SKILL/scripts/export_review.py" --work "WORK"
```

Crée `WORK/build/relecture/lot1.txt`, `lot2.txt`… (chapitres entiers, ~110 000 caractères par lot, soit 5 à
8 lots pour un roman de 300 pages). Chaque ligne : `[chapitre.paragraphe] texte` ; les notes sont `[3.n1]`.

## 2. Prompt des sous-agents

Adapter les éléments entre ‹ › (personnages et lieux : tirés de `review.txt` et des noms propres fréquents) :

```
Tu relis le texte OCR d'un livre ‹français de ‹année› (« Titre » de Auteur, traduit de ‹langue›) ;
personnages : … ; lieux : …›.

Lis INTÉGRALEMENT le fichier (outil Read, en plusieurs morceaux si besoin) :
‹WORK›\build\relecture\lot‹N›.txt
Chaque ligne est un paragraphe préfixé par son identifiant [chapitre.numéro].

Repère UNIQUEMENT les erreurs de lecture OCR : lettres confondues (c/e, rn/m, l/i, 1/l, f/t…), mots collés
ou coupés, césure mal recollée (« sousmarin », « pou-Vait »), caractères parasites (\ / | ! * ? isolés au
milieu d'un mot ou d'une phrase), ponctuation aberrante (virgule lue comme un point devant une minuscule),
accents erronés (« entremélaient »), numéros ou lignes de bas de page incrustés dans le texte,
vrai mot qui casse le sens de la phrase (« le pain sur la fable » pour « le pain sur la table »), « ü », « ï » ou « ö »
dans un nom transcrit du japonais (souvent « û », « î », « ô » mal lus), dialogue sans son tiret.

NE signale PAS : les mots étrangers corrects, les noms propres, les subjonctifs imparfaits (eût, fût, portât…),
le style ou les tournures vieillies, la typographie (espaces insécables, « », —, …) gérée ailleurs.
Ne réécris jamais le texte de l'auteur. En cas de doute réel, confiance « basse ».

Réponds uniquement par une liste, une ligne par erreur :
[id] <TAB> fragment erroné tel qu'il apparaît (2-5 mots, assez pour être unique) <TAB> fragment corrigé <TAB> confiance (haute/basse)
Pas d'autre commentaire.
```

## 3. Trier les propositions (toi, pas les sous-agents)

Haiku propose beaucoup de bonnes corrections mais aussi des faux positifs. **Rejeter** :
- `A` → `À` en début de phrase quand le livre imprimé n'accentue pas les capitales (vérifier sur une page) ;
- les graphies d'époque ou d'auteur (« graphie d'époque », « nom de lieu à l'ancienne ») et les formes correctes mal comprises
  (« étudiiez » est un subjonctif imparfait) ;
- toute proposition qui ajoute, retire ou reformule des mots (« son actuel mari » → « Paul, son actuel
  mari ») ;
- les confiances « basses » non vérifiées sur l'image de la page (`render_pages.py`).

Les verdicts « erreur OCR » des sous-agents « mots » (`prop-mots*.tsv`) demandent le même tri. **Rejeter** :
- les appels de note : « possibilité » → « possibilité¹¹ », « Marie8 » → « Marie » (la chaîne place et retire
  elle-même les appels ; si l'un est mal placé, c'est `note_anchors`) ;
- les « corrections » de faits, de noms de lieux ou de translittération (un nom imprimé inexact, l'ajout d'un ō ou
  d'un ô) : le livre imprimé fait référence, l'anomalie va dans le rapport ;
- les coquilles de l'imprimeur, que le relecteur voudrait recopier (« avec » → « avce »,
  « chapitre » → « chapittre ») : on corrige l'OCR, pas le livre ;
- un trait d'union de **fin de ligne** pris pour un vrai trait d'union (« motétranger » → « mot-étranger »,
  « soustitre » → « sous-titre ») : le sous-agent le signale souvent dans sa réponse (« césure ») ; ne
  garder que les mots composés réels (« sousgenre » → « sous-genre », « bienfondé » → « bien-fondé ») ;
- une simple différence de casse dans une translittération en italique (« (kaze : le vent) ») ;
- les mots coupés à l'apostrophe (« aujourd », « jusqu », « quelqu ») : ce sont des morceaux d'« aujourd’hui »,
  déjà corrects dans le texte ;
- les « corrections » dans une zone que l'OCR n'a pas su lire (transcriptions phonétiques API, caractères
  non latins) : le relecteur y reconstruit à l'aveugle (« [idéogramme] ») ; laisser la zone
  dans les points à vérifier à la main ;
- les verdicts qui changent un mot correct pour un autre (« droitistes » → « droitiers », « mot rare » →
  « mot courant », « S(ignifiant) » voulu par l'auteur).

Les pages des remarques sont souvent décalées d'une page (ligne OCR en bas de page, mot vu en haut de la
suivante) : ce n'est pas une erreur du relecteur. Diacritiques de translittération (sanskrit ā ś ṣ ṇ, japonais
ō ū) : à 1 100 px de haut les relecteurs les devinent ; avant de corriger en série, vérifier une page en haute
résolution (`render_pages.py … --height 3200`) pour savoir si le livre les imprime vraiment.

Puis écrire les règles dans `WORK/fixes.tsv`. Attention : les sous-agents citent le texte **final**, mais les
règles s'appliquent au texte **brut** de l'OCR (avant typographie et corrections automatiques). Ne pas recopier
le fragment cité tel quel :
- pas d'espace (ou une espace ordinaire) avant `! ? ; :` dans le texte brut : écrire `Ab ?!`, pas `Ab !` ;
  ne jamais rendre *toutes* les espaces facultatives (`I ?(?=[a-z])` transformerait « Ils » en « Il ls ») ;
- un mot final corrigé automatiquement peut avoir une autre forme brute (« l’ignore » ← « Pignore ») : en cas
  de doute, viser les deux (`\b[PJl]['’]?ignore\b`) ;
- vérifier après relance que chaque motif a bien disparu de `build/content.json`.

Règles à écrire :
- **regrouper** les erreurs récurrentes en une règle générale (`\b[PD]['’]?ai\b` → `J’ai`, `\bII\b` → `Il`,
  `\b1l\b` → `il`, `\bFlle\b` → `Elle`, un nom propre mal lu partout) ;
- garder les autres en règles ciblées avec assez de contexte pour être uniques ;
- un mot en italique est entouré de marqueurs internes : écrire `{I}` à l'endroit où un marqueur peut se
  trouver, et le capturer pour le remettre (`\bob({I}) ?;` → `obi\1`), sinon la règle ne s'applique pas ou
  l'italique reste ouvert.

Relancer `run.py --from extract`, puis vérifier que les motifs corrigés ont disparu (recherche dans
`build/content.json`) avant de livrer.
