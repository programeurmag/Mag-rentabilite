"""
Sync soumissions Jobber -> GHL — étape 2 : auth GHL + recherche d'opportunité,
test de la cascade de matching sur les données réelles (voir
spec-sync-soumissions-jobber-ghl.md, Ordre de travail, point 2, et section
"Cascade de matching").

Aucune écriture nulle part (ni GHL, ni fichier) : lecture seule.

Endpoints vérifiés contre la doc officielle HighLevel (marketplace.gohighlevel.com/docs) :
  - GET https://services.leadconnectorhq.com/opportunities/search
    Header requis : Version: v3 (variante actuelle, remplace 2021-07-28 etc.)
    Query requis : locationId. Utilisés aussi : pipelineId, status=all, limit=100.
    Pagination : meta.nextPage / startAfter / startAfterId (pas de "page" fiable
    au-delà de la première page — on suit startAfter/startAfterId comme la doc
    l'indique).
    La réponse embarque bien `contact { id name companyName email phone tags }`
    par opportunité (vérifié sur des vraies données, pas juste la doc) — donc
    PAS besoin d'un GET /contacts/:id séparé pour email/phone. Un GET séparé
    reste nécessaire pour `postalCode`, qui n'est PAS dans l'objet contact
    imbriqué de /opportunities/search (confirmé par lecture directe de la
    réponse), seulement sur GET /contacts/:contactId.
  - GET https://services.leadconnectorhq.com/contacts/:contactId
    Confirme le champ `postalCode` (String, plat, pas dans un sous-objet
    address).

Pipelines trouvées sur le sub-account MAG : SETTING (id Q8EYGzimcGKdgZeTbu7D) et
CLOSING (id DRsf93wLTlsI55zjmywa). Confirmé avec Justin : le stage "Soumission
envoyée" du spec correspond au stage EXISTANT "SUIVI À FAIRE" de la pipeline
CLOSING (id c6fde8cf-ecaa-4277-a1b0-fbe3cfffbb6c) — pas besoin d'en créer un.

Décision de Justin (2026-07-29) : la recherche de correspondance (cascade de
matching) doit couvrir TOUTES les pipelines/stages, pas seulement CLOSING —
un premier test limité à CLOSING seul ne trouvait que 64% des soumissions,
alors que 17 des 28 manquantes avaient déjà une opportunité GHL, juste encore
dans SETTING (pas déplacée manuellement vers CLOSING). Implication pour
l'étape 5 (écritures, hors scope ici) : matcher une opportunité encore dans
SETTING veut dire la déplacer aussi vers la pipeline CLOSING, pas seulement
changer son stage.

Usage : python3 src/valider_etape2_sync_soumissions.py
"""

from __future__ import annotations

import unicodedata
from datetime import date, timedelta

import requests
from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber
from valider_etape1_sync_soumissions import N_JOURS, obtenir_quotes_envoyees, _valeur_primaire

URL_GHL = "https://services.leadconnectorhq.com"
VERSION_GHL = "v3"
PIPELINE_SETTING = "Q8EYGzimcGKdgZeTbu7D"
PIPELINE_CLOSING = "DRsf93wLTlsI55zjmywa"
STAGE_SOUMISSION_ENVOYEE = "c6fde8cf-ecaa-4277-a1b0-fbe3cfffbb6c"  # CLOSING / SUIVI À FAIRE
NOMS_PIPELINES = {PIPELINE_SETTING: "SETTING", PIPELINE_CLOSING: "CLOSING"}


def normaliser_email(email: str) -> str:
    return (email or "").strip().lower()


def normaliser_telephone(tel: str) -> str:
    """Garde les 10 derniers chiffres seulement."""
    chiffres = "".join(c for c in (tel or "") if c.isdigit())
    return chiffres[-10:] if len(chiffres) >= 10 else chiffres


def normaliser_nom(nom: str) -> str:
    """Sans accents, minuscule, sans espaces (règle du spec pour le fallback nom+CP)."""
    sans_accents = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode()
    return "".join(sans_accents.lower().split())


def normaliser_postal(cp: str) -> str:
    return "".join((cp or "").upper().split())


def _en_tete_ghl(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {config['GHL_PRIVATE_TOKEN']}",
        "Version": VERSION_GHL,
        "Accept": "application/json",
    }


