"""
Source de données Rapport de chantier (Google Form -> Google Sheets, Module 3).

Une ligne du Sheets = une soumission du Form remplie par un chauffeur (spec
section 1) : un job visité, un jour donné. Le Sheets entier est relu à chaque
exécution (pas seulement la fenêtre de la semaine) : un job à cheval sur
plusieurs semaines a besoin de TOUTES ses soumissions pour accumuler ses
coûts correctement (spec 4c), et le Sheets lui-même est la seule mémoire de
ces données (contrairement aux timesheets Jobber, interrogeables par plage
de dates via l'API).

Le matching des colonnes se fait par mot-clé dans l'en-tête (insensible aux
accents/majuscules) plutôt que par position, pour rester robuste aux petites
variations de libellé du Form (espace, majuscule, emoji dans "✅ Terminé"...).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time

from dateutil import parser as dateutil_parser

from parseur import normaliser_compagnie, normaliser_nom

# Lecture-écriture (pas .readonly) : requis depuis le Module 4 (dashboard_sheets.py),
# qui réutilise ce même compte de service pour écrire dans MAG_Dashboard_Data. La
# protection en écriture du Sheets de réponses du Form reste le partage Google Drive
# (le compte de service y est en Lecteur, pas en Éditeur) — voir SPEC_module_dashboard_MAG.md
# section 1.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

STATUT_TERMINE = "Terminé"
STATUT_ON_REVIENT = "On revient"

# mot-clé normalisé (sans accent, minuscule) -> champ interne. Premher mot-clé
# trouvé dans l'en-tête qui matche gagne (voir _index_colonnes).
_MOTS_CLES_COLONNES = [
    ("horodateur", "horodatage"),
    ("timestamp", "horodatage"),
    ("camion", "truck"),
    ("chauffeur", "truck"),
    ("chef d'equipe", "truck"),
    ("chef d equipe", "truck"),
    ("equipe", "truck"),
    ("no de job", "job_num"),
    ("numero de job", "job_num"),
    ("# de job", "job_num"),
    ("job jobber", "job_num"),
    ("client", "client_adresse"),
    ("adresse", "client_adresse"),
    ("present", "gars_presents"),
    ("gars", "gars_presents"),
    ("arrivee", "heure_arrivee"),
    ("depart", "heure_depart"),
    ("poussiere", "bacs_poussiere_pierre"),
    ("sac", "sacs_polymere"),
    ("scellant", "litres_scellant"),
    ("autre", "autres_materiaux"),
    ("statut", "statut"),
    ("imprevu", "notes"),
    ("note", "notes"),
]


@dataclass
class SoumissionChantier:
    """Une soumission du Form = un job visité par un truck, un jour donné."""

    ligne: int  # numéro de ligne dans le Sheets (retrouver une soumission problématique)
    horodatage: datetime | None
    truck: str
    job_num: int | None
    client_adresse: str
    gars_presents: list[str]
    heure_arrivee: time | None
    heure_depart: time | None
    sacs_polymere: float
    litres_scellant: float
    bacs_poussiere_pierre: float
    autres_materiaux: str
    statut: str  # STATUT_TERMINE / STATUT_ON_REVIENT / "" si illisible
    notes: str

    @property
    def date_jour(self) -> date | None:
        """Jour de présence : le chauffeur remplit le Form avant de quitter le site (spec 1)."""
        return self.horodatage.date() if self.horodatage else None

    @property
    def duree_heures(self) -> float:
        if not (self.heure_arrivee and self.heure_depart):
            return 0.0
        minutes = (self.heure_depart.hour * 60 + self.heure_depart.minute) - (
            self.heure_arrivee.hour * 60 + self.heure_arrivee.minute
        )
        return max(minutes, 0) / 60

    @property
    def heures_personnes(self) -> float:
        return self.duree_heures * len(self.gars_presents)


def obtenir_credentials_gspread(json_credentials: str):
    """Construit les credentials Google à partir du JSON du compte de service (voir spec section 2)."""
    from google.oauth2.service_account import Credentials

    info = json.loads(json_credentials)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def obtenir_client_gspread(json_credentials: str):
    import gspread

    return gspread.authorize(obtenir_credentials_gspread(json_credentials))


def _normaliser_entete(valeur: str) -> str:
    return normaliser_compagnie(valeur)


def _index_colonnes(entete: list[str]) -> dict[str, int]:
    """Associe chaque champ interne à l'index (0-based) de sa colonne, par mot-clé."""
    index: dict[str, int] = {}
    for i, libelle in enumerate(entete):
        libelle_norm = _normaliser_entete(libelle)
        for mot_cle, champ in _MOTS_CLES_COLONNES:
            if champ in index:
                continue
            if mot_cle in libelle_norm:
                index[champ] = i
                break
    return index


def _valeur(ligne: list[str], index: dict[str, int], champ: str) -> str:
    i = index.get(champ)
    if i is None or i >= len(ligne):
        return ""
    return (ligne[i] or "").strip()


