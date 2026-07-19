"""Construit le message Slack hebdomadaire du module Ventes (spec — Format Slack).

Message séparé de celui de la rentabilité, envoyé juste après dans le même run.
Contrairement au rapport rentabilité (où les alertes ne vont que dans l'Excel),
la spec Ventes demande les alertes directement dans Slack — on les garde donc
ici, plafonnées pour rester court.

Le détail par vendeur est en petites "cartes" (un section block par vendeur,
avec fields) plutôt qu'un tableau en bloc de code : ça évite le désalignement
du monospace sur mobile Slack et reste lisible même à 3-4 vendeurs.
"""

from __future__ import annotations

from rapport_ventes import RapportVentes

MAX_ALERTES_SLACK = 6

ICONES_ALERTES = {
    "zero_envoi": "🚫",
    "chute_ventes": "📉",
    "chute_ventes_equipe": "📉",
    "closing_bas": "🎯",
    "closing_bas_equipe": "🎯",
    "soumissions_dormantes": "💤",
}


def _fmt_dollars(montant: float) -> str:
    return f"{montant:,.0f} $".replace(",", " ")


def _fmt_variation(pct: float | None) -> str:
    if pct is None:
        return "s/o"
    emoji = "🟢" if pct >= 0 else "🔴"
    signe = "+" if pct >= 0 else "−"
    return f"{emoji} {signe}{abs(pct):.0%}"


def _fmt_taux(pct: float | None) -> str:
    return f"{pct:.0%}" if pct is not None else "s/o"


def _espaceur() -> dict:
    """Bloc invisible (context avec un espace) : ajoute un peu d'air entre deux paragraphes,
    plus léger visuellement qu'un divider entre chaque vendeur."""
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": " "}]}


def construire_message_slack_ventes(rapport: RapportVentes) -> dict:
    periode = f"{rapport.debut.strftime('%d %b')} au {rapport.fin.strftime('%d %b %Y')}"

    variation_envois = (
        (rapport.equipe_envoyees_n - rapport.equipe_envoyees_n_precedente) / rapport.equipe_envoyees_n_precedente
        if rapport.equipe_envoyees_n_precedente
        else None
    )

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📈 Ventes MAG — {periode}"},
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*$ vendus*\n{_fmt_dollars(rapport.equipe_vendues_dollars)}  {_fmt_variation(rapport.equipe_variation_pct)}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Soumissions envoyées*\n{rapport.equipe_envoyees_n}  {_fmt_variation(variation_envois)}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Taux de closing*\n{_fmt_taux(rapport.equipe_taux_closing_cohorte)} cohorte "
                    f"_(hebdo {_fmt_taux(rapport.equipe_taux_closing_hebdo)})_",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Panier moyen*\n{_fmt_dollars(rapport.montant_moyen_vendu)}",
                },
            ],
        },
    ]

    if rapport.vendeurs:
        blocks.append(_espaceur())
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Par vendeur*  _(trié par $ vendus)_"}}
        )
        blocks.append(_espaceur())
        for i, v in enumerate(rapport.vendeurs):
            if i > 0:
                blocks.append(_espaceur())
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{v.nom}* — {_fmt_dollars(v.vendues_dollars)}  {_fmt_variation(v.variation_pct)}",
                    },
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"Envoyées\n{v.envoyees_n}  ({_fmt_dollars(v.envoyees_dollars)})",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"Closing\n{_fmt_taux(v.taux_closing_cohorte)} cohorte "
                            f"_(hebdo {_fmt_taux(v.taux_closing_hebdo)})_",
                        },
                    ],
                }
            )

    blocks.append(_espaceur())
    blocks.append({"type": "divider"})
    blocks.append(
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*📅 Projection du mois*\n{rapport.verdict_projection}"}}
    )

    if rapport.alertes:
        alertes_affichees = rapport.alertes[:MAX_ALERTES_SLACK]
        texte_alertes = "\n".join(f"{ICONES_ALERTES.get(a.type, '•')} {a.message}" for a in alertes_affichees)
        if len(rapport.alertes) > MAX_ALERTES_SLACK:
            texte_alertes += f"\n… et {len(rapport.alertes) - MAX_ALERTES_SLACK} de plus."
        blocks.append(_espaceur())
        blocks.append({"type": "divider"})
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Alertes*\n{texte_alertes}"}}
        )

    texte_repli = f"Ventes MAG {periode} : {_fmt_dollars(rapport.equipe_vendues_dollars)} vendus"
    return {"text": texte_repli, "blocks": blocks}
