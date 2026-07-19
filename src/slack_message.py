"""Construit et envoie le message Slack hebdomadaire (spec 1.6, point 1)."""

from __future__ import annotations

import requests

from rapport import RapportSemaine

MAX_LIGNES_TABLEAU = 15  # évite un message Slack trop long


def _formater_jobs(rapport: RapportSemaine) -> str:
    if not rapport.jobs_fermes:
        return "_Aucun job fermé cette semaine._"

    lignes = ["`$/h    Job#   Client                          Heures   Marge`"]
    for l in rapport.jobs_fermes[:MAX_LIGNES_TABLEAU]:
        alerte = "⚠ " if l.heures_attribuees == 0 else ""
        lignes.append(
            f"`{l.dollars_heure:6.0f}  #{l.numero:<5} {l.client[:28]:<28} "
            f"{l.heures_attribuees:6.1f}h  {l.marge:8.0f}$`  {alerte}"
        )
    if len(rapport.jobs_fermes) > MAX_LIGNES_TABLEAU:
        lignes.append(f"_...et {len(rapport.jobs_fermes) - MAX_LIGNES_TABLEAU} autres jobs (détail dans l'Excel)._")
    return "\n".join(lignes)


def _formater_alertes(rapport: RapportSemaine) -> str:
    if not rapport.alertes:
        return "✅ Aucune alerte cette semaine."
    return "\n".join(f"• {a.message}" for a in rapport.alertes)


def construire_message_slack(rapport: RapportSemaine) -> dict:
    """Construit le payload JSON (Block Kit) pour le webhook Slack entrant."""
    periode = f"{rapport.debut.strftime('%d %b')} au {rapport.fin.strftime('%d %b %Y')}"
    pct_non_attribue = (
        rapport.heures_non_attribuees_total / rapport.heures_punchees_total
        if rapport.heures_punchees_total
        else 0
    )

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Rentabilité MAG — semaine du {periode}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Revenu (jobs fermés)*\n{rapport.revenu_ferme_total:,.0f} $".replace(",", " ")},
                {"type": "mrkdwn", "text": f"*$/h global*\n{rapport.dollars_heure_global:.0f} $/h"},
                {"type": "mrkdwn", "text": f"*Heures punchées*\n{rapport.heures_punchees_total:.1f} h"},
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*Attribuées / non attribuées*\n"
                        f"{rapport.heures_attribuees_total:.1f} h / "
                        f"{rapport.heures_non_attribuees_total:.1f} h ({pct_non_attribue:.0%})"
                    ),
                },
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*% main d'œuvre*\n{rapport.pct_main_doeuvre:.0%} "
                        f"({rapport.cout_mo_ferme_total:,.0f} $)".replace(",", " ")
                    ),
                },
            ],
        },
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Jobs fermés — du pire au meilleur $/h*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _formater_jobs(rapport)}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Alertes*"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _formater_alertes(rapport)}},
    ]

    texte_repli = f"Rentabilité MAG {periode} : {rapport.dollars_heure_global:.0f} $/h global"
    return {"text": texte_repli, "blocks": blocks}


def envoyer_slack(webhook_url: str, payload: dict):
    """Poste le message sur le canal Slack configuré par le webhook entrant."""
    reponse = requests.post(webhook_url, json=payload, timeout=30)
    reponse.raise_for_status()
