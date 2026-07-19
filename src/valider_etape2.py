"""
Étape 2 : valide que la source de données API Jobber produit des résultats
cohérents avec l'étape 1 (CSV), sur la même fenêtre (6 au 12 juillet 2026).

Usage : python3 src/valider_etape2.py
"""

from datetime import date

from dotenv import dotenv_values

from attribution import calculer_attribution, heures_par_job
from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber
from jobber_source import jobs_fermes_dans_fenetre, obtenir_jobs_semaine, obtenir_timesheets_semaine

DEBUT = date(2026, 7, 6)
FIN = date(2026, 7, 12)
SEUIL_TIMER_OUBLIE = 12.0


def main():
    config = dotenv_values(CHEMIN_ENV)

    client = ClientJobber(
        config["JOBBER_CLIENT_ID"],
        config["JOBBER_CLIENT_SECRET"],
        config["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    print("Récupération des jobs (visites cédulées dans la fenêtre)...")
    jobs = obtenir_jobs_semaine(client, DEBUT, FIN)
    print(f"  {len(jobs)} jobs candidats trouvés.")

    print("Récupération des timesheets de la fenêtre...")
    entrees = obtenir_timesheets_semaine(client, DEBUT, FIN, seuil_timer_oublie=SEUIL_TIMER_OUBLIE)
    print(f"  {len(entrees)} entrées de timesheet trouvées.")

    resultat = calculer_attribution(entrees, jobs)
    totaux_jobs = heures_par_job(resultat)

    fermes = jobs_fermes_dans_fenetre(jobs, DEBUT, FIN)
    print(f"  dont {len(fermes)} jobs fermés dans la fenêtre.")

    heures_punchees_total = sum(e.heures for e in entrees)
    heures_attribuees_total = sum(totaux_jobs.values())  # tous jobs (ouverts + fermés)
    heures_non_attribuees_total = sum(h for _, _, h, _ in resultat.non_attribue)

    # Le "$/h global" du rapport porte sur les jobs FERMÉS uniquement (rentabilité
    # réalisée) : revenu des jobs fermés / heures attribuées à CES MÊMES jobs.
    # Ne pas diviser par heures_attribuees_total, qui inclut aussi les jobs
    # encore ouverts (candidats à la répartition mais pas encore facturés).
    heures_fermes_total = sum(totaux_jobs.get(num, 0.0) for num in fermes)
    revenu_ferme_total = sum(j.revenu_total for j in fermes.values())
    dollars_heure_global = revenu_ferme_total / heures_fermes_total if heures_fermes_total else 0

    print("\n" + "=" * 80)
    print("VALIDATION ÉTAPE 2 — données live API Jobber (fenêtre 6-12 juillet 2026)")
    print("=" * 80)

    h198 = totaux_jobs.get(198, 0)
    revenu_198 = jobs[198].revenu_total if 198 in jobs else 0
    dh198 = revenu_198 / h198 if h198 else 0
    print(f"\nJob #198 : {h198:.2f} h attribuées, {dh198:.2f} $/h  (référence CSV : ~170 h, ~51 $/h)")
    print(
        f"Global (jobs fermés) : {heures_fermes_total:.2f} h attribuées, {dollars_heure_global:.2f} $/h "
        f"(référence CSV : ~544 h, ~95 $/h)"
    )
    print(f"\nHeures punchées                : {heures_punchees_total:.2f}")
    print(f"Heures attribuées (tous jobs)  : {heures_attribuees_total:.2f}")
    print(f"  dont jobs fermés             : {heures_fermes_total:.2f}")
    print(f"  dont jobs encore ouverts     : {heures_attribuees_total - heures_fermes_total:.2f}")
    print(f"Heures non attribuées          : {heures_non_attribuees_total:.2f}")

    print("\n--- Jobs fermés dans la fenêtre (comparer visuellement avec l'onglet Jobs de référence) ---")
    for num in sorted(fermes):
        h = totaux_jobs.get(num, 0.0)
        rev = fermes[num].revenu_total
        dh = rev / h if h else 0
        print(f"  Job #{num:<5} {fermes[num].client:<35s} {h:8.2f} h   {dh:8.2f} $/h   revenu={rev:.2f}")


if __name__ == "__main__":
    main()
