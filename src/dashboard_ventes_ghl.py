"""
Script principal — Module Dashboard Ventes GHL. Calcule les 5 métriques par
vendeur et pour l'équipe (fenêtre roulante de 30 jours vs les 30 jours
précédents), écrit un JSON de stats (docs/stats_ventes_ghl.json) et une page
HTML statique (docs/index.html) publiée via GitHub Pages.

Exécuté par le GitHub Action toutes les heures, 7h-19h heure de Montréal, du
lundi au vendredi (voir .github/workflows/dashboard_ventes_ghl.yml).

Usage : python3 src/dashboard_ventes_ghl.py
"""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values

from cache_ventes_ghl import charger_cache, sauvegarder_cache
from dashboard_html_ventes import CarteVendeur, MetriqueAffichee, generer_page
from env_utils import CHEMIN_ENV, RACINE, maj_env
from jobber_client import ClientJobber
from metriques_ventes_ghl import MetriquesVendeur, calculer_metriques_equipe_et_vendeurs
from ventes_ghl_source import obtenir_opportunites_ventes
from ventes_jobber_source import obtenir_soumissions_jobber

FUSEAU_MAG = ZoneInfo("America/Montreal")
FENETRE_JOURS = 30
DOSSIER_PAGES = RACINE / "docs"
CHEMIN_JSON = DOSSIER_PAGES / "stats_ventes_ghl.json"
CHEMIN_HTML = DOSSIER_PAGES / "index.html"


def _couleur(valeur: float | None, seuils: dict) -> str | None:
    if valeur is None:
        return None
    if valeur >= seuils["vert"]:
        return "vert"
    if valeur >= seuils["rouge"]:
        return "jaune"
    return "rouge"


def _fmt_pct(v: float | None) -> str:
    return f"{v:.0f} %" if v is not None else "s/o"


def _fmt_dollars(v: float | None) -> str:
    return f"{v:,.0f} $".replace(",", " ") if v else "s/o"


def _liste_avec_reste(noms: list[str], max_affiches: int = 5) -> str:
    affiches = ", ".join(noms[:max_affiches])
    reste = len(noms) - max_affiches
    return f"{affiches}, +{reste} autre(s)" if reste > 0 else affiches


def _conseil_contact(m: MetriquesVendeur, couleur: str | None) -> str | None:
    if couleur not in ("rouge", "jaune") or not m.leads_non_contactes:
        return None
    return f"{len(m.leads_non_contactes)} lead(s) non contacté(s) le jour même : {_liste_avec_reste(m.leads_non_contactes)}"


def _conseil_visite(m: MetriquesVendeur, couleur: str | None) -> str | None:
    if couleur not in ("rouge", "jaune"):
        return None
    n = m.n_leads - m.n_visite_bookee
    if n <= 0:
        return None
    return f"{n} lead(s) n'ont pas encore dépassé les tentatives d'appel — relancer en priorité."


def _conseil_rappel(m: MetriquesVendeur, couleur: str | None) -> str | None:
    if couleur not in ("rouge", "jaune") or not m.soumissions_sans_rappel:
        return None
    return f"{len(m.soumissions_sans_rappel)} soumission(s) sans rappel depuis 48h : {_liste_avec_reste(m.soumissions_sans_rappel)}"


def _conseil_closing(m: MetriquesVendeur, couleur: str | None) -> str | None:
    if couleur not in ("rouge", "jaune") or not m.n_soumissions_envoyees:
        return None
    return f"{m.n_won}/{m.n_soumissions_envoyees} soumissions gagnées — creuser les motifs de perte des autres."


def _metriques_affichees(m: MetriquesVendeur, seuils: dict) -> list[MetriqueAffichee]:
    c1 = _couleur(m.pct_contact_meme_jour, seuils["contact_meme_jour"])
    c2 = _couleur(m.pct_visite_bookee, seuils["visite_bookee"])
    c3 = _couleur(m.pct_rappel_48h, seuils["rappel_48h"])
    c4 = _couleur(m.pct_closing, seuils["closing"])
    return [
        MetriqueAffichee(
            "Contact même jour",
            f"{_fmt_pct(m.pct_contact_meme_jour)} ({m.n_contactes_meme_jour}/{m.n_leads})",
            c1,
            _conseil_contact(m, c1),
        ),
        MetriqueAffichee(
            "Visite bookée",
            f"{_fmt_pct(m.pct_visite_bookee)} ({m.n_visite_bookee}/{m.n_leads})",
            c2,
            _conseil_visite(m, c2),
        ),
        MetriqueAffichee(
            "Rappel 48h après soumission",
            f"{_fmt_pct(m.pct_rappel_48h)} ({m.n_soumissions_avec_rappel}/{m.n_soumissions_ghl})",
            c3,
            _conseil_rappel(m, c3),
        ),
        MetriqueAffichee(
            "Taux de closing",
            f"{_fmt_pct(m.pct_closing)} ({m.n_won}/{m.n_soumissions_envoyees})",
            c4,
            _conseil_closing(m, c4),
        ),
        MetriqueAffichee("Valeur moyenne (won)", _fmt_dollars(m.valeur_moyenne_won), None, None),
    ]


