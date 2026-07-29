"""
Sync soumissions Jobber -> GHL — étape 5 : écritures réelles + garde-fous (voir
spec-sync-soumissions-jobber-ghl.md, Ordre de travail, point 5).

Réutilise intégralement la cascade de matching et la logique de garde-fous
déjà validées et testées sur données réelles aux étapes 2/3
(valider_etape2_sync_soumissions.py, valider_etape3_sync_soumissions.py) —
`simuler_ecriture()` calcule le corps PUT machine-readable ET le résumé
lisible pour le rapport, donc aucune duplication de la logique d'idempotence
/ Won-Lost / anti-recul de stage entre la simulation et l'écriture réelle.

Sécurité : DRY-RUN PAR DÉFAUT (voir spec, section "Modes d'exécution" —
"Mode par défaut au premier lancement"). Rien n'est écrit dans GHL sauf si
--live est passé explicitement.

Endpoint d'écriture vérifié contre la doc officielle HighLevel :
  PUT https://services.leadconnectorhq.com/opportunities/:id
  Header requis : Version: v3. Corps JSON PARTIEL (seuls les champs présents
  sont modifiés) — on n'envoie donc jamais `name`, `status`, `assignedTo` ni
  `tags`, conformément à la règle du spec ("Ne jamais modifier : le nom de
  l'opportunité, l'owner assigné, le statut, les tags").

Mode incrémental (sans --since, pour le cron) : lit le timestamp du dernier
run dans etat_sync_soumissions.json (voir spec, Flow point 1) et récupère les
soumissions dont `updatedAt` est postérieur, via obtenir_quotes_maj_depuis
(capte aussi une soumission envoyée il y a longtemps mais approuvée depuis le
dernier run). Au tout premier lancement (fichier d'état absent), retombe sur
la fenêtre fixe des 30 derniers jours déjà testée aux étapes 1-3. Le nouvel
état n'est sauvegardé qu'en mode --live (un dry-run ne doit rien faire
avancer) et seulement si le run s'est terminé sans exception.

Usage :
  python3 src/sync_soumissions_ghl.py                    # dry-run, incrémental (ou 30j au 1er lancement)
  python3 src/sync_soumissions_ghl.py --live              # écritures réelles, incrémental
  python3 src/sync_soumissions_ghl.py --since 2026-04-01  # override : fenêtre fixe jusqu'à aujourd'hui
  python3 src/sync_soumissions_ghl.py --limit 5 --live    # test sur 5 soumissions
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date, datetime, timedelta, timezone

import requests
from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, RACINE, maj_env
from etat_sync_soumissions import lire_dernier_run, sauvegarder_dernier_run
from jobber_client import ClientJobber
from valider_etape1_sync_soumissions import (
    N_JOURS,
    _valeur_primaire,
    obtenir_quotes_envoyees,
    obtenir_quotes_maj_depuis,
)
from valider_etape2_sync_soumissions import URL_GHL, VERSION_GHL, _en_tete_ghl, obtenir_toutes_opportunites
from valider_etape3_sync_soumissions import (
    choisir_soumission_canonique,
    construire_index,
    matcher,
    simuler_ecriture,
)

DELAI_ENTRE_ECRITURES = 0.15  # ~6-7 req/s, largement sous la limite GHL (100 / 10s)


def ecrire_opportunite(config: dict, opp_id: str, corps: dict, tentative: int = 0) -> None:
    headers = {**_en_tete_ghl(config), "Content-Type": "application/json"}
    r = requests.put(f"{URL_GHL}/opportunities/{opp_id}", headers=headers, json=corps, timeout=30)
    if r.status_code == 429 and tentative < 3:
        time.sleep(2 * (tentative + 1))
        return ecrire_opportunite(config, opp_id, corps, tentative + 1)
    r.raise_for_status()


def parse_args():
    p = argparse.ArgumentParser(description="Sync soumissions Jobber -> GHL")
    p.add_argument("--live", action="store_true", help="Effectue les écritures réelles dans GHL (défaut : dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Explicite, sans effet (comportement par défaut)")
    p.add_argument("--since", type=str, default=None, help="Date de début (YYYY-MM-DD), remplace les 30 derniers jours")
    p.add_argument("--limit", type=int, default=None, help="Limite le nombre de soumissions traitées (test)")
    return p.parse_args()


def _sur_nouveau_refresh_token(nouveau_token: str):
    """En local : écrit dans .env. Sur GitHub Actions (.env absent, secrets via
    l'environnement) : écrit dans nouveau_refresh_token.txt — même limitation
    documentée dans generer_rapport.py (rotation désactivée sur l'app MAG en
    pratique, donc ce chemin n'est jamais emprunté aujourd'hui)."""
    if CHEMIN_ENV.exists():
        maj_env("JOBBER_REFRESH_TOKEN", nouveau_token)
    (RACINE / "nouveau_refresh_token.txt").write_text(nouveau_token, encoding="utf-8")


def main():
    args = parse_args()
    mode_live = args.live

    # En local : .env. Sur GitHub Actions : variables d'environnement (secrets) —
    # même pattern que generer_rapport.py, dotenv_values() seul retourne {} en CI.
    config = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else os.environ
    client_jobber = ClientJobber(
        config["JOBBER_CLIENT_ID"],
        config["JOBBER_CLIENT_SECRET"],
        config["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=_sur_nouveau_refresh_token,
    )

    debut_run = datetime.now(timezone.utc)  # capturé avant la lecture, pour ne rien manquer pendant le run

    if args.since:
        debut, fin = datetime.strptime(args.since, "%Y-%m-%d").date(), date.today()
        quotes = obtenir_quotes_envoyees(client_jobber, debut, fin)
        fenetre_affichee = f"{debut} au {fin} (--since)"
        mode_incremental = False
    else:
        dernier_run = lire_dernier_run()
        if dernier_run is None:
            debut, fin = date.today() - timedelta(days=N_JOURS), date.today()
            quotes = obtenir_quotes_envoyees(client_jobber, debut, fin)
            fenetre_affichee = f"{debut} au {fin} (1er lancement, pas d'état sauvegardé)"
        else:
            quotes = obtenir_quotes_maj_depuis(client_jobber, dernier_run)
            fenetre_affichee = f"depuis {dernier_run.isoformat()} (incrémental)"
        mode_incremental = True

    if args.limit:
        quotes = quotes[: args.limit]

    opportunites = obtenir_toutes_opportunites(config)
    par_email, par_telephone = construire_index(opportunites)
    opportunites_par_id = {o["id"]: o for o in opportunites}

    non_matchees = []
    ambigues = []
    groupes: dict[str, list[dict]] = {}

    for q in quotes:
        opp, niveau, n_candidats = matcher(q, par_email, par_telephone)
        if opp is None:
            non_matchees.append(q)
            continue
        if n_candidats > 1:
            c = q.get("client") or {}
            nom = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            ambigues.append((q["quoteNumber"], nom, niveau, n_candidats))
        groupes.setdefault(opp["id"], []).append(q)

    multi_soumissions = []
    ecrites = []
    ignorees_deja_a_jour = []
    diminutions = []
    erreurs = []

    for opp_id, quotes_groupe in groupes.items():
        opp = opportunites_par_id[opp_id]
        if len(quotes_groupe) > 1:
            c0 = quotes_groupe[0].get("client") or {}
            nom = f"{c0.get('firstName', '')} {c0.get('lastName', '')}".strip()
            multi_soumissions.append(
                (nom, [q["quoteNumber"] for q in quotes_groupe], [q["quoteStatus"] for q in quotes_groupe])
            )

        q_canonique = choisir_soumission_canonique(quotes_groupe)
        sous_total = q_canonique["amounts"]["subtotal"] or 0.0
        simulation = simuler_ecriture(opp, sous_total)
        numero = q_canonique["quoteNumber"]

        if simulation["valeur_diminuee"]:
            diminutions.append((numero, opp["name"], simulation["action_valeur"]))

        if not simulation["corps_put"]:
            ignorees_deja_a_jour.append(numero)
            continue

        if mode_live:
            try:
                ecrire_opportunite(config, opp_id, simulation["corps_put"])
                time.sleep(DELAI_ENTRE_ECRITURES)
            except requests.HTTPError as e:
                erreurs.append((numero, opp["name"], str(e)))
                continue

        ecrites.append((numero, opp["name"], simulation["action_valeur"], simulation["action_stage"]))

    print("=" * 100)
    print(f"SYNC SOUMISSIONS {'(LIVE)' if mode_live else '(DRY-RUN)'} — {fenetre_affichee}")
    print("=" * 100)

    for numero, nom, action_valeur, action_stage in ecrites:
        print(f"  #{numero:<6} {nom:<25} valeur: {action_valeur:<26} stage: {action_stage}")

    n_traitees = len(quotes)
    n_matchees = len(ecrites) + len(ignorees_deja_a_jour) + len(erreurs)
    n_echecs = len(non_matchees) + len(erreurs)

    print("\n" + "-" * 100)
    print(f"Sync soumissions : {n_traitees} traitées, {n_matchees} matchées, {n_echecs} échec(s)")
    print(
        f"  {'Écrites' if mode_live else 'À écrire'} : {len(ecrites)}  |  "
        f"Déjà à jour (ignorées) : {len(ignorees_deja_a_jour)}  |  Erreurs GHL : {len(erreurs)}"
    )

    if non_matchees:
        print("\nNon matchées :")
        for q in non_matchees:
            c = q.get("client") or {}
            nom = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            email = _valeur_primaire(c.get("emails") or [], "address")
            print(f"  - Soumission #{q['quoteNumber']} — {nom} ({email}) — {q['amounts']['subtotal']:,.2f} $")

    if ambigues:
        print("\nAmbiguës (plusieurs opportunités trouvées, la plus récente utilisée) :")
        for numero, nom, niveau, n in ambigues:
            print(f"  - Soumission #{numero} — {nom} — match par {niveau} — {n} opportunités trouvées")

    if multi_soumissions:
        print("\nMulti-soumissions (même opportunité, plusieurs soumissions — jamais additionnées) :")
        for nom, numeros, statuts in multi_soumissions:
            details = ", ".join(f"#{n} ({s})" for n, s in zip(numeros, statuts))
            print(f"  - {nom} — {details}")

    if diminutions:
        print("\n⚠ Valeurs diminuées (soumission révisée à la baisse, ou mauvais match à vérifier) :")
        for numero, nom, action in diminutions:
            print(f"  - Soumission #{numero} — {nom} — {action}")

    if erreurs:
        print("\nErreurs d'écriture GHL :")
        for numero, nom, erreur in erreurs:
            print(f"  - Soumission #{numero} — {nom} — {erreur}")

    if not mode_live:
        print("\n(dry-run — aucune écriture réelle. Relancer avec --live pour écrire dans GHL.)")
    elif mode_incremental:
        sauvegarder_dernier_run(debut_run)
        print(f"\nÉtat sauvegardé : prochain run partira de {debut_run.isoformat()}.")


if __name__ == "__main__":
    main()
