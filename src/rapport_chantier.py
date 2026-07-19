"""
Module 3 — logique d'intégration du Rapport de chantier (Form) dans le calcul
de rentabilité (spec sections 4, 4b, 4c, 5).

Hiérarchie des sources pour les heures/coûts d'un job (spec section 4) :
  1. Rapport de chantier (Form) = source primaire, si au moins une soumission
     matche le job (voir matcher_soumissions).
  2. Punchs Jobber = validation croisée seulement (écart affiché en alerte,
     jamais utilisés pour le calcul si le Form est présent).
  3. Fallback : aucun rapport de chantier pour le job -> rapport.py retombe
     sur attribution.py (ancienne logique), ce module ne s'en occupe pas.

Ce module ne connaît pas non plus la source des jobs/timesheets (CSV ou API) :
il ne travaille qu'avec les dataclasses communes (Job, EntreeTimesheet) et la
liste de SoumissionChantier déjà lue par form_chantier_source.py.
"""

from __future__ import annotations

import difflib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from form_chantier_source import STATUT_ON_REVIENT, STATUT_TERMINE, SoumissionChantier
from parseur import EntreeTimesheet, Job, normaliser_nom

SOURCE_CHANTIER = "Rapport chantier"


@dataclass
class LigneAttributionChantier:
    """Équivalent de attribution.LigneAttribution, dérivée du Form (source primaire)."""

    job_num: int
    employe: str
    date_entree: date
    heures: float
    source: str = SOURCE_CHANTIER
    anomalie: bool = False


@dataclass
class JobChantier:
    """Agrégat d'un job à partir de TOUTES ses soumissions Form, peu importe la date (spec 4c)."""

    job_num: int
    client: str
    soumissions: list  # list[SoumissionChantier], triées par horodatage
    heures_personnes: float
    cout_mo: float
    materiaux: float
    overhead: float
    trucks: list  # list[str]
    statut_form_dernier: str  # STATUT_TERMINE / STATUT_ON_REVIENT / ""
    date_derniere_soumission: date | None
    complete: bool
    valeur: float  # job.revenu_total, pour le % de burn
    couts_accumules: float
    pct_burn: float
    alerte_burn: bool
    ecart_heures: dict = field(default_factory=dict)  # {date: (heures_form, heures_jobber, ecart_pct)}


@dataclass
class ResultatChantier:
    lignes_attribution: list = field(default_factory=list)  # list[LigneAttributionChantier]
    jobs: dict = field(default_factory=dict)  # {job_num: JobChantier} — seulement jobs matchés
    alertes: list = field(default_factory=list)  # list[(type, message)]


def fusionner_taux(config: dict) -> dict:
    """Fusionne les taux Jobber (taux_horaires) et les employés hors Jobber (employes_hors_jobber)."""
    taux = dict(config.get("taux_horaires", {}) or {})
    taux.update(config.get("employes_hors_jobber", {}) or {})
    return taux


def _heures_truck_jour(soumissions: list) -> dict:
    """Heures totales d'un camion, un jour donné, tous jobs confondus (spec 4b, base de la répartition)."""
    totaux = defaultdict(float)
    for s in soumissions:
        if s.date_jour is not None:
            totaux[(s.truck, s.date_jour)] += s.duree_heures
    return dict(totaux)


def matcher_soumissions(soumissions: list, jobs: dict) -> tuple:
    """
    Sépare les soumissions dont le no de job matche un job Jobber connu des
    soumissions orphelines. Une fausse association coûterait plus cher qu'une
    absence de données : le matching flou sur client/adresse (spec 7.2) ne
    sert qu'à SUGGÉRER une correction dans l'alerte, jamais à assigner
    automatiquement les coûts.

    Retourne (soumissions_matchees, [(soumission, suggestion_str), ...]).
    """
    noms_jobs = {num: f"{j.client} {j.ville}".strip() for num, j in jobs.items()}

    matchees = []
    orphelines = []
    for s in soumissions:
        if s.job_num is not None and s.job_num in jobs:
            matchees.append(s)
            continue
        suggestion = ""
        if s.client_adresse and noms_jobs:
            meilleur = difflib.get_close_matches(s.client_adresse, list(noms_jobs.values()), n=1, cutoff=0.5)
            if meilleur:
                num_suggere = next(num for num, nom in noms_jobs.items() if nom == meilleur[0])
                suggestion = f"job #{num_suggere} ({meilleur[0]}) ?"
        orphelines.append((s, suggestion))
    return matchees, orphelines


