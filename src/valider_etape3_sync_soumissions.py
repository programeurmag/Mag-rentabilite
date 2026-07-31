"""
Sync soumissions Jobber -> GHL — étape 3 : dry-run complet sur 30 jours, rapport
de taux de match (voir spec-sync-soumissions-jobber-ghl.md, Ordre de travail,
point 3). Applique la cascade de matching (étape 2, décision Justin du
2026-07-29 : toutes pipelines confondues), la règle multi-soumissions, et
simule les garde-fous d'écriture — mais n'écrit RIEN dans GHL ni ailleurs.
C'est le point d'arrêt : le taux de match ci-dessous doit être validé avant
l'étape 5 (écritures réelles).

Garde-fous simulés (voir spec, section "Garde-fous obligatoires") :
  - Idempotence : si monetaryValue == subtotal déjà, aucune écriture logguée.
  - Ne jamais écraser une valeur plus grande par une plus petite sans le
    signaler (log "valeur diminuée", l'écriture aurait quand même lieu).
  - Ne pas reculer un stage : dans CLOSING, si le stage courant est déjà à la
    position >= celle de "Soumission envoyée" (SUIVI À FAIRE, position 1),
    valeur seulement. Stages CLOSING confirmés par l'API : RDV À VENIR (0),
    SUIVI À FAIRE (1, cible), EN ATTENTE DE DÉPÔT (2) — pas d'autres stages.
  - Opportunités Won/Lost (status won/lost) : valeur seulement, jamais de
    changement de stage ni de pipeline.
  - Une opportunité encore dans SETTING (pas Won/Lost) : le "déplacement de
    stage" simulé implique aussi un déplacement de PIPELINE vers CLOSING
    (conséquence de la décision de matcher sur toutes les pipelines).

Règle multi-soumissions (spec, section "Plusieurs soumissions pour un même
client") : les soumissions matchées sur la MÊME opportunité sont groupées ;
si au moins une est approuvée/convertie, la plus récente approuvée est
retenue comme valeur à écrire, sinon la plus récente tout court. Jamais de
somme. Tous les cas sont listés dans le rapport pour vérification manuelle.

Usage : python3 src/valider_etape3_sync_soumissions.py
"""

from __future__ import annotations

from datetime import date, timedelta

from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber
from valider_etape1_sync_soumissions import N_JOURS, obtenir_quotes_envoyees, _valeur_primaire
from valider_etape2_sync_soumissions import (
    NOMS_PIPELINES,
    PIPELINE_CLOSING,
    STAGE_SOUMISSION_ENVOYEE,
    normaliser_email,
    normaliser_telephone,
    obtenir_toutes_opportunites,
)

STATUTS_APPROUVES = {"approved", "converted"}

# Position du stage cible dans CLOSING (voir docstring) : tout stage à cette
# position ou plus loin ne doit pas reculer.
POSITIONS_STAGES_CLOSING = {
    "0bd51fd0-adda-4a92-acba-3c82fc404a3a": 0,  # RDV À VENIR
    STAGE_SOUMISSION_ENVOYEE: 1,  # SUIVI À FAIRE (cible)
    "ab3b0d40-1adc-4387-a82f-b4e30059e2af": 2,  # EN ATTENTE DE DÉPÔT
}
POSITION_CIBLE = POSITIONS_STAGES_CLOSING[STAGE_SOUMISSION_ENVOYEE]


def construire_index(opportunites: list[dict]) -> tuple[dict, dict]:
    par_email: dict[str, list[dict]] = {}
    par_telephone: dict[str, list[dict]] = {}
    for o in opportunites:
        c = o.get("contact") or {}
        email = normaliser_email(c.get("email") or "")
        tel = normaliser_telephone(c.get("phone") or "")
        if email:
            par_email.setdefault(email, []).append(o)
        if tel:
            par_telephone.setdefault(tel, []).append(o)
    return par_email, par_telephone


def matcher(q: dict, par_email: dict, par_telephone: dict) -> tuple[dict | None, str | None, int]:
    """Retourne (opportunité choisie, niveau, nb_candidats) ou (None, None, 0)."""
    c = q.get("client") or {}
    email = _valeur_primaire(c.get("emails") or [], "address")
    tel = _valeur_primaire(c.get("phones") or [], "normalizedPhoneNumber") or _valeur_primaire(
        c.get("phones") or [], "number"
    )

    candidats = par_email.get(normaliser_email(email)) if email else None
    if candidats:
        return max(candidats, key=lambda o: o["updatedAt"]), "email", len(candidats)

    if tel:
        candidats = par_telephone.get(normaliser_telephone(tel))
        if candidats:
            return max(candidats, key=lambda o: o["updatedAt"]), "telephone", len(candidats)

    return None, None, 0


