# Mots Croisés — TUI Remake

Remake terminal de la grille de mots croisés du jour de
[jeux.franceinfo.fr/mots-croises](https://jeux.franceinfo.fr/mots-croises/).

La grille du jour (fournie par le SDK `mygamify.fr`) est récupérée automatiquement
au lancement. La validation des mots est faite côté serveur, exactement comme sur
le site : les réponses ne sont jamais présentes en clair côté client.

## Lancer

```bash
uv run mots-croises/mots-croises.py
```

La grille du jour s'ouvre automatiquement. Appuyez sur **Ctrl+G** pour choisir
une autre grille (du jour ou des archives) dans un sélecteur, avec l'état de
progression de chacune.

## Jouer

| Action | Effet |
| --- | --- |
| **Clic sur une case** | sélectionne le mot **horizontal** de la case |
| **Re-clic sur la même case** | bascule sur le mot **vertical** |
| **Lettre** | écrit dans la case courante puis avance à la case non verrouillée suivante |
| **Flèches** | déplacent le curseur (et choisissent le sens) |
| **Espace** | bascule horizontal / vertical |
| **Tab** | mot suivant non trouvé |
| **Retour arrière** | efface |
| **Ctrl+G** | choisir une grille (jour / archives) |
| **Ctrl+Q** | quitter |

Dès qu'un mot est correctement rempli, il est validé : ses cases deviennent
vertes et **verrouillées** (on ne peut plus les modifier). Un mot rempli mais
incorrect apparaît en **orange** jusqu'à ce qu'on le modifie.

## Sauvegarde

La progression est enregistrée automatiquement dans `saves.json`, **par grille**
(indexée par son numéro, ex. `1187`). On peut fermer le jeu et le rouvrir pour
reprendre là où on s'était arrêté, y compris sur plusieurs grilles différentes
en parallèle.

Les grilles téléchargées sont mises en cache dans `grids_cache.json` (elles ne
changent jamais) : une grille déjà ouverte se recharge instantanément, même
après redémarrage. La liste des grilles du jour est elle aussi mise en cache
(`gridlist_cache.json`) : rouvrir l'app le même jour ne fait **aucun** appel
réseau au démarrage.
