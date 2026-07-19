"""
Étape 3 : génère un exemple de message Slack + fichier Excel à partir des CSV
d'exemple (mêmes données que l'étape 1), pour valider le rendu avant de
brancher le vrai webhook Slack.

Usage : python3 src/valider_etape3.py
"""

import json
import os
from datetime import date
from pathlib import Path

import yaml
from dotenv import dotenv_values

from analyse_claude import analyser_semaine, construire_payload
from excel_report import generer_excel
from historique import charger_semaines_precedentes
from parseur import parser_jobs, parser_timesheets
from rapport import construire_rapport
from slack_message import construire_message_slack

RACINE = Path(__file__).resolve().parent.parent
CSV_TIMESHEETS = RACINE / "data" / "timesheets_sample.csv"
CSV_JOBS = RACINE / "data" / "one_off_jobs_sample.csv"
CHEMIN_SORTIE_XLSX = RACINE / "outputs" / "exemple_rapport.xlsx"

DEBUT = date(2026, 7, 6)
FIN = date(2026, 7, 12)


def main():
    config = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))

    jobs = parser_jobs(str(CSV_JOBS))
    entrees = parser_timesheets(str(CSV_TIMESHEETS), seuil_timer_oublie=config["seuil_timer_oublie"])

    jobs_fermes_fenetre = {
        num: j for num, j in jobs.items() if j.date_fermeture and DEBUT <= j.date_fermeture <= FIN
    }

    rapport = construire_rapport(jobs, jobs_fermes_fenetre, entrees, config, DEBUT, FIN)

    print("=== Alertes générées ===")
    for a in rapport.alertes:
        print(f"  [{a.type}] {a.message}")

    # Phase 2 : dégradation gracieuse testée ici sans clé (ou clé invalide) —
    # analyse doit rester None et le reste du script continuer normalement.
    env = {**dotenv_values(RACINE / ".env"), **os.environ}
    payload_semaine = construire_payload(rapport)
    semaines_precedentes = charger_semaines_precedentes(DEBUT)
    analyse = analyser_semaine(env.get("ANTHROPIC_API_KEY"), payload_semaine, semaines_precedentes)
    print(f"\n=== Analyse IA : {'disponible' if analyse else 'absente (attendu sans clé valide)'} ===")

    message_slack = construire_message_slack(rapport, analyse)
    print("\n=== Aperçu message Slack (JSON) ===")
    print(json.dumps(message_slack, indent=2, ensure_ascii=False))

    CHEMIN_SORTIE_XLSX.parent.mkdir(exist_ok=True)
    generer_excel(
        str(CHEMIN_SORTIE_XLSX),
        jobs_fermes_fenetre,
        entrees,
        rapport.resultat,
        config,
        DEBUT,
        FIN,
        rapport.alertes,
        analyse,
    )
    print(f"\nExcel généré : {CHEMIN_SORTIE_XLSX}")


if __name__ == "__main__":
    main()
