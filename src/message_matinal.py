"""
Message matinal automatique dans Slack (spec section 5b) : rappel amical en
français québécois aux chauffeurs de remplir le Rapport de chantier, avec le
lien du Form. Formulation variée pour ne pas devenir du bruit ignoré, plus un
bonus intelligent qui mentionne (par camion, sans blâmer personne) les jobs
de la veille fermés dans Jobber sans rapport.
"""

from __future__ import annotations

import random

from slack_message import envoyer_slack  # noqa: F401 — réexporté pour les scripts appelants

TEMPLATES_RAPPEL = [
    "Bon matin la gang! ☀️ Petit rappel : le formulaire de chantier, un par job avant de quitter le "
    "site. C'est ça qui calcule la rentabilité. Lien : {url} — Bonne journée! 💪",
    "Salut l'équipe! 👋 N'oubliez pas le rapport de chantier avant de partir du site, un par job. "
    "Ça prend 30 secondes et ça compte pour vrai. Lien ici : {url}",
    "Bon matin! 🚛 Petit rappel du matin : un rapport de chantier par job, avant de quitter. "
    "{url} — merci la gang, bonne journée!",
    "Yo la gang! ☀️ Aujourd'hui comme d'habitude : un formulaire de chantier par job visité, "
    "avant de partir du site. Lien : {url} 💪",
    "Bon matin tout le monde! 🙌 Rappel amical : remplissez le rapport de chantier pour chaque job "
    "avant de quitter — ça nous aide à voir clair dans la rentabilité. {url}",
]


def construire_message_matinal(url_form: str, jobs_manquants_par_truck: dict | None = None) -> dict:
    """Construit le payload Slack (Block Kit) du rappel matinal.

    `jobs_manquants_par_truck` (optionnel) : {truck: [Job, ...]} fermés hier
    dans Jobber sans aucun rapport de chantier (voir rapport_chantier.grouper_jobs_sans_rapport).
    """
    texte_rappel = random.choice(TEMPLATES_RAPPEL).format(url=url_form)

    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": texte_rappel}}]

    if jobs_manquants_par_truck:
        lignes = []
        for truck, jobs in sorted(jobs_manquants_par_truck.items()):
            n = len(jobs)
            mot = "job" if n == 1 else "jobs"
            lignes.append(f"• Hier il manque le rapport pour {n} {mot} du {truck} — vous pouvez les remplir ce matin.")
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lignes)}})

    return {"text": texte_rappel, "blocks": blocks}
