# Orchestration : sous-agents en parallèle

Toi (l'agent principal) tu es l'**orchestrateur** : tu prends les décisions, tu es le **seul à écrire**
`book.json` et `fixes.tsv`, et le seul à lancer `run.py` sur un dossier de travail. Tout ce qui se découpe
(regarder des images, relire du texte, vérifier une liste de mots) est confié à des **sous-agents Haiku**
(`model: "haiku"` dans l'appel Agent) : ils sont largement suffisants pour ces tâches, rapides et peu coûteux.
Lance-les **tous dans le même message** (plusieurs appels Agent) pour qu'ils tournent en parallèle, puis trie
leurs réponses.

## Règles

- Les sous-agents **proposent**, ils ne modifient rien : réponse sous forme de liste ou de JSON. Interdis-leur
  explicitement d'écrire dans le dossier de travail (sauf leurs propres fichiers temporaires).
- Donne-leur des chemins absolus et les commandes exactes (`uv run --project "SKILL" python "SKILL/scripts/…"`).
- Exception : chaque sous-agent écrit ses propositions dans **son** fichier `WORK/build/relecture/prop-*.tsv`
  (jamais ailleurs) et ne répond que « fini, N lignes ». C'est ce fichier que tu lis pour trier.
- Commandes autorisées pour eux : `render_pages.py` (images de pages) et `apercu.py` (texte final de quelques
  pages), qui sont légères et n'écrivent pas dans `build/`. Jamais `run.py`, `ocr.py` ni `extract_text.py`.
- Limite de 20 sous-agents simultanés pour toute la session (orchestrateurs de livres compris) : vagues de 3
  Haiku par livre ; sur « Concurrent subagent limit », attendre 2-3 minutes puis relancer les seuls manquants.
  Détails et cas pratiques : `livres-difficiles.md`.
- Pour trier les `prop-*.tsv`, `propositions_vers_regles.py` produit des règles candidates filtrées (confiance
  haute, pas de reformulation) ; elles restent à tester avec `apercu.py` avant de les verser dans `fixes.tsv`.
- Charge machine : l'OCR occupe tous les cœurs. Un seul OCR à la fois sur la machine, même avec plusieurs livres.
- Suivi : `WORK/progress.log` (réécrit à chaque lancement) indique l'étape en cours (« étape 2/5 extract »)
  et l'avancement (pages, reste estimé). Lis-le au lieu d'attendre à l'aveugle.

## Déroulé accéléré

1. `inspect_pdf.py` (rapide). Puis **lance tout de suite l'OCR complet en arrière-plan** :
   `run.py --work WORK --to ocr`. L'OCR ne dépend que de `source`, `ocr_lang`, `dpi`, `psm` : pas besoin
   d'attendre la configuration. (Si `recommended_source` est `textlayer`, mets `source` avant de lancer.)
2. Pendant l'OCR, **en parallèle** :
   - toi : métadonnées depuis la page de titre (`render_pages.py`), `book.json` ;
   - sous-agent Haiku « planches » (prompt A) pour proposer couverture, pages à exclure, début et fin du corps.
   Compare sa proposition à ta propre lecture des planches avant de remplir `pages`.
3. OCR fini : `run.py --from extract`. Lis `review.txt`, compare les chapitres à la table imprimée, règle
   la structure (`heading_min_size_ratio`, `running_heads`, `chapter_titles`, notes).
4. Structure correcte : lance **en parallèle, dans un même message** :
   - sous-agent Haiku « lettrines » (prompt B) : débuts de chapitre ;
   - sous-agents Haiku « italiques » (prompt D), un par planche ; puis `italiques.py --work WORK --appliquer` ;
   - sous-agent Haiku « notes » (prompt E) : appels, texte et italique des notes ;
   - sous-agents Haiku « mots » (prompt C) : `lots_mots.py --work WORK` prépare les tranches
     `build/relecture/mots-in-N.txt` (40 remarques sans verdict, une occurrence par mot inconnu) ; un
     sous-agent par tranche, qui écrit `build/relecture/prop-motsN.tsv` ;
   - sous-agents Haiku « relecture » : `export_review.py` puis un par lot (prompt de `relecture.md`).
5. Trie toutes les propositions (règles de `relecture.md` § 3), écris `fixes.tsv`, **teste les règles avec
   `apercu.py`** sur les pages concernées (quelques secondes), puis **une seule** relance complète
   `run.py --from extract`. Vérifie que les motifs ont disparu de `build/content.json`.

Plusieurs livres : un dossier de travail par livre ; enchaîne les OCR (un à la fois, en arrière-plan) et
traite pendant ce temps les étapes de jugement des livres dont l'OCR est fini.

## Prompts

### A. Planches (repérage des pages)

```
Regarde les planches de miniatures numérotées d'un livre scanné (outil Read sur chaque image) :
‹WORK›\inspect\sheet-debut.png, sheet-fin.png, sheet-corps.png
Le numéro rouge de chaque vignette est le numéro de page du PDF. Pour voir une page de près :
uv run --project "‹SKILL›" python "‹SKILL›/scripts/render_pages.py" "‹PDF›" --pages N --out "‹TMP›"
(‹TMP› : dossier temporaire à toi ; n'écris rien d'autre.)

Réponds uniquement par ce JSON :
{"cover": N, "cover_dirt": "étiquette/tampon en bas ? fraction de hauteur à garder, ex. 0.83, ou null",
 "skip_outside_body": [pages parasites : Internet Archive, Google, pubs, blanches, faux-titre, table imprimée,
   achevé d'imprimer, 4e de couverture],
 "front_sections": [{"title": "Avant-propos", "first": N, "last": N}],
 "body_first": N, "body_last": N, "skip_inside_body": [planches, pubs intercalées],
 "back_sections": [{"title": "...", "first": N, "last": N}],
 "toc_page": N, "chapters_in_printed_toc": ["titre 1", "titre 2", ...],
 "title_page": {"title": "...", "author": "...", "translator": "...", "publisher": "...", "year": "..."}}
```

### B. Lettrines (débuts de chapitre)

```
Pour chaque chapitre ci-dessous, la grande initiale (lettrine) a pu être mal lue par l'OCR.
Rends l'image de la page et lis les premiers mots imprimés :
uv run --project "‹SKILL›" python "‹SKILL›/scripts/render_pages.py" "‹PDF›" --pages ‹p1,p2,…› --out "‹TMP›"
Chapitres (page PDF : début lu par l'OCR) :
‹p.35 : « D‘ toutes les villes de la péninsule »›
‹…›
Réponds uniquement, une ligne par chapitre :
page <TAB> début OCR (3-6 mots) <TAB> début imprimé (mêmes mots, corrigés) <TAB> identique (oui/non)
```

### C. Mots inconnus ou douteux (vérification sur l'image)

```
Voici des mots signalés par le contrôle orthographique d'un texte OCR (page PDF : mot -> suggestion).
Les suggestions sont souvent fausses (noms propres, mots étrangers, mots rares). Pour chaque mot, rends
la page et regarde ce qui est réellement imprimé :
uv run --project "‹SKILL›" python "‹SKILL›/scripts/render_pages.py" "‹PDF›" --pages ‹…› --out "‹TMP›"
Pour le contexte du texte final :
uv run --project "‹SKILL›" python "‹SKILL›/scripts/apercu.py" --work "‹WORK›" --pages N --grep "mot"
Mots : lis le fichier ‹WORK›/build/relecture/mots-in-N.txt (une remarque par ligne, « p.N » = page du PDF).
Écris le résultat (outil Write) dans ‹WORK›/build/relecture/prop-motsN.tsv et nulle part ailleurs,
une ligne par remarque, champs séparés par une tabulation, sans en-tête :
page <TAB> mot OCR (exactement comme entre « » dans la remarque) <TAB> mot imprimé <TAB> verdict
verdict = « erreur OCR », « correct tel quel » (mot imprimé identique au mot OCR) ou « illisible ».
Un trait d'union de fin de ligne n'est pas un trait d'union du mot : le signaler dans la réponse.
Réponds seulement « fini, N lignes ».
```

### D. Italiques douteux (une planche par sous-agent)

```
Tu vérifies si des mots d'un livre scanné sont imprimés en italique. Lis avec Read l'image
‹WORK›/build/relecture/italiques-N.png et le fichier ‹WORK›/build/relecture/italiques-in-N.tsv
(colonnes : numéro, page, id, mot, raison). Chaque rangée de l'image porte un numéro rouge à gauche ; le mot à
juger est souligné en rouge. Compare-le aux mots voisins : l'italique a des lettres penchées et cursives, le romain
des lettres droites. Un mot en romain peut se trouver dans une ligne en italique, et inversement. La colonne
« raison » ne dit pas la réponse.
Écris dans ‹WORK›/build/relecture/prop-italiquesN.tsv et nulle part ailleurs, une ligne par rangée, sans en-tête :
numéro <TAB> page <TAB> id <TAB> mot <TAB> verdict (« italique », « romain » ou « illisible »)
Réponds seulement « fini, N lignes ».
```

### E. Notes (appels, texte et italique)

```
Pour chaque note du fichier ‹liste : texte de la note avec <i>…</i> et contexte où [APPEL] marque l'appel placé›,
ouvre l'image de sa page. L'appel imprimé est un petit chiffre surélevé dans le texte. Vérifie : l'appel est-il
derrière le bon mot, à la bonne occurrence ? le texte de la note est-il exact ? quels mots de la note sont en
italique ? le mot appelé est-il en italique ? quelle ponctuation suit l'appel ?
Écris dans ‹WORK›/build/relecture/prop-notes.tsv, une ligne par note, sans en-tête :
page <TAB> mot appelé <TAB> appel bien placé (oui/non + où) <TAB> texte imprimé <TAB> mots en italique <TAB> mot appelé en italique (oui/non) <TAB> ponctuation après l'appel
Recopie ce qui est imprimé, même si cela semble fautif. Réponds seulement « fini, N lignes ».
```

### G. Ponctuation (un lot de planches par sous-agent)

```
Lis avec Read les images ‹WORK›/build/relecture/ponctuation-N.png (‹liste des N›) et les fichiers
‹WORK›/build/relecture/ponctuation-in-N.tsv (colonnes : numéro, page, mot lu par l'OCR, mot suivant).
Chaque case porte un numéro rouge ; le mot à juger est souligné en rouge. Regarde le signe imprimé juste après ce mot :
un point est rond et posé sur la ligne ; une virgule descend sous la ligne avec une petite queue ; des points de
suspension sont trois points. Compare avec les autres signes de la case. Ne juge que le signe, pas le sens.
Écris dans ‹WORK›/build/relecture/prop-ponctuation‹K›.tsv et nulle part ailleurs, une ligne par numéro, sans en-tête :
numéro <TAB> page <TAB> mot <TAB> verdict (« point », « virgule », « suspension » ou « illisible »)
Réponds seulement « fini, N lignes ».
```

### F. Contrôle à l'aveugle avant livraison

Sur un lot important, fais juger par un sous-agent Sonnet un échantillon aléatoire d'appels et de décisions d'italique,
en lui cachant les décisions prises. Sépare ce qui vient d'un verdict sur l'image et ce qui vient de la mesure seule :
la mesure seule se trompe bien plus souvent. Ne livre pas tant qu'un motif d'erreur reste inexpliqué.
