"""
Script principal — Module Dashboard Ventes GHL. Récupère 180 jours
d'opportunités GHL et de soumissions Jobber (+ tout le pipeline actuellement
ouvert, peu importe son âge), calcule l'agrégat 30j/30j par défaut (pour le
résumé Slack hebdo), et écrit un JSON (docs/stats_ventes_ghl.json) contenant à
la fois cet agrégat ET les données brutes. Les pages HTML (docs/*.html) sont
des shells statiques qui chargent du JS — TOUT le calcul par plage de dates
choisie et le rendu se font côté navigateur à partir de ces données brutes.

Exécuté par le GitHub Action toutes les heures, 7h-19h heure de Montréal, du
lundi au vendredi (voir .github/workflows/dashboard_ventes_ghl.yml).

Usage : python3 src/dashboard_ventes_ghl.py
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values

from cache_ventes_ghl import charger_cache, sauvegarder_cache
from dashboard_html_ventes import generer_pages
from env_utils import CHEMIN_ENV, RACINE, maj_env
from jobber_client import ClientJobber
from metriques_ventes_ghl import calculer_metriques_equipe_et_vendeurs
from ventes_ghl_source import construire_contexte, enrichir_fenetre, enrichir_pipeline_ouvert, recuperer_opportunites_brutes
from ventes_jobber_source import obtenir_soumissions_jobber

FUSEAU_MAG = ZoneInfo("America/Montreal")
FENETRE_JOURS = 30
# Fenêtre des données brutes envoyées au navigateur (page interactive, 2026-08-01) :
# doit couvrir au moins 2x le plus grand preset (90j) pour permettre le calcul de
# tendance côté JS (période choisie vs période équivalente précédente), plus une
# marge pour les graphiques historiques par semaine.
FENETRE_DONNEES_BRUTES_JOURS = 180
DOSSIER_PAGES = RACINE / "docs"
CHEMIN_JSON = DOSSIER_PAGES / "stats_ventes_ghl.json"


def _sauvegarder_refresh_token_partout(nouveau_token: str):
    """Même limitation documentée dans generer_rapport.py / sync_soumissions_ghl.py :
    rotation désactivée en pratique sur l'app MAG, ce chemin n'est aujourd'hui jamais emprunté."""
    if CHEMIN_ENV.exists():
        maj_env("JOBBER_REFRESH_TOKEN", nouveau_token)
    (RACINE / "nouveau_refresh_token.txt").write_text(nouveau_token, encoding="utf-8")


def _opp_vers_dict(o) -> dict:
    return {
        "id": o.id,
        "nom": o.nom,
        "vendeur": o.vendeur,
        "source": o.source,
        "stage_nom": o.stage_nom,
        "statut": o.statut,
        "valeur": o.valeur,
        "date_creation": o.date_creation.isoformat(),
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
        "visite_bookee": o.visite_bookee,
        "soumission_envoyee": o.soumission_envoyee,
        "contacte_meme_jour": o.contacte_meme_jour,
        "rappel_48h": o.rappel_48h,
        "n_appels_sortants": o.n_appels_sortants,
        "n_appels_repondus": o.n_appels_repondus,
        "n_sms_sortants": o.n_sms_sortants,
        "n_emails_sortants": o.n_emails_sortants,
        "derniere_activite": o.derniere_activite.isoformat() if o.derniere_activite else None,
    }


def _soumission_vers_dict(s) -> dict:
    return {
        "numero": s.numero,
        "client": s.client,
        "vendeur": s.vendeur,
        "montant": s.montant,
        "statut": s.statut,
        "won": s.won,
        "date_envoi": s.date_envoi.isoformat(),
        "date_gagnee": s.date_gagnee.isoformat() if s.date_gagnee else None,
        "delai_lead_soumission_heures": s.delai_lead_soumission_heures,
        "delai_soumission_won_heures": s.delai_soumission_won_heures,
        "delai_lead_won_heures": s.delai_lead_won_heures,
        "source_lead": s.source_lead,
    }


