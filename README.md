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
```

## Automatisation

Un GitHub Action (`.github/workflows/rapport_hebdomadaire.yml`) exécute `generer_rapport.py`
chaque lundi matin. Secrets requis dans Settings → Secrets and variables → Actions :

- `JOBBER_CLIENT_ID`
- `JOBBER_CLIENT_SECRET`
- `JOBBER_REFRESH_TOKEN` (rotation désactivée côté Jobber — token stable)
- `SLACK_WEBHOOK_URL`

## Structure

- `src/parseur.py` / `src/jobber_source.py` — deux sources de données (CSV ou API Jobber),
  produisant les mêmes structures.
- `src/attribution.py` — logique d'attribution des heures aux jobs (spec section 1.3).
- `src/rapport.py` — agrégats et alertes automatiques.
- `src/slack_message.py` / `src/excel_report.py` — génération des sorties.
- `src/generer_rapport.py` — script principal (celui exécuté par le GitHub Action).
