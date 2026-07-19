"""
Script principal — exécuté chaque lundi matin par le GitHub Action.

Récupère les données Jobber de la semaine précédente (lundi->dimanche),
calcule la rentabilité, envoie le message Slack et génère l'Excel.

Usage : python3 src/generer_rapport.py
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import yaml
from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from excel_report import generer_excel
from jobber_client import ClientJobber
from jobber_source import jobs_fermes_dans_fenetre, obtenir_jobs_semaine, obtenir_timesheets_semaine
from rapport import construire_rapport
from slack_message import construire_message_slack, envoyer_slack

RACINE = Path(__file__).resolve().parent.parent


def fenetre_semaine_precedente(aujourdhui: date | None = None) -> tuple[date, date]:
    """
    Lundi->dimanche de la semaine précédente, par rapport à aujourd'hui.

    Le GitHub Action tourne le lundi matin : "aujourd'hui" est donc un lundi,
    et la fenêtre voulue est le lundi au dimanche précédents (7 jours pile).
    """
    aujourdhui = aujourdhui or date.today()
    lundi_courant = aujourdhui - timedelta(days=aujourdhui.weekday())
    debut = lundi_courant - timedelta(days=7)
    fin = lundi_courant - timedelta(days=1)
    return debut, fin


def _sauvegarder_refresh_token_partout(nouveau_token: str):
    """
    Persiste le nouveau refresh_token (rotation active sur l'app Jobber).

    En local : dans .env. Sur GitHub Actions : .env n'existe pas (les secrets
    viennent des variables d'environnement), donc on écrit le nouveau token
    dans un fichier que le workflow lit ensuite pour mettre à jour le secret
    GitHub via l'API (voir .github/workflows/rapport_hebdomadaire.yml).
    """
    if CHEMIN_ENV.exists():
        maj_env("JOBBER_REFRESH_TOKEN", nouveau_token)
    (RACINE / "nouveau_refresh_token.txt").write_text(nouveau_token, encoding="utf-8")


def main():
    import os

    config = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))

    # En local : .env. Sur GitHub Actions : variables d'environnement (secrets).
    env = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else os.environ

    debut, fin = fenetre_semaine_precedente()
    print(f"Fenêtre du rapport : {debut} au {fin}")

    client = ClientJobber(
        env["JOBBER_CLIENT_ID"],
        env["JOBBER_CLIENT_SECRET"],
        env["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=_sauvegarder_refresh_token_partout,
    )

    print("Récupération des jobs...")
    jobs = obtenir_jobs_semaine(client, debut, fin)
    print(f"  {len(jobs)} jobs candidats.")

    print("Récupération des timesheets...")
    entrees = obtenir_timesheets_semaine(client, debut, fin, seuil_timer_oublie=config["seuil_timer_oublie"])
    print(f"  {len(entrees)} entrées.")

    jobs_fermes = jobs_fermes_dans_fenetre(jobs, debut, fin)
    print(f"  {len(jobs_fermes)} jobs fermés dans la fenêtre.")

    rapport = construire_rapport(jobs, jobs_fermes, entrees, config, debut, fin)

    print(f"$/h global : {rapport.dollars_heure_global:.2f}")
    print(f"{len(rapport.alertes)} alerte(s) générée(s).")

    payload = construire_message_slack(rapport)
    envoyer_slack(env["SLACK_WEBHOOK_URL"], payload)
    print("Message Slack envoyé.")

    dossier_sortie = RACINE / "outputs"
    dossier_sortie.mkdir(exist_ok=True)
    chemin_xlsx = dossier_sortie / f"rentabilite_{debut.isoformat()}_au_{fin.isoformat()}.xlsx"
    generer_excel(str(chemin_xlsx), jobs_fermes, entrees, rapport.resultat, config, debut, fin)
    print(f"Excel généré : {chemin_xlsx}")


if __name__ == "__main__":
    sys.exit(main())
