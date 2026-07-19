"""
Assemble les données brutes (jobs + timesheets) en un rapport hebdomadaire :
agrégats par job / par employé, et alertes automatiques (spec 1.6 et 1.7).

Ce module ne connaît pas la source des données (CSV ou API Jobber) : il ne
travaille qu'avec les dataclasses communes (Job, EntreeTimesheet) et le
résultat de calculer_attribution().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from attribution import ResultatAttribution, calculer_attribution, cout_mo_par_job, heures_par_job
from parseur import EntreeTimesheet, Job, deduire_etape, normaliser_compagnie


@dataclass
class LigneJob:
    numero: int
    client: str
    ville: str
    etape: str
    date_fermeture: date | None
    revenu: float
    materiaux: float
    heures_attribuees: float
    cout_mo: float
    marge: float
    dollars_heure: float
    marge_pct: float


@dataclass
class Alerte:
    type: str  # "dollars_heure_bas" / "timer_oublie" / "zero_punch_visite" / "job_zero_heure" / "non_attribue_eleve"
    message: str


@dataclass
class RapportSemaine:
    debut: date
    fin: date
    resultat: ResultatAttribution
    jobs_fermes: list  # list[LigneJob], triés du pire au meilleur $/h
    heures_punchees_total: float
    heures_attribuees_total: float  # tous jobs (ouverts + fermés)
    heures_non_attribuees_total: float
    revenu_ferme_total: float
    heures_fermes_total: float
    cout_mo_ferme_total: float
    pct_main_doeuvre: float  # coût MO / revenu, jobs fermés
    dollars_heure_global: float
    heures_par_employe: dict  # employe -> heures totales punchées (toutes, y compris non attribuées)
    alertes: list = field(default_factory=list)  # list[Alerte]


def _heures_totales_par_employe(entrees: list[EntreeTimesheet]) -> dict:
    totaux = {}
    for e in entrees:
        totaux[e.employe] = totaux.get(e.employe, 0.0) + e.heures
    return totaux


def _heures_punch_direct_par_employe(resultat: ResultatAttribution) -> dict:
    totaux = {}
    for ligne in resultat.lignes:
        if ligne.source == "Punch direct":
            totaux[ligne.employe] = totaux.get(ligne.employe, 0.0) + ligne.heures
    return totaux


def construire_rapport(
    jobs: dict,
    jobs_fermes_fenetre: dict,
    entrees: list,
    config: dict,
    debut: date,
    fin: date,
) -> RapportSemaine:
    compte_compagnie = config.get("compte_compagnie", "MAG Lavage À Pression")
    seuil_timer = config.get("seuil_timer_oublie", 12)
    seuil_alerte_dh = config.get("seuil_alerte_dollars_heure", 80)
    roles_vente = set(config.get("roles_vente", []))
    ex_employes = set(config.get("ex_employes", []))
    taux_horaires = config.get("taux_horaires", {})
    facteur_charges = config.get("facteur_charges", 1.0)

    resultat = calculer_attribution(entrees, jobs, compte_compagnie=compte_compagnie)
    totaux_heures = heures_par_job(resultat)
    totaux_cout = cout_mo_par_job(resultat, taux_horaires, facteur_charges)

    lignes_jobs = []
    for numero, job in jobs_fermes_fenetre.items():
        heures = totaux_heures.get(numero, 0.0)
        cout_mo = totaux_cout.get(numero, 0.0)
        materiaux = 0.0  # à remplir manuellement dans l'Excel (colonne jaune, voir spec 1.4)
        marge = job.revenu_total - cout_mo - materiaux
        dollars_heure = job.revenu_total / heures if heures else 0.0
        marge_pct = marge / job.revenu_total if job.revenu_total else 0.0
        lignes_jobs.append(
            LigneJob(
                numero=numero,
                client=job.client,
                ville=job.ville,
                etape=deduire_etape(job.line_items),
                date_fermeture=job.date_fermeture,
                revenu=job.revenu_total,
                materiaux=materiaux,
                heures_attribuees=heures,
                cout_mo=cout_mo,
                marge=marge,
                dollars_heure=dollars_heure,
                marge_pct=marge_pct,
            )
        )
    lignes_jobs.sort(key=lambda l: l.dollars_heure)  # pire $/h en premier

    heures_punchees_total = sum(e.heures for e in entrees)
    heures_attribuees_total = sum(totaux_heures.values())
    heures_non_attribuees_total = sum(h for _, _, h, _ in resultat.non_attribue)
    heures_fermes_total = sum(l.heures_attribuees for l in lignes_jobs)
    revenu_ferme_total = sum(l.revenu for l in lignes_jobs)
    dollars_heure_global = revenu_ferme_total / heures_fermes_total if heures_fermes_total else 0.0
    cout_mo_ferme_total = sum(l.cout_mo for l in lignes_jobs)
    pct_main_doeuvre = cout_mo_ferme_total / revenu_ferme_total if revenu_ferme_total else 0.0

    heures_employe = _heures_totales_par_employe(entrees)
    heures_punch_direct_employe = _heures_punch_direct_par_employe(resultat)

    alertes = []

    # 1. Jobs sous le seuil $/h (seulement ceux avec des heures attribuées :
    #    un job à 0h a sa propre alerte plus bas, pas besoin de doublon)
    for l in lignes_jobs:
        if l.heures_attribuees > 0 and l.dollars_heure < seuil_alerte_dh:
            alertes.append(
                Alerte(
                    "dollars_heure_bas",
                    f"Job #{l.numero} ({l.client}) : {l.dollars_heure:.0f} $/h "
                    f"(sous le seuil de {seuil_alerte_dh} $/h)",
                )
            )

    # 2. Timers oubliés
    lignes_anomalies = [l for l in resultat.lignes if l.anomalie]
    non_attribue_anomalies = [
        (e, d, h) for e, d, h, a in resultat.non_attribue if a
    ]
    vus = set()
    for l in lignes_anomalies:
        cle = (l.employe, l.date_entree)
        if cle in vus:
            continue
        vus.add(cle)
        alertes.append(
            Alerte(
                "timer_oublie",
                f"⚠ {l.employe}, {l.date_entree.strftime('%d %b')} — job #{l.job_num}",
            )
        )
    for e, d, h in non_attribue_anomalies:
        cle = (e, d)
        if cle in vus:
            continue
        vus.add(cle)
        alertes.append(Alerte("timer_oublie", f"⚠ {e}, {d.strftime('%d %b')} — General"))

    # 3. Employés terrain à 0% de punches sur visites
    for employe, heures_totales in heures_employe.items():
        if employe in roles_vente or employe in ex_employes:
            continue
        if normaliser_compagnie(employe) == normaliser_compagnie(compte_compagnie):
            continue
        if heures_totales > 0 and heures_punch_direct_employe.get(employe, 0.0) == 0.0:
            alertes.append(
                Alerte(
                    "zero_punch_visite",
                    f"{employe} : 0% de punches sur visites cette semaine "
                    f"({heures_totales:.1f} h, toutes en General) — problème d'assignation ou de téléphone?",
                )
            )

    # 4. Jobs fermés avec 0 heure attribuée
    for l in lignes_jobs:
        if l.heures_attribuees == 0:
            alertes.append(
                Alerte("job_zero_heure", f"Job #{l.numero} ({l.client}) : fermé, 0 heure attribuée")
            )

    # 5. % d'heures non attribuées trop élevé
    pct_non_attribue = (
        heures_non_attribuees_total / heures_punchees_total if heures_punchees_total else 0
    )
    if pct_non_attribue > 0.35:
        alertes.append(
            Alerte(
                "non_attribue_eleve",
                f"{pct_non_attribue:.0%} des heures de la semaine ne sont pas attribuées à un job "
                f"({heures_non_attribuees_total:.1f} h sur {heures_punchees_total:.1f} h)",
            )
        )

    return RapportSemaine(
        debut=debut,
        fin=fin,
        resultat=resultat,
        jobs_fermes=lignes_jobs,
        heures_punchees_total=heures_punchees_total,
        heures_attribuees_total=heures_attribuees_total,
        heures_non_attribuees_total=heures_non_attribuees_total,
        revenu_ferme_total=revenu_ferme_total,
        heures_fermes_total=heures_fermes_total,
        cout_mo_ferme_total=cout_mo_ferme_total,
        pct_main_doeuvre=pct_main_doeuvre,
        dollars_heure_global=dollars_heure_global,
        heures_par_employe=heures_employe,
        alertes=alertes,
    )
