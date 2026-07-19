"""
Logique d'attribution des heures aux jobs (section 1.3 de la spec).

Ordre de priorité pour chaque entrée de timesheet :
  1. Punch direct  -> 100% des heures vont au job punché.
  2. "General"      -> réparti entre les jobs où l'employé était assigné
                        (Visits assigned to) ET actifs ce jour-là, au prorata
                        du revenu total de chaque job candidat.
  3. Aucun candidat -> "non attribué" (vente / déplacement / admin).

Les entrées > seuil_timer_oublie sont marquées comme anomalie mais restent
incluses dans les heures ET les coûts (voir note dans valider_etape1.py :
c'est un écart volontaire par rapport au texte de la spec, pour matcher
le comportement du fichier de référence).
"""

from collections import defaultdict
from dataclasses import dataclass, field

from parseur import EntreeTimesheet, Job, normaliser_compagnie


@dataclass
class LigneAttribution:
    """Une ligne de l'onglet Attribution : la part d'une entrée de timesheet donnée à un job."""

    job_num: int
    employe: str
    date_entree: object
    heures: float
    source: str  # "Punch direct" ou "Réparti /N jobs"
    anomalie: bool


@dataclass
class ResultatAttribution:
    lignes: list = field(default_factory=list)  # list[LigneAttribution]
    non_attribue: list = field(default_factory=list)  # list[(employe, date, heures, anomalie)]
    jobs_hors_rapport: dict = field(default_factory=dict)  # {job_num: Job} jobs vus seulement via punch


def _trouver_candidats(employe: str, jour, jobs: dict[int, Job]) -> list[Job]:
    """Jobs où l'employé est assigné (Visits assigned to) ET actifs ce jour-là."""
    return [
        job
        for job in jobs.values()
        if job.dans_rapport and employe in job.employes_assignes and job.actif_le(jour)
    ]


def calculer_attribution(
    entrees: list[EntreeTimesheet],
    jobs: dict[int, Job],
    compte_compagnie: str = "MAG Lavage À Pression",
) -> ResultatAttribution:
    resultat = ResultatAttribution()
    compte_compagnie_norm = normaliser_compagnie(compte_compagnie)
    jobs_hors_rapport: dict[int, Job] = {}

    for entree in entrees:
        # Le compte compagnie n'est pas un employé : ses heures ne sont jamais
        # attribuées à un job, elles tombent dans le seau "non attribué".
        if normaliser_compagnie(entree.employe) == compte_compagnie_norm:
            resultat.non_attribue.append(
                (entree.employe, entree.date_entree, entree.heures, entree.anomalie)
            )
            continue

        if entree.job_num is not None:
            # Punch direct : 100% des heures vont au job, même s'il n'est pas
            # (encore) dans le rapport One-off jobs (job probablement encore ouvert).
            if entree.job_num not in jobs and entree.job_num not in jobs_hors_rapport:
                jobs_hors_rapport[entree.job_num] = Job(
                    numero=entree.job_num,
                    client=entree.client or "(job pas dans le rapport — encore ouvert?)",
                    dans_rapport=False,
                )
            resultat.lignes.append(
                LigneAttribution(
                    job_num=entree.job_num,
                    employe=entree.employe,
                    date_entree=entree.date_entree,
                    heures=entree.heures,
                    source="Punch direct",
                    anomalie=entree.anomalie,
                )
            )
            continue

        # Punch "General" : on cherche les jobs candidats ce jour-là
        candidats = _trouver_candidats(entree.employe, entree.date_entree, jobs)

        if not candidats:
            resultat.non_attribue.append(
                (entree.employe, entree.date_entree, entree.heures, entree.anomalie)
            )
            continue

        if len(candidats) == 1:
            resultat.lignes.append(
                LigneAttribution(
                    job_num=candidats[0].numero,
                    employe=entree.employe,
                    date_entree=entree.date_entree,
                    heures=entree.heures,
                    source="Réparti /1 jobs",
                    anomalie=entree.anomalie,
                )
            )
            continue

        # Plusieurs candidats : répartition au prorata du revenu total de chaque job
        revenu_total_candidats = sum(c.revenu_total for c in candidats)
        for candidat in candidats:
            if revenu_total_candidats > 0:
                part = entree.heures * candidat.revenu_total / revenu_total_candidats
            else:
                # Cas limite (aucun candidat n'a de revenu connu) : split égal
                part = entree.heures / len(candidats)
            resultat.lignes.append(
                LigneAttribution(
                    job_num=candidat.numero,
                    employe=entree.employe,
                    date_entree=entree.date_entree,
                    heures=part,
                    source=f"Réparti /{len(candidats)} jobs",
                    anomalie=entree.anomalie,
                )
            )

    resultat.jobs_hors_rapport = jobs_hors_rapport
    return resultat


def heures_par_job(resultat: ResultatAttribution) -> dict:
    """Additionne les heures attribuées par job (toutes sources confondues)."""
    totaux = defaultdict(float)
    for ligne in resultat.lignes:
        totaux[ligne.job_num] += ligne.heures
    return dict(totaux)


def cout_mo_par_job(
    resultat: ResultatAttribution, taux_horaires: dict, facteur_charges: float
) -> dict:
    """Coût MO chargé par job = somme(heures employé x taux horaire x facteur charges)."""
    totaux = defaultdict(float)
    for ligne in resultat.lignes:
        taux = taux_horaires.get(ligne.employe, 0)
        totaux[ligne.job_num] += ligne.heures * taux * facteur_charges
    return dict(totaux)


def heures_par_employe(resultat: ResultatAttribution) -> dict:
    """Heures attribuées à un job, par employé (toutes jobs confondus)."""
    totaux = defaultdict(float)
    for ligne in resultat.lignes:
        totaux[ligne.employe] += ligne.heures
    return dict(totaux)
