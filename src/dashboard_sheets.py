"""
Module 4 — Dashboard Looker Studio, alimenté par le Google Sheets
« MAG_Dashboard_Data » (voir SPEC_module_dashboard_MAG.md).

Principe : les onglets `jobs`, `attribution`, `ventes`, `quotidien` et `hebdo`
doivent contenir TOUT l'historique depuis le début, mais chaque run de l'agent
(GitHub Action) tourne sur un runner éphémère qui ne connaît que la semaine en
cours. Comme pour l'analyse Claude (historique.py), la mémoire cross-semaines
vient des fichiers historique/semaine_*.json commités au repo : chaque run
calcule les lignes de LA semaine courante (fonctions lignes_*_semaine
ci-dessous), les ajoute au payload avant sauvegarder_semaine(), puis ce module
recharge TOUT l'historique et réécrit les onglets au complet (idempotent,
écriture en batch, jamais cellule par cellule).

Exception : l'onglet `quotidien` pourrait en théorie être recalculé en entier
à chaque run (form_chantier_source relit tout le Sheets de réponses à chaque
fois), mais on le construit aussi à partir de l'historique pour rester
cohérent avec les autres onglets et pour figer les coûts/taux de config qui
étaient en vigueur la semaine où le travail a eu lieu (plutôt que de tout
recalculer avec les taux d'AUJOURD'HUI).

Les jobs EN COURS (`jobs_en_cours_actuels`) ne sont PAS stockés dans
l'historique : ils sont recalculés à chaque run à partir de l'état actuel
(déjà basé sur la lecture complète du Form) et simplement ajoutés par-dessus
l'historique des jobs fermés.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from form_chantier_source import STATUT_TERMINE, obtenir_client_gspread
from parseur import Job, deduire_etape
from rapport_chantier import fusionner_taux
from rapport_ventes import RapportVentes

VERSION = "1.0"
FUSEAU_MAG = ZoneInfo("America/Montreal")

ENTETES = {
    "jobs": [
        "job_id", "client", "ville", "etape", "statut", "date_debut", "date_fin",
        "revenu", "heures_totales", "cout_mo", "cout_materiaux", "overhead_alloue",
        "marge_avant_overhead", "marge_apres_overhead", "dollars_par_heure",
        "marge_pct", "burn_pct", "source_heures", "semaine", "mois",
    ],
    "attribution": ["job_id", "date", "employe", "truck", "heures", "cout", "revenu_genere", "source"],
    "ventes": [
        "semaine", "date_lundi_semaine", "vendeur", "soumissions_envoyees",
        "montant_envoye", "soumissions_approuvees", "montant_approuve",
        "taux_closing_cohorte",
    ],
    "quotidien": ["date", "truck", "revenu_complete", "heures", "cout_mo", "cout_materiaux", "overhead", "marge"],
    "hebdo": [
        "semaine", "date_lundi_semaine", "revenu", "heures", "dollars_par_heure", "cout_mo",
        "marge", "marge_pct", "heures_non_attribuees", "ventes_totales", "soumissions_envoyees",
        "projection_fin_mois", "mois_precedent_total",
    ],
    "meta": ["derniere_maj", "version", "nb_jobs_traites"],
}


def _semaine_iso(d: date) -> str:
    annee, semaine, _ = d.isocalendar()
    return f"{annee}-W{semaine:02d}"


# ---------------------------------------------------------------------------
# Construction des lignes de LA SEMAINE COURANTE (appelées depuis generer_rapport.py,
# résultat fusionné dans le payload avant historique.sauvegarder_semaine()).
# ---------------------------------------------------------------------------

def lignes_attribution_semaine(
    resultat, resultat_chantier, config: dict, dollars_heure_par_job: dict | None = None
) -> list[dict]:
    """
    Onglet attribution : une ligne = une part d'heures d'un employé sur un job,
    un jour donné. Priorité au Rapport de chantier (Form) quand il existe pour
    un job (même hiérarchie que rapport.py) : les lignes de l'ancienne logique
    (punchs) ne sont incluses QUE pour les jobs sans aucune donnée Form, pour
    ne pas compter deux fois les mêmes heures dans les totaux par employé
    (Page 3 — Équipe).

    revenu_genere = heures x $/h du job (même logique que la formule Excel
    Attribution!H, voir excel_report.py) : 0 si le job n'est pas dans
    dollars_heure_par_job (job pas encore fermé cette semaine-là).
    """
    taux = fusionner_taux(config)
    facteur_charges = config.get("facteur_charges", 1.0)
    taux_defaut_hors_jobber = config.get("taux_horaire_defaut_hors_jobber", 0)
    jobs_couverts_par_chantier = set(resultat_chantier.jobs) if resultat_chantier else set()
    dollars_heure_par_job = dollars_heure_par_job or {}

    lignes = []
    for la in (resultat_chantier.lignes_attribution if resultat_chantier else []):
        cout = la.heures * taux.get(la.employe, taux_defaut_hors_jobber) * facteur_charges
        revenu_genere = la.heures * dollars_heure_par_job.get(la.job_num, 0.0)
        lignes.append(
            {
                "job_id": la.job_num,
                "date": la.date_entree.isoformat() if la.date_entree else None,
                "employe": la.employe,
                "truck": la.truck,
                "heures": round(la.heures, 4),
                "cout": round(cout, 2),
                "revenu_genere": round(revenu_genere, 2),
                "source": "Rapport chantier (Form)",
            }
        )

    for l in resultat.lignes:
        if l.job_num in jobs_couverts_par_chantier:
            continue
        cout = l.heures * taux.get(l.employe, 0) * facteur_charges
        revenu_genere = l.heures * dollars_heure_par_job.get(l.job_num, 0.0)
        lignes.append(
            {
                "job_id": l.job_num,
                "date": l.date_entree.isoformat() if l.date_entree else None,
                "employe": l.employe,
                "truck": "",
                "heures": round(l.heures, 4),
                "cout": round(cout, 2),
                "revenu_genere": round(revenu_genere, 2),
                "source": l.source,
            }
        )

    return lignes


def lignes_ventes_semaine(rapport_ventes: RapportVentes) -> list[dict]:
    """Onglet ventes : une ligne par vendeur suivi, pour la semaine du rapport."""
    semaine_iso = _semaine_iso(rapport_ventes.debut)
    return [
        {
            "semaine": semaine_iso,
            "date_lundi_semaine": rapport_ventes.debut.isoformat(),
            "vendeur": v.nom,
            "soumissions_envoyees": v.envoyees_n,
            "montant_envoye": round(v.envoyees_dollars, 2),
            "soumissions_approuvees": v.vendues_n,
            "montant_approuve": round(v.vendues_dollars, 2),
            "taux_closing_cohorte": round(v.taux_closing_cohorte, 4) if v.taux_closing_cohorte is not None else None,
        }
        for v in rapport_ventes.vendeurs
    ]


def lignes_quotidien_semaine(
    soumissions_chantier: list, jobs_actuels: dict[int, Job], config: dict, debut: date, fin: date
) -> list[dict]:
    """
    Onglet quotidien (grain journalier, spec section 1) : une ligne par truck
    par jour, pour les soumissions Form dont le jour tombe dans [debut, fin].

    revenu_complete : revenu des jobs marqués « Terminé » dans une soumission
    de ce truck ce jour-là (limité aux jobs présents dans jobs_actuels, la
    fenêtre Jobber de la semaine du rapport — un job clôturé une semaine
    différente de celle où il a été rapporté « Terminé » n'est pas compté ici,
    cas rare en pratique).
    """
    taux = fusionner_taux(config)
    facteur_charges = config.get("facteur_charges", 1.0)
    taux_defaut_hors_jobber = config.get("taux_horaire_defaut_hors_jobber", 0)
    couts_mat = config.get("couts_materiaux", {}) or {}
    cout_sac = couts_mat.get("sac_polymere", 0)
    cout_scellant = couts_mat.get("scellant_litre", 0)
    cout_poussiere = couts_mat.get("poussiere_pierre", 0)
    overhead_par_camion = config.get("overhead_quotidien_total", 0) / max(config.get("nb_camions", 1), 1)
    alias_noms_form = config.get("alias_noms_form", {}) or {}

    soums_semaine = [s for s in soumissions_chantier if s.date_jour and debut <= s.date_jour <= fin]

    par_jour_truck = defaultdict(list)
    for s in soums_semaine:
        par_jour_truck[(s.date_jour, s.truck)].append(s)

    lignes = []
    for (jour, truck), subs in sorted(par_jour_truck.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        heures = 0.0
        cout_mo = 0.0
        cout_materiaux = 0.0
        revenu_complete = 0.0

        for s in subs:
            duree = s.duree_heures
            for gars_brut in s.gars_presents:
                gars = alias_noms_form.get(gars_brut, gars_brut)
                heures += duree
                cout_mo += duree * taux.get(gars, taux_defaut_hors_jobber) * facteur_charges

            cout_materiaux += (
                s.sacs_polymere * cout_sac
                + s.litres_scellant * cout_scellant
                + s.bacs_poussiere_pierre * cout_poussiere
            )

            if s.statut == STATUT_TERMINE and s.job_num is not None:
                job = jobs_actuels.get(s.job_num)
                if job is not None:
                    revenu_complete += job.revenu_total

        overhead = overhead_par_camion
        marge = revenu_complete - cout_mo - cout_materiaux - overhead
        lignes.append(
            {
                "date": jour.isoformat(),
                "truck": truck,
                "revenu_complete": round(revenu_complete, 2),
                "heures": round(heures, 2),
                "cout_mo": round(cout_mo, 2),
                "cout_materiaux": round(cout_materiaux, 2),
                "overhead": round(overhead, 2),
                "marge": round(marge, 2),
            }
        )

    return lignes


# ---------------------------------------------------------------------------
# Reconstruction des tables complètes à partir de l'historique + de l'état
# actuel des jobs en cours (appelé juste avant l'écriture dans le Sheets).
# ---------------------------------------------------------------------------

def _lignes_jobs_historique(historique: list[dict]) -> list[dict]:
    lignes = []
    for payload in historique:
        for j in payload.get("jobs_fermes", []):
            date_fin = date.fromisoformat(j["date_fin"]) if j.get("date_fin") else None
            lignes.append(
                {
                    "job_id": j["numero"],
                    "client": j["client"],
                    "ville": j.get("ville", ""),
                    "etape": j["etape"],
                    "statut": "complété",
                    "date_debut": j.get("date_debut") or "",
                    "date_fin": j.get("date_fin") or "",
                    "revenu": j["revenu"],
                    "heures_totales": j["heures"],
                    "cout_mo": j.get("cout_mo", ""),
                    "cout_materiaux": j.get("materiaux", 0),
                    "overhead_alloue": j.get("overhead", 0),
                    "marge_avant_overhead": j.get("marge_avant_overhead", ""),
                    "marge_apres_overhead": j["marge"],
                    "dollars_par_heure": j["dollars_heure"],
                    "marge_pct": j.get("marge_pct", ""),
                    "burn_pct": "",
                    "source_heures": j.get("source_heures", ""),
                    "semaine": _semaine_iso(date_fin) if date_fin else "",
                    "mois": date_fin.strftime("%Y-%m") if date_fin else "",
                }
            )
    return lignes


def _lignes_jobs_en_cours(jobs_en_cours_actuels: list, jobs_actuels: dict[int, Job]) -> list[dict]:
    lignes = []
    for jc in jobs_en_cours_actuels:
        job = jobs_actuels.get(jc.job_num)
        ville = job.ville if job else ""
        etape = deduire_etape(job.line_items) if job else ""
        date_debut = job.date_debut_cedulee.isoformat() if job and job.date_debut_cedulee else ""
        ref_date = jc.date_derniere_soumission or datetime.now(FUSEAU_MAG).date()

        marge_avant = jc.valeur - jc.cout_mo - jc.materiaux
        marge_apres = marge_avant - jc.overhead
        dollars_heure = jc.valeur / jc.heures_personnes if jc.heures_personnes else 0.0
        marge_pct = marge_apres / jc.valeur if jc.valeur else 0.0

        lignes.append(
            {
                "job_id": jc.job_num,
                "client": jc.client,
                "ville": ville,
                "etape": etape,
                "statut": "en cours",
                "date_debut": date_debut,
                "date_fin": "",
                "revenu": round(jc.valeur, 2),
                "heures_totales": round(jc.heures_personnes, 2),
                "cout_mo": round(jc.cout_mo, 2),
                "cout_materiaux": round(jc.materiaux, 2),
                "overhead_alloue": round(jc.overhead, 2),
                "marge_avant_overhead": round(marge_avant, 2),
                "marge_apres_overhead": round(marge_apres, 2),
                "dollars_par_heure": round(dollars_heure, 2),
                "marge_pct": round(marge_pct, 4),
                "burn_pct": round(jc.pct_burn, 4),
                "source_heures": "Rapport chantier",
                "semaine": _semaine_iso(ref_date),
                "mois": ref_date.strftime("%Y-%m"),
            }
        )
    return lignes


def _ligne_hebdo(payload: dict) -> dict:
    debut = date.fromisoformat(payload["debut"])
    marge_totale = sum(j["marge"] for j in payload.get("jobs_fermes", []))
    revenu = payload.get("revenu_ferme_total", 0.0)
    ventes = payload.get("ventes", [])
    # Projection du mois (pacing jours ouvrés, voir rapport_ventes._construire_verdict) :
    # stockée telle que calculée par l'agent CETTE semaine-là, plutôt que recalculée par
    # Looker (qui n'a pas la logique de jours ouvrés écoulés) — voir generer_rapport.py.
    projection = payload.get("projection_ventes", {})
    return {
        "semaine": _semaine_iso(debut),
        "date_lundi_semaine": debut.isoformat(),
        "revenu": revenu,
        "heures": payload.get("heures_fermes_total", 0.0),
        "dollars_par_heure": payload.get("dollars_heure_global", 0.0),
        "cout_mo": payload.get("cout_mo_ferme_total", 0.0),
        "marge": round(marge_totale, 2),
        "marge_pct": round(marge_totale / revenu, 4) if revenu else 0.0,
        "heures_non_attribuees": payload.get("heures_non_attribuees_total", 0.0),
        "ventes_totales": round(sum(v["montant_approuve"] for v in ventes), 2),
        "soumissions_envoyees": sum(v["soumissions_envoyees"] for v in ventes),
        "projection_fin_mois": projection.get("projection_fin_mois"),
        "mois_precedent_total": projection.get("mois_precedent_total"),
    }


def _lignes_vers_valeurs(entetes: list[str], lignes: list[dict]) -> list[list]:
    valeurs = [entetes]
    for ligne in lignes:
        valeurs.append(["" if ligne.get(c) is None else ligne.get(c) for c in entetes])
    return valeurs


def construire_tables(
    historique: list[dict], jobs_en_cours_actuels: list, jobs_actuels: dict[int, Job]
) -> dict[str, list[list]]:
    """Reconstruit le contenu complet (en-tête + lignes) des 6 onglets, prêt à écrire."""
    lignes_jobs = _lignes_jobs_historique(historique) + _lignes_jobs_en_cours(jobs_en_cours_actuels, jobs_actuels)
    lignes_attrib = [l for payload in historique for l in payload.get("attribution", [])]
    lignes_ventes = [l for payload in historique for l in payload.get("ventes", [])]
    lignes_quot = [l for payload in historique for l in payload.get("quotidien", [])]
    lignes_hebdo = [_ligne_hebdo(payload) for payload in historique]
    ligne_meta = {
        "derniere_maj": datetime.now(FUSEAU_MAG).isoformat(timespec="seconds"),
        "version": VERSION,
        "nb_jobs_traites": len(lignes_jobs),
    }

    return {
        "jobs": _lignes_vers_valeurs(ENTETES["jobs"], lignes_jobs),
        "attribution": _lignes_vers_valeurs(ENTETES["attribution"], lignes_attrib),
        "ventes": _lignes_vers_valeurs(ENTETES["ventes"], lignes_ventes),
        "quotidien": _lignes_vers_valeurs(ENTETES["quotidien"], lignes_quot),
        "hebdo": _lignes_vers_valeurs(ENTETES["hebdo"], lignes_hebdo),
        "meta": _lignes_vers_valeurs(ENTETES["meta"], [ligne_meta]),
    }


def _obtenir_ou_creer_onglet(spreadsheet, nom: str):
    import gspread

    try:
        return spreadsheet.worksheet(nom)
    except gspread.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=nom, rows=100, cols=len(ENTETES[nom]) + 2)


def publier_dashboard(
    env,
    config: dict,
    jobs_en_cours_actuels: list,
    jobs_actuels: dict[int, Job],
    historique: list[dict],
):
    """
    Réécrit au complet les 6 onglets de MAG_Dashboard_Data (spec section 3,
    étape 1) : dégradation gracieuse si le compte de service ou le Sheet ID
    sont absents/invalides, ou si l'appel échoue — jamais bloquant pour le
    reste du script (même logique que form_chantier_source).
    """
    json_credentials = env.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = config.get("google_sheet_id_dashboard")
    if not json_credentials or not sheet_id:
        print("Dashboard : compte de service ou Sheet ID (google_sheet_id_dashboard) absent, section ignorée.")
        return

    try:
        client = obtenir_client_gspread(json_credentials)
        spreadsheet = client.open_by_key(sheet_id)

        for nom in ENTETES:
            _obtenir_ou_creer_onglet(spreadsheet, nom)

        tables = construire_tables(historique, jobs_en_cours_actuels, jobs_actuels)

        spreadsheet.values_batch_clear(body={"ranges": [f"{nom}!A1:Z" for nom in ENTETES]})
        spreadsheet.values_batch_update(
            body={
                "valueInputOption": "RAW",
                "data": [{"range": f"{nom}!A1", "values": valeurs} for nom, valeurs in tables.items()],
            }
        )

        nb_lignes = sum(len(v) - 1 for v in tables.values())
        print(f"Dashboard : {nb_lignes} ligne(s) écrite(s) dans {len(tables)} onglet(s) de MAG_Dashboard_Data.")
    except Exception as e:  # noqa: BLE001 — jamais bloquant pour le reste du script
        print(f"Dashboard : échec d'écriture ({type(e).__name__}: {e}), section ignorée.")