def choisir_soumission_canonique(quotes_groupe: list[dict]) -> dict:
    """Règle multi-soumissions : la plus récente approuvée/convertie, sinon la plus récente tout court."""
    approuvees = [q for q in quotes_groupe if q["quoteStatus"] in STATUTS_APPROUVES]
    pool = approuvees or quotes_groupe
    return max(pool, key=lambda q: q["updatedAt"])


def simuler_ecriture(opp: dict, sous_total: float) -> dict:
    """
    Applique les garde-fous du spec et retourne à la fois un résumé lisible
    (pour les rapports) et le corps PUT machine-readable correspondant
    (`corps_put`, vide si aucune écriture n'est nécessaire — idempotence).
    Réutilisé tel quel par le script de production (écritures réelles) pour
    ne jamais dupliquer cette logique.
    """
    corps_put: dict = {}

    valeur_actuelle = opp["monetaryValue"] or 0.0
    if valeur_actuelle == sous_total:
        action_valeur = "aucun changement (déjà à jour)"
    else:
        action_valeur = f"{valeur_actuelle:,.2f} $ -> {sous_total:,.2f} $"
        corps_put["monetaryValue"] = sous_total
    valeur_diminuee = sous_total < valeur_actuelle

    if opp["status"] in ("won", "lost"):
        action_stage = f"aucun changement (opportunité {opp['status']})"
    elif opp["pipelineId"] != PIPELINE_CLOSING:
        pipeline_actuelle = NOMS_PIPELINES.get(opp["pipelineId"], opp["pipelineId"])
        action_stage = f"déplacer {pipeline_actuelle} -> CLOSING / SUIVI À FAIRE"
        corps_put["pipelineId"] = PIPELINE_CLOSING
        corps_put["pipelineStageId"] = STAGE_SOUMISSION_ENVOYEE
    else:
        position_actuelle = POSITIONS_STAGES_CLOSING.get(opp["pipelineStageId"])
        if position_actuelle is not None and position_actuelle >= POSITION_CIBLE:
            action_stage = "aucun changement (déjà à ce stage ou plus loin)"
        else:
            action_stage = "avancer -> SUIVI À FAIRE"
            corps_put["pipelineStageId"] = STAGE_SOUMISSION_ENVOYEE

    return {
        "action_valeur": action_valeur,
        "valeur_diminuee": valeur_diminuee,
        "action_stage": action_stage,
        "corps_put": corps_put,
    }


STATUTS_OPP_FINAUX = {"won", "lost", "abandoned"}


def simuler_passage_won(opp: dict, statut_quote_canonique: str) -> dict:
    """
    Règle ajoutée le 2026-07-30 (demande Justin), distincte des garde-fous
    d'origine du spec : quand la soumission canonique de l'opportunité (même
    sélection que simuler_ecriture — cascade + règle multi-soumissions) est
    approuvée ou convertie côté Jobber, faire passer l'opportunité GHL à
    status "won" — SAUF si elle est déjà dans un statut final (won, lost,
    abandoned), pour ne jamais écraser une décision déjà prise côté GHL.

    Ne touche jamais monetaryValue ni pipelineStageId ici (simuler_ecriture
    s'en occupe séparément, sur l'état de l'opportunité en début de run) —
    et ne fait plus partie de la garantie "le statut n'est jamais modifié"
    documentée ailleurs dans ce module pour tout le reste (nom, owner, tags) :
    seule cette transition précise vers "won" est autorisée.
    """
    if statut_quote_canonique not in STATUTS_APPROUVES:
        return {"action_statut": "aucun changement (soumission pas approuvée/convertie)", "corps_put": {}}
    if opp["status"] in STATUTS_OPP_FINAUX:
        return {"action_statut": f"aucun changement (déjà {opp['status']})", "corps_put": {}}
    return {"action_statut": f"{opp['status']} -> won", "corps_put": {"status": "won"}}


