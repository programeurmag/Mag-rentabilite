"""
Source de données Jobber — Module Dashboard Ventes GHL, métriques "closing" et
"valeur moyenne won" (métriques 4 et 5). Décision de Justin (2026-08-01) : ces
deux métriques (et le compte de soumissions envoyées qui leur sert de base)
doivent venir de Jobber, la vraie source des soumissions chiffrées — le
passage en pipeline GHL CLOSING (utilisé au départ comme proxy) ne veut pas
dire qu'un prix a été envoyé (RDV À VENIR / SUIVI À FAIRE sans soumission
réelle), ce qui gonflait le dénominateur et écrasait artificiellement le taux
de closing. Les métriques 1-3 (contact même jour, visite bookée, rappel 48h)
restent basées sur GHL (voir ventes_ghl_source.py) — pas touchées ici.

Vendeur : directement le champ `salesperson` de Jobber, sans crossref vers GHL
(Justin a validé que les rapports Jobber suffisent tels quels). Un alias reste
nécessaire pour un seul cas connu : les soumissions de Justin Blaquiere
apparaissent sous le nom du compte compagnie "MAG Lavage À Pression" dans
Jobber, pas son nom personnel (voir SPEC_module_ventes_MAG.md et
config.yaml -> ghl_vendeurs_alias_jobber).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jobber_client import ClientJobber
from rapport_ventes import STATUTS_VENDUS
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
      amounts { subtotal }
      salesperson { name { full } }
      client { id firstName lastName }
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
    won: bool
    date_envoi: datetime


def obtenir_soumissions_jobber(
    client_jobber: ClientJobber, alias_vendeurs: dict, debut: date, fin: date
) -> list[SoumissionJobber]:
    """Soumissions dont le sentAt tombe dans [debut, fin] (bornes incluses, jours
    locaux MAG) — même contournement que obtenir_quotes_envoyees (le filtre
    `sentAt` de l'API Jobber est cassé côté serveur, voir
    valider_etape1_sync_soumissions.py) : filtre server-side sur `updatedAt`,
    puis `sentAt` côté client.

    Règle multi-soumissions (même client, plusieurs révisions/renvois dans la
    fenêtre — vu en vrai le 2026-08-01, ex. Ximena Arteaga avec 2 soumissions
    #621/#622) : regroupées par client et réduites à UNE soumission canonique
    via choisir_soumission_canonique (déjà validée dans le sync Jobber->GHL),
    jamais sommées ni comptées séparément — sinon le dénominateur du taux de
    closing est gonflé par des renvois qui ne sont pas de vrais nouveaux leads.
    """
    variables = {"debut": _vers_iso(debut), "fin": _vers_iso(fin, fin_de_journee=True)}
    nodes = _paginer(client_jobber, REQUETE_QUOTES_ENVOYEES_VENTES_GHL, variables)

    dans_fenetre = [n for n in nodes if (d := _vers_local(n["sentAt"])) is not None and debut <= d.date() <= fin]

    groupes: dict[str, list[dict]] = {}
    for n in dans_fenetre:
        c = n.get("client") or {}
        cle = c.get("id") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip().lower()
        groupes.setdefault(cle, []).append(n)

    resultats = []
    for quotes_groupe in groupes.values():
        n = choisir_soumission_canonique(quotes_groupe)
        c = n.get("client") or {}
        nom_client = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
        nom_brut = (n.get("salesperson") or {}).get("name", {}).get("full", "")
        vendeur = alias_vendeurs.get(nom_brut, nom_brut) or "(non assigné)"
        resultats.append(
            SoumissionJobber(
                numero=n["quoteNumber"],
                client=nom_client,
                vendeur=vendeur,
                montant=n["amounts"]["subtotal"] or 0.0,
                won=n["quoteStatus"] in STATUTS_VENDUS,
                date_envoi=_vers_local(n["sentAt"]),
            )
        )
    return resultats