def _chevauchement(a: SoumissionChantier, b: SoumissionChantier) -> bool:
    """True si les plages [arrivée, départ] de deux soumissions se chevauchent."""
    if not (a.heure_arrivee and a.heure_depart and b.heure_arrivee and b.heure_depart):
        return True  # heure manquante : on ne peut pas exclure le chevauchement, mieux vaut alerter
    return a.heure_arrivee < b.heure_depart and b.heure_arrivee < a.heure_depart


def _alertes_double_truck(soumissions: list) -> list:
    """Même employé rapporté présent sur deux trucks en même temps (spec 5)."""
    par_jour = defaultdict(list)
    for s in soumissions:
        if s.date_jour is not None:
            par_jour[s.date_jour].append(s)

    alertes = []
    vus = set()
    for jour, subs in par_jour.items():
        for i, s1 in enumerate(subs):
            for s2 in subs[i + 1 :]:
                if s1.truck == s2.truck:
                    continue
                communs = set(s1.gars_presents) & set(s2.gars_presents)
                if not communs or not _chevauchement(s1, s2):
                    continue
                for employe in communs:
                    cle = (employe, jour, frozenset({s1.truck, s2.truck}))
                    if cle in vus:
                        continue
                    vus.add(cle)
                    alertes.append(
                        (
                            "chantier_double_truck",
                            f"{employe}, {jour.strftime('%d %b')} : rapporté présent sur "
                            f"{s1.truck} ET {s2.truck} en même temps",
                        )
                    )
    return alertes