def main():
    config = dotenv_values(CHEMIN_ENV)
    client_jobber = ClientJobber(
        config["JOBBER_CLIENT_ID"],
        config["JOBBER_CLIENT_SECRET"],
        config["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    fin = date.today()
    debut = fin - timedelta(days=N_JOURS)
    quotes = obtenir_quotes_envoyees(client_jobber, debut, fin)
    opportunites = obtenir_toutes_opportunites(config)
    par_email, par_telephone = construire_index(opportunites)

    non_matchees = []
    ambigues = []
    groupes: dict[str, list[dict]] = {}

    for q in quotes:
        opp, niveau, n_candidats = matcher(q, par_email, par_telephone)
        if opp is None:
            non_matchees.append(q)
            continue
        if n_candidats > 1:
            c = q.get("client") or {}
            nom = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            ambigues.append((q["quoteNumber"], nom, niveau, n_candidats))
        groupes.setdefault(opp["id"], []).append(q)

    multi_soumissions = []
    lignes_ecriture = []

    opportunites_par_id = {o["id"]: o for o in opportunites}

    for opp_id, quotes_groupe in groupes.items():
        opp = opportunites_par_id[opp_id]
        if len(quotes_groupe) > 1:
            c0 = quotes_groupe[0].get("client") or {}
            nom = f"{c0.get('firstName', '')} {c0.get('lastName', '')}".strip()
            multi_soumissions.append(
                (nom, [q["quoteNumber"] for q in quotes_groupe], [q["quoteStatus"] for q in quotes_groupe])
            )
        q_canonique = choisir_soumission_canonique(quotes_groupe)
        sous_total = q_canonique["amounts"]["subtotal"] or 0.0
        simulation = simuler_ecriture(opp, sous_total)

        lignes_ecriture.append(
            {
                "numero": q_canonique["quoteNumber"],
                "client": f"{(q_canonique.get('client') or {}).get('firstName', '')} "
                f"{(q_canonique.get('client') or {}).get('lastName', '')}".strip(),
                "opp_nom": opp["name"],
                **simulation,
            }
        )

    print("=" * 110)
    print(f"DRY-RUN — sync soumissions Jobber -> GHL — {debut} au {fin} ({N_JOURS} jours)")
    print("=" * 110)

    print(f"\n{'#':<8} {'Client':<25} {'Opportunité':<25} {'Valeur (dry-run)':<28} {'Stage (dry-run)'}")
    print("-" * 110)
    for l in sorted(lignes_ecriture, key=lambda x: int(x["numero"])):
        marqueur = " ⚠" if l["valeur_diminuee"] else ""
        print(f"{l['numero']:<8} {l['client']:<25} {l['opp_nom']:<25} {l['action_valeur']:<26}{marqueur}  {l['action_stage']}")

    n_traitees = len(quotes)
    n_matchees = len(lignes_ecriture)
    n_echecs = len(non_matchees)

    print("\n" + "=" * 110)
    print(f"Sync soumissions : {n_traitees} traitées, {n_matchees} matchées, {n_echecs} échec(s)")

    if non_matchees:
        print("\nNon matchées :")
        for q in non_matchees:
            c = q.get("client") or {}
            nom = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
            email = _valeur_primaire(c.get("emails") or [], "address")
            print(f"  - Soumission #{q['quoteNumber']} — {nom} ({email}) — {q['amounts']['subtotal']:,.2f} $")

    if ambigues:
        print("\nAmbiguës (plusieurs opportunités trouvées, la plus récente utilisée) :")
        for numero, nom, niveau, n in ambigues:
            print(f"  - Soumission #{numero} — {nom} — match par {niveau} — {n} opportunités trouvées")

    if multi_soumissions:
        print("\nMulti-soumissions (même opportunité, plusieurs soumissions — jamais additionnées) :")
        for nom, numeros, statuts in multi_soumissions:
            details = ", ".join(f"#{n} ({s})" for n, s in zip(numeros, statuts))
            print(f"  - {nom} — {details}")

    n_diminutions = sum(1 for l in lignes_ecriture if l["valeur_diminuee"])
    n_deplacements = sum(1 for l in lignes_ecriture if "déplacer" in l["action_stage"])
    print(
        f"\n{n_diminutions} valeur(s) diminuée(s) (⚠ ci-dessus), "
        f"{n_deplacements} opportunité(s) à déplacer vers CLOSING."
    )


if __name__ == "__main__":
    main()
