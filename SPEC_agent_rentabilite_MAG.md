# SPEC — Agent de rentabilité hebdomadaire MAG Lavage À Pression

Document à donner à Claude Code au début du projet. Contexte : MAG fait de la remise à neuf de pavé uni en 2 étapes (lavage par les wash trucks, ensuite redressement/sable par les crews sable). Objectif : un rapport automatique chaque lundi matin qui montre la rentabilité ($/heure, marge) de chaque job et de chaque employé, avec analyse et alertes.

---

## Phase 1 — Collecte + rapport (à bâtir en premier)

### 1.1 Source de données : API Jobber (GraphQL)

- Compte développeur gratuit : https://developer.getjobber.com
- Créer une app privée, la connecter au compte MAG via OAuth 2.0
- Endpoint : `https://api.getjobber.com/api/graphql`
- Scopes nécessaires (lecture seule) : jobs, invoices, timesheets (`timeSheetEntries`), users, visits, clients
- Respecter la version d'API dans le header `X-JOBBER-GRAPHQL-VERSION` (vérifier la version courante dans la doc)

### 1.2 Données à extraire chaque semaine (fenêtre : lundi précédent → dimanche)

1. **Jobs fermés dans la fenêtre** : numéro, client, ville, titre, line items, date début cédulée, date fermeture, revenu total, employés assignés aux visites
2. **Jobs encore ouverts mais avec du temps punché dans la fenêtre** (important : des punches pointent souvent vers des jobs pas encore fermés)
3. **TimeSheetEntries de la fenêtre** : employé, date, début, fin, heures, visite/job lié (ou General), note
4. **Visites de la fenêtre** : job lié, date, employés assignés

### 1.3 Logique d'attribution des heures (validée sur données réelles)

Ordre de priorité pour attribuer chaque entrée de timesheet à un job :

1. **Punch direct** : l'entrée est liée à une visite/job → 100% des heures vont à ce job. Le plus fiable.
2. **Répartition** : l'entrée est « General » → répartir entre les jobs où l'employé était **assigné à une visite active ce jour-là**, au prorata de la valeur ($) des jobs. Si un seul job candidat, tout va là.
3. **Non attribué** : aucun job candidat ce jour-là → catégorie « vente / déplacement / admin ». Ne pas forcer l'attribution. Afficher le total dans le rapport.

Règles de qualité de données :
- **Anomalie timer oublié** : toute entrée > 12 h est flaggée « ⚠ timer? » et EXCLUE des calculs de coût (mais listée dans le rapport pour correction manuelle). Cas réel observé : 23,5 h de suite.
- Ignorer le compte « MAG Lavage À Pression » (compte compagnie, pas un employé).
- Les noms d'employés doivent matcher exactement entre timesheets et assignations (attention aux espaces en fin de nom, ex. « Michael  »).

### 1.4 Calculs par job

- `heures_attribuées` = somme des heures attribuées (directes + réparties)
- `coût_MO` = Σ (heures de chaque employé × taux horaire × facteur charges 1,125)
- `marge` = revenu − coût_MO − matériaux (matériaux : champ manuel ou custom field Jobber si disponible)
- `$_par_heure` = revenu ÷ heures_attribuées
- `étape` déduite des line items : « Remise à neuf » / « Redressement » / « Nettoyage » / « Sable » / « Réfection dalles »

### 1.5 Configuration (fichier `config.yaml`, jamais commité avec le token)

