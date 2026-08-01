"""
Source de données Jobber — Module Dashboard Ventes GHL. Deux usages :
  1. Métriques 4-5 (closing, valeur moyenne won) — vendeur = champ salesperson
     de Jobber directement (décision de Justin, 2026-08-01), pas de crossref
     GHL nécessaire pour ça.
  2. Délais lead->soumission / soumission->won (pages Sources et Temporel,
     extension du 2026-08-01) — nécessite de relier chaque soumission à son
     opportunité GHL d'origine (pour connaître la date de création du LEAD, pas
     seulement de la soumission). Réutilise la cascade de matching email/
     téléphone déjà validée dans valider_etape3_sync_soumissions.py (même
     limite acceptée : ~85-95% de taux de match, voir sync_soumissions_ghl.py).
     Les soumissions non matchées gardent delai_lead_soumission=None plutôt
     qu'une valeur inventée (voir dashboard_ventes_ghl.py -> "données
     insuffisantes").

Vendeur (métriques 4-5) : alias nécessaire pour un seul cas connu — les
soumissions de Justin Blaquiere apparaissent sous le nom du compte compagnie
"MAG Lavage À Pression" dans Jobber (voir config.yaml -> ghl_vendeurs_alias_jobber).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jobber_client import ClientJobber
from rapport_ventes import STATUTS_VENDUS
from valider_etape2_sync_soumissions import normaliser_email, normaliser_telephone
from valider_etape3_sync_soumissions import choisir_soumission_canonique

FUSEAU_MAG = ZoneInfo("America/Montreal")

REQUETE_QUOTES_ENVOYEES_VENTES_GHL = """
query QuotesEnvoyeesVentesGHL($debut: ISO8601DateTime!, $fin: ISO8601DateTime!, $after: String) {
  quotes(filter: { updatedAt: { after: $debut, before: $fin } }, first: 50, after: $after) {
    nodes {
      quoteNumber
      quoteStatus
      sentAt
      updatedAt
      lastTransitioned { approvedAt convertedAt }
      amounts { subtotal }
      salesperson { name { full } }
      client {
        id
        firstName
        lastName
        emails { address primary }
        phones { number normalizedPhoneNumber primary }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _vers_iso(jour: date, fin_de_journee: bool = False) -> str:
    heure = "23:59:59" if fin_de_journee else "00:00:00"
    dt_local = datetime.fromisoformat(f"{jour.isoformat()}T{heure}").replace(tzinfo=FUSEAU_MAG)
    return dt_local.astimezone(ZoneInfo("UTC")).isoformat()


def _vers_local(iso_datetime: str | None) -> datetime | None:
    if not iso_datetime:
        return None
    return datetime.fromisoformat(iso_datetime.replace("Z", "+00:00")).astimezone(FUSEAU_MAG)


def _valeur_primaire(items: list[dict], cle: str) -> str:
    if not items:
        return ""
    for item in items:
        if item.get("primary"):
            return item.get(cle, "") or ""
    return items[0].get(cle, "") or ""


def _paginer(client: ClientJobber, requete: str, variables: dict) -> list[dict]:
    tous_les_nodes = []
    apres = None
    while True:
        data = client.executer(requete, {**variables, "after": apres})["quotes"]
        tous_les_nodes.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        apres = data["pageInfo"]["endCursor"]
    return tous_les_nodes


@dataclass
class SoumissionJobber:
    numero: str
    client: str
    vendeur: str
    montant: float
    statut: str  # quoteStatus brut Jobber (draft/awaiting_response/approved/converted/archived/changes_requested)
    won: bool
    date_envoi: datetime
    date_gagnee: datetime | None  # approvedAt ou convertedAt, la première des deux non nulle
    delai_lead_soumission_heures: float | None  # None si non matchée à une opportunité GHL
    delai_soumission_won_heures: float | None  # None si non gagnée ou non matchée
    delai_lead_won_heures: float | None  # None si non gagnée ou non matchée (lead->won direct)
    source_lead: str | None  # source GHL du lead matché (ex. Facebook) — None si non matchée


def _recuperer_quotes_brutes(client_jobber: ClientJobber, debut: date, fin: date) -> list[dict]:
    variables = {"debut": _vers_iso(debut), "fin": _vers_iso(fin, fin_de_journee=True)}
    nodes = _paginer(client_jobber, REQUETE_QUOTES_ENVOYEES_VENTES_GHL, variables)
    return [n for n in nodes if (d := _vers_local(n["sentAt"])) is not None and debut <= d.date() <= fin]


def _dedupliquer_par_client(nodes: list[dict]) -> list[dict]:
    """Même règle multi-soumissions que le sync Jobber->GHL : plusieurs
    soumissions au même client dans la fenêtre sont réduites à une seule
    (canonique), jamais sommées ni comptées séparément (voir Ximena Arteaga,
    #621/#622, repéré le 2026-08-01)."""
    groupes: dict[str, list[dict]] = {}
    for n in nodes:
        c = n.get("client") or {}
        cle = c.get("id") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip().lower()
        groupes.setdefault(cle, []).append(n)
    return [choisir_soumission_canonique(g) for g in groupes.values()]


def _index_opportunites(opportunites_brutes: list[dict]) -> tuple[dict, dict]:
    par_email: dict[str, list[dict]] = {}
    par_telephone: dict[str, list[dict]] = {}
    for o in opportunites_brutes:
        c = o.get("contact") or {}
        email = normaliser_email(c.get("email") or "")
        tel = normaliser_telephone(c.get("phone") or "")
        if email:
            par_email.setdefault(email, []).append(o)
        if tel:
            par_telephone.setdefault(tel, []).append(o)
    return par_email, par_telephone


def _matcher_opportunite(n: dict, par_email: dict, par_telephone: dict) -> dict | None:
    c = n.get("client") or {}
    email = _valeur_primaire(c.get("emails") or [], "address")
    tel = _valeur_primaire(c.get("phones") or [], "normalizedPhoneNumber") or _valeur_primaire(c.get("phones") or [], "number")

    candidats = par_email.get(normaliser_email(email)) if email else None
    if candidats:
        return max(candidats, key=lambda o: o["updatedAt"])
    if tel:
        candidats = par_telephone.get(normaliser_telephone(tel))
        if candidats:
            return max(candidats, key=lambda o: o["updatedAt"])
    return None


def obtenir_soumissions_jobber(
    client_jobber: ClientJobber,
    alias_vendeurs: dict,
    debut: date,
    fin: date,
    opportunites_brutes_ghl: list[dict] | None = None,
) -> list[SoumissionJobber]:
    """Soumissions envoyées dans [debut, fin] (jours locaux MAG), dédupliquées
    par client. Si `opportunites_brutes_ghl` est fourni, chaque soumission est
    en plus matchée à son opportunité GHL d'origine (cascade email->téléphone)
    pour calculer les délais lead->soumission et soumission->won ; sinon ces
    deux champs restent à None (voir dashboard_ventes_ghl.py, "données
    insuffisantes" si le matching est désactivé)."""
    nodes = _dedupliquer_par_client(_recuperer_quotes_brutes(client_jobber, debut, fin))

    par_email, par_telephone = ({}, {})
    if opportunites_brutes_ghl is not None:
        par_email, par_telephone = _index_opportunites(opportunites_brutes_ghl)

    resultats = []
    for n in nodes:
        c = n.get("client") or {}
        nom_client = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
        nom_brut = (n.get("salesperson") or {}).get("name", {}).get("full", "")
        vendeur = alias_vendeurs.get(nom_brut, nom_brut) or "(non assigné)"
        date_envoi = _vers_local(n["sentAt"])
        transitions = n.get("lastTransitioned") or {}
        date_gagnee = _vers_local(transitions.get("approvedAt")) or _vers_local(transitions.get("convertedAt"))

        delai_lead_soumission = None
        delai_lead_won = None
        source_lead = None
        if opportunites_brutes_ghl is not None:
            opp = _matcher_opportunite(n, par_email, par_telephone)
            if opp:
                source_lead = opp.get("source") or "(inconnue)"
                date_creation_lead = _vers_local(opp.get("createdAt"))
                if date_creation_lead and date_envoi:
                    delai_lead_soumission = (date_envoi - date_creation_lead).total_seconds() / 3600
                if date_creation_lead and date_gagnee:
                    delai_lead_won = (date_gagnee - date_creation_lead).total_seconds() / 3600
        delai_soumission_won = None
        if date_gagnee and date_envoi:
            delai_soumission_won = (date_gagnee - date_envoi).total_seconds() / 3600

        resultats.append(
            SoumissionJobber(
                numero=n["quoteNumber"],
                client=nom_client,
                vendeur=vendeur,
                montant=n["amounts"]["subtotal"] or 0.0,
                statut=n["quoteStatus"],
                won=n["quoteStatus"] in STATUTS_VENDUS,
                date_envoi=date_envoi,
                date_gagnee=date_gagnee,
                delai_lead_soumission_heures=delai_lead_soumission,
                delai_soumission_won_heures=delai_soumission_won,
                delai_lead_won_heures=delai_lead_won,
                source_lead=source_lead,
            )
        )
    return resultats
