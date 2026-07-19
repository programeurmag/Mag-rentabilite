# SPEC — Module 4 : Dashboard de rentabilité (Google Sheets + Looker Studio)

Extension du projet existant. Objectif : un vrai dashboard visuel, consultable en tout temps (desktop + mobile), qui se met à jour automatiquement à chaque run de l'agent. Architecture : l'agent écrit ses résultats calculés dans un Google Sheets « base de données », et un dashboard Looker Studio (gratuit) se branche dessus. Zéro serveur, zéro hébergement.

---

## 1. Le Google Sheets « MAG_Dashboard_Data »

Créé une fois par Justin (ou par l'agent au premier run). Le compte de service existant y écrit — **changer le scope de `spreadsheets.readonly` à `spreadsheets`** (lecture + écriture) et partager ce Sheets avec le compte de service en Éditeur. Le Sheets de réponses du Form reste en lecture seule.

L'agent efface et réécrit les onglets à chaque run (quotidien, après le traitement des rapports de chantier — pas seulement le lundi). Structure en tables plates, une ligne = un enregistrement, en-têtes en ligne 1, aucune cellule fusionnée, aucun formatage nécessaire (Looker s'occupe du visuel) :

### Onglet `jobs`
job_id, client, ville, etape, statut (en cours / complété), date_debut, date_fin, revenu, heures_totales, cout_mo, cout_materiaux, overhead_alloue, marge_avant_overhead, marge_apres_overhead, dollars_par_heure, marge_pct, burn_pct (jobs en cours), source_heures (form / punchs / mixte), semaine (ISO), mois (AAAA-MM)

### Onglet `attribution`
job_id, date, employe, truck, heures, cout, source (form / punch direct / réparti)

### Onglet `ventes`
semaine, vendeur, soumissions_envoyees, montant_envoye, soumissions_approuvees, montant_approuve, taux_closing_cohorte

### Onglet `quotidien` (IMPORTANT — grain journalier pour le filtrage par dates)
date (AAAA-MM-JJ), truck, revenu_complete, heures, cout_mo, cout_materiaux, overhead, marge
Une ligne par truck par jour. C'est CET onglet qui alimente les graphiques temporels : Looker Studio agrège lui-même au grain choisi (jour, semaine, mois, année) selon la période sélectionnée par l'utilisateur. Ne jamais pré-agréger en semaines seulement — le grain journalier est ce qui permet de filtrer sur un jour précis ou une plage custom.

### Onglet `hebdo` (agrégat de commodité, optionnel pour le dashboard)
semaine, revenu, heures, dollars_par_heure, cout_mo, marge, marge_pct, heures_non_attribuees, ventes_totales, soumissions_envoyees

**Règle transversale : toute table doit avoir au moins une colonne de date au format AAAA-MM-JJ** (jobs : date_fin; attribution : date; ventes : ajouter date_lundi_semaine en plus de la colonne semaine). Looker Studio a besoin d'un vrai champ date pour ses contrôles de période.

### Onglet `meta`
derniere_maj (timestamp), version, nb_jobs_traites — pour afficher la fraîcheur des données sur le dashboard

## 2. Le dashboard Looker Studio (construit par Justin, guidé — Claude Code ne peut pas le créer à sa place)

lookerstudio.google.com → Créer → Rapport → source de données : Google Sheets → MAG_Dashboard_Data. Chaque onglet devient une source. Pages recommandées :

### Contrôle de période (sur TOUTES les pages)
En haut de chaque page : un contrôle « Plage de dates » (Insérer → Contrôle de plage de dates) épinglé au niveau du rapport. La plage par défaut : « Cette semaine ». L'utilisateur peut choisir dans les présélections natives de Looker : aujourd'hui, hier, cette semaine, la semaine dernière, ce mois-ci, le mois dernier, cette année (depuis le 1er janvier), ou une plage personnalisée (n'importe quel jour ou intervalle). Tous les graphiques et tableaux de la page se filtrent automatiquement.
Activer aussi la « comparaison avec la période précédente » sur les scorecards (revenu, $/h, marge) pour afficher les flèches ↑/↓ vs la période équivalente d'avant.

### Page 1 — Vue d'ensemble
- Scorecards : revenu du mois, $/h global, marge % (avec comparaison période précédente)
- Graphique en ligne : $/h et revenu par semaine (onglet hebdo)
- Tableau : jobs en cours avec burn_pct (barres conditionnelles : vert < 40%, jaune 40-60%, rouge > 60%)

### Page 2 — Jobs
- Tableau filtrable (par période, étape, statut) : tous les jobs avec revenu, heures, $/h, marges — tri par défaut : $/h croissant (les pires en haut)
- Filtres en haut de page : plage de dates, étape, truck
- Graphique : $/h moyen par étape (barres)

### Page 3 — Équipe
- Tableau par employé : heures, coût, revenu généré, $/h généré (onglet attribution agrégé)
- Répartition des heures par truck (camembert ou barres)

### Page 4 — Ventes
- $ vendus par semaine par vendeur (barres empilées)
- Taux de closing par vendeur (scorecards)
- Projection du mois vs mois précédent

Partage : bouton Partager de Looker → accès restreint aux courriels de Justin (et partenaires au besoin). L'app mobile Looker Studio ou le navigateur du téléphone affichent le rapport.

## 3. Ordre de travail pour Claude Code

1. Étendre le module d'écriture : après chaque run (quotidien et hebdo), pousser toutes les données calculées dans MAG_Dashboard_Data selon le schéma de la section 1 (utiliser l'API Sheets en batch, pas cellule par cellule)
2. Historique : les onglets contiennent TOUTES les semaines depuis le début (append de la logique, réécriture complète du contenu à chaque run pour rester idempotent)
3. Mettre à jour le scope du compte de service et guider Justin pour le partage en Éditeur
4. Test : run manuel → vérifier que les 5 onglets se remplissent correctement
5. Guider Justin pas à pas dans la création du rapport Looker Studio (pages de la section 2) — étape manuelle mais simple, ~30-45 min une seule fois
