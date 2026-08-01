"""
Source de données — Module Dashboard Ventes GHL. Récupère les opportunités des
pipelines SETTING+CLOSING (voir config.yaml -> ghl_pipelines_ventes) sur une
fenêtre donnée, enrichies des indicateurs d'activité (contact même jour, rappel
48h) déduits des conversations/messages GHL.

Décisions du 2026-08-01 (voir conversation + src/explorer_ventes_ghl.py) :
  - "Visite bookée" = a atteint le stage RDV BOOKÉ (dans SETTING) OU est déjà
    dans la pipeline CLOSING.
  - "Soumission envoyée" = l'opportunité est dans la pipeline CLOSING
    (n'importe quel stage).
  - "Contacté même jour" = appel/SMS SORTANT loggé le jour calendaire (heure
    Montréal) de création du lead.
  - "Rappel 48h" = appel/SMS SORTANT loggé dans les 48h suivant l'entrée en
    CLOSING (Tasks/Notes GHL quasi jamais utilisées — 0 et 39/324 sur le test
    du 2026-07-31 — donc pas une source fiable pour cette métrique).

Cache incrémental (cache_ventes_ghl.json, committé par le workflow) : une fois
qu'une opportunité a dépassé sa journée de création ET/OU les 48h suivant son
entrée en CLOSING, ces indicateurs sont figés et ne sont plus jamais
recalculés — seules les opportunités récentes ou tout juste entrées en CLOSING
déclenchent de nouveaux appels API (conversations/messages) à chaque run. Sans
ça, un cron horaire re-téléchargerait les conversations de TOUTES les
opportunités de la fenêtre à chaque exécution (un run complet sur 324
opportunités prend déjà ~12-13 minutes, voir explorer_ventes_ghl.py).

L'ancre "date d'entrée en CLOSING" (nécessaire pour la fenêtre de 48h) est
capturée la PREMIÈRE fois qu'une opportunité est observée dans CLOSING et
jamais réajustée ensuite : lastStageChangeAt bouge aussi sur un changement de
stage interne à CLOSING (ex. SUIVI À FAIRE -> EN ATTENTE DE DÉPÔT), ce qui ne
doit pas redémarrer la fenêtre de "rappel après l'envoi de la soumission".
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ghl_client import (
    obtenir_conversations_contact,
    obtenir_messages_conversation,
    obtenir_opportunites_pipeline,
    obtenir_pipelines,
)

FUSEAU_MAG = ZoneInfo("America/Montreal")
FENETRE_RAPPEL_HEURES = 48


def _sans_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _vers_local(iso_datetime: str | None) -> datetime | None:
    if not iso_datetime:
        return None
    dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
    return dt.astimezone(FUSEAU_MAG)


def _est_appel_sms_sortant(m: dict) -> bool:
    type_brut = str(m.get("messageType") or m.get("type") or "").upper()
    return m.get("direction") == "outbound" and any(x in type_brut for x in ("CALL", "SMS"))


@dataclass
class OpportuniteVente:
    id: str
    nom: str
    vendeur: str
    stage_nom: str
    statut: str  # open / won / lost / abandoned
    valeur: float | None
    date_creation: datetime
    visite_bookee: bool
    soumission_envoyee: bool  # dans la pipeline CLOSING
    contacte_meme_jour: bool
    rappel_48h: bool | None  # None si soumission_envoyee est False (pas applicable)


def _resoudre_pipelines(config: dict, toutes_pipelines: list[dict]):
    noms_voulus = {_sans_accents(n) for n in config["ghl_pipelines_ventes"]}
    retenues = [p for p in toutes_pipelines if _sans_accents(p["name"]) in noms_voulus]
    stage_nom = {}
    id_soumission = None
    nom_soumission = _sans_accents(config["ghl_pipeline_soumission"])
    for p in retenues:
        for s in p.get("stages", []):
            stage_nom[s["id"]] = s["name"]
        if _sans_accents(p["name"]) == nom_soumission:
            id_soumission = p["id"]
    return stage_nom, id_soumission, retenues


def obtenir_opportunites_ventes(
    config: dict, debut: datetime, fin: datetime, cache: dict
) -> list[OpportuniteVente]:
    toutes_pipelines = obtenir_pipelines(config)
    stage_nom, id_soumission, retenues = _resoudre_pipelines(config, toutes_pipelines)
    nom_stage_visite = _sans_accents(config["ghl_stage_visite_bookee"])
    vendeurs_config: dict = config.get("ghl_vendeurs", {})

    maintenant_utc = datetime.now(timezone.utc)
    maintenant_local = maintenant_utc.astimezone(FUSEAU_MAG)
    cache_opps: dict = cache.setdefault("opportunites", {})

    brutes = []
    for p in retenues:
        brutes.extend(obtenir_opportunites_pipeline(config, p["id"]))

    resultats: list[OpportuniteVente] = []
    for o in brutes:
        date_creation = _vers_local(o.get("createdAt"))
        if date_creation is None or not (debut <= date_creation <= fin):
            continue

        dans_closing = o.get("pipelineId") == id_soumission
        nom_stage = stage_nom.get(o.get("pipelineStageId"), "")
        visite_bookee = dans_closing or _sans_accents(nom_stage) == nom_stage_visite

        entree = cache_opps.get(o["id"], {})

        if dans_closing and "date_entree_closing" not in entree:
            entree["date_entree_closing"] = o.get("lastStageChangeAt") or o.get("createdAt")

        contact_fige = entree.get("contacte_meme_jour_fige", False)
        rappel_fige = entree.get("rappel_48h_fige", False)

        date_entree_closing = _vers_local(entree.get("date_entree_closing")) if dans_closing else None
        fin_fenetre_rappel = (
            date_entree_closing + timedelta(hours=FENETRE_RAPPEL_HEURES) if date_entree_closing else None
        )

        besoin_contact = not contact_fige
        besoin_rappel = dans_closing and not rappel_fige

        if besoin_contact or besoin_rappel:
            contact_id = o.get("contactId") or (o.get("contact") or {}).get("id")
            messages = []
            if contact_id:
                for conv in obtenir_conversations_contact(config, contact_id):
                    conv_id = conv.get("id")
                    if conv_id:
                        messages.extend(obtenir_messages_conversation(config, conv_id))

            if besoin_contact:
                contacte_meme_jour = any(
                    _est_appel_sms_sortant(m)
                    and (dm := _vers_local(m.get("dateAdded"))) is not None
                    and dm.date() == date_creation.date()
                    for m in messages
                )
                entree["contacte_meme_jour"] = contacte_meme_jour
                # Figé dès que la journée de création est terminée (aucun message futur
                # ne peut plus changer ce résultat) — sinon revérifié au prochain run.
                entree["contacte_meme_jour_fige"] = maintenant_local.date() > date_creation.date()

            if besoin_rappel and fin_fenetre_rappel:
                rappel_48h = any(
                    _est_appel_sms_sortant(m)
                    and (dm := _vers_local(m.get("dateAdded"))) is not None
                    and date_entree_closing <= dm <= fin_fenetre_rappel
                    for m in messages
                )
                entree["rappel_48h"] = rappel_48h
                entree["rappel_48h_fige"] = maintenant_utc > fin_fenetre_rappel.astimezone(timezone.utc)

        cache_opps[o["id"]] = entree

        assigned = o.get("assignedTo")
        resultats.append(
            OpportuniteVente(
                id=o["id"],
                nom=o.get("name") or "(sans nom)",
                vendeur=vendeurs_config.get(assigned, assigned or "(non assigné)"),
                stage_nom=nom_stage,
                statut=o.get("status", ""),
                valeur=o.get("monetaryValue"),
                date_creation=date_creation,
                visite_bookee=visite_bookee,
                soumission_envoyee=dans_closing,
                contacte_meme_jour=entree.get("contacte_meme_jour", False),
                rappel_48h=entree.get("rappel_48h") if dans_closing else None,
            )
        )

    return resultats