def calculer_chantier(
    soumissions: list,
    jobs: dict,
    entrees: list,
    config: dict,
) -> ResultatChantier:
    """Calcule heures/coûts/matériaux/overhead par job à partir des soumissions Form (spec 4/4b/4c)."""
    resultat = ResultatChantier()
    if not soumissions:
        return resultat

    taux = fusionner_taux(config)
    taux_defaut_hors_jobber = config.get("taux_horaire_defaut_hors_jobber", 0)
    facteur_charges = config.get("facteur_charges", 1.0)
    couts_mat = config.get("couts_materiaux", {}) or {}
    cout_sac = couts_mat.get("sac_polymere", 0)
    cout_scellant = couts_mat.get("scellant_litre", 0)
    cout_poussiere = couts_mat.get("poussiere_pierre", 0)
    overhead_par_camion = config.get("overhead_quotidien_total", 0) / max(config.get("nb_camions", 1), 1)
    seuil_ecart = config.get("seuil_ecart_heures", 0.15)
    seuil_burn = config.get("seuil_burn_job_en_cours", 0.60)

    alias_noms_form = config.get("alias_noms_form", {}) or {}

    matchees, orphelines = matcher_soumissions(soumissions, jobs)
    totaux_truck_jour = _heures_truck_jour(soumissions)
    noms_non_reconnus_vus = set()

    for s, suggestion in orphelines:
        msg = (
            f"Rapport de chantier ligne {s.ligne} : no de job "
            f"\"{s.job_num if s.job_num is not None else '(vide)'}\" introuvable dans Jobber, "
            f"client/adresse indiqué : \"{s.client_adresse}\""
        )
        if suggestion:
            msg += f" — suggestion : {suggestion}"
        resultat.alertes.append(("chantier_job_introuvable", msg))

    par_job = defaultdict(list)
    for s in matchees:
        par_job[s.job_num].append(s)

    for job_num, subs in par_job.items():
        job = jobs[job_num]
        heures_personnes = 0.0
        cout_mo = 0.0
        materiaux = 0.0
        overhead = 0.0
        ecarts = {}

        for s in subs:
            duree = s.duree_heures
            for gars_brut in s.gars_presents:
                # Le Form utilise parfois des prénoms/surnoms courts ("Matis") au
                # lieu du nom complet Jobber ("Matis Rodrigue") : alias_noms_form
                # les fait correspondre à leur vrai taux plutôt que de les traiter
                # (silencieusement) comme des gars hors Jobber au taux par défaut.
                gars = alias_noms_form.get(gars_brut, gars_brut)
                resultat.lignes_attribution.append(
                    LigneAttributionChantier(
                        job_num=job_num,
                        employe=gars,
                        date_entree=s.date_jour,
                        heures=duree,
                    )
                )
                heures_personnes += duree
                if gars not in taux and gars_brut not in noms_non_reconnus_vus:
                    noms_non_reconnus_vus.add(gars_brut)
                    resultat.alertes.append(
                        (
                            "chantier_nom_non_reconnu",
                            f"\"{gars_brut}\" (rapporté présent, job #{job_num}) ne matche aucun nom connu "
                            f"(taux_horaires / employes_hors_jobber / alias_noms_form) — coûté au taux par "
                            f"défaut de {taux_defaut_hors_jobber} $/h en attendant",
                        )
                    )
                # Un gars présent mais absent de employes_hors_jobber (ex. l'option
                # générique "Autre" du Form) coûte le taux par défaut plutôt que 0$
                # silencieux (voir config.yaml -> taux_horaire_defaut_hors_jobber).
                cout_mo += duree * taux.get(gars, taux_defaut_hors_jobber) * facteur_charges

            materiaux += (
                s.sacs_polymere * cout_sac
                + s.litres_scellant * cout_scellant
                + s.bacs_poussiere_pierre * cout_poussiere
            )
            if s.autres_materiaux:
                resultat.alertes.append(
                    (
                        "chantier_materiaux_a_verifier",
                        f"Job #{job_num}, {s.date_jour.strftime('%d %b') if s.date_jour else '?'} "
                        f"({s.truck}) : matériaux \"Autres\" à vérifier/pricer manuellement — "
                        f"\"{s.autres_materiaux}\"",
                    )
                )

            if s.date_jour is not None:
                total_jour = totaux_truck_jour.get((s.truck, s.date_jour), 0.0)
                if total_jour > 0:
                    overhead += overhead_par_camion * (duree / total_jour)

                heures_jobber_jour = sum(
                    e.heures for e in entrees if e.job_num == job_num and e.date_entree == s.date_jour
                )
                heures_form_jour = s.heures_personnes
                base = max(heures_form_jour, heures_jobber_jour)
                ecart_pct = abs(heures_form_jour - heures_jobber_jour) / base if base > 0 else 0.0
                ecarts[s.date_jour] = (heures_form_jour, heures_jobber_jour, ecart_pct)
                if base > 0 and ecart_pct > seuil_ecart:
                    resultat.alertes.append(
                        (
                            "chantier_ecart_heures",
                            f"Job #{job_num}, {s.date_jour.strftime('%d %b')} : écart heures Form "
                            f"({heures_form_jour:.1f} h) vs punchs Jobber ({heures_jobber_jour:.1f} h) "
                            f"= {ecart_pct:.0%}",
                        )
                    )

        subs_triees = sorted(subs, key=lambda s: s.horodatage or datetime.min)
        derniere = subs_triees[-1]
        form_dit_termine = derniere.statut == STATUT_TERMINE
        jobber_ferme = job.date_fermeture is not None
        complete = form_dit_termine or jobber_ferme

        if derniere.statut and form_dit_termine != jobber_ferme:
            if form_dit_termine and not jobber_ferme:
                resultat.alertes.append(
                    (
                        "chantier_contradiction_statut",
                        f"Job #{job_num} : le rapport de chantier dit « Terminé » mais Jobber "
                        f"montre le job encore ouvert",
                    )
                )
            else:
                resultat.alertes.append(
                    (
                        "chantier_contradiction_statut",
                        f"Job #{job_num} : Jobber montre le job fermé mais le dernier rapport de "
                        f"chantier dit « On revient »",
                    )
                )

        if not complete:
            a_visite_future = any(v > (derniere.date_jour or date.min) for v in job.dates_visites)
            if not a_visite_future:
                resultat.alertes.append(
                    (
                        "chantier_on_revient_sans_visite",
                        f"Job #{job_num} : « On revient » (dernier rapport "
                        f"{derniere.date_jour.strftime('%d %b') if derniere.date_jour else '?'}) "
                        f"mais aucune visite de suivi cédulée dans Jobber",
                    )
                )

        couts_accumules = cout_mo + materiaux + overhead
        pct_burn = couts_accumules / job.revenu_total if job.revenu_total else 0.0
        alerte_burn = not complete and pct_burn > seuil_burn
        if alerte_burn:
            resultat.alertes.append(
                (
                    "chantier_burn_eleve",
                    f"Job #{job_num} ({job.client}) : {pct_burn:.0%} de la valeur déjà dépensée en "
                    f"coûts, pas terminé",
                )
            )

        resultat.jobs[job_num] = JobChantier(
            job_num=job_num,
            client=job.client,
            soumissions=subs_triees,
            heures_personnes=heures_personnes,
            cout_mo=cout_mo,
            materiaux=materiaux,
            overhead=overhead,
            trucks=sorted({s.truck for s in subs}),
            statut_form_dernier=derniere.statut,
            date_derniere_soumission=derniere.date_jour,
            complete=complete,
            valeur=job.revenu_total,
            couts_accumules=couts_accumules,
            pct_burn=pct_burn,
            alerte_burn=alerte_burn,
            ecart_heures=ecarts,
        )

    resultat.alertes.extend(_alertes_double_truck(soumissions))
    return resultat