def _delta_points(actuel: float | None, precedent: float | None) -> tuple[str | None, bool | None]:
    if actuel is None or precedent is None:
        return None, None
    d = actuel - precedent
    return f"{abs(d):.0f} pts", d >= 0


def _delta_relatif(actuel: float, precedent: float) -> tuple[str | None, bool | None]:
    if not precedent:
        return None, None
    d = (actuel - precedent) / precedent
    return f"{abs(d):.0%}", d >= 0


def _stats_equipe(actuelle: MetriquesVendeur, precedente: MetriquesVendeur) -> list[tuple]:
    return [
        ("Leads (30j)", str(actuelle.n_leads), *_delta_relatif(actuelle.n_leads, precedente.n_leads)),
        ("Contact même jour", _fmt_pct(actuelle.pct_contact_meme_jour), *_delta_points(actuelle.pct_contact_meme_jour, precedente.pct_contact_meme_jour)),
        ("Visite bookée", _fmt_pct(actuelle.pct_visite_bookee), *_delta_points(actuelle.pct_visite_bookee, precedente.pct_visite_bookee)),
        ("Rappel 48h", _fmt_pct(actuelle.pct_rappel_48h), *_delta_points(actuelle.pct_rappel_48h, precedente.pct_rappel_48h)),
        ("Taux de closing", _fmt_pct(actuelle.pct_closing), *_delta_points(actuelle.pct_closing, precedente.pct_closing)),
        ("Valeur moyenne (won)", _fmt_dollars(actuelle.valeur_moyenne_won), *_delta_relatif(actuelle.valeur_moyenne_won or 0, precedente.valeur_moyenne_won or 0)),
    ]


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
    debut_60j = fin - timedelta(days=2 * FENETRE_JOURS)
    milieu = fin - timedelta(days=FENETRE_JOURS)

    cache = charger_cache()

    print(f"Récupération des opportunités GHL ({debut_60j.date()} -> {fin.date()})...")
    toutes = obtenir_opportunites_ventes(config, debut_60j, fin, cache)
    toutes = [o for o in toutes if o.vendeur in set(noms_vendeurs)]
    print(f"  {len(toutes)} opportunité(s) retenues (vendeurs configurés seulement).")

    actuelle_opps = [o for o in toutes if o.date_creation > milieu]
    precedente_opps = [o for o in toutes if o.date_creation <= milieu]

    print("Récupération des soumissions Jobber (métriques closing/valeur moyenne)...")
    client_jobber = ClientJobber(
        env["JOBBER_CLIENT_ID"],
        env["JOBBER_CLIENT_SECRET"],
        env["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=_sauvegarder_refresh_token_partout,
    )
    soumissions_actuelle = obtenir_soumissions_jobber(client_jobber, alias_jobber, milieu.date(), fin.date())
    soumissions_precedente = obtenir_soumissions_jobber(client_jobber, alias_jobber, debut_60j.date(), milieu.date())
    soumissions_actuelle = [s for s in soumissions_actuelle if s.vendeur in set(noms_vendeurs)]
    soumissions_precedente = [s for s in soumissions_precedente if s.vendeur in set(noms_vendeurs)]
    print(f"  {len(soumissions_actuelle)} soumission(s) envoyée(s) (période actuelle, vendeurs configurés).")

    equipe_actuelle, vendeurs_actuelle = calculer_metriques_equipe_et_vendeurs(actuelle_opps, soumissions_actuelle, noms_vendeurs)
    equipe_precedente, vendeurs_precedente = calculer_metriques_equipe_et_vendeurs(precedente_opps, soumissions_precedente, noms_vendeurs)

    sauvegarder_cache(cache)
    print("Cache sauvegardé.")

    DOSSIER_PAGES.mkdir(parents=True, exist_ok=True)
    (DOSSIER_PAGES / ".nojekyll").touch()

    genere_le = datetime.now(FUSEAU_MAG)
    genere_le_texte = genere_le.strftime("%A %d %B %Y, %H:%M")
    periode_texte = f"Fenêtre roulante : {milieu.date()} au {fin.date()} (vs {debut_60j.date()} au {milieu.date()})"

    stats_json = {
        "genere_le": genere_le.isoformat(),
        "periode": {"debut": milieu.isoformat(), "fin": fin.isoformat()},
        "periode_precedente": {"debut": debut_60j.isoformat(), "fin": milieu.isoformat()},
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
    }
    CHEMIN_JSON.write_text(json.dumps(stats_json, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON écrit : {CHEMIN_JSON.relative_to(RACINE)}")

    cartes = [
        CarteVendeur(nom, _metriques_affichees(next(v for v in vendeurs_actuelle if v.nom == nom), seuils))
        for nom in noms_vendeurs
    ]
    html = generer_page(
        genere_le_texte,
        periode_texte,
        _stats_equipe(equipe_actuelle, equipe_precedente),
        cartes,
    )
    CHEMIN_HTML.write_text(html, encoding="utf-8")
    print(f"HTML écrit : {CHEMIN_HTML.relative_to(RACINE)}")


if __name__ == "__main__":
    main()
