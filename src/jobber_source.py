"""
Source de données Jobber (remplace parseur.py en phase 2).

Produit exactement les mêmes structures (EntreeTimesheet, Job) que parseur.py,
pour que attribution.py fonctionne sans aucune modification, peu importe la
source (CSV ou API).

Différences avec la version CSV, permises par les données plus riches de l'API :
  - TimeSheetEntry.job est un lien direct vers le Job (fini le parsing du texte
    libre "Working on Job #123 - Client X").
  - TimeSheetEntry.ticking (chronomètre encore actif) s'ajoute au seuil de
    12h comme signal d'anomalie « timer oublié ».
  - Le pool de jobs candidats à la répartition "General" n'est plus limité aux
    jobs FERMÉS (contrainte du rapport CSV, qui ne contenait que des jobs
    fermés) : l'API donne le revenu (`total`) même pour un job encore ouvert,
    donc un job ouvert mais actif cette semaine peut aussi recevoir une part
    de répartition. C'est plus fidèle au texte de la spec (1.3) que la version
    CSV, où seuls les jobs fermés pouvaient être candidats.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from jobber_client import ClientJobber
from parseur import EntreeTimesheet, Job, normaliser_nom

FUSEAU_MAG = ZoneInfo("America/Montreal")

TAILLE_PAGE = 50


def _vers_date_locale(iso_datetime: str | None) -> date | None:
    """Convertit un ISO8601DateTime (UTC) Jobber en date locale (fuseau MAG)."""
    if not iso_datetime:
        return None
    dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
    return dt.astimezone(FUSEAU_MAG).date()


def _vers_iso(jour: date, fin_de_journee: bool = False) -> str:
    """Convertit une date locale en ISO8601DateTime UTC pour les filtres GraphQL."""
    heure = "23:59:59" if fin_de_journee else "00:00:00"
    dt_local = datetime.fromisoformat(f"{jour.isoformat()}T{heure}").replace(tzinfo=FUSEAU_MAG)
    return dt_local.astimezone(ZoneInfo("UTC")).isoformat()


# Tailles de pagination volontairement petites : le coût d'une requête GraphQL
# Jobber avec des connexions imbriquées est multiplicatif (voir
# https://developer.getjobber.com/docs/using_jobbers_api/api_rate_limits).
# jobs(20) x visits(10) x assignedUsers(10) reste sous la limite de 10 000 points.
_CHAMPS_JOB = """
      jobNumber
      title
      total
      completedAt
      startAt
      createdAt
      client { name }
      property { address { city } }
      lineItems(first: 10) { nodes { name } }
      visits(first: 10) {
        nodes {
          assignedUsers(first: 10) { nodes { name { full } } }
        }
      }
