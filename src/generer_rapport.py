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

from analyse_claude import analyser_semaine, construire_payload
from dashboard_sheets import lignes_attribution_semaine, lignes_quotidien_semaine, lignes_ventes_semaine, publier_dashboard
from env_utils import CHEMIN_ENV, maj_env
from excel_report import generer_excel
from form_chantier_source import obtenir_soumissions_avec_degradation
from historique import charger_semaines_precedentes, charger_toutes_semaines, sauvegarder_semaine
from jobber_client import ClientJobber
from jobber_source import jobs_fermes_dans_fenetre, obtenir_jobs_semaine, obtenir_timesheets_semaine
from rapport import construire_rapport
from rapport_ventes import construire_rapport_ventes
from slack_message import construire_message_slack, envoyer_slack
from slack_message_ventes import construire_message_slack_ventes
from ventes_source import obtenir_quotes_creees

RACINE = Path(__file__).resolve().parent.parent

# Fenêtre d'extraction des soumissions : 8 semaines complètes se terminant à la
# fin de la semaine du rapport (voir valider_etape1_ventes.py — approche validée
# sur les données réelles du compte en juillet 2026).
N_SEMAINES_VENTES = 8


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
    Filet de sécurité si Jobber active un jour la rotation des refresh tokens
    (actuellement DÉSACTIVÉE sur l'app MAG, confirmé empiriquement le 19
    juillet 2026 — voir le docstring de ClientJobber._rafraichir dans
    jobber_client.py) : ClientJobber n'appelle cette fonction que si l'API
    retourne un refresh_token différent de celui utilisé, ce qui n'arrive
    jamais dans l'état actuel.

    En local : écrit dans .env. Sur GitHub Actions : .env n'existe pas (les
    secrets viennent des variables d'environnement), donc écrit le nouveau
    token dans nouveau_refresh_token.txt — mais AUCUNE étape du workflow ne
    lit ce fichier pour mettre à jour le secret GitHub JOBBER_REFRESH_TOKEN.
    Si la rotation devient active un jour, il faudra ajouter cette étape à
    .github/workflows/rapport_hebdomadaire.yml (et rappel_matin.yml), sinon
    l'exécution suivante échouera avec un refresh_token invalide.
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

    print("Récupération des rapports de chantier (module 3)...")
    soumissions_chantier = obtenir_soumissions_avec_degradation(env, config)

    rapport = construire_rapport(jobs, jobs_fermes, entrees, config, debut, fin, soumissions_chantier)

    print(f"$/h global : {rapport.dollars_heure_global:.2f}")
    print(f"{len(rapport.alertes)} alerte(s) générée(s).")

    # Phase 2 : analyse Claude, en comparant aux ~4 semaines précédentes.
    # Dégradation gracieuse : sans clé (ou en cas d'échec), analyse=None et
    # le reste du rapport part normalement, juste sans cette section.
    payload_semaine = construire_payload(rapport)
    # Module 4 (dashboard) : lignes de la semaine pour les onglets attribution/
    # quotidien, ajoutées au même payload que l'historique Phase 2 (voir
    # dashboard_sheets.py — la sauvegarde est différée après le calcul des
    # ventes ci-dessous pour inclure aussi l'onglet ventes dans ce payload).
    dollars_heure_par_job = {l.numero: l.dollars_heure for l in rapport.jobs_fermes}
    payload_semaine["attribution"] = lignes_attribution_semaine(
        rapport.resultat, rapport.resultat_chantier, config, dollars_heure_par_job
    )
    payload_semaine["quotidien"] = lignes_quotidien_semaine(soumissions_chantier, jobs, config, debut, fin)
    semaines_precedentes = charger_semaines_precedentes(debut)
    analyse = analyser_semaine(env.get("ANTHROPIC_API_KEY"), payload_semaine, semaines_precedentes)

    message_slack = construire_message_slack(rapport, analyse)
    envoyer_slack(env["SLACK_WEBHOOK_URL"], message_slack)
    print("Message Slack (rentabilité) envoyé.")

    print("Récupération des soumissions (module Ventes)...")
    debut_extraction_ventes = fin - timedelta(days=7 * N_SEMAINES_VENTES - 1)
    soumissions = obtenir_quotes_creees(client, debut_extraction_ventes, fin)
    print(f"  {len(soumissions)} soumissions récupérées.")

    rapport_ventes = construire_rapport_ventes(soumissions, config, debut, fin)
    print(f"  {len(rapport_ventes.alertes)} alerte(s) ventes générée(s).")
    payload_semaine["ventes"] = lignes_ventes_semaine(rapport_ventes)
    # Projection du mois (pacing jours ouvrés) : stockée telle que calculée cette
    # semaine-là — Looker Studio n'a pas cette logique, voir dashboard_sheets._ligne_hebdo.
    payload_semaine["projection_ventes"] = {
        "projection_fin_mois": round(rapport_ventes.projection_fin_mois, 2),
        "mois_precedent_total": round(rapport_ventes.mois_precedent_total, 2),
    }
    sauvegarder_semaine(payload_semaine)

    message_slack_ventes = construire_message_slack_ventes(rapport_ventes)
    envoyer_slack(env["SLACK_WEBHOOK_URL"], message_slack_ventes)
    print("Message Slack (ventes) envoyé.")

    print("Publication du dashboard (module 4)...")
    historique_complet = charger_toutes_semaines()
    publier_dashboard(env, config, rapport.jobs_en_cours, jobs, historique_complet)

    dossier_sortie = RACINE / "outputs"
    dossier_sortie.mkdir(exist_ok=True)
    chemin_xlsx = dossier_sortie / f"rentabilite_{debut.isoformat()}_au_{fin.isoformat()}.xlsx"
    generer_excel(
        str(chemin_xlsx),
        jobs_fermes,
        entrees,
        rapport.resultat,
        config,
        debut,
        fin,
        rapport.alertes,
        analyse,
        rapport.resultat_chantier,
        rapport.jobs_en_cours,
        soumissions_chantier,
    )
    print(f"Excel généré : {chemin_xlsx}")


if __name__ == "__main__":
    sys.exit(main())
