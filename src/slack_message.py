"""Construit et envoie le message Slack hebdomadaire (spec 1.6, point 1).

Version condensée : juste les chiffres clés de la semaine. Le détail complet
(tous les jobs, toutes les alertes) est dans le fichier Excel joint.
"""

from __future__ import annotations

import requests

from rapport import RapportSemaine


def construire_message_slack(rapport: RapportSemaine) -> dict:
    """Construit le payload JSON (Block Kit) pour le webhook Slack entrant."""
    periode = f"{rapport.debut.strftime('%d %b')} au {rapport.fin.strftime('%d %b %Y')}"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Rentabilité MAG — {periode}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Revenu*\n{rapport.revenu_ferme_total:,.0f} $".replace(",", " ")},
                {"type": "mrkdwn", "text": f"*$/h global*\n{rapport.dollars_heure_global:.0f} $/h"},
                {"type": "mrkdwn", "text": f"*% main d'œuvre*\n{rapport.pct_main_doeuvre:.0%}"},
            ],
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Détail complet (jobs, alertes) : voir l'Excel joint."}],
        },
    ]

    texte_repli = f"Rentabilité MAG {periode} : {rapport.dollars_heure_global:.0f} $/h global"
    return {"text": texte_repli, "blocks": blocks}


def envoyer_slack(webhook_url: str, payload: dict):
    """Poste le message sur le canal Slack configuré par le webhook entrant."""
    reponse = requests.post(webhook_url, json=payload, timeout=30)
    reponse.raise_for_status()
