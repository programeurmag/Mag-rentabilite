"""
Script du rappel matinal (spec section 5b) — exécuté chaque matin (7 jours
sur 7) par un GitHub Action SÉPARÉ du rapport hebdomadaire du lundi.

Poste dans le canal Slack de production un rappel amical en français
québécois de remplir le Rapport de chantier, avec le lien du Form. Bonus
intelligent : si des jobs fermés HIER dans Jobber n'ont aucun rapport de
chantier, le message le mentionne par camion (jamais par personne).

Usage : python3 src/generer_rappel_matin.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from form_chantier_source import obtenir_soumissions_avec_degradation
from jobber_client import ClientJobber
from jobber_source import jobs_fermes_dans_fenetre, obtenir_jobs_semaine
from message_matinal import construire_message_matinal, envoyer_slack
from rapport_chantier import calculer_chantier, grouper_jobs_sans_rapport

RACINE = Path(__file__).resolve().parent.parent
FUSEAU_MAG = ZoneInfo("America/Montreal")


def _sauvegarder_refresh_token_partout(nouveau_token: str):
    if CHEMIN_ENV.exists():
        maj_env("JOBBER_REFRESH_TOKEN", nouveau_token)
    (RACINE / "nouveau_refresh_token.txt").write_text(nouveau_token, encoding="utf-8")


def main():
    import os

    config = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))
    env = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else os.environ

    url_form = config.get("url_form")
    if not url_form:
        print("Rappel matinal : url_form absent de config.yaml, envoi annulé.")
        return 1

    # "Aujourd'hui" doit être calculé côté Montréal, pas UTC (runner GitHub) :
    # sinon, autour de minuit, "hier" serait décalé d'un jour.
    aujourdhui_mag = datetime.now(FUSEAU_MAG).date()
    hier = aujourdhui_mag - timedelta(days=1)

    jobs_manquants_par_truck = {}
    try:
        client = ClientJobber(
            env["JOBBER_CLIENT_ID"],
            env["JOBBER_CLIENT_SECRET"],
            env["JOBBER_REFRESH_TOKEN"],
            sur_nouveau_refresh_token=_sauvegarder_refresh_token_partout,
        )
        jobs_hier = obtenir_jobs_semaine(client, hier, hier)
        jobs_fermes_hier = jobs_fermes_dans_fenetre(jobs_hier, hier, hier)
        print(f"  {len(jobs_fermes_hier)} job(s) fermé(s) hier ({hier}).")

        soumissions = obtenir_soumissions_avec_degradation(env, config)
        resultat_chantier = calculer_chantier(soumissions, jobs_hier, [], config)
        jobs_manquants_par_truck = grouper_jobs_sans_rapport(jobs_fermes_hier, resultat_chantier, config)
    except Exception as e:  # noqa: BLE001 — le rappel doit partir même si le "bonus" échoue
        print(f"Rappel matinal : bonus 'jobs sans rapport' ignoré ({type(e).__name__}: {e}).")

    message = construire_message_matinal(url_form, jobs_manquants_par_truck)

    webhook = env.get("SLACK_WEBHOOK_URL_PRODUCTION") or env.get("SLACK_WEBHOOK_URL")
    if not webhook:
        print("Rappel matinal : aucun webhook Slack configuré (SLACK_WEBHOOK_URL_PRODUCTION / SLACK_WEBHOOK_URL).")
        return 1

    envoyer_slack(webhook, message)
    print("Rappel matinal envoyé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