def main():
    config_yaml = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))
    env = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else dict(os.environ)
    config = {**env, **config_yaml}
    seuils = config_yaml["seuils_dashboard_ventes"]
    noms_vendeurs = list(config_yaml["ghl_vendeurs"].values())
    alias_jobber = config_yaml.get("ghl_vendeurs_alias_jobber", {})
    location_id = config["GHL_LOCATION_ID"]

    fin = datetime.now(FUSEAU_MAG)
    debut_donnees = fin - timedelta(days=FENETRE_DONNEES_BRUTES_JOURS)
    debut_60j = fin - timedelta(days=2 * FENETRE_JOURS)
    milieu = fin - timedelta(days=FENETRE_JOURS)

    cache = charger_cache()

    print("Récupération du contexte GHL (pipelines/stages)...")
    ctx = construire_contexte(config)
    print(f"Récupération des opportunités GHL brutes ({', '.join(p['name'] for p in ctx['pipelines'])})...")
    brutes = recuperer_opportunites_brutes(config, ctx)
    print(f"  {len(brutes)} opportunité(s) au total (toutes dates, tout statut).")

    print(f"Enrichissement fenêtre {debut_donnees.date()} -> {fin.date()}...")
    toutes = enrichir_fenetre(config, ctx, brutes, cache, debut_donnees, fin)
    toutes = [o for o in toutes if o.vendeur in set(noms_vendeurs)]
    print(f"  {len(toutes)} opportunité(s) retenues (vendeurs configurés seulement).")

    print("Enrichissement du pipeline ouvert complet (page Pipeline)...")
    pipeline_ouvert = enrichir_pipeline_ouvert(config, ctx, brutes, cache)
    pipeline_ouvert = [o for o in pipeline_ouvert if o.vendeur in set(noms_vendeurs)]
    print(f"  {len(pipeline_ouvert)} opportunité(s) actuellement ouvertes (tout historique).")

    actuelle_opps = [o for o in toutes if o.date_creation > milieu]
    precedente_opps = [o for o in toutes if debut_60j < o.date_creation <= milieu]

    print("Récupération des soumissions Jobber (closing, valeur moyenne, délais)...")
    client_jobber = ClientJobber(
        env["JOBBER_CLIENT_ID"],
        env["JOBBER_CLIENT_SECRET"],
        env["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=_sauvegarder_refresh_token_partout,
    )
    toutes_soumissions = obtenir_soumissions_jobber(
        client_jobber, alias_jobber, debut_donnees.date(), fin.date(), opportunites_brutes_ghl=brutes
    )
    toutes_soumissions = [s for s in toutes_soumissions if s.vendeur in set(noms_vendeurs)]
    soumissions_actuelle = [s for s in toutes_soumissions if s.date_envoi > milieu]
    soumissions_precedente = [s for s in toutes_soumissions if debut_60j < s.date_envoi <= milieu]
    n_matchees = sum(1 for s in toutes_soumissions if s.delai_lead_soumission_heures is not None)
    print(
        f"  {len(toutes_soumissions)} soumission(s) au total sur {FENETRE_DONNEES_BRUTES_JOURS}j "
        f"({n_matchees} matchée(s) à un lead GHL pour les délais)."
    )

    equipe_actuelle, vendeurs_actuelle = calculer_metriques_equipe_et_vendeurs(actuelle_opps, soumissions_actuelle, noms_vendeurs)
    equipe_precedente, vendeurs_precedente = calculer_metriques_equipe_et_vendeurs(precedente_opps, soumissions_precedente, noms_vendeurs)

    sauvegarder_cache(cache)
    print("Cache sauvegardé.")

    DOSSIER_PAGES.mkdir(parents=True, exist_ok=True)
    (DOSSIER_PAGES / ".nojekyll").touch()

    genere_le = datetime.now(FUSEAU_MAG)

    stats_json = {
        "genere_le": genere_le.isoformat(),
        "periode": {"debut": milieu.isoformat(), "fin": fin.isoformat()},
        "periode_precedente": {"debut": debut_60j.isoformat(), "fin": milieu.isoformat()},
        "vendeurs_config": noms_vendeurs,
        "seuils": seuils,
        "ghl_location_id": location_id,
        # Agrégat 30j/30j par défaut — utilisé par le résumé Slack hebdo (voir
        # slack_hebdo_ventes_ghl.py) et comme vue initiale de la page avant que
        # l'utilisateur choisisse une autre plage.
        "equipe": {
            "actuelle": dataclasses.asdict(equipe_actuelle),
            "precedente": dataclasses.asdict(equipe_precedente),
        },
        "vendeurs": [
            {
                "nom": nom,
                "actuelle": dataclasses.asdict(next(v for v in vendeurs_actuelle if v.nom == nom)),
                "precedente": dataclasses.asdict(next(v for v in vendeurs_precedente if v.nom == nom)),
            }
            for nom in noms_vendeurs
        ],
        # Données brutes (180j) — les pages interactives recalculent tout côté
        # navigateur selon la plage choisie. Les indicateurs qui dépendent de
        # l'historique GHL (appels/SMS) sont déjà calculés côté serveur.
        "opportunites_brutes": [_opp_vers_dict(o) for o in toutes],
        "soumissions_brutes": [_soumission_vers_dict(s) for s in toutes_soumissions],
        # Tout le pipeline actuellement ouvert (peu importe la date de création) —
        # page Pipeline uniquement, "l'argent qui dort".
        "pipeline_ouvert_brutes": [_opp_vers_dict(o) for o in pipeline_ouvert],
    }
    CHEMIN_JSON.write_text(json.dumps(stats_json, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON écrit : {CHEMIN_JSON.relative_to(RACINE)}")

    for nom_fichier, html in generer_pages().items():
        chemin = DOSSIER_PAGES / nom_fichier
        chemin.write_text(html, encoding="utf-8")
    print(f"{len(generer_pages())} page(s) HTML écrite(s) dans {DOSSIER_PAGES.relative_to(RACINE)}/")


if __name__ == "__main__":
    main()
