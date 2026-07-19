# MAG Rentabilité

Agent de rentabilité hebdomadaire pour MAG Lavage À Pression : chaque lundi matin, un rapport
($/h, marge, alertes) est envoyé sur Slack et généré en Excel, à partir des données Jobber
(jobs, visites, timesheets) de la semaine précédente (lundi → dimanche).

Voir [SPEC_agent_rentabilite_MAG.md](SPEC_agent_rentabilite_MAG.md) pour le contexte complet et
la logique d'attribution des heures aux jobs.

## Installation

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env   # puis remplir avec les vrais identifiants
```

Remplir `config.yaml` avec les taux horaires réels et les seuils d'alerte.

**Module 3 (Rapport de chantier)** : voir [SPEC_module_rapport_chantier_MAG.md](SPEC_module_rapport_chantier_MAG.md).
Remplir dans `config.yaml` : `couts_materiaux`, `employes_hors_jobber`, `url_form`,
`google_sheet_id_chantier`, `camions_equipes`. Remplir dans `.env` : `GOOGLE_SERVICE_ACCOUNT_JSON`
(contenu JSON complet de la clé du compte de service Google, une seule ligne) et
`SLACK_WEBHOOK_URL_PRODUCTION` (webhook du canal de production — jamais dans `config.yaml`,
c'est un secret). Sans ces valeurs, le rapport part quand même normalement — la hiérarchie des
sources d'heures retombe entièrement sur l'ancienne logique (dégradation gracieuse).

## Utilisation

**Autorisation Jobber (une seule fois)** :

```bash
python3 src/autoriser.py
```

**Génération manuelle du rapport** (semaine précédente, lundi → dimanche) :

```bash
python3 src/generer_rapport.py
```

**Scripts de validation** (utilisent les CSV d'exemple dans `data/`, pas l'API) :

```bash
python3 src/valider_etape1.py   # logique d'attribution vs fichier Excel de référence
python3 src/valider_etape2.py   # comparaison CSV vs API Jobber en direct
python3 src/valider_etape3.py   # aperçu du message Slack + génération Excel d'exemple
python3 src/valider_etape_chantier.py   # Module 3 hors-ligne : CSV d'exemple + soumissions Form synthétiques
```

## Automatisation

Un GitHub Action (`.github/workflows/rapport_hebdomadaire.yml`) exécute `generer_rapport.py`
chaque lundi matin. Secrets requis dans Settings → Secrets and variables → Actions :

- `JOBBER_CLIENT_ID`
- `JOBBER_CLIENT_SECRET`
- `JOBBER_REFRESH_TOKEN` (rotation désactivée côté Jobber — token stable)
- `SLACK_WEBHOOK_URL`
- `ANTHROPIC_API_KEY` (Phase 2, optionnel — voir ci-dessous)
- `GOOGLE_SERVICE_ACCOUNT_JSON` (Module 3, optionnel — voir ci-dessous)

Le workflow committe aussi automatiquement l'historique de la semaine (`historique/*.json`),
d'où le `permissions: contents: write` dans le fichier YAML.

Un second GitHub Action (`.github/workflows/rappel_matin.yml`) exécute `generer_rappel_matin.py`
chaque matin (7 jours sur 7, voir Module 3 section 5b) : rappel Slack aux chauffeurs de remplir
le Rapport de chantier. Mêmes secrets que ci-dessus (pas besoin de `ANTHROPIC_API_KEY`).

## Phase 2 — Analyse Claude (optionnelle)

Si `ANTHROPIC_API_KEY` est configurée (secret GitHub ou `.env` local), chaque rapport inclut
3-5 constats + des recommandations générés par Claude (modèle `claude-opus-4-8`), en comparant
la semaine actuelle aux ~4 semaines précédentes (`historique/`). Résumé court dans Slack, détail
complet dans l'onglet Analyse de l'Excel.

**Dégradation gracieuse** : si la clé est absente, invalide, ou que l'appel échoue pour
n'importe quelle raison, le rapport part quand même normalement — juste sans cette section
(voir `src/analyse_claude.py`).

## Structure

- `src/parseur.py` / `src/jobber_source.py` — deux sources de données (CSV ou API Jobber),
  produisant les mêmes structures.
- `src/attribution.py` — logique d'attribution des heures aux jobs (spec section 1.3).
- `src/rapport.py` — agrégats et alertes automatiques.
- `src/slack_message.py` / `src/excel_report.py` — génération des sorties.
- `src/analyse_claude.py` / `src/historique.py` — analyse IA (Phase 2) et sa mémoire de semaines.
- `src/generer_rapport.py` — script principal (celui exécuté par le GitHub Action).
- `src/form_chantier_source.py` — Module 3 : lecture/parsing du Sheets de réponses du Form.
- `src/rapport_chantier.py` — Module 3 : hiérarchie des sources, overhead, jobs multi-jours, alertes.
- `src/message_matinal.py` / `src/generer_rappel_matin.py` — Module 3 : rappel Slack matinal.
