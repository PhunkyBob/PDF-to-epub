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
    {"sujet": "Majuscules accentuées", "decision": "« A » et « Etait » non accentués", "pourquoi": "le livre imprimé n'accentue pas les capitales (vérifié p. 139)"}
  ],
  "points": [
    {"ou": "p. 205 (note)", "constat": "« 1255-1205 » : date probablement mal lue", "traite": "laissé tel quel", "a_verifier": "relire la date sur la page imprimée", "dans_epub": ["1255-1205"]}
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
que dit l'EPUB, pas ce que dit l'image. Ces champs ne s'impriment pas. Le contrôle ne comprend pas le reste du texte libre.

Si le contrôle refuse, corrige ce qui est faux, le rapport ou le livre, sans reformuler pour le contourner. Un nombre qui ne
désigne pas le total du livre se reformule (« 96 des notes »). `livrer.py` refuse un EPUB différent de celui sur lequel le
rapport a été contrôlé : après toute reconstruction, relance `rapport_humain.py`.

## Générer le rapport

```
uv run --project "SKILL" python "SKILL/scripts/rapport_humain.py" --work "WORK"
```

Écrit `WORK/build/rapport-relecture.md` : livre et fichier livré, résumé, contrôles, décisions (tableau), structure
(chapitres, pages, nombre de paragraphes), points à vérifier (cases à cocher), limites, validation humaine, et en
annexe les remarques du contrôle orthographique automatique. Les mots inconnus ou douteux y forment un tableau : une
ligne par mot, toutes ses pages et la décision prise pour l'EPUB, établie d'après la relecture « mots » et le texte
livré ; les mots à vérifier viennent en tête. Copie les verdicts `prop-mots*.tsv` dans chaque nouvelle version du dossier
de travail : sans eux, tous les mots ressortent « à vérifier ». Relire le résultat ; ne coche jamais
la validation humaine. Puis `livrer.py` copie l'EPUB et le rapport à côté du PDF.
