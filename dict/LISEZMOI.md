# Lexiques Hunspell

`fr.aff` / `fr.dic` : dictionnaire orthographique français « classique » v7.5 de Grammalecte (Olivier R.),
licence MPL 2.0, récupéré via le paquet npm `dictionary-fr@3.0.0`.

`extract_text.py` s'en sert (bibliothèque `spylls`) pour décider si un mot existe ; pyspellchecker ne sert
plus qu'aux suggestions. Pour une autre langue, déposer `<code langue>.aff` et `<code langue>.dic` ici.
