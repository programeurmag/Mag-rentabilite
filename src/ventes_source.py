"""
Source de données Jobber pour le module Ventes (soumissions / quotes).

Même approche que jobber_source.py : requêtes GraphQL paginées, avec les
fenêtres de dates converties en UTC à partir du fuseau MAG.

Champ vendeur : l'API Jobber n'expose PAS de champ "créateur de la soumission"
distinct sur le type Quote (vérifié par introspection du schéma) — seul le
champ `salesperson` (type User) existe directement sur Quote. Le rapport
Jobber "Sent by user" que Justin voit dans Reports -> Quotes vient d'un
historique d'activité interne, pas d'un champ GraphQL séparé. Sur les 62
soumissions exportées par Justin (Quotes_Report_1_of_1_2026-07-19.csv),
Salesperson == Sent by user dans 100% des cas -> `salesperson` est fiable
comme seul champ vendeur.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jobber_client import ClientJobber

FUSEAU_MAG = ZoneInfo("America/Montreal")


def _vers_date_locale(iso_datetime: str | None) -> date | None:
    if not iso_datetime:
        return None
    dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
    return dt.astimezone(FUSEAU_MAG).date()


def _vers_iso(jour: date, fin_de_journee: bool = False) -> str:
    heure = "23:59:59" if fin_de_journee else "00:00:00"
    dt_local = datetime.fromisoformat(f"{jour.isoformat()}T{heure}").replace(tzinfo=FUSEAU_MAG)
    return dt_local.astimezone(ZoneInfo("UTC")).isoformat()


@dataclass
class Soumission:
    numero: str
    client: str
    titre: str
    statut: str  # draft / awaiting_response / archived / approved / converted / changes_requested
    vendeur: str  # salesperson.name.full, ou "" si non assigné
    total: float
    date_creation: date | None
    date_envoi: date | None
    date_approbation: date | None
    date_conversion: date | None
    numeros_jobs: list[str]


REQUETE_QUOTES = """
query QuotesCreees($debut: ISO8601DateTime!, $fin: ISO8601DateTime!, $after: String) {
  quotes(filter: { createdAt: { after: $debut, before: $fin } }, first: 50, after: $after) {
    nodes {
      quoteNumber
      title
      quoteStatus
      createdAt
      sentAt
      lastTransitioned { approvedAt convertedAt }
      amounts { total }
      client { name }
      salesperson { name { full } }
      jobs(first: 5) { nodes { jobNumber } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _paginer(client: ClientJobber, requete: str, variables: dict, chemin: list):
    tous_les_nodes = []
    apres = None
    while True:
        data = client.executer(requete, {**variables, "after": apres})
        for cle in chemin:
            data = data[cle]
        tous_les_nodes.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        apres = data["pageInfo"]["endCursor"]
    return tous_les_nodes


def _noeud_vers_soumission(n: dict) -> Soumission:
    return Soumission(
        numero=n["quoteNumber"],
        client=n["client"]["name"] if n["client"] else "",
        titre=n["title"] or "",
        statut=n["quoteStatus"],
        vendeur=(n["salesperson"]["name"]["full"] if n.get("salesperson") else ""),
        total=n["amounts"]["total"] or 0.0,
        date_creation=_vers_date_locale(n["createdAt"]),
        date_envoi=_vers_date_locale(n["sentAt"]),
        date_approbation=_vers_date_locale((n.get("lastTransitioned") or {}).get("approvedAt")),
        date_conversion=_vers_date_locale((n.get("lastTransitioned") or {}).get("convertedAt")),
        numeros_jobs=[j["jobNumber"] for j in n["jobs"]["nodes"]],
    )


def obtenir_quotes_creees(client: ClientJobber, debut: date, fin: date) -> list[Soumission]:
    """Toutes les soumissions CRÉÉES dans [debut, fin] (bornes incluses, jours locaux MAG)."""
    variables = {"debut": _vers_iso(debut), "fin": _vers_iso(fin, fin_de_journee=True)}
    nodes = _paginer(client, REQUETE_QUOTES, variables, ["quotes"])
    return [_noeud_vers_soumission(n) for n in nodes]
