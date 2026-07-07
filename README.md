# PortaSplit Watch ❄️

Surveillance automatique du stock du **Midea PortaSplit** (climatiseur split mobile
réversible 12 000 BTU, réf. MMCS-12HRN8-QRD0) chez les enseignes françaises en ligne,
avec alerte push (ntfy) + e-mail dès qu'il repasse en stock à **999 € ou moins**.

## Comment ça marche

- GitHub Actions exécute [check.py](check.py) toutes les ~5-15 min (24 h/24, aucun PC requis).
- Source de données : API publique de [ClimRadar](https://climradar.fr/produit/portasplit)
  (7 enseignes en ligne : Leroy Merlin, Castorama, Boulanger, Darty, Fnac, ManoMano, Amazon
  + comptage des magasins physiques).
- Sur transition rupture → en stock (≤ 999 €) : notification **ntfy** haute priorité
  (canal privé, secret `NTFY_TOPIC`) + ouverture d'une **issue GitHub** (→ e-mail de
  notification GitHub). Anti-spam : 6 h de délai par enseigne.
- Bilan quotidien vers 9h (heure de Paris) : « PortaSplitWatch OK » ou « EN PANNE ».
- [Mini-app](index.html) hébergée sur GitHub Pages : état en direct + liens vers les fiches.

## Utilisation depuis le téléphone

- **Voir l'état** : ouvrir la page GitHub Pages (épinglée sur l'écran d'accueil).
- **Vérifier maintenant** : bouton « ▶️ Vérifier maintenant » → GitHub Actions →
  *Run workflow* → résultat en push ntfy ~1 min après.
- **Recevoir les alertes** : app ntfy abonnée au canal privé.

## Arrêter la surveillance (climatiseur acheté 🎉)

Onglet *Actions* → workflow « PortaSplit Watch » → menu ⋯ → *Disable workflow*
(ou supprimer le dépôt).
