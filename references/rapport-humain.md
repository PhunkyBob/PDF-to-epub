# Rapport de relecture pour un humain

Livrable obligatoire avec chaque EPUB. Il s'adresse à la personne qui validera le livre, pas à un agent : il se
lit seul, **sans renvoi à des fichiers de travail** (pas de `qa.txt`, `review.txt`, `fixes.tsv`, `prop-*.tsv`…),
**sans section « pour l'agent »**, et il contient **tous** les points à vérifier et **toutes** les décisions
prises, avec les pages.

## Ce que tu rédiges : `WORK/rapport.json`

```json
{
  "decalage_pages": 2,
  "resume": "Roman de X, traduit par Y. 38 chapitres, EPUB validé. Relecture complète ; points à trancher en § 6.",
  "controles": ["EPUBCheck : aucune erreur ni avertissement", "Table des matières comparée au livre : 25/25"],
  "relecture": "Tout le texte a été relu par un relecteur automatique (7 lots sur 7), les mots douteux (456) et les 42 débuts de chapitre ont été vérifiés sur l'image des pages. 160 corrections générales ont été appliquées ; ... ce qui n'a PAS été fait : ...",
  "decisions": [
    {"sujet": "Pages exclues", "decision": "p. 2-6 et 293-300", "pourquoi": "pages blanches, achevé d'imprimer, quatrième de couverture"},
    {"sujet": "Majuscules accentuées", "decision": "« A » et « Etait » accentués en « À » et « Était »", "pourquoi": "le livre imprimé n'accentue pas les capitales (vérifié p. 139) ; écart voulu, conforme à l'usage typographique"}
  ],
  "points": [
    {"ou": "p. 205 (note)", "constat": "« 1255-1205 » : date probablement mal lue", "traite": "laissé tel quel", "a_verifier": "relire la date sur la page imprimée", "dans_epub": ["régna sur la province de 1255-1205 avant"]}
  ],
  "limites": ["Les caractères non latins des exemples sont perdus.", "Les appels de note ne sont pas liés à leur note."],
  "reproduction": "(facultatif, technique) Comment refaire l'EPUB si le livre a un traitement particulier."
}
```

Règles d'écriture :
- **Langage clair, pas de noms de fichiers ni d'outils** (« le relecteur automatique », « le contrôle orthographique »). Le script refuse un rapport qui cite `*.json/.tsv/.txt/.py`, « sous-agent » ou « À compléter par l'agent ».
- **Décisions** : tout ce qui oriente le résultat : pages exclues et pourquoi, options de structure retenues,
  règles de correction refusées (et pourquoi), choix d'orthographe, métadonnées incertaines (date, éditeur),
  ce qui a été écarté ou fusionné.
- **Points** : chaque doute avec **page** (`ou`), ce qui a été **constaté**, le **traitement** (corrigé en…, laissé
  tel quel, règle appliquée partout) et ce qu'il reste à **vérifier**. Y mettre aussi les propositions non appliquées
  faute de certitude, les mots illisibles, les règles à risque appliquées en masse (donner un exemple et un
  ordre de grandeur), les erreurs d'OCR résiduelles par zone de pages. Pas de « voir tri.json » : tout est recopié.
- **Relecture** : dire honnêtement ce qui a été fait (nombre de lots, de mots, d'ouvertures, vérifiés sur image ou
  non) et ce qui ne l'a pas été.
- **Limites** : ce qui manque ou est imparfait dans l'EPUB (images, caractères non latins, notes, parties).
- **Vérité du rapport** : avant d'affirmer quelque chose sur le texte, lis-le **dans l'EPUB**. L'image dit ce qui est imprimé,
  pas ce qui a été livré. Toute différence voulue avec l'imprimé va dans une décision « Écarts volontaires au PDF », y compris
  celles qui viennent d'une règle de correction ancienne.

## Contrôle de cohérence

`rapport_humain.py` lit l'EPUB et refuse de produire un rapport qui le contredit :

| Où | Forme | Vérification |
|---|---|---|
| `resume`, `controles` | « 44 chapitres », « 8 gravures », « 112 notes », « deux parties », « 3 textes liminaires » | le nombre est le total de l'EPUB |
| `points`, `decisions` | `"dans_epub": ["…"]` : extraits recopiés de l'EPUB | chaque extrait y figure, en mots entiers |
| `points`, `decisions` | `"plus_dans_epub": ["…"]` : formes corrigées | aucune n'y figure encore |

`dans_epub` est **obligatoire** pour tout point dont le constat ou le traitement contient une citation « … », et pour la
décision « Écarts volontaires au PDF » ; une liste vide indique que la citation n'est pas du texte du livre. Recopie-y ce
que dit l'EPUB, pas ce que dit l'image : **un morceau de la phrase (5 à 10 mots), pas le mot seul**, car `dans_epub` est
imprimé dans le rapport comme « Extrait de l'EPUB » et sert au lecteur à retrouver l'endroit. `plus_dans_epub` ne
s'imprime pas. Le contrôle ne comprend pas le reste du texte libre.

Si le contrôle refuse, corrige ce qui est faux, le rapport ou le livre, sans reformuler pour le contourner. Un nombre qui ne
désigne pas le total du livre se reformule (« 96 des notes »). `livrer.py` refuse un EPUB différent de celui sur lequel le
rapport a été contrôlé : après toute reconstruction, relance `rapport_humain.py`.

## Générer le rapport

```
uv run --project "SKILL" python "SKILL/scripts/rapport_humain.py" --work "WORK"
```

Écrit `WORK/build/rapport-relecture.md` : livre et fichier livré, résumé, contrôles, décisions (tableau), structure
(chapitres, pages, nombre de paragraphes), points à vérifier (cases à cocher), limites, validation humaine, puis trois
annexes que **le script remplit seul** (tu n'as rien à y écrire) :

- **A. Mots inconnus ou douteux** : une ligne par mot, avec ce que l'OCR a lu, ce que le relecteur a lu sur la page, ce que contient
  l'EPUB, la décision, les pages et un extrait de la phrase (le mot en gras). Les mots à vérifier viennent en tête.
- **B. Corrections appliquées** : chaque forme lue et ce qui l'a remplacée, le nombre de fois, les pages, un extrait.
- **C. Propositions de relecture non appliquées** : texte lu conservé, proposition, raison, extrait.

Le script **refuse** d'écrire le rapport tant que `corrections_restantes.py` liste une correction vérifiée absente du
texte : finis d'abord la boucle de `relecture.md` § 3. Les raisons de `decisions.tsv` sont imprimées telles quelles dans
les annexes : écris-les en langage clair. Copie les verdicts `prop-mots*.tsv` et `decisions.tsv` dans chaque nouvelle
version du dossier de travail : sans eux, tous les mots ressortent « non vérifié ». Relire le résultat ; ne coche jamais
la validation humaine. Puis `livrer.py` copie l'EPUB et le rapport à côté du PDF.