```yaml
taux_horaires:            # $/h par employé — fournis par Justin (juillet 2026)
  "Kevin Bierry": 24
  "Paul": 24
  "Theo Hennebert": 24
  "Matis Rodrigue": 25
  "Gabriel Paquet": 26
  "Michael": 24            # attention: nom avec espace final dans Jobber ("Michael ")
  "Rafael": 24
  "Charly Pearson": 23
  "Zach Plessis Belair": 23
  "Adam Menard": 23
  "Hansley Gauthier Minuty": 23
  "Jerome Pare": 23
  "Mathis Lavallée": 23
  "Kevin Mailhot": 24
  "Victor Delisle": 23
  "Justin Boivin": 0       # vendeur (payé autrement) — ses heures terrain comptent dans les heures du job mais pas dans le coût MO
  "Pascal": 0              # ex-employé (renvoyé) — garder à 0 pour les données historiques
facteur_charges: 1.125     # 12,5% — fourni par Justin (juillet 2026)
seuil_alerte_dollars_heure: 80   # en bas de ça = alerte rouge
seuil_timer_oublie: 12     # heures
roles_vente: ["Charly Pearson", "Justin Boivin"]  # heures non attribuées = normal
ex_employes: ["Pascal"]    # ignorer dans les alertes (ex. "0% punch sur visites")
```

### 1.6 Sorties du rapport

1. **Message Slack** (webhook entrant, canal #rentabilite) :
   - En-tête : revenu semaine, heures totales, heures attribuées vs non attribuées, $/h global
   - Tableau top/flop : chaque job fermé avec revenu, heures, $/h, marge — trié du pire au meilleur $/h
   - Section alertes (voir 1.7)
2. **Fichier Excel** en pièce jointe ou dans un dossier partagé : détail complet (onglets Jobs / Attribution / Heures / Par employé) — même structure que le fichier `MAG_rentabilite_reelle.xlsx` fourni comme référence.

### 1.7 Alertes automatiques (règles fixes, pas d'IA nécessaire en phase 1)

- Job avec $/h sous le seuil configuré
- Timers oubliés (> 12 h) avec nom, date, job touché
- Employés terrain avec 0% de punches sur visites cette semaine (problème d'assignation ou de téléphone)
- Jobs fermés avec 0 heure attribuée (données manquantes)
- % d'heures non attribuées si > 35%

### 1.8 Automatisation

- GitHub Actions, cron chaque lundi 5h00 AM heure de Montréal (`0 10 * * 1` UTC, ajuster selon l'heure d'été)
- Secrets GitHub : token Jobber, webhook Slack
- Alternative si préféré : tâche planifiée Windows sur l'ordi de bureau

---

## Phase 2 — Le « cerveau » (après 2-3 semaines de rapports fiables)

Ajouter un appel à l'API Claude (modèle Sonnet suffit) qui reçoit le JSON des résultats de la semaine + les 4 semaines précédentes et produit :

- 3 à 5 constats en français québécois direct (tendances, comparaisons semaine sur semaine)
- Recommandations concrètes (pricing, dispatch, discipline de punch)
- Réponses aux questions posées en thread Slack (optionnel, phase 3 : bot Slack interactif)

Prompt système suggéré : « Tu es l'analyste opérations de MAG Lavage À Pression (remise à neuf de pavé uni, Montréal). Ton lecteur est le proprio. Sois direct, concis, en français québécois, chiffres à l'appui. Priorise : jobs non rentables, dérive des coûts de main-d'œuvre, problèmes de qualité de données qui faussent les chiffres. »

---

## Checklist de démarrage pour Justin

1. [ ] Créer le compte développeur Jobber + app privée, récupérer le token OAuth
2. [ ] Créer un webhook Slack entrant pour le canal du rapport
3. [ ] Remplir les taux horaires réels dans `config.yaml`
4. [ ] Fournir à Claude Code : ce document + le fichier Excel de référence + un export CSV Timesheets et One-off Jobs (pour les tests avec des vraies données avant de brancher l'API)
5. [ ] Créer un repo GitHub privé pour le code + les secrets

## Ordre de travail suggéré pour Claude Code

1. Parser les CSV d'exemple et reproduire les chiffres du fichier Excel de référence (validation de la logique d'attribution)
2. Remplacer les CSV par les requêtes GraphQL Jobber
3. Générer le message Slack + l'Excel
4. Mettre en place le GitHub Action
5. (Phase 2) Ajouter l'analyse Claude API
