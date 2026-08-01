"""
Source de données — Module Dashboard Ventes GHL. Récupère les opportunités des
pipelines SETTING+CLOSING (voir config.yaml -> ghl_pipelines_ventes), enrichies
des indicateurs d'activité déduits des conversations/messages GHL.

Décisions du 2026-08-01 (voir conversation + src/explorer_ventes_ghl.py) :
  - "Visite bookée" = a atteint le stage RDV BOOKÉ (dans SETTING) OU est déjà
    dans la pipeline CLOSING.
  - "Soumission envoyée" = l'opportunité est dans la pipeline CLOSING
    (n'importe quel stage).
  - "Contacté même jour" = appel/SMS SORTANT loggé le jour calendaire (heure
    Montréal) de création du lead.
  - "Rappel 48h" = appel/SMS SORTANT loggé dans les 48h suivant l'entrée en
    CLOSING (Tasks/Notes GHL quasi jamais utilisées — pas une source fiable).

Décision du 2026-08-01 (extension centre de contrôle multi-pages) : en plus des
deux booléens ci-dessus, chaque opportunité porte maintenant des COMPTES bruts
d'activité (appels/SMS/emails sortants, appels répondus) pour les pages
Activité/Pipeline/Temporel. Contrairement aux booléens (figés une fois pour
toutes après un délai fixe), ces comptes continuent d'évoluer tant que
l'opportunité reste "open" — donc PAS de gel définitif possible sur ce
critère-là : on ne les fige qu'au passage won/lost/abandoned (voir
"activite_figee" dans le cache). Conséquence acceptée : toute opportunité
encore ouverte redéclenche un appel API à CHAQUE run (plus lourd que l'ancien
schéma, mais nécessaire pour des comptes exacts).

Cache incrémental (cache_ventes_ghl.json, committé par le workflow) — voir
ci-dessus pour la règle de gel par indicateur. L'ancre "date d'entrée en
CLOSING" (nécessaire pour la fenêtre de 48h) est capturée la PREMIÈRE fois
qu'une opportunité est observée dans CLOSING et jamais réajustée ensuite.
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
STATUTS_FERMES = ("won", "lost", "abandoned")


def _sans_accents(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _vers_local(iso_datetime: str | None) -> datetime | None:
    if not iso_datetime:
        return None
    dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
    return dt.astimezone(FUSEAU_MAG)


def _type_message(m: dict) -> str:
    return str(m.get("messageType") or m.get("type") or "").upper()


def _sortant(m: dict) -> bool:
    return m.get("direction") == "outbound"


def _est_call(m: dict) -> bool:
    return "CALL" in _type_message(m)


def _est_sms(m: dict) -> bool:
    return "SMS" in _type_message(m)


def _est_email(m: dict) -> bool:
    return "EMAIL" in _type_message(m)


def _appel_repondu(m: dict) -> bool:
    return str(m.get("status", "")).lower() == "completed"


@dataclass
class OpportuniteVente:
    id: str
    nom: str
    vendeur: str
    source: str  # source du lead (ex. Facebook) — champ GHL "source", vide si absent
    stage_nom: str
    statut: str  # open / won / lost / abandoned
    valeur: float | None
    date_creation: datetime
    updated_at: datetime | None
    visite_bookee: bool
    soumission_envoyee: bool  # dans la pipeline CLOSING
    contacte_meme_jour: bool
    rappel_48h: bool | None  # None si soumission_envoyee est False (pas applicable)
    n_appels_sortants: int
    n_appels_repondus: int
    n_sms_sortants: int
    n_emails_sortants: int
    derniere_activite: datetime | None  # max(dernier message sortant, updatedAt)


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


def construire_contexte(config: dict) -> dict:
    toutes_pipelines = obtenir_pipelines(config)
    stage_nom, id_soumission, retenues = _resoudre_pipelines(config, toutes_pipelines)
    return {
        "stage_nom": stage_nom,
        "id_soumission": id_soumission,
        "pipelines": retenues,
        "nom_stage_visite": _sans_accents(config["ghl_stage_visite_bookee"]),
        "vendeurs": config.get("ghl_vendeurs", {}),
    }


def recuperer_opportunites_brutes(config: dict, ctx: dict) -> list[dict]:
    brutes = []
    for p in ctx["pipelines"]:
        brutes.extend(obtenir_opportunites_pipeline(config, p["id"]))
    return brutes


def _enrichir_opportunite(config: dict, cache_opps: dict, ctx: dict, o: dict, maintenant_utc, maintenant_local) -> OpportuniteVente:
    date_creation = _vers_local(o.get("createdAt"))
    updated_at = _vers_local(o.get("updatedAt"))
    dans_closing = o.get("pipelineId") == ctx["id_soumission"]
    nom_stage = ctx["stage_nom"].get(o.get("pipelineStageId"), "")
    visite_bookee = dans_closing or _sans_accents(nom_stage) == ctx["nom_stage_visite"]
    statut = o.get("status", "")

    entree = cache_opps.get(o["id"], {})

    if dans_closing and "date_entree_closing" not in entree:
        entree["date_entree_closing"] = o.get("lastStageChangeAt") or o.get("createdAt")

    contact_fige = entree.get("contacte_meme_jour_fige", False)
    rappel_fige = entree.get("rappel_48h_fige", False)
    activite_figee = entree.get("activite_figee", False)

    date_entree_closing = _vers_local(entree.get("date_entree_closing")) if dans_closing else None
    fin_fenetre_rappel = (
        date_entree_closing + timedelta(hours=FENETRE_RAPPEL_HEURES) if date_entree_closing else None
    )

    besoin_contact = not contact_fige
    besoin_rappel = dans_closing and not rappel_fige
    besoin_activite = not activite_figee

    if besoin_contact or besoin_rappel or besoin_activite:
        contact_id = o.get("contactId") or (o.get("contact") or {}).get("id")
        messages = []
        if contact_id:
            for conv in obtenir_conversations_contact(config, contact_id):
                conv_id = conv.get("id")
                if conv_id:
                    messages.extend(obtenir_messages_conversation(config, conv_id))

        if besoin_contact and date_creation:
            contacte_meme_jour = any(
                _sortant(m)
                and (_est_call(m) or _est_sms(m))
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
                _sortant(m)
                and (_est_call(m) or _est_sms(m))
                and (dm := _vers_local(m.get("dateAdded"))) is not None
                and date_entree_closing <= dm <= fin_fenetre_rappel
                for m in messages
            )
            entree["rappel_48h"] = rappel_48h
            entree["rappel_48h_fige"] = maintenant_utc > fin_fenetre_rappel.astimezone(timezone.utc)

        if besoin_activite:
            sortants = [m for m in messages if _sortant(m)]
            appels = [m for m in sortants if _est_call(m)]
            entree["n_appels_sortants"] = len(appels)
            entree["n_appels_repondus"] = sum(1 for m in appels if _appel_repondu(m))
            entree["n_sms_sortants"] = sum(1 for m in sortants if _est_sms(m))
            entree["n_emails_sortants"] = sum(1 for m in sortants if _est_email(m))
            dates_sortantes = [d for m in sortants if (d := _vers_local(m.get("dateAdded"))) is not None]
            derniere = max(dates_sortantes) if dates_sortantes else None
            if updated_at and (derniere is None or updated_at > derniere):
                derniere = updated_at
            entree["derniere_activite"] = derniere.isoformat() if derniere else None
            # Figé seulement une fois l'opportunité fermée (won/lost/abandoned) — tant
            # qu'elle reste "open", l'activité continue de s'accumuler dans le temps.
            entree["activite_figee"] = statut in STATUTS_FERMES

    cache_opps[o["id"]] = entree

    assigned = o.get("assignedTo")
    return OpportuniteVente(
        id=o["id"],
        nom=o.get("name") or "(sans nom)",
        vendeur=ctx["vendeurs"].get(assigned, assigned or "(non assigné)"),
        source=o.get("source") or "(inconnue)",
        stage_nom=nom_stage,
        statut=statut,
        valeur=o.get("monetaryValue"),
        date_creation=date_creation,
        updated_at=updated_at,
        visite_bookee=visite_bookee,
        soumission_envoyee=dans_closing,
        contacte_meme_jour=entree.get("contacte_meme_jour", False),
        rappel_48h=entree.get("rappel_48h") if dans_closing else None,
        n_appels_sortants=entree.get("n_appels_sortants", 0),
        n_appels_repondus=entree.get("n_appels_repondus", 0),
        n_sms_sortants=entree.get("n_sms_sortants", 0),
        n_emails_sortants=entree.get("n_emails_sortants", 0),
        derniere_activite=_vers_local(entree.get("derniere_activite")),
    )


def enrichir_fenetre(config: dict, ctx: dict, brutes: list[dict], cache: dict, debut: datetime, fin: datetime) -> list[OpportuniteVente]:
    """Opportunités créées dans [debut, fin] (heures Montréal), enrichies."""
    cache_opps: dict = cache.setdefault("opportunites", {})
    maintenant_utc = datetime.now(timezone.utc)
    maintenant_local = maintenant_utc.astimezone(FUSEAU_MAG)

    resultats: list[OpportuniteVente] = []
    for o in brutes:
        date_creation = _vers_local(o.get("createdAt"))
        if date_creation is None or not (debut <= date_creation <= fin):
            continue
        resultats.append(_enrichir_opportunite(config, cache_opps, ctx, o, maintenant_utc, maintenant_local))
    return resultats


def enrichir_pipeline_ouvert(config: dict, ctx: dict, brutes: list[dict], cache: dict) -> list[OpportuniteVente]:
    """TOUTES les opportunités actuellement 'open', peu importe leur date de
    création — utilisé par la page Pipeline (argent qui dort) : un vieux lead
    jamais fermé doit être visible même s'il a été créé il y a plus de 180 jours."""
    cache_opps: dict = cache.setdefault("opportunites", {})
    maintenant_utc = datetime.now(timezone.utc)
    maintenant_local = maintenant_utc.astimezone(FUSEAU_MAG)

    resultats: list[OpportuniteVente] = []
    for o in brutes:
        if o.get("status") != "open":
            continue
        date_creation = _vers_local(o.get("createdAt"))
        if date_creation is None:
            continue
        resultats.append(_enrichir_opportunite(config, cache_opps, ctx, o, maintenant_utc, maintenant_local))
    return resultats


def obtenir_opportunites_ventes(config: dict, debut: datetime, fin: datetime, cache: dict) -> list[OpportuniteVente]:
    """Rétro-compatible : un seul appel, fait le fetch + enrichissement pour la fenêtre donnée."""
    ctx = construire_contexte(config)
    brutes = recuperer_opportunites_brutes(config, ctx)
    return enrichir_fenetre(config, ctx, brutes, cache, debut, fin)
