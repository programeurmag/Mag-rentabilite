"""
Étape 1 : valide la logique d'attribution en comparant nos résultats calculés
à partir des CSV d'exemple avec les chiffres du fichier Excel de référence
(MAG_rentabilite_reelle.xlsx, onglets Jobs et Dashboard).

Usage : python3 src/valider_etape1.py
"""

import sys
from pathlib import Path

import openpyxl

from attribution import calculer_attribution, heures_par_job
from parseur import parser_jobs, parser_timesheets

RACINE = Path(__file__).resolve().parent.parent
CSV_TIMESHEETS = RACINE / "data" / "timesheets_sample.csv"
CSV_JOBS = RACINE / "data" / "one_off_jobs_sample.csv"
XLSX_REFERENCE = RACINE / "data" / "reference_MAG_rentabilite_reelle.xlsx"

SEUIL_TIMER_OUBLIE = 12.0


def charger_reference():
    """Lit les chiffres de référence depuis l'Excel fourni par Justin."""
    wb = openpyxl.load_workbook(XLSX_REFERENCE, data_only=True)

    # Onglet Jobs : {job_num: (heures_attribuees, dollars_heure)}
    ws_jobs = wb["Jobs"]
    ref_jobs = {}
    for row in ws_jobs.iter_rows(min_row=2, values_only=True):
        job_num = row[0]
        if job_num is None:
            continue
        heures_attribuees, dollars_heure = row[7], row[10]
        ref_jobs[int(job_num)] = (heures_attribuees, dollars_heure)

    # Onglet Dashboard : ligne "GLOBAL"
    ws_dash = wb["Dashboard"]
    ligne_globale = ws_dash[5]  # ligne 5 = valeurs sous l'en-tête GLOBAL (ligne 4)
    ref_global = {
        "heures_punchees": ligne_globale[2].value,
        "heures_attribuees": ligne_globale[3].value,
        "heures_non_attribuees": ligne_globale[4].value,
        "dollars_heure_global": ligne_globale[6].value,
    }
    return ref_jobs, ref_global


def main():
    jobs = parser_jobs(str(CSV_JOBS))
    entrees = parser_timesheets(str(CSV_TIMESHEETS), seuil_timer_oublie=SEUIL_TIMER_OUBLIE)

    resultat = calculer_attribution(entrees, jobs)
    totaux_jobs = heures_par_job(resultat)

    # Fusionne les jobs du rapport + les jobs vus seulement via punch direct
    tous_jobs = {**jobs, **resultat.jobs_hors_rapport}

    heures_punchees_total = sum(e.heures for e in entrees)
    heures_attribuees_total = sum(totaux_jobs.values())
    heures_non_attribuees_total = sum(h for _, _, h, _ in resultat.non_attribue)
    revenu_ferme_total = sum(j.revenu_total for j in jobs.values())
    dollars_heure_global = (
        revenu_ferme_total / heures_attribuees_total if heures_attribuees_total else 0
    )

    ref_jobs, ref_global = charger_reference()

    print("=" * 80)
    print("VALIDATION ÉTAPE 1 — comparaison avec MAG_rentabilite_reelle.xlsx")
    print("=" * 80)

    # ---- Points de contrôle explicites ----
    print("\n--- Points de contrôle ---")
    h198 = totaux_jobs.get(198, 0)
    dh198 = jobs[198].revenu_total / h198 if h198 else 0
    print(f"Job #198 : {h198:.2f} h attribuées, {dh198:.2f} $/h  (attendu : ~170 h, ~51 $/h)")
    print(
        f"Global   : {heures_attribuees_total:.2f} h attribuées, {dollars_heure_global:.2f} $/h "
        f"(attendu : ~544 h, ~95 $/h)"
    )

    # ---- Comparaison détaillée globale ----
    print("\n--- Comparaison globale (calculé vs référence) ---")
    comparaisons_globales = [
        ("Heures punchées", heures_punchees_total, ref_global["heures_punchees"]),
        ("Heures attribuées", heures_attribuees_total, ref_global["heures_attribuees"]),
        ("Heures non attribuées", heures_non_attribuees_total, ref_global["heures_non_attribuees"]),
        ("$ / h global", dollars_heure_global, ref_global["dollars_heure_global"]),
    ]
    ecarts_globaux = False
    for nom, calcule, reference in comparaisons_globales:
        ecart = calcule - reference
        marqueur = "OK" if abs(ecart) < 0.05 else "ÉCART"
        if marqueur == "ÉCART":
            ecarts_globaux = True
        print(f"  {nom:28s} calculé={calcule:10.3f}  référence={reference:10.3f}  ecart={ecart:+.3f}  [{marqueur}]")

    # ---- Comparaison détaillée par job ---- (jobs fermés seulement, présents dans le rapport)
    print("\n--- Comparaison par job (heures attribuées, $/h) ---")
    ecarts_jobs = []
    for job_num in sorted(jobs.keys()):
        heures_calc = totaux_jobs.get(job_num, 0.0)
        revenu = jobs[job_num].revenu_total
        dh_calc = revenu / heures_calc if heures_calc else 0
        heures_ref, dh_ref = ref_jobs.get(job_num, (None, None))
        if heures_ref is None:
            continue
        ecart_h = heures_calc - heures_ref
        ecart_dh = dh_calc - (dh_ref or 0)
        marqueur = "OK" if abs(ecart_h) < 0.05 else "ÉCART"
        if marqueur == "ÉCART":
            ecarts_jobs.append(job_num)
        print(
            f"  Job #{job_num:<5} calc={heures_calc:8.2f} h  ref={heures_ref:8.2f} h  "
            f"ecart={ecart_h:+7.2f}  |  $/h calc={dh_calc:8.2f}  ref={(dh_ref or 0):8.2f}  [{marqueur}]"
        )

    print("\n" + "=" * 80)
    if ecarts_globaux or ecarts_jobs:
        print(f"RÉSULTAT : des écarts subsistent. Jobs en écart : {ecarts_jobs}")
        sys.exit(1)
    else:
        print("RÉSULTAT : tout matche avec la référence (tolérance 0.05 h).")


if __name__ == "__main__":
    main()
