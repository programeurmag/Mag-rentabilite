"""
Auto-test hors-ligne du Module 3 (Rapport de chantier), AVANT d'avoir accès
au vrai Sheets (compte de service). Utilise les mêmes CSV d'exemple que
valider_etape1.py/valider_etape3.py + un CSV synthétique qui imite un export
"get_all_values()" du Sheets de réponses (data/form_chantier_sample.csv),
pour vérifier que le parsing, le matching, la hiérarchie des sources, les
alertes et l'Excel ne plantent pas avant le vrai test de bout en bout (spec
étape 7.6, qui se fait sur les vraies soumissions de test de Justin).

Usage : python3 src/valider_etape_chantier.py
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import yaml

from excel_report import generer_excel
from form_chantier_source import parser_valeurs_sheet
from message_matinal import construire_message_matinal
from parseur import parser_jobs, parser_timesheets
from rapport import construire_rapport
from rapport_chantier import grouper_jobs_sans_rapport

RACINE = Path(__file__).resolve().parent.parent
CSV_TIMESHEETS = RACINE / "data" / "timesheets_sample.csv"
CSV_JOBS = RACINE / "data" / "one_off_jobs_sample.csv"
CSV_CHANTIER = RACINE / "data" / "form_chantier_sample.csv"
CHEMIN_SORTIE_XLSX = RACINE / "outputs" / "exemple_rapport_chantier.xlsx"

DEBUT = date(2026, 7, 6)
FIN = date(2026, 7, 12)


def main():
    config = yaml.safe_load((RACINE / "config.yaml").read_text(encoding="utf-8"))

    jobs = parser_jobs(str(CSV_JOBS))
    entrees = parser_timesheets(str(CSV_TIMESHEETS), seuil_timer_oublie=config["seuil_timer_oublie"])
    jobs_fermes_fenetre = {
        num: j for num, j in jobs.items() if j.date_fermeture and DEBUT <= j.date_fermeture <= FIN
    }

    with open(CSV_CHANTIER, encoding="utf-8-sig", newline="") as f:
        valeurs = list(csv.reader(f))
    soumissions = parser_valeurs_sheet(valeurs)
    print(f"=== {len(soumissions)} soumission(s) de chantier parsée(s) ===")
    for s in soumissions:
        print(
            f"  ligne {s.ligne} : job={s.job_num} truck={s.truck!r} jour={s.date_jour} "
            f"gars={s.gars_presents} heures={s.duree_heures:.2f} statut={s.statut!r}"
        )

    rapport = construire_rapport(jobs, jobs_fermes_fenetre, entrees, config, DEBUT, FIN, soumissions)

    print("\n=== Jobs fermés (source des heures) ===")
    for l in rapport.jobs_fermes:
        print(
            f"  #{l.numero} ({l.client}) — {l.source_heures} — {l.heures_attribuees:.2f} h, "
            f"MO {l.cout_mo:.0f} $, matériaux {l.materiaux:.0f} $, overhead {l.overhead:.0f} $, "
            f"marge avant {l.marge_avant_overhead:.0f} $ / après {l.marge:.0f} $"
        )

    print(f"\n=== {len(rapport.alertes)} alerte(s) générée(s) ===")
    for a in rapport.alertes:
        print(f"  [{a.type}] {a.message}")

    print(f"\n=== {len(rapport.jobs_en_cours)} job(s) en cours (multi-jours, pas complétés) ===")
    for jc in rapport.jobs_en_cours:
        print(f"  #{jc.job_num} ({jc.client}) — burn {jc.pct_burn:.0%}")

    # Aperçu du message matinal (bonus "jobs sans rapport", scope réduit à la
    # fenêtre de la semaine ici puisqu'on n'a pas de vraie notion de "hier"
    # dans des CSV d'exemple statiques).
    manquants_par_truck = grouper_jobs_sans_rapport(jobs_fermes_fenetre, rapport.resultat_chantier, config)
    message_matin = construire_message_matinal(
        config.get("url_form") or "https://forms.gle/EXEMPLE", manquants_par_truck
    )
    print("\n=== Aperçu message matinal (JSON) ===")
    print(json.dumps(message_matin, indent=2, ensure_ascii=False))

    CHEMIN_SORTIE_XLSX.parent.mkdir(exist_ok=True)
    generer_excel(
        str(CHEMIN_SORTIE_XLSX),
        jobs_fermes_fenetre,
        entrees,
        rapport.resultat,
        config,
        DEBUT,
        FIN,
        rapport.alertes,
        None,
        rapport.resultat_chantier,
        rapport.jobs_en_cours,
        soumissions,
    )
    print(f"\nExcel généré : {CHEMIN_SORTIE_XLSX}")


if __name__ == "__main__":
    main()
