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

Signale aussi les coquilles évidentes : un mot qui n'existe pas et qui n'a qu'une correction possible (« avce » → « avec »).

NE signale PAS : les mots étrangers corrects, les noms propres, les subjonctifs imparfaits (eût, fût, portât…),
le style ou les tournures vieillies, la typographie (espaces insécables, « », —, …) gérée ailleurs.
Ne réécris jamais le texte de l'auteur : même nombre de mots avant et après, sauf pour un mot collé ou coupé.
Confiance « haute » : une seule correction possible. Confiance « basse » : tu hésites entre deux lectures.

Écris le résultat (outil Write) dans ‹WORK›\build\relecture\prop-lot‹N›.tsv et nulle part ailleurs,
une ligne par erreur, champs séparés par une tabulation, sans en-tête :
[id] <TAB> fragment erroné recopié tel quel (2-5 mots, assez pour être unique) <TAB> fragment corrigé <TAB> confiance (haute/basse)
Exemple : [3.42] <TAB> le pain sur la fable <TAB> le pain sur la table <TAB> haute
Réponds seulement « fini, N lignes ».
```

## 3. Appliquer les corrections (la boucle)

**Par défaut, une proposition de confiance haute est appliquée.** Tu ne tries pas les propositions une à une et tu
ne recopies aucune règle : les scripts le font, puis te donnent la courte liste de ce qui demande ton jugement.

Les étapes et les commandes de la boucle sont dans `SKILL.md` § 4 (« Applique ensuite toutes les corrections par cette
boucle ») : verser les propositions, reconstruire, lister ce qui reste, traiter chaque ligne, recommencer. Cette page
dit seulement quoi faire de chaque ligne.

### Ce que le script écarte tout seul (rien à faire)

Reformulation (mots ajoutés ou retirés, mot remplacé par un mot sans rapport) ; appel de note (« Marie8 » → « Marie » :
la chaîne place les appels, voir `note_anchors`) ; accent ajouté sur une capitale (« A » → « À ») ; fragment déjà corrigé.
Ces propositions sont listées dans l'annexe C du rapport avec leur raison : le lecteur humain les verra.

### Que faire de chaque ligne de `corrections_restantes.py`

| Ligne | Ce que cela veut dire | Ce que tu fais |
|---|---|---|
| `NON VÉRIFIÉ` | Mot signalé que personne n'a regardé sur l'image | `lots_mots.py --work "WORK"`, puis un sous-agent « mots » par tranche (prompt C) |
| `NON CORRIGÉ` | L'image porte « attendu », le texte porte autre chose | Écrire une règle dans `fixes.tsv`, **au-dessus** de la section automatique (voir plus bas) |
| `SANS EFFET` | La règle automatique n'a rien changé | Regarder le passage (`apercu.py --pages N --grep "mot"`), puis écrire une règle à la main |
| `CONTEXTE` | Le mot fautif est un vrai mot et le fragment existe plusieurs fois (« la met » → « la mer ») | Écrire une règle à la main avec 2 ou 3 mots de contexte |
| `CONFLIT` | Deux relecteurs proposent deux corrections différentes | Regarder l'image (`render_pages.py "PDF" --pages N`), puis une ligne dans `decisions.tsv` |
| `INCERTAIN` | Proposition de confiance basse, jamais vue sur l'image | Regarder l'image, puis une ligne dans `decisions.tsv` : fragment lu, fragment retenu (le corrigé, ou le lu pour le garder), raison |

Beaucoup de lignes `NON VÉRIFIÉ`, `CONFLIT` ou `INCERTAIN` (plus de 10) : confie-les à des sous-agents Haiku par tranches
de 20 (« pour chaque ligne, rends la page indiquée, lis le passage sur l'image, réponds dans ton message, une ligne par
cas : fragment lu <TAB> fragment imprimé <TAB> page »), puis écris toi-même `decisions.tsv` d'après leurs réponses.

Cas fréquents :
- `NON CORRIGÉ` avec un texte abîmé par une règle (« T6kyÔ » devenu « TôkyôÔ ») : une règle à la main trop large est
  passée avant. Pour la trouver : cherche dans `fixes.tsv`, au-dessus de la section automatique, les règles qui
  contiennent un morceau du mot lu (ici `6k` ou `T6`). Corrige-la (elle doit couvrir le mot entier) ou supprime-la : la
  règle automatique suffira.
- `SANS EFFET` sur un « ! », un « * » ou un chiffre isolé derrière un mot qui a une note de bas de page (« tempura ! ou ») :
  c'est l'appel de note mal lu. Règle à la main qui le retire (`tempura({I})\s*!` → `tempura\1`), puis vérifier que la note
  reste reliée (`qa.txt`, sinon `note_anchors`).
- `NON CORRIGÉ` causé par une règle de la **section automatique** (un relecteur s'est trompé et sa règle abîme le mot) :
  ne touche pas à la section ; écris dans `decisions.tsv` la ligne `fragment lu par ce relecteur<TAB>bonne forme<TAB>raison`,
  elle remplace sa proposition au tour suivant.
- Trait d'union de fin de ligne pris pour un vrai trait d'union (« motétranger » → « mot-étranger ») : refuser dans
  `decisions.tsv` ; ne garder que les vrais mots composés (« bienfondé » → « bien-fondé »).
- Mot coupé à l'apostrophe (« aujourd », « jusqu ») : morceau d'« aujourd’hui », déjà correct ; le garder dans
  `decisions.tsv`.

### `WORK/decisions.tsv` : trancher ou refuser

Une ligne par décision, trois champs séparés par une tabulation : `forme lue<TAB>forme retenue<TAB>raison`.
La raison est obligatoire : elle est imprimée dans le rapport ; cite la page du PDF où tu as regardé. La décision ne
change rien toute seule : elle devient une règle quand tu reprends la boucle à l'étape 1.

```
omée	ornée	image p. 317 : « ornée d'une perle »
fabi	tabi	image p. 207 ; chaussettes japonaises, cité ainsi dans tout le livre
droitistes	droitistes	mot voulu par l'auteur, imprimé ainsi p. 88
```

Tu ne refuses une correction (forme retenue = forme lue) que dans ces cas, après avoir regardé l'image :
- l'image porte bien la forme lue et elle est correcte (nom propre, mot étranger, mot rare, graphie d'époque) ;
- la proposition change un fait, un nom de lieu ou une translittération que le livre imprime ainsi (pas d'ajout de ō,
  de ô) ;
- la proposition remplace un mot correct par un autre (« droitistes » → « droitiers ») ;
- la zone est illisible ou non latine (transcription phonétique, idéogrammes) : raison « illisible ».

« Je ne suis pas sûr » n'est pas une raison de refuser : regarde l'image (`render_pages.py … --height 3200` pour les
accents et les diacritiques) et décide. Une coquille évidente du livre imprimé se corrige (règle de `SKILL.md`).

### Écrire une règle à la main dans `fixes.tsv`

Format : `regex<TAB>remplacement`, une règle par ligne, **au-dessus** de la ligne `# >>> RÈGLES AUTOMATIQUES` (tout ce
qui est en dessous est réécrit par le script). Chaque règle passe deux fois : sur le texte **brut** de l'OCR, puis sur
le texte **final** (celui que montrent `apercu.py` et les relecteurs). Tu peux donc recopier le texte d'`apercu.py` ; une
règle qui ne mord pas sur ce texte recopié est mal écrite (caractère oublié, `{I}` manquant) :
- l'espace avant `! ? ; :` est absente du texte brut et insécable dans le texte final : écrire `\s*!`, pas ` !` ;
- apostrophe droite ou courbe : écrire `['’]` ;
- mot en italique ou suivi d'un appel de note : écrire `{I}` là où un marqueur peut se trouver, le capturer et le
  remettre (`\bob({I})\s*;` → `obi\1`) ;
- borner le mot (`\bmaisou\b`) et couvrir le mot entier, sinon la règle abîme d'autres mots ;
- une erreur qui se répète partout mérite une règle générale (`\bII\b` → `Il`, `\b1l\b` → `il`), après avoir vérifié
  qu'elle ne touche pas un vrai mot du livre (un « II » de « Napoléon II ») ;
- tester avant de relancer : `apercu.py --work "WORK" --pages 81 --grep "maison"`.

Les pages des remarques sont souvent décalées d'une page (ligne OCR en bas de page, mot vu en haut de la suivante) :
ce n'est pas une erreur du relecteur.