def jobs_en_cours(resultat: ResultatChantier) -> list:
    """Jobs multi-jours pas encore complétés, triés par % de burn décroissant (spec 4c, section « Jobs en cours »)."""
    liste = [jc for jc in resultat.jobs.values() if not jc.complete]
    liste.sort(key=lambda jc: -jc.pct_burn)
    return liste


def _inferer_truck(job: Job, camions_equipes: dict) -> str | None:
    """
    Devine le camion d'un job sans AUCUN rapport de chantier, à partir de son
    équipe assignée Jobber (Jobber n'a pas de notion de "camion"). Renvoie
    None si aucune équipe ne matche clairement, plutôt que de risquer de
    blâmer le mauvais camion.
    """
    equipes_normalisees = {
        truck: {normaliser_nom(n) for n in gars} for truck, gars in (camions_equipes or {}).items()
    }
    assignes = set(job.employes_assignes)
    matches = [truck for truck, equipe in equipes_normalisees.items() if equipe & assignes]
    return matches[0] if len(matches) == 1 else None


def grouper_jobs_sans_rapport(jobs_cibles: dict, resultat: ResultatChantier, config: dict) -> dict:
    """
    Jobs (fermés) SANS aucun rapport de chantier matché, groupés par camion
    deviné (spec 5 point 1, et spec 5b — message matinal). Utilisé pour le
    rapport hebdo (fenêtre = semaine) et le rappel matinal (fenêtre = hier).
    """
    manquants = [job for num, job in jobs_cibles.items() if num not in resultat.jobs]
    if not manquants:
        return {}

    camions_equipes = config.get("camions_equipes", {}) or {}
    par_truck = defaultdict(list)
    for job in manquants:
        truck = _inferer_truck(job, camions_equipes) or "camion non identifié"
        par_truck[truck].append(job)
    return dict(par_truck)


def alertes_jobs_sans_rapport(jobs_fermes_fenetre: dict, resultat: ResultatChantier, config: dict) -> list:
    """Jobs fermés cette semaine SANS aucun rapport de chantier, groupés par camion (spec 5, point 1)."""
    par_truck = grouper_jobs_sans_rapport(jobs_fermes_fenetre, resultat, config)

    alertes = []
    for truck, jobs_manquants in sorted(par_truck.items()):
        nums = ", ".join(f"#{j.numero}" for j in sorted(jobs_manquants, key=lambda j: j.numero))
        alertes.append(
            (
                "chantier_rapport_manquant",
                f"{truck} : {len(jobs_manquants)} job(s) fermé(s) cette semaine sans rapport de "
                f"chantier ({nums})",
            )
        )
    return alertes