def _parser_horodatage(valeur: str) -> datetime | None:
    if not valeur:
        return None
    try:
        # Sheets en français (Québec) exporte l'horodateur en JJ/MM/AAAA, pas MM/JJ/AAAA.
        return dateutil_parser.parse(valeur, dayfirst=True)
    except (ValueError, OverflowError):
        return None


def _parser_heure(valeur: str) -> time | None:
    if not valeur:
        return None
    try:
        return dateutil_parser.parse(valeur).time()
    except (ValueError, OverflowError):
        return None


def _parser_liste_gars(valeur: str) -> list[str]:
    if not valeur:
        return []
    return [normaliser_nom(n) for n in valeur.split(",") if n.strip()]


def _parser_nombre(valeur: str) -> float:
    valeur = (valeur or "").strip().replace(",", ".")
    if not valeur:
        return 0.0
    try:
        return float(valeur)
    except ValueError:
        return 0.0


def _parser_job_num(valeur: str) -> int | None:
    chiffres = "".join(c for c in valeur if c.isdigit())
    return int(chiffres) if chiffres else None


def _parser_statut(valeur: str) -> str:
    norm = _normaliser_entete(valeur)
    if "termine" in norm:
        return STATUT_TERMINE
    if "revient" in norm:
        return STATUT_ON_REVIENT
    return ""


def _ligne_vers_soumission(ligne_brute: list[str], index: dict[str, int], num_ligne: int) -> SoumissionChantier | None:
    if not any((v or "").strip() for v in ligne_brute):
        return None

    return SoumissionChantier(
        ligne=num_ligne,
        horodatage=_parser_horodatage(_valeur(ligne_brute, index, "horodatage")),
        truck=_valeur(ligne_brute, index, "truck"),
        job_num=_parser_job_num(_valeur(ligne_brute, index, "job_num")),
        client_adresse=_valeur(ligne_brute, index, "client_adresse"),
        gars_presents=_parser_liste_gars(_valeur(ligne_brute, index, "gars_presents")),
        heure_arrivee=_parser_heure(_valeur(ligne_brute, index, "heure_arrivee")),
        heure_depart=_parser_heure(_valeur(ligne_brute, index, "heure_depart")),
        sacs_polymere=_parser_nombre(_valeur(ligne_brute, index, "sacs_polymere")),
        litres_scellant=_parser_nombre(_valeur(ligne_brute, index, "litres_scellant")),
        bacs_poussiere_pierre=_parser_nombre(_valeur(ligne_brute, index, "bacs_poussiere_pierre")),
        autres_materiaux=_valeur(ligne_brute, index, "autres_materiaux"),
        statut=_parser_statut(_valeur(ligne_brute, index, "statut")),
        notes=_valeur(ligne_brute, index, "notes"),
    )


def parser_valeurs_sheet(valeurs: list[list[str]]) -> list[SoumissionChantier]:
    """Parse les valeurs brutes (get_all_values()) en liste de SoumissionChantier."""
    if not valeurs:
        return []
    entete, *lignes = valeurs
    index = _index_colonnes(entete)
    soumissions = []
    for i, ligne_brute in enumerate(lignes, start=2):  # ligne 1 = en-tête
        s = _ligne_vers_soumission(ligne_brute, index, i)
        if s is not None:
            soumissions.append(s)
    return soumissions


def lire_soumissions(sheet_id: str, client) -> list[SoumissionChantier]:
    """Lit TOUTES les soumissions du Sheets de réponses (pas de fenêtre de dates, voir docstring du module)."""
    feuille = client.open_by_key(sheet_id).get_worksheet(0)
    valeurs = feuille.get_all_values()
    return parser_valeurs_sheet(valeurs)


def obtenir_soumissions_avec_degradation(env, config: dict) -> list[SoumissionChantier]:
    """
    Lit le Sheets de réponses, avec dégradation gracieuse (comme
    analyse_claude.analyser_semaine) : si le compte de service ou l'ID du
    Sheets est absent/invalide, ou que l'appel échoue pour n'importe quelle
    raison, retourne [] plutôt que de bloquer le script appelant (rapport
    hebdo OU rappel matinal).
    """
    json_credentials = env.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = config.get("google_sheet_id_chantier")
    if not json_credentials or not sheet_id:
        print("Rapport de chantier : compte de service ou Sheet ID absent, section ignorée.")
        return []
    try:
        client = obtenir_client_gspread(json_credentials)
        soumissions = lire_soumissions(sheet_id, client)
        print(f"Rapport de chantier : {len(soumissions)} soumission(s) lue(s).")
        return soumissions
    except Exception as e:  # noqa: BLE001 — jamais bloquant pour le reste du script
        print(f"Rapport de chantier : échec de lecture ({type(e).__name__}: {e}), section ignorée.")
        return []