def obtenir_toutes_opportunites(config: dict) -> list[dict]:
    """Toutes les opportunités (tout statut, toutes pipelines) du sub-account, paginées."""
    headers = _en_tete_ghl(config)
    opportunites = []
    params = {
        "locationId": config["GHL_LOCATION_ID"],
        "status": "all",
        "limit": 100,
    }
    while True:
        r = requests.get(f"{URL_GHL}/opportunities/search", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        opportunites.extend(data["opportunities"])
        meta = data.get("meta") or {}
        if not meta.get("startAfter") or len(data["opportunities"]) < params["limit"]:
            break
        params = {**params, "startAfter": meta["startAfter"], "startAfterId": meta["startAfterId"]}
    return opportunites


def obtenir_postal_code_contact(config: dict, contact_id: str) -> str:
    headers = _en_tete_ghl(config)
    r = requests.get(f"{URL_GHL}/contacts/{contact_id}", headers=headers, timeout=30)
    r.raise_for_status()
    return (r.json().get("contact") or {}).get("postalCode") or ""


def main():
    config_jobber = dotenv_values(CHEMIN_ENV)
    client_jobber = ClientJobber(
        config_jobber["JOBBER_CLIENT_ID"],
        config_jobber["JOBBER_CLIENT_SECRET"],
        config_jobber["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    fin = date.today()
    debut = fin - timedelta(days=N_JOURS)
    quotes = obtenir_quotes_envoyees(client_jobber, debut, fin)
    print(f"{len(quotes)} soumission(s) envoyée(s) côté Jobber ({debut} -> {fin}).")

    print("Récupération des opportunités GHL (toutes pipelines)...")
    opportunites = obtenir_toutes_opportunites(config_jobber)
    print(f"{len(opportunites)} opportunité(s) trouvée(s) au total.\n")

    # Index par email et par téléphone normalisés (plusieurs opps possibles par clé)
    par_email: dict[str, list[dict]] = {}
    par_telephone: dict[str, list[dict]] = {}
    par_nom: dict[str, list[dict]] = {}
    for o in opportunites:
        c = o.get("contact") or {}
        email = normaliser_email(c.get("email") or "")
        tel = normaliser_telephone(c.get("phone") or "")
        nom = normaliser_nom(c.get("name") or "")
        if email:
            par_email.setdefault(email, []).append(o)
        if tel:
            par_telephone.setdefault(tel, []).append(o)
        if nom:
            par_nom.setdefault(nom, []).append(o)

    def plus_recente(candidats: list[dict]) -> dict:
        return max(candidats, key=lambda o: o["updatedAt"])

    resultats = {"email": 0, "telephone": 0, "nom_cp": 0, "non_matche": 0}
    ambigues = []
    lignes = []

    for q in quotes:
        c = q.get("client") or {}
        nom_client = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
        email = _valeur_primaire(c.get("emails") or [], "address")
        tel = _valeur_primaire(c.get("phones") or [], "normalizedPhoneNumber") or _valeur_primaire(
            c.get("phones") or [], "number"
        )
        cp_soumission = normaliser_postal(((q.get("property") or {}).get("address") or {}).get("postalCode") or "")

        match = None
        niveau = None

        candidats = par_email.get(normaliser_email(email)) if email else None
        if candidats:
            match = plus_recente(candidats)
            niveau = "email"
            if len(candidats) > 1:
                ambigues.append((q["quoteNumber"], nom_client, "email", len(candidats)))

        if match is None and tel:
            candidats = par_telephone.get(normaliser_telephone(tel))
            if candidats:
                match = plus_recente(candidats)
                niveau = "telephone"
                if len(candidats) > 1:
                    ambigues.append((q["quoteNumber"], nom_client, "téléphone", len(candidats)))

        if match is None and nom_client and cp_soumission:
            candidats_nom = par_nom.get(normaliser_nom(nom_client))
            if candidats_nom:
                # Fallback nom+CP : il faut le postal code réel du contact GHL (pas dans
                # l'objet contact imbriqué de /opportunities/search), donc un GET par candidat.
                candidats_cp = [
                    o
                    for o in candidats_nom
                    if normaliser_postal(obtenir_postal_code_contact(config_jobber, o["contactId"])) == cp_soumission
                ]
                if candidats_cp:
                    match = plus_recente(candidats_cp)
                    niveau = "nom+cp"
                    if len(candidats_cp) > 1:
                        ambigues.append((q["quoteNumber"], nom_client, "nom+cp", len(candidats_cp)))

        if match:
            resultats[{"email": "email", "telephone": "telephone", "nom+cp": "nom_cp"}[niveau]] += 1
        else:
            resultats["non_matche"] += 1

        lignes.append(
            {
                "numero": q["quoteNumber"],
                "client": nom_client,
                "niveau": niveau or "-",
                "opp_id": match["id"] if match else "",
                "opp_nom": match["name"] if match else "",
                "opp_valeur_actuelle": match["monetaryValue"] if match else None,
                "opp_pipeline": NOMS_PIPELINES.get(match["pipelineId"], match["pipelineId"]) if match else "",
            }
        )

    print(f"{'#':<8} {'Client':<25} {'Match':<12} {'Opportunité GHL':<30} {'Pipeline':<10} {'Valeur actuelle':>15}")
    print("-" * 105)
    for l in lignes:
        valeur = f"{l['opp_valeur_actuelle']:,.2f} $" if l["opp_valeur_actuelle"] is not None else "-"
        print(
            f"{l['numero']:<8} {l['client']:<25} {l['niveau']:<12} "
            f"{(l['opp_nom'] or '(aucune)'):<30} {l['opp_pipeline']:<10} {valeur:>15}"
        )

    total = len(quotes)
    matchees = total - resultats["non_matche"]
    print("\n" + "=" * 95)
    print(
        f"Résumé : {total} soumissions -> {matchees} matchées "
        f"(email: {resultats['email']}, téléphone: {resultats['telephone']}, nom+CP: {resultats['nom_cp']}), "
        f"{resultats['non_matche']} non matchées"
    )
    if ambigues:
        print("\nAmbiguës (plusieurs opportunités trouvées, la plus récente utilisée) :")
        for numero, nom, niveau, n in ambigues:
            print(f"  - Soumission #{numero} — {nom} — match par {niveau} — {n} opportunités trouvées")


if __name__ == "__main__":
    main()
