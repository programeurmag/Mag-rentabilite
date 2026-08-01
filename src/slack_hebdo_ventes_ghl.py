"""
Résumé hebdomadaire Slack — Module Dashboard Ventes GHL. Envoyé chaque lundi
7h30 heure de Montréal (voir .github/workflows/slack_hebdo_ventes_ghl.yml).

Ne fait AUCUN appel à l'API GHL : lit simplement le JSON déjà généré par le run
horaire du dashboard (docs/stats_ventes_ghl.json, committé dans le repo). Le
run de 7h00 du lundi a donc toujours quelques minutes d'avance sur celui-ci.

Usage : python3 src/slack_hebdo_ventes_ghl.py
"""

from __future__ import annotations

import json
import os

import yaml
from dotenv import dotenv_values

from dashboard_ventes_ghl import CHEMIN_JSON
from env_utils import CHEMIN_ENV, RACINE
from metriques_ventes_ghl import couleur_seuil
from slack_message import envoyer_slack

LABELS_METRIQUES = {
    "pct_contact_meme_jour": ("Contact même jour", "contact_meme_jour"),
    "pct_visite_bookee": ("Visite bookée", "visite_bookee"),
    "pct_rappel_48h": ("Rappel 48h", "rappel_48h"),
    "pct_closing": ("Taux de closing", "closing"),
}


def _fmt_pct(v: float | None) -> str:
    return f"{v:.0f}%" if v is not None else "s/o"


def _points_rouges(vendeurs: list[dict], seuils: dict) -> list[str]:
    points = []
    for v in vendeurs:
        for cle, (label, cle_seuil) in LABELS_METRIQUES.items():
            valeur = v["actuelle"][cle]
            if couleur_seuil(valeur, seuils[cle_seuil]) == "rouge":
                points.append(f"🔴 *{v['nom']}* — {label} : {_fmt_pct(valeur)}")
    return points


def _top_mover(vendeurs: list[dict]) -> tuple[str, float] | None:
    """Vendeur avec la plus grosse amélioration moyenne (en points) sur les 4 métriques,
    entre la fenêtre précédente et l'actuelle. None si rien de comparable."""
    meilleur = None
    for v in vendeurs:
        deltas = []
        for cle in LABELS_METRIQUES:
            actuel, precedent = v["actuelle"][cle], v["precedente"][cle]
            if actuel is not None and precedent is not None:
                deltas.append(actuel - precedent)
        if not deltas:
            continue
        moyenne = sum(deltas) / len(deltas)
        if meilleur is None or moyenne > meilleur[1]:
            meilleur = (v["nom"], moyenne)
    return meilleur


def construire_message(stats: dict, seuils: dict) -> dict:
    equipe = stats["equipe"]["actuelle"]
    vendeurs = stats["vendeurs"]
    periode = f"{stats['periode']['debut'][:10]} au {stats['periode']['fin'][:10]}"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"📊 Dashboard Ventes — {periode}"}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Leads*\n{equipe['n_leads']}"},
                {"type": "mrkdwn", "text": f"*Contact même jour*\n{_fmt_pct(equipe['pct_contact_meme_jour'])}"},
                {"type": "mrkdwn", "text": f"*Visite bookée*\n{_fmt_pct(equipe['pct_visite_bookee'])}"},
                {"type": "mrkdwn", "text": f"*Rappel 48h*\n{_fmt_pct(equipe['pct_rappel_48h'])}"},
                {"type": "mrkdwn", "text": f"*Taux de closing*\n{_fmt_pct(equipe['pct_closing'])}"},
            ],
        },
    ]

    top = _top_mover(vendeurs)
    if top:
        nom, moyenne = top
        blocks.append({"type": "divider"})
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"🏆 *Top mover* : {nom} ({'+' if moyenne >= 0 else ''}{moyenne:.0f} pts en moyenne sur les 4 métriques vs les 30 jours précédents)"},
            }
        )

    rouges = _points_rouges(vendeurs, seuils)
    blocks.append({"type": "divider"})
    if rouges:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Points rouges*\n" + "\n".join(rouges)}})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "✅ Aucun point rouge cette semaine."}})

    texte_repli = f"Dashboard Ventes {periode} — {len(rouges)} point(s) rouge(s)"
    return {"text": texte_repli, "blocks": blocks}


def main():
    config_yaml = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))
    env = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else dict(os.environ)

    if not CHEMIN_JSON.exists():
        raise SystemExit(f"{CHEMIN_JSON} introuvable — le dashboard horaire doit tourner avant ce résumé.")
    stats = json.loads(CHEMIN_JSON.read_text(encoding="utf-8"))

    message = construire_message(stats, config_yaml["seuils_dashboard_ventes"])
    envoyer_slack(env["SLACK_WEBHOOK_URL"], message)
    print("Résumé hebdo Slack (dashboard ventes GHL) envoyé.")


if __name__ == "__main__":
    main()
