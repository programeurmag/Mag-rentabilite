# SPEC — Module 2 : Rapport Ventes hebdomadaire MAG

Extension du projet existant (agent de rentabilité). Même stack, même repo, même API Jobber, même webhook Slack (ou un 2e canal #ventes si préféré). S'exécute dans le même GitHub Action du lundi matin, envoyé comme message séparé.

---

## Données à extraire de l'API Jobber (GraphQL)

1. **Soumissions (quotes)** créées dans les 8 dernières semaines : numéro, client, montant total, date de création, date d'approbation (si approuvée), statut (draft / awaiting response / approved / converted / archived), vendeur (créateur de la soumission ou champ salesperson selon ce que l'API expose — vérifier les deux)
2. **Jobs créés à partir de soumissions approuvées** (pour les montants convertis réels)
3. Historique suffisant pour comparer semaine vs semaine précédente et calculer le cumul du mois

Vendeurs à suivre : **Jeremy Dagenais, MAG Lavage À Pression (compte de Justin, le proprio) et Justin Boivin** (configurable dans config.yaml — liste `vendeurs`). Note : Charly Pearson est passé à la production, ne plus le compter comme vendeur. Vérifier le nom exact de Jeremy tel qu'il apparaît dans Jobber.

## Métriques du rapport

### Par vendeur (tableau, trié par $ vendus)
- Soumissions envoyées (nombre + $ total)
- Soumissions approuvées / converties (nombre + $ total)
- Taux de closing (voir définition plus bas)
- Comparaison vs semaine précédente : $ vendus, en % (↑ vert / ↓ rouge)

### Équipe (totaux)
- $ vendus cette semaine vs semaine passée (% de variation)
- Soumissions envoyées cette semaine vs semaine passée
- Taux de closing global
- Montant moyen par soumission approuvée

### Définition du taux de closing (important — les deux se calculent)
1. **Taux hebdo simple** : approuvées cette semaine ÷ envoyées cette semaine (rapide mais trompeur : une soumission approuvée cette semaine a souvent été envoyée avant)
2. **Taux de cohorte (le vrai)** : sur les soumissions envoyées il y a 2 à 6 semaines, % approuvées à date. C'est celui-là qui mesure la vraie performance de closing.
Afficher le taux de cohorte en principal, le hebdo entre parenthèses.

### Projection du mois (« est-ce qu'on va avoir un bon mois? »)
- **Pacing** : $ vendus du mois à date ÷ jours ouvrés écoulés × jours ouvrés totaux du mois = projection fin de mois
- Comparer à : (a) l'objectif mensuel dans config.yaml (`objectif_mensuel: 0` = pas d'objectif, comparer seulement au mois précédent), (b) le mois précédent complet
- Verdict en une ligne : « Projection : 87 400 $ — 12% au-dessus de juin, en avance de 8% sur l'objectif » ou l'inverse

## Alertes automatiques
- Vendeur avec 0 soumission envoyée dans la semaine
- Chute de plus de 25% des $ vendus vs semaine précédente (équipe ou vendeur)
- Taux de closing de cohorte sous le seuil configuré (`seuil_closing: 0.30` par défaut)
- Soumissions en attente depuis plus de 14 jours sans suivi (nombre + $ dormant) — c'est de l'argent sur la table

## Format Slack
Message séparé du rapport rentabilité, envoyé juste après. Structure : en-tête équipe (3-4 lignes avec les variations %), tableau par vendeur, projection du mois, alertes. Court — le détail complet va dans un Excel en artefact du GitHub Action si besoin, pas dans Slack.

## Phase 2 (analyse IA, même clé Anthropic)
Ajouter les données ventes au contexte envoyé à Claude : tendances 4 semaines, mix soumissions par type de service si détectable dans les line items, recommandations (ex. « les soumissions de redressement closent à 55% vs 25% pour les remises à neuf — priorisez les leads redressement »).

## config.yaml — ajouts
```yaml
vendeurs: ["Jeremy Dagenais", "MAG Lavage À Pression", "Justin Boivin"]  # vérifier les noms exacts dans Jobber
objectif_mensuel: 0        # $ — 0 = comparer seulement au mois précédent
seuil_closing: 0.30
seuil_chute_ventes: 0.25
jours_relance_soumission: 14
```

## Ordre de travail
1. Requête GraphQL quotes + validation sur les données réelles du compte (comparer les totaux avec ce que Jobber affiche dans Reports → Quotes)
2. Calculs + message Slack
3. Intégrer au GitHub Action existant (même run du lundi, 2e message)
4. (Phase 2) Ajouter au contexte de l'analyse IA
