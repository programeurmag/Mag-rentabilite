"""
Script principal — Module Dashboard Ventes GHL. Récupère 180 jours
d'opportunités GHL et de soumissions Jobber, calcule l'agrégat 30j/30j par
défaut (pour le résumé Slack hebdo), et écrit un JSON (docs/stats_ventes_ghl.json)
contenant à la fois cet agrégat ET les données brutes. La page HTML
(docs/index.html) est un shell statique qui charge dashboard.js — TOUT le
calcul par plage de dates choisie et le rendu se font côté navigateur à partir
de ces données brutes (page interactive, voir dashboard.js).

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
from dashboard_html_ventes import generer_page
from env_utils import CHEMIN_ENV, RACINE, maj_env
from jobber_client import ClientJobber
from metriques_ventes_ghl import calculer_metriques_equipe_et_vendeurs
from ventes_ghl_source import obtenir_opportunites_ventes
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
CHEMIN_HTML = DOSSIER_PAGES / "index.html"


def _sauvegarder_refresh_token_partout(nouveau_token: str):
    """Même limitation documentée dans generer_rapport.py / sync_soumissions_ghl.py :
    rotation désactivée en pratique sur l'app MAG, ce chemin n'est aujourd'hui jamais emprunté."""
    if CHEMIN_ENV.exists():
        maj_env("JOBBER_REFRESH_TOKEN", nouveau_token)
    (RACINE / "nouveau_refresh_token.txt").write_text(nouveau_token, encoding="utf-8")


def main():
    config_yaml = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))
    env = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else dict(os.environ)
    config = {**env, **config_yaml}
    seuils = config_yaml["seuils_dashboard_ventes"]
    noms_vendeurs = list(config_yaml["ghl_vendeurs"].values())
    alias_jobber = config_yaml.get("ghl_vendeurs_alias_jobber", {})

    fin = datetime.now(FUSEAU_MAG)
    debut_donnees = fin - timedelta(days=FENETRE_DONNEES_BRUTES_JOURS)
    debut_60j = fin - timedelta(days=2 * FENETRE_JOURS)
    milieu = fin - timedelta(days=FENETRE_JOURS)

    cache = charger_cache()

    print(f"Récupération des opportunités GHL ({debut_donnees.date()} -> {fin.date()})...")
    toutes = obtenir_opportunites_ventes(config, debut_donnees, fin, cache)
    toutes = [o for o in toutes if o.vendeur in set(noms_vendeurs)]
    print(f"  {len(toutes)} opportunité(s) retenues (vendeurs configurés seulement).")

    actuelle_opps = [o for o in toutes if o.date_creation > milieu]
    precedente_opps = [o for o in toutes if debut_60j < o.date_creation <= milieu]

    print("Récupération des soumissions Jobber (métriques closing/valeur moyenne)...")
    client_jobber = ClientJobber(
        env["JOBBER_CLIENT_ID"],
        env["JOBBER_CLIENT_SECRET"],
        env["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=_sauvegarder_refresh_token_partout,
    )
    toutes_soumissions = obtenir_soumissions_jobber(client_jobber, alias_jobber, debut_donnees.date(), fin.date())
    toutes_soumissions = [s for s in toutes_soumissions if s.vendeur in set(noms_vendeurs)]
    soumissions_actuelle = [s for s in toutes_soumissions if s.date_envoi > milieu]
    soumissions_precedente = [s for s in toutes_soumissions if debut_60j < s.date_envoi <= milieu]
    print(f"  {len(toutes_soumissions)} soumission(s) au total sur {FENETRE_DONNEES_BRUTES_JOURS}j (vendeurs configurés).")

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
        # Données brutes (180j) — la page interactive recalcule tout côté navigateur
        # à partir d'ici selon la plage choisie (7j/30j/90j/mois). Les booléens
        # (contacte_meme_jour, visite_bookee, rappel_48h) sont déjà calculés côté
        # serveur (dépendent de l'historique GHL des appels/SMS) ; seul le filtrage
        # par date se fait côté client.
        "opportunites_brutes": [
            {
                "id": o.id,
                "nom": o.nom,
                "vendeur": o.vendeur,
                "source": o.source,
                "statut": o.statut,
                "valeur": o.valeur,
                "date_creation": o.date_creation.isoformat(),
                "visite_bookee": o.visite_bookee,
                "soumission_envoyee": o.soumission_envoyee,
                "contacte_meme_jour": o.contacte_meme_jour,
                "rappel_48h": o.rappel_48h,
            }
            for o in toutes
        ],
        "soumissions_brutes": [
            {
                "numero": s.numero,
                "client": s.client,
                "vendeur": s.vendeur,
                "montant": s.montant,
                "won": s.won,
                "date_envoi": s.date_envoi.isoformat(),
            }
            for s in toutes_soumissions
        ],
    }
    CHEMIN_JSON.write_text(json.dumps(stats_json, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON écrit : {CHEMIN_JSON.relative_to(RACINE)}")

    if not CHEMIN_HTML.exists():
        CHEMIN_HTML.write_text(generer_page(), encoding="utf-8")
        print(f"HTML écrit : {CHEMIN_HTML.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
