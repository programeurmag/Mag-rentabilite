"""
Exploration — Module Dashboard Ventes GHL, étape 1 (obligatoire avant de
construire les métriques) : pull N jours de données réelles et rapporte ce
qui est RÉELLEMENT disponible et loggé, pour valider avec Justin que les 5
métriques du dashboard sont calculables avant d'écrire le moindre calcul.

Lecture seule (aucune écriture GHL). Gère la pagination (opportunities/search
via meta.startAfter/startAfterId) et le rate limiting (voir ghl_client.py :
pause entre appels + reprise avec backoff sur 429).

Périmètre confirmé avec Justin (2026-07-31) — il n'existe PAS de pipeline
"Ventes MAG 2026" sur ce sub-account, seulement SETTING et CLOSING :
  - Le funnel traverse les deux : un lead est créé dans SETTING, avance
    jusqu'à "RDV BOOKÉ", puis est déplacé manuellement vers CLOSING une fois
    la visite faite.
  - "Visite bookée" (métrique 2) = a atteint le stage "RDV BOOKÉ" dans
    SETTING, OU est déjà dans CLOSING (implique RDV BOOKÉ dépassé).
  - "Soumission envoyée" (métriques 3 et 4) = l'opportunité est dans la
    pipeline CLOSING (n'importe quel stage).
  Encore À VALIDER (empiriquement, voir section 4 du rapport) : GHL ne
  fournit pas de champ "date d'entrée dans le stage/pipeline courant" sur
  opportunities/search — seulement updatedAt (modifié par n'importe quel
  changement, pas juste un changement de stage). Sans un vrai timestamp de
  passage à CLOSING, la métrique 3 ("rappel dans les 48h après l'envoi de la
  soumission") ne peut pas être calculée avec précision tant qu'on n'a pas
  confirmé une source fiable (voir alerte en fin de rapport).

Usage :
  python3 src/explorer_ventes_ghl.py                       # 30 derniers jours, SETTING + CLOSING
  python3 src/explorer_ventes_ghl.py --jours 60
  python3 src/explorer_ventes_ghl.py --pipelines "SETTING,CLOSING"
  python3 src/explorer_ventes_ghl.py --limit 20             # test rapide sur 20 opportunités
"""

from __future__ import annotations

import argparse
import json
import os
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, RACINE
from ghl_client import (
    obtenir_conversations_contact,
    obtenir_messages_conversation,
    obtenir_nom_utilisateur,
    obtenir_notes_contact,
    obtenir_opportunites_pipeline,
    obtenir_pipelines,
    obtenir_taches_contact,
    tester_scope,
)

FUSEAU_MAG = ZoneInfo("America/Montreal")
DOSSIER_DUMPS = RACINE / "data" / "exploration_ghl"

STAGE_VISITE_BOOKEE = "rdv booke"  # normalisé sans accents/majuscules — dernier stage de SETTING
NOM_PIPELINE_CLOSING = "closing"


def _sans_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _vers_local(iso_datetime: str | None):
    if not iso_datetime:
        return None
    dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
    return dt.astimezone(FUSEAU_MAG)


def parse_args():
    p = argparse.ArgumentParser(description="Exploration des données GHL pour le dashboard ventes")
    p.add_argument("--jours", type=int, default=30, help="Fenêtre en jours (défaut 30)")
    p.add_argument("--pipelines", type=str, default="SETTING,CLOSING", help="Noms des pipelines à inspecter, séparés par virgule")
    p.add_argument("--limit", type=int, default=None, help="Limite le nombre d'opportunités traitées (test rapide)")
    return p.parse_args()


def trouver_pipeline(pipelines: list[dict], nom_recherche: str) -> dict | None:
    cible = _sans_accents(nom_recherche)
    for p in pipelines:
        if _sans_accents(p["name"]) == cible:
            return p
    for p in pipelines:
        if cible in _sans_accents(p["name"]):
            return p
    return None