"""

# Deux filtres, fusionnés ensuite par obtenir_jobs_semaine() :
#  - visitsScheduledBetween : le pool de jobs "candidats" à la répartition
#    General (spec 1.3), ouverts ou fermés.
#  - completedAt : garantit qu'un job fermé dans la fenêtre mais SANS visite
#    cédulée dedans (ex. job ponctuel sans activité cette semaine-là) apparaît
#    quand même dans le rapport, avec 0 heure attribuée (spec 1.7).
REQUETE_JOBS_CANDIDATS = f"""
query JobsCandidats($debut: ISO8601DateTime!, $fin: ISO8601DateTime!, $after: String) {{
  jobs(filter: {{ visitsScheduledBetween: {{ after: $debut, before: $fin }} }},
       first: 20, after: $after) {{
    nodes {{{_CHAMPS_JOB}}}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

REQUETE_JOBS_FERMES = f"""
query JobsFermes($debut: ISO8601DateTime!, $fin: ISO8601DateTime!, $after: String) {{
  jobs(filter: {{ completedAt: {{ after: $debut, before: $fin }} }},
       first: 20, after: $after) {{
    nodes {{{_CHAMPS_JOB}}}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

REQUETE_TIMESHEETS = """
query TimesheetsSemaine($debut: ISO8601DateTime!, $fin: ISO8601DateTime!, $after: String) {
  timeSheetEntries(filter: { startAt: { after: $debut, before: $fin } },
                    first: 50, after: $after) {
    nodes {
      startAt
      endAt
      finalDuration
      ticking
      note
      user { name { full } }
      job { jobNumber client { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _paginer(client: ClientJobber, requete: str, variables: dict, chemin: list):
    """Exécute une requête paginée (Relay-style) et retourne tous les nodes."""
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


def _noeud_vers_job(n: dict) -> Job:
    employes = {
        normaliser_nom(u["name"]["full"])
        for v in n["visits"]["nodes"]
        for u in v["assignedUsers"]["nodes"]
    }
    return Job(
        numero=n["jobNumber"],
        client=n["client"]["name"] if n["client"] else "",
        ville=((n.get("property") or {}).get("address") or {}).get("city", "") or "",
        titre=n["title"] or "",
        line_items=", ".join(li["name"] for li in n["lineItems"]["nodes"]),
        date_creation=_vers_date_locale(n["createdAt"]),
        date_debut_cedulee=_vers_date_locale(n["startAt"]),
        date_fermeture=_vers_date_locale(n["completedAt"]),
        revenu_total=n["total"] or 0.0,
        employes_assignes=list(employes),
        # Seuls les jobs FERMÉS sont candidats à la répartition des heures
        # "General" (voir _trouver_candidats dans attribution.py), pour rester
        # fidèle à la logique validée à l'étape 1 sur les CSV. Un job encore
        # ouvert peut quand même recevoir des heures en punch direct.
        dans_rapport=n["completedAt"] is not None,
    )


def obtenir_jobs_semaine(client: ClientJobber, debut: date, fin: date) -> dict[int, Job]:
    """
    Fusionne deux ensembles de jobs pour la fenêtre [debut, fin] :
      1. Jobs candidats à la répartition General (au moins une visite cédulée
         dans la fenêtre), ouverts ou fermés (spec 1.3).
      2. Jobs fermés dans la fenêtre, même sans visite cédulée dedans (spec
         1.2 point 1 + 1.7 : un job fermé à 0 heure doit quand même apparaître).
    """
    variables = {"debut": _vers_iso(debut), "fin": _vers_iso(fin, fin_de_journee=True)}

    jobs: dict[int, Job] = {}
    for n in _paginer(client, REQUETE_JOBS_CANDIDATS, variables, ["jobs"]):
        jobs[n["jobNumber"]] = _noeud_vers_job(n)
    for n in _paginer(client, REQUETE_JOBS_FERMES, variables, ["jobs"]):
        jobs.setdefault(n["jobNumber"], _noeud_vers_job(n))
    return jobs


def jobs_fermes_dans_fenetre(jobs: dict[int, Job], debut: date, fin: date) -> dict[int, Job]:
    """Sous-ensemble des jobs (obtenus via obtenir_jobs_semaine) fermés dans la fenêtre."""
    return {
        num: j
        for num, j in jobs.items()
        if j.date_fermeture is not None and debut <= j.date_fermeture <= fin
    }


def obtenir_timesheets_semaine(
    client: ClientJobber, debut: date, fin: date, seuil_timer_oublie: float = 12.0
) -> list[EntreeTimesheet]:
    """TimeSheetEntries de la fenêtre [debut, fin], mappées vers EntreeTimesheet."""
    variables = {"debut": _vers_iso(debut), "fin": _vers_iso(fin, fin_de_journee=True)}
    nodes = _paginer(client, REQUETE_TIMESHEETS, variables, ["timeSheetEntries"])

    entrees = []
    for n in nodes:
        heures = (n["finalDuration"] or 0) / 3600
        job = n.get("job")
        entrees.append(
            EntreeTimesheet(
                employe=normaliser_nom(n["user"]["name"]["full"]),
                date_entree=_vers_date_locale(n["startAt"]),
                heures=heures,
                working_on=f"Job #{job['jobNumber']}" if job else "General",
                job_num=job["jobNumber"] if job else None,
                note=n.get("note") or "",
                client=(job["client"]["name"] if job and job["client"] else ""),
                # Anomalie si >seuil OU si le chronomètre tourne encore
                # (signal direct de l'API, en plus du seuil d'heures).
                anomalie=(heures > seuil_timer_oublie) or bool(n.get("ticking")),
            )
        )
    return entrees
