"""
Module Ventes — étape 2 : calculs complets (par vendeur, équipe, projection,
alertes) sur les données live, à valider avant de construire le message Slack.

Usage : python3 src/valider_etape2_ventes.py
"""

from __future__ import annotations

from datetime import timedelta

import yaml
from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber
from rapport_ventes import construire_rapport_ventes
from valider_etape1_ventes import fenetre_n_semaines
from ventes_source import obtenir_quotes_creees

N_SEMAINES = 8


def main():
    racine_config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    env = dotenv_values(CHEMIN_ENV)

    client = ClientJobber(
        env["JOBBER_CLIENT_ID"],
        env["JOBBER_CLIENT_SECRET"],
        env["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    debut_extraction, fin = fenetre_n_semaines(N_SEMAINES)
    debut_rapport = fin - timedelta(days=6)

    print(f"Extraction : {debut_extraction} au {fin}")
    print(f"Semaine du rapport : {debut_rapport} au {fin}")

    soumissions = obtenir_quotes_creees(client, debut_extraction, fin)
    print(f"{len(soumissions)} soumissions récupérées.\n")

    rapport = construire_rapport_ventes(soumissions, racine_config, debut_rapport, fin)

    print("=" * 80)
    print("PAR VENDEUR")
    print("=" * 80)
    for v in rapport.vendeurs:
        var = f"{v.variation_pct:+.0%}" if v.variation_pct is not None else "s/o"
        hebdo = f"{v.taux_closing_hebdo:.0%}" if v.taux_closing_hebdo is not None else "s/o"
        cohorte = (
            f"{v.taux_closing_cohorte:.0%} (sur {v.cohorte_envoyees_n})"
            if v.taux_closing_cohorte is not None
            else "s/o"
        )
        print(f"\n{v.nom}")
        print(f"  Envoyées   : {v.envoyees_n}  ({v.envoyees_dollars:,.0f} $)".replace(",", " "))
        print(f"  Vendues    : {v.vendues_n}  ({v.vendues_dollars:,.0f} $)  vs sem. préc. {var}".replace(",", " "))
        print(f"  Closing    : cohorte {cohorte}  (hebdo {hebdo})")

    print("\n" + "=" * 80)
    print("ÉQUIPE")
    print("=" * 80)
    var_equipe = f"{rapport.equipe_variation_pct:+.0%}" if rapport.equipe_variation_pct is not None else "s/o"
    print(f"$ vendus cette semaine    : {rapport.equipe_vendues_dollars:,.0f} $ (vs préc. {var_equipe})".replace(",", " "))
    print(f"Envoyées cette semaine    : {rapport.equipe_envoyees_n} (vs préc. {rapport.equipe_envoyees_n_precedente})")
    print(
        f"Closing équipe            : cohorte "
        f"{rapport.equipe_taux_closing_cohorte:.0%} (hebdo {rapport.equipe_taux_closing_hebdo:.0%})"
        if rapport.equipe_taux_closing_cohorte is not None
        else "Closing équipe            : s/o"
    )
    print(f"Montant moyen (vendu)     : {rapport.montant_moyen_vendu:,.0f} $".replace(",", " "))

    print("\n" + "=" * 80)
    print("PROJECTION DU MOIS")
    print("=" * 80)
    print(f"Mois précédent (total)    : {rapport.mois_precedent_total:,.0f} $".replace(",", " "))
    print(f"Objectif mensuel configuré: {rapport.objectif_mensuel:,.0f} $".replace(",", " "))
    print(f"Verdict : {rapport.verdict_projection}")

    print("\n" + "=" * 80)
    print(f"ALERTES ({len(rapport.alertes)})")
    print("=" * 80)
    for a in rapport.alertes:
        print(f"  [{a.type}] {a.message}")


if __name__ == "__main__":
    main()
