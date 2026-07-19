"""
Module Ventes — étape 3 : aperçu du message Slack (JSON Block Kit), à partir
des données live, SANS envoyer au webhook. Sert à valider le rendu avant de
brancher pour de vrai (voir generer_rapport.py).

Usage : python3 src/valider_etape3_ventes.py
"""

from __future__ import annotations

import json
from datetime import timedelta

import yaml
from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber
from rapport_ventes import construire_rapport_ventes
from slack_message_ventes import construire_message_slack_ventes
from valider_etape1_ventes import fenetre_n_semaines
from ventes_source import obtenir_quotes_creees

N_SEMAINES = 8


def main():
    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    env = dotenv_values(CHEMIN_ENV)

    client = ClientJobber(
        env["JOBBER_CLIENT_ID"],
        env["JOBBER_CLIENT_SECRET"],
        env["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    debut_extraction, fin = fenetre_n_semaines(N_SEMAINES)
    debut = fin - timedelta(days=6)

    soumissions = obtenir_quotes_creees(client, debut_extraction, fin)
    rapport = construire_rapport_ventes(soumissions, config, debut, fin)

    message_slack = construire_message_slack_ventes(rapport)
    print("=== Aperçu message Slack Ventes (JSON) ===")
    print(json.dumps(message_slack, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
