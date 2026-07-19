"""
Parseur des exports CSV Jobber (Timesheets et One-off jobs).

En phase 1, la source de données est constituée de deux exports CSV manuels.
En phase 2 (étape 2 de la spec), ce module sera remplacé par des requêtes
GraphQL vers l'API Jobber, mais la structure de données retournée
(TimesheetEntry, Job) reste la même pour ne pas casser attribution.py.
"""

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


# Format de date utilisé par les exports Jobber (ex. "Jul 07, 2026")
FORMAT_DATE_JOBBER = "%b %d, %Y"

# Regex pour extraire le numéro de job depuis la colonne "Working on"
# (ex. "Job #198 - 9139-0880 Québec inc." -> 198)
RE_JOB_NUM = re.compile(r"Job #(\d+)")


def normaliser_nom(nom: str) -> str:
    """
    Nettoie un nom d'employé pour permettre les comparaisons fiables.

    La spec signale explicitement des espaces en fin de nom dans Jobber
    (ex. "Michael "), qui feraient échouer un match exact sans ce nettoyage.
    """
    return " ".join(nom.strip().split())


def normaliser_compagnie(nom: str) -> str:
    """Normalise un nom pour comparer indépendamment des accents/majuscules."""
    sans_accents = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    return sans_accents.strip().lower()


def parser_date_jobber(valeur: str) -> Optional[date]:
    """Parse une date au format Jobber ('Jul 07, 2026'). Retourne None si vide."""
    valeur = (valeur or "").strip()
    if not valeur:
        return None
    return datetime.strptime(valeur, FORMAT_DATE_JOBBER).date()


@dataclass
class EntreeTimesheet:
    """Une ligne de punch dans le rapport Timesheets."""

    employe: str
    date_entree: date
    heures: float
    working_on: str
    job_num: Optional[int]  # None si punch "General"
    note: str = ""
    client: str = ""
    anomalie: bool = False  # True si heures > seuil_timer_oublie (timer oublié)


@dataclass
class Job:
    """Un job (fermé ou non) tel que décrit dans le rapport One-off jobs."""

    numero: int
    client: str
    ville: str = ""
    titre: str = ""
    line_items: str = ""
    date_creation: Optional[date] = None
    date_debut_cedulee: Optional[date] = None
    date_fermeture: Optional[date] = None
    revenu_total: float = 0.0
    temps_tracke_jobber: float = 0.0  # colonne "Time tracked", pour référence seulement
    employes_assignes: list = field(default_factory=list)
    dans_rapport: bool = True  # False = job vu seulement via les timesheets (job encore ouvert)

    def actif_le(self, jour: date) -> bool:
        """
        Un job est considéré "actif" un jour donné s'il était cédulé/en cours
        ce jour-là : entre son début (cédulé, ou création si pas de date cédulée)
        et sa fermeture (inclusivement des deux bords).
        """
        debut = self.date_debut_cedulee or self.date_creation
        fin = self.date_fermeture
        if debut is None or fin is None:
            return False
        return debut <= jour <= fin


def parser_timesheets(chemin_csv: str, seuil_timer_oublie: float = 12.0) -> list[EntreeTimesheet]:
    """Lit le rapport Timesheets et retourne la liste des entrées de punch (hors totaux)."""
    entrees = []
    with open(chemin_csv, encoding="utf-8-sig", newline="") as f:
        lignes = f.readlines()

    # Le fichier a un bloc "Totals" avant la vraie table ; on cherche la ligne d'en-tête réelle
    idx_entete = next(
        i for i, ligne in enumerate(lignes) if ligne.startswith("Name,Date,Start time")
    )
    lecteur = csv.DictReader(lignes[idx_entete:])

    for row in lecteur:
        nom = (row.get("Name") or "").strip()
        if not nom or nom.lower().startswith("report totals"):
            continue

        date_entree = parser_date_jobber(row["Date"])
        heures = float(row["Hours"] or 0)
        working_on = (row.get("Working on") or "").strip()
        match = RE_JOB_NUM.search(working_on)
        job_num = int(match.group(1)) if match else None

        entrees.append(
            EntreeTimesheet(
                employe=normaliser_nom(nom),
                date_entree=date_entree,
                heures=heures,
                working_on=working_on,
                job_num=job_num,
                note=(row.get("Note") or "").strip(),
                client=(row.get("Client name") or "").strip(),
                anomalie=heures > seuil_timer_oublie,
            )
        )

    return entrees


def _parser_liste_employes(valeur: str) -> list[str]:
    """
    Parse la colonne "Visits assigned to", qui liste les employés sous forme
    "A, B, Charly Pearson, and Kevin Bierry" (séparateur virgule + "and" avant le dernier).
    """
    valeur = (valeur or "").strip()
    if not valeur:
        return []
    valeur = valeur.replace(", and ", ", ").replace(" and ", ", ")
    return [normaliser_nom(n) for n in valeur.split(",") if n.strip()]


def parser_jobs(chemin_csv: str) -> dict[int, Job]:
    """Lit le rapport One-off jobs et retourne un dict {numero_job: Job}."""
    jobs: dict[int, Job] = {}
    with open(chemin_csv, encoding="utf-8-sig", newline="") as f:
        lecteur = csv.DictReader(f)
        for row in lecteur:
            numero_str = (row.get("Job #") or "").strip()
            if not numero_str:
                continue
            numero = int(numero_str)
            jobs[numero] = Job(
                numero=numero,
                client=(row.get("Client name") or "").strip(),
                ville=(row.get("Service city") or row.get("Billing city") or "").strip(),
                titre=(row.get("Title") or "").strip(),
                line_items=(row.get("Line items") or "").strip(),
                date_creation=parser_date_jobber(row.get("Created date", "")),
                date_debut_cedulee=parser_date_jobber(row.get("Scheduled start date", "")),
                date_fermeture=parser_date_jobber(row.get("Closed date", "")),
                revenu_total=float(row.get("Total revenue ($)") or 0),
                temps_tracke_jobber=float(row.get("Time tracked") or 0),
                employes_assignes=_parser_liste_employes(row.get("Visits assigned to", "")),
                dans_rapport=True,
            )
    return jobs


# Mots-clés pour déduire l'étape à partir des line items, par ordre de priorité
# (un job peut avoir plusieurs line items ; on prend la première étape qui matche)
_MOTS_CLES_ETAPE = [
    ("remise à neuf", "Remise à neuf"),
    ("redressement", "Redressement"),
    ("nettoyage", "Nettoyage"),
    ("sable", "Sable"),
    ("dalle", "Réfection dalles"),
]


def deduire_etape(line_items: str) -> str:
    """Déduit l'étape des travaux (remise à neuf / redressement / etc.) des line items."""
    texte = normaliser_compagnie(line_items)  # réutilise le retrait d'accents/minuscule
    for mot_cle, etape in _MOTS_CLES_ETAPE:
        if mot_cle in texte:
            return etape
    return "Autre"