def main():
    args = parse_args()
    config = {**dotenv_values(CHEMIN_ENV), **os.environ} if CHEMIN_ENV.exists() else os.environ
    for cle in ("GHL_PRIVATE_TOKEN", "GHL_LOCATION_ID"):
        if not config.get(cle):
            raise SystemExit(f"Variable {cle} manquante dans .env — voir .env.example")

    print("=" * 100)
    print("EXPLORATION DASHBOARD VENTES GHL — étape 1 (validation avant calcul des métriques)")
    print("=" * 100)

    # --- 1. Pipelines et stages -------------------------------------------------
    print("\n[1/7] Pipelines disponibles sur ce sub-account...")
    toutes_pipelines = obtenir_pipelines(config)
    print(f"  {len(toutes_pipelines)} pipeline(s) trouvée(s) au total.")

    noms_demandes = [n.strip() for n in args.pipelines.split(",") if n.strip()]
    pipelines_retenues = []
    for nom in noms_demandes:
        p = trouver_pipeline(toutes_pipelines, nom)
        if p is None:
            print(f"\n⚠ Pipeline \"{nom}\" introuvable. Pipelines existantes :")
            for pp in toutes_pipelines:
                print(f"    - \"{pp['name']}\" (id {pp['id']})")
            return
        pipelines_retenues.append(p)

    stage_nom: dict[str, str] = {}
    pipeline_nom_par_id: dict[str, str] = {}
    print("  Pipelines retenues pour l'exploration :")
    for p in pipelines_retenues:
        pipeline_nom_par_id[p["id"]] = p["name"]
        print(f"\n  \"{p['name']}\" (id {p['id']}) :")
        for i, s in enumerate(p.get("stages", [])):
            stage_nom[s["id"]] = s["name"]
            print(f"    {i}. {s['name']} (id {s['id']})")

    id_pipeline_closing = next(
        (p["id"] for p in pipelines_retenues if _sans_accents(p["name"]) == NOM_PIPELINE_CLOSING), None
    )

    # --- 2. Opportunités des pipelines retenues, fenêtre de N jours -------------
    print(f"\n[2/7] Récupération des opportunités ({', '.join(p['name'] for p in pipelines_retenues)})...")
    toutes = []
    for p in pipelines_retenues:
        opps_pipeline = obtenir_opportunites_pipeline(config, p["id"])
        print(f"  {len(opps_pipeline)} opportunité(s) au total dans \"{p['name']}\" (toutes dates, tout statut).")
        toutes.extend(opps_pipeline)

    debut_fenetre = datetime.now(timezone.utc) - timedelta(days=args.jours)
    opps = []
    sans_date = 0
    for o in toutes:
        dt = _vers_local(o.get("createdAt"))
        if dt is None:
            sans_date += 1
            continue
        if dt.astimezone(timezone.utc) < debut_fenetre:
            continue
        opps.append(o)

    print(f"  {len(opps)} opportunité(s) créée(s) dans les {args.jours} derniers jours (across pipelines retenues).")
    if sans_date:
        print(f"  ⚠ {sans_date} opportunité(s) SANS createdAt exploitable — à examiner.")

    if not opps:
        print("\nAucune opportunité dans la fenêtre — rien à analyser plus loin.")
        return

    print(f"  Clés brutes observées sur un objet opportunité (pour repérer un éventuel timestamp de changement de stage) :")
    print(f"    {sorted(opps[0].keys())}")

    # --- 3. Champs de base : stage, statut, vendeur, valeur, funnel -------------
    print("\n[3/7] Champs de base des opportunités...")
    par_stage = Counter()
    par_statut = Counter()
    par_vendeur = Counter()
    sans_assigned = 0
    sans_valeur = 0
    sans_contact = 0
    n_visite_bookee = 0
    n_soumission_envoyee = 0
    par_vendeur_soumission = defaultdict(lambda: [0, 0])  # [soumissions, opps totales]
    opps_soumission: list[dict] = []

    for o in opps:
        nom_stage = stage_nom.get(o.get("pipelineStageId"), f"(stage inconnu {o.get('pipelineStageId')})")
        par_stage[nom_stage] += 1
        par_statut[o.get("status", "(vide)")] += 1
        assigned = o.get("assignedTo")
        if not assigned:
            sans_assigned += 1
        vendeur = obtenir_nom_utilisateur(config, assigned)
        par_vendeur[vendeur] += 1
        if not o.get("monetaryValue"):
            sans_valeur += 1
        if not o.get("contactId") and not (o.get("contact") or {}).get("id"):
            sans_contact += 1

        dans_closing = o.get("pipelineId") == id_pipeline_closing
        if dans_closing or _sans_accents(nom_stage) == STAGE_VISITE_BOOKEE:
            n_visite_bookee += 1
        if dans_closing:
            n_soumission_envoyee += 1
            opps_soumission.append(o)
            par_vendeur_soumission[vendeur][0] += 1
        par_vendeur_soumission[vendeur][1] += 1

    print("  Répartition par stage :")
    for nom, n in par_stage.most_common():
        print(f"    {n:>4}  {nom}")
    print("  Répartition par statut :")
    for nom, n in par_statut.most_common():
        print(f"    {n:>4}  {nom}")
    print("  Répartition par vendeur (assignedTo) :")
    for nom, n in par_vendeur.most_common():
        print(f"    {n:>4}  {nom}")
    print(f"  Champs vides : sans assignedTo={sans_assigned}, sans monetaryValue={sans_valeur}, sans contactId={sans_contact}")
    opps_won = [o for o in opps if o.get("status") == "won"]
    won_avec_valeur = [o for o in opps_won if o.get("monetaryValue")]
    print(f"  Opportunités 'won' : {len(opps_won)}, dont {len(won_avec_valeur)} avec monetaryValue rempli")
    if won_avec_valeur:
        moyenne = sum(o["monetaryValue"] for o in won_avec_valeur) / len(won_avec_valeur)
        print(f"    Valeur moyenne (sur celles remplies seulement) : {moyenne:,.2f} $")
    print(f"  Visite bookée (RDV BOOKÉ ou CLOSING) : {n_visite_bookee} / {len(opps)}")
    print(f"  Soumission envoyée (dans CLOSING) : {n_soumission_envoyee} / {len(opps)}")
    print("  Par vendeur (soumissions / total opportunités) :")
    for vendeur, (soum, total) in sorted(par_vendeur_soumission.items(), key=lambda kv: -kv[1][1]):
        print(f"    {vendeur:<25} {soum:>3} / {total:<3}")

    # --- 4. Timestamp de passage à CLOSING (nécessaire pour la métrique 48h) ---
    print("\n[4/7] Recherche d'un timestamp fiable de passage à CLOSING (pour la métrique 'rappel 48h')...")
    if opps_soumission:
        exemple = opps_soumission[0]
        print(
            f"  Exemple d'opportunité dans CLOSING : lastStageChangeAt={exemple.get('lastStageChangeAt')}, "
            f"lastStatusChangeAt={exemple.get('lastStatusChangeAt')}, updatedAt={exemple.get('updatedAt')}"
        )
        print("  ✓ 'lastStageChangeAt' existe bien sur l'opportunité — bonne nouvelle, ça peut servir de proxy pour")
        print("    la date d'entrée dans CLOSING (à condition qu'elle ne change plus de stage après, ce qui est le cas")
        print("    tant qu'elle reste sur le MÊME stage CLOSING — à re-vérifier si l'opp progresse ensuite dans CLOSING).")
    else:
        print("  Aucune opportunité dans CLOSING sur la fenêtre — impossible de vérifier pour l'instant.")

    # --- 4bis. Préflight scopes (conversations / tasks / notes / users) --------
    print("\n[4bis/7] Vérification des scopes du token pour conversations / tâches / notes / users...")
    contact_test = next(
        (o.get("contactId") or (o.get("contact") or {}).get("id") for o in opps if o.get("contactId") or (o.get("contact") or {}).get("id")),
        None,
    )
    scopes_ok = {"conversations": True, "tasks": True, "notes": True, "users": True}
    if contact_test is None:
        print("  Aucun contact disponible pour tester — sections 5/6 sautées.")
        scopes_ok = {k: False for k in scopes_ok}
    else:
        tests = {
            "conversations": ("GET", "/conversations/search", {"locationId": config["GHL_LOCATION_ID"], "contactId": contact_test, "limit": 1}),
            "tasks": ("GET", f"/contacts/{contact_test}/tasks", None),
            "notes": ("GET", f"/contacts/{contact_test}/notes", None),
            "users": ("GET", f"/users/{next((o.get('assignedTo') for o in opps if o.get('assignedTo')), '')}", None),
        }
        for nom, (methode, chemin, params) in tests.items():
            ok, erreur = tester_scope(config, methode, chemin, params)
            scopes_ok[nom] = ok
            print(f"    {'✓' if ok else '✗'} {nom:<14} {'OK' if ok else erreur}")
        if not all(scopes_ok.values()):
            print("\n  ⚠ Scope(s) manquant(s) sur le Private Integration Token GHL — va dans Settings >")
            print("    Private Integrations > (ton intégration) > coche les scopes manquants (Conversations,")
            print("    Conversations Messages, Contacts Tasks, Contacts Notes, Users), puis relance ce script.")
            print("    Les sections concernées ci-dessous sont sautées plutôt que de planter.")

    # --- 5. Conversations / messages (appels loggés, SMS) par contact ----------
    print("\n[5/7] Conversations et messages par contact (appels loggés, SMS)...")
    opps_api = opps[: args.limit] if args.limit else opps
    if args.limit:
        print(f"  (--limit {args.limit} appliqué pour les sections API-intensives 5/6)")
    types_messages_bruts = Counter()
    cles_message_vues = set()
    contacts_sans_conversation = 0
    contacts_sans_message = 0
    opps_avec_contact_meme_jour: list[dict] = []
    opps_avec_contact_meme_jour_toutes_directions: list[dict] = []
    par_vendeur_meme_jour = defaultdict(lambda: [0, 0])  # [contactés meme jour (sortant), total]
    exemples_messages: list[dict] = []

    if not scopes_ok["conversations"]:
        print("  SAUTÉ — scope 'conversations' manquant sur le token (voir 4bis).")
    else:
        print("  (ceci fait 1-2+ appels API par opportunité — patience, rate-limité)")
    for o in opps_api if scopes_ok["conversations"] else []:
        contact_id = o.get("contactId") or (o.get("contact") or {}).get("id")
        vendeur = obtenir_nom_utilisateur(config, o.get("assignedTo"))
        par_vendeur_meme_jour[vendeur][1] += 1
        if not contact_id:
            continue

        conversations = obtenir_conversations_contact(config, contact_id)
        if not conversations:
            contacts_sans_conversation += 1
            continue

        messages = []
        for conv in conversations:
            conv_id = conv.get("id")
            if not conv_id:
                continue
            messages.extend(obtenir_messages_conversation(config, conv_id))

        if not messages:
            contacts_sans_message += 1
            continue

        date_creation_locale = _vers_local(o.get("createdAt"))
        contacte_meme_jour_sortant = False
        contacte_meme_jour_toutes_directions = False
        for m in messages:
            cles_message_vues.update(m.keys())
            type_brut = str(m.get("messageType") or m.get("type") or m.get("contentType") or "(type inconnu)")
            types_messages_bruts[type_brut] += 1
            if len(exemples_messages) < 5:
                exemples_messages.append({k: m.get(k) for k in list(m.keys())[:12]})

            est_appel_ou_sms = any(x in type_brut.upper() for x in ("CALL", "SMS"))
            date_msg = _vers_local(m.get("dateAdded") or m.get("dateUpdated"))
            meme_jour = est_appel_ou_sms and date_msg and date_creation_locale and date_msg.date() == date_creation_locale.date()
            if meme_jour:
                contacte_meme_jour_toutes_directions = True
                if m.get("direction") == "outbound":
                    contacte_meme_jour_sortant = True

        if contacte_meme_jour_toutes_directions:
            opps_avec_contact_meme_jour_toutes_directions.append(o)
        if contacte_meme_jour_sortant:
            opps_avec_contact_meme_jour.append(o)
            par_vendeur_meme_jour[vendeur][0] += 1

    if scopes_ok["conversations"]:
        print(f"  Clés observées sur un message (pour valider la forme réelle de l'API) : {sorted(cles_message_vues)}")
        print("  Types de message rencontrés (brut, à valider) :")
        for t, n in types_messages_bruts.most_common():
            print(f"    {n:>4}  {t}")
        print(f"  Contacts sans AUCUNE conversation trouvée : {contacts_sans_conversation}")
        print(f"  Contacts avec conversation(s) mais sans message : {contacts_sans_message}")
        print(
            f"  Opportunités avec appel/SMS SORTANT loggé le jour même : {len(opps_avec_contact_meme_jour)} / {len(opps_api)} "
            f"(toutes directions confondues : {len(opps_avec_contact_meme_jour_toutes_directions)} / {len(opps_api)})"
        )
        print("  Par vendeur (contacté même jour, sortant seulement / total) :")
        for vendeur, (contactes, total) in sorted(par_vendeur_meme_jour.items(), key=lambda kv: -kv[1][1]):
            pct = f"{100 * contactes / total:.0f}%" if total else "-"
            print(f"    {vendeur:<25} {contactes:>3} / {total:<3}  ({pct})")

    # --- 6. Notes et tâches -------------------------------------------------
    print("\n[6/7] Notes et tâches liées aux contacts...")
    total_notes = 0
    total_taches = 0
    contacts_sans_notes = 0
    contacts_sans_taches = 0
    contacts_traites = 0
    if not (scopes_ok["notes"] or scopes_ok["tasks"]):
        print("  SAUTÉ — scopes 'notes'/'tasks' manquants sur le token (voir 4bis).")
    else:
        for o in opps_api:
            contact_id = o.get("contactId") or (o.get("contact") or {}).get("id")
            if not contact_id:
                continue
            contacts_traites += 1
            notes = obtenir_notes_contact(config, contact_id) if scopes_ok["notes"] else []
            taches = obtenir_taches_contact(config, contact_id) if scopes_ok["tasks"] else []
            total_notes += len(notes)
            total_taches += len(taches)
            if not notes:
                contacts_sans_notes += 1
            if not taches:
                contacts_sans_taches += 1

        print(f"  {total_notes} note(s) au total sur {contacts_traites} contact(s) ({contacts_sans_notes} sans aucune note)")
        print(f"  {total_taches} tâche(s) au total sur {contacts_traites} contact(s) ({contacts_sans_taches} sans aucune tâche)")

    # --- 7. Dump brut pour inspection manuelle ----------------------------------
    DOSSIER_DUMPS.mkdir(parents=True, exist_ok=True)
    horodatage = datetime.now(FUSEAU_MAG).strftime("%Y-%m-%dT%H-%M-%S")
    chemin_dump = DOSSIER_DUMPS / f"exploration_{horodatage}.json"
    chemin_dump.write_text(
        json.dumps(
            {
                "pipelines": [{"id": p["id"], "name": p["name"], "stages": p.get("stages", [])} for p in pipelines_retenues],
                "fenetre_jours": args.jours,
                "n_opportunites": len(opps),
                "cles_opportunite_vues": sorted(opps[0].keys()) if opps else [],
                "exemples_messages": exemples_messages,
                "cles_message_vues": sorted(cles_message_vues),
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n[7/7] Dump brut écrit dans {chemin_dump.relative_to(RACINE)} (contient des données clients — jamais commité, voir .gitignore).")

    # --- Résumé pour validation --------------------------------------------
    print("\n" + "=" * 100)
    print("À VALIDER AVEC JUSTIN AVANT DE CONSTRUIRE LE DASHBOARD :")
    print("=" * 100)
    if not all(scopes_ok.values()):
        manquants = [k for k, v in scopes_ok.items() if not v]
        print(f"  ⚠ BLOQUANT : scope(s) manquant(s) sur le token ({', '.join(manquants)}) — voir section 4bis.")
        print("     Ajoute les scopes dans GHL (Settings > Private Integrations), relance le script, PUIS")
        print("     seulement là les points ci-dessous redeviennent vérifiables.")
    else:
        print("  1. Les types de message ci-dessus (section 5) sont-ils bien des appels/SMS loggés,")
        print("     ou du bruit (emails, notes internes automatiques, etc.) ? Voir exemples dans le dump JSON.")
        if contacts_sans_conversation > len(opps_api) * 0.2:
            print(f"  ⚠ {contacts_sans_conversation}/{len(opps_api)} contacts sans aucune conversation trouvée — vérifier si")
            print("     les appels/SMS sont vraiment loggés dans GHL, ou juste pas encore pour ce sous-ensemble.")
        print(f"  2. {total_notes} notes / {total_taches} tâches trouvées au total — assez pour la métrique")
        print("     \"rappel loggé en dedans de 48h\", ou faut-il élargir la définition (ex. inclure les appels sortants) ?")
    print("  3. lastStageChangeAt (section 4) comme proxy de 'date d'envoi de la soumission' pour le calcul du")
    print("     rappel 48h — bon à confirmer sur plus de cas une fois les scopes actifs.")
    if sans_assigned > 0:
        print(f"  ⚠ {sans_assigned} opportunité(s) sans assignedTo — ne pourront pas être attribuées à un vendeur.")


if __name__ == "__main__":
    main()
