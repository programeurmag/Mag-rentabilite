"""
Module Ventes — étape 1 : requête des soumissions des 8 dernières semaines et
validation des totaux par vendeur avant de poursuivre (voir SPEC_module_ventes_MAG.md,
Ordre de travail, point 1).

Compare deux façons d'identifier le "vendeur" d'une soumission :
  1. Le champ `salesperson` exposé par l'API Jobber sur le type Quote.
  2. Le créateur / expéditeur de la soumission — vérification faite par
     introspection du schéma GraphQL Jobber : il n'existe PAS de champ distinct
     pour ça sur Quote (seul `salesperson` existe). Le rapport Jobber en ligne
     (Reports -> Quotes, colonne "Sent by user") vient d'un historique
     d'activité interne, pas d'un champ GraphQL. Ce script valide donc le champ
     `salesperson` en le comparant directement à cette colonne, à partir du CSV
     exporté par Justin (Quotes_Report_1_of_1_2026-07-19.csv).

Usage : python3 src/valider_etape1_ventes.py
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber
from ventes_source import obtenir_quotes_creees

N_SEMAINES = 8
CSV_REFERENCE = Path("/Users/justin/Documents/Quotes_Report_1_of_1_2026-07-19.csv")

STATUTS_APPROUVES = {"approved", "converted"}


def fenetre_n_semaines(n: int, aujourdhui: date | None = None) -> tuple[date, date]:
    """Les n dernières semaines complètes (lundi->dimanche), se terminant la semaine
    précédant celle en cours (même logique que fenetre_semaine_precedente dans
    generer_rapport.py, étendue à n semaines)."""
    aujourdhui = aujourdhui or date.today()
    lundi_courant = aujourdhui - timedelta(days=aujourdhui.weekday())
    fin = lundi_courant - timedelta(days=1)  # dimanche de la semaine précédente
    debut = lundi_courant - timedelta(days=7 * n)
    return debut, fin


def charger_reference_csv(chemin: Path) -> list[dict]:
    if not chemin.exists():
        return []
    with chemin.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    config = dotenv_values(CHEMIN_ENV)
    client = ClientJobber(
        config["JOBBER_CLIENT_ID"],
        config["JOBBER_CLIENT_SECRET"],
        config["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    debut, fin = fenetre_n_semaines(N_SEMAINES)
    print("=" * 80)
    print(f"VALIDATION ÉTAPE 1 — VENTES — soumissions créées du {debut} au {fin} ({N_SEMAINES} semaines)")
    print("=" * 80)

    print("\nRécupération des soumissions (filtre createdAt)...")
    soumissions = obtenir_quotes_creees(client, debut, fin)
    print(f"  {len(soumissions)} soumissions trouvées.")

    # ---- Test 1 : y a-t-il un champ "créateur" distinct de salesperson ? ----
    print("\n--- Créateur vs salesperson ---")
    print(
        "L'introspection du schéma Jobber (type Quote) ne montre AUCUN champ "
        "distinct pour le créateur/expéditeur de la soumission : seul `salesperson` "
        "(type User) existe. Il n'y a donc rien à comparer côté API — la colonne "
        "\"Sent by user\" du rapport web Jobber vient d'un historique d'activité "
        "interne, pas exposé en GraphQL."
    )

    # ---- Test 2 : salesperson (API) vs "Sent by user" (export CSV Jobber) ----
    ref_rows = charger_reference_csv(CSV_REFERENCE)
    if ref_rows:
        diffs = [
            r
            for r in ref_rows
            if r["Salesperson"].strip() != r["Sent by user"].strip()
            and r["Sent by user"].strip() not in ("-", "")
        ]
        print(
            f"\nComparaison avec {CSV_REFERENCE.name} ({len(ref_rows)} lignes, export Jobber "
            f"Reports -> Quotes) : colonne Salesperson vs colonne 'Sent by user'."
        )
        print(f"  Écarts trouvés : {len(diffs)} / {len(ref_rows)}")
        for r in diffs[:10]:
            print(f"    Quote #{r['Quote #']} : salesperson={r['Salesperson']!r}  sent_by={r['Sent by user']!r}")
        if not diffs:
            print("  -> `salesperson` concorde à 100% avec 'Sent by user' sur cet échantillon.")
        print(
            "  NOTE : ce CSV ne contient que des soumissions Approved/Converted (pas les "
            "draft/awaiting_response/archived), et couvre une période plus large que 8 semaines "
            "— il sert seulement à valider le champ vendeur, pas les totaux ci-dessous."
        )
    else:
        print(f"\n(CSV de référence introuvable à {CSV_REFERENCE} — comparaison sautée.)")

    # ---- Totaux par vendeur, sur la fenêtre de 8 semaines ----
    print("\n--- Totaux par vendeur (8 dernières semaines, basé sur salesperson) ---")
    par_vendeur: dict[str, dict] = defaultdict(
        lambda: {"envoyees_n": 0, "envoyees_$": 0.0, "approuvees_n": 0, "approuvees_$": 0.0}
    )
    for s in soumissions:
        vendeur = s.vendeur or "(non assigné)"
        v = par_vendeur[vendeur]
        if s.date_envoi is not None:
            v["envoyees_n"] += 1
            v["envoyees_$"] += s.total
        if s.statut in STATUTS_APPROUVES:
            v["approuvees_n"] += 1
            v["approuvees_$"] += s.total

    largeur = max(len(v) for v in par_vendeur) if par_vendeur else 20
    print(f"  {'Vendeur':<{largeur}}  {'Envoyées':>10}  {'$ envoyées':>14}  {'Approuvées':>11}  {'$ approuvées':>14}")
    for vendeur in sorted(par_vendeur, key=lambda v: -par_vendeur[v]["envoyees_$"]):
        v = par_vendeur[vendeur]
        print(
            f"  {vendeur:<{largeur}}  {v['envoyees_n']:>10}  {v['envoyees_$']:>14,.0f}  "
            f"{v['approuvees_n']:>11}  {v['approuvees_$']:>14,.0f}"
        )

    total_envoyees_n = sum(v["envoyees_n"] for v in par_vendeur.values())
    total_envoyees_dollars = sum(v["envoyees_$"] for v in par_vendeur.values())
    total_approuvees_n = sum(v["approuvees_n"] for v in par_vendeur.values())
    total_approuvees_dollars = sum(v["approuvees_$"] for v in par_vendeur.values())
    print(
        f"  {'TOTAL':<{largeur}}  {total_envoyees_n:>10}  {total_envoyees_dollars:>14,.0f}  "
        f"{total_approuvees_n:>11}  {total_approuvees_dollars:>14,.0f}"
    )

    # ---- Répartition par statut (pour repérer d'éventuelles surprises) ----
    print("\n--- Répartition par statut (toutes soumissions créées dans la fenêtre) ---")
    par_statut = defaultdict(lambda: [0, 0.0])
    for s in soumissions:
        par_statut[s.statut][0] += 1
        par_statut[s.statut][1] += s.total
    for statut, (n, dollars) in sorted(par_statut.items(), key=lambda kv: -kv[1][1]):
        print(f"  {statut:<20} {n:>5}  {dollars:>14,.0f} $")

    print("\n" + "=" * 80)
    print(
        "À valider manuellement : ouvrir Jobber -> Reports -> Quotes, filtrer sur la même "
        f"fenêtre ({debut} au {fin}) et comparer les totaux envoyées/$ par vendeur ci-dessus."
    )


if __name__ == "__main__":
    main()
