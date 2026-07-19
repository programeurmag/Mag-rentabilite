"""
Module Ventes — assemble les soumissions (Soumission, voir ventes_source.py) en
un rapport hebdomadaire : par vendeur, équipe, projection du mois, alertes
(voir SPEC_module_ventes_MAG.md).

Même approche que rapport.py (rentabilité) : ce module ne connaît pas la
source des données, il ne travaille qu'avec la liste de Soumission déjà
récupérée (fenêtre de 8 semaines, filtrée par createdAt — voir
valider_etape1_ventes.py pour la validation de cette approche).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ventes_source import Soumission

STATUTS_VENDUS = {"approved", "converted"}

MOIS_FR = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril", 5: "mai", 6: "juin",
    7: "juillet", 8: "août", 9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
}


def _date_vente(s: Soumission) -> date | None:
    """Date à laquelle le client a dit oui : approbation, ou conversion si pas d'approbation distincte."""
    return s.date_approbation or s.date_conversion


def _est_vendue(s: Soumission) -> bool:
    return s.statut in STATUTS_VENDUS


@dataclass
class VendeurVentes:
    nom: str
    envoyees_n: int
    envoyees_dollars: float
    vendues_n: int
    vendues_dollars: float
    vendues_dollars_precedente: float
    variation_pct: float | None  # None si rien à comparer (semaine précédente à 0$)
    taux_closing_hebdo: float | None  # None si 0 envoyée cette semaine
    taux_closing_cohorte: float | None  # None si 0 envoyée dans la fenêtre de cohorte
    cohorte_envoyees_n: int


@dataclass
class AlerteVente:
    type: str
    message: str


@dataclass
class RapportVentes:
    debut: date
    fin: date
    vendeurs: list[VendeurVentes]  # triés par $ vendus, décroissant
    equipe_envoyees_n: int
    equipe_envoyees_n_precedente: int
    equipe_vendues_dollars: float
    equipe_vendues_dollars_precedente: float
    equipe_variation_pct: float | None
    equipe_taux_closing_hebdo: float | None
    equipe_taux_closing_cohorte: float | None
    montant_moyen_vendu: float
    projection_fin_mois: float
    mois_precedent_total: float
    objectif_mensuel: float
    verdict_projection: str
    alertes: list[AlerteVente] = field(default_factory=list)


def _jours_ouvres(debut: date, fin: date) -> int:
    """Nombre de jours ouvrés (lundi-vendredi) dans [debut, fin] inclusivement. Pas de calendrier de congés fériés."""
    if fin < debut:
        return 0
    n = 0
    jour = debut
    while jour <= fin:
        if jour.weekday() < 5:
            n += 1
        jour += timedelta(days=1)
    return n


def _dernier_jour_mois(annee: int, mois: int) -> date:
    if mois == 12:
        return date(annee, 12, 31)
    return date(annee, mois + 1, 1) - timedelta(days=1)


def _mois_precedent(annee: int, mois: int) -> tuple[int, int]:
    return (annee - 1, 12) if mois == 1 else (annee, mois - 1)


def _taux(numerateur: int, denominateur: int) -> float | None:
    return numerateur / denominateur if denominateur else None


def _variation(actuel: float, precedent: float) -> float | None:
    return (actuel - precedent) / precedent if precedent else None


def construire_rapport_ventes(
    soumissions: list[Soumission], config: dict, debut: date, fin: date
) -> RapportVentes:
    vendeurs_config = config.get("vendeurs", [])
    seuil_closing = config.get("seuil_closing", 0.30)
    seuil_chute = config.get("seuil_chute_ventes", 0.25)
    jours_relance = config.get("jours_relance_soumission", 14)
    objectif_mensuel = config.get("objectif_mensuel", 0)

    debut_prec, fin_prec = debut - timedelta(days=7), fin - timedelta(days=7)
    # Cohorte : soumissions envoyées il y a 2 à 6 semaines (spec — taux de closing "le vrai").
    cohorte_fin, cohorte_debut = fin - timedelta(days=14), fin - timedelta(days=48)

    def _filtrer(champ_date: str, d0: date, d1: date) -> list[Soumission]:
        return [s for s in soumissions if getattr(s, champ_date) and d0 <= getattr(s, champ_date) <= d1]

    envoyees_semaine = _filtrer("date_envoi", debut, fin)
    envoyees_semaine_prec = _filtrer("date_envoi", debut_prec, fin_prec)
    vendues_semaine = [s for s in soumissions if _est_vendue(s) and _date_vente(s) and debut <= _date_vente(s) <= fin]
    vendues_semaine_prec = [
        s for s in soumissions if _est_vendue(s) and _date_vente(s) and debut_prec <= _date_vente(s) <= fin_prec
    ]
    cohorte = _filtrer("date_envoi", cohorte_debut, cohorte_fin)

    def _stats_vendeur(nom: str) -> VendeurVentes:
        env = [s for s in envoyees_semaine if s.vendeur == nom]
        env_prec = [s for s in envoyees_semaine_prec if s.vendeur == nom]
        vendu = [s for s in vendues_semaine if s.vendeur == nom]
        vendu_prec = [s for s in vendues_semaine_prec if s.vendeur == nom]
        coh = [s for s in cohorte if s.vendeur == nom]
        coh_vendues = sum(1 for s in coh if _est_vendue(s))

        vendues_dollars = sum(s.total for s in vendu)
        vendues_dollars_prec = sum(s.total for s in vendu_prec)

        return VendeurVentes(
            nom=nom,
            envoyees_n=len(env),
            envoyees_dollars=sum(s.total for s in env),
            vendues_n=len(vendu),
            vendues_dollars=vendues_dollars,
            vendues_dollars_precedente=vendues_dollars_prec,
            variation_pct=_variation(vendues_dollars, vendues_dollars_prec),
            taux_closing_hebdo=_taux(len(vendu), len(env)),
            taux_closing_cohorte=_taux(coh_vendues, len(coh)),
            cohorte_envoyees_n=len(coh),
        )

    lignes_vendeurs = [_stats_vendeur(nom) for nom in vendeurs_config]
    lignes_vendeurs.sort(key=lambda v: -v.vendues_dollars)

    # Équipe : toute l'entreprise (pas seulement les vendeurs configurés), pour que le
    # pacing/projection reflète le vrai chiffre d'affaires, pas juste celui des 3 vendeurs suivis.
    equipe_vendues_dollars = sum(s.total for s in vendues_semaine)
    equipe_vendues_dollars_prec = sum(s.total for s in vendues_semaine_prec)
    montant_moyen_vendu = equipe_vendues_dollars / len(vendues_semaine) if vendues_semaine else 0.0

    coh_vendues_total = sum(1 for s in cohorte if _est_vendue(s))

    # ---- Pacing / projection du mois ----
    annee_mois, mois_mois = fin.year, fin.month
    debut_mois = date(annee_mois, mois_mois, 1)
    jours_ouvres_ecoules = _jours_ouvres(debut_mois, fin)
    jours_ouvres_total = _jours_ouvres(debut_mois, _dernier_jour_mois(annee_mois, mois_mois))

    mois_a_date = sum(
        s.total for s in soumissions if _est_vendue(s) and _date_vente(s) and debut_mois <= _date_vente(s) <= fin
    )
    projection_fin_mois = (
        mois_a_date / jours_ouvres_ecoules * jours_ouvres_total if jours_ouvres_ecoules else 0.0
    )

    annee_prec, mois_prec_num = _mois_precedent(annee_mois, mois_mois)
    debut_mois_prec = date(annee_prec, mois_prec_num, 1)
    fin_mois_prec = _dernier_jour_mois(annee_prec, mois_prec_num)
    mois_precedent_total = sum(
        s.total
        for s in soumissions
        if _est_vendue(s) and _date_vente(s) and debut_mois_prec <= _date_vente(s) <= fin_mois_prec
    )

    verdict_projection = _construire_verdict(
        projection_fin_mois, mois_precedent_total, mois_prec_num, objectif_mensuel
    )

    # ---- Alertes ----
    alertes: list[AlerteVente] = []

    for v in lignes_vendeurs:
        if v.envoyees_n == 0:
            alertes.append(AlerteVente("zero_envoi", f"{v.nom} : 0 soumission envoyée cette semaine"))

    for v in lignes_vendeurs:
        if v.variation_pct is not None and v.variation_pct <= -seuil_chute:
            alertes.append(
                AlerteVente(
                    "chute_ventes",
                    f"{v.nom} : $ vendus en chute de {abs(v.variation_pct):.0%} vs semaine précédente "
                    f"({v.vendues_dollars:,.0f} $ vs {v.vendues_dollars_precedente:,.0f} $)".replace(",", " "),
                )
            )

    equipe_variation_pct = _variation(equipe_vendues_dollars, equipe_vendues_dollars_prec)
    if equipe_variation_pct is not None and equipe_variation_pct <= -seuil_chute:
        alertes.append(
            AlerteVente(
                "chute_ventes_equipe",
                f"Équipe : $ vendus en chute de {abs(equipe_variation_pct):.0%} vs semaine précédente "
                f"({equipe_vendues_dollars:,.0f} $ vs {equipe_vendues_dollars_prec:,.0f} $)".replace(",", " "),
            )
        )

    for v in lignes_vendeurs:
        if v.taux_closing_cohorte is not None and v.taux_closing_cohorte < seuil_closing:
            alertes.append(
                AlerteVente(
                    "closing_bas",
                    f"{v.nom} : taux de closing (cohorte) à {v.taux_closing_cohorte:.0%}, "
                    f"sous le seuil de {seuil_closing:.0%}",
                )
            )

    equipe_taux_closing_cohorte = _taux(coh_vendues_total, len(cohorte))
    if equipe_taux_closing_cohorte is not None and equipe_taux_closing_cohorte < seuil_closing:
        alertes.append(
            AlerteVente(
                "closing_bas_equipe",
                f"Équipe : taux de closing (cohorte) à {equipe_taux_closing_cohorte:.0%}, "
                f"sous le seuil de {seuil_closing:.0%}",
            )
        )

    aujourdhui = fin + timedelta(days=1)
    dormantes = [
        s
        for s in soumissions
        if s.statut == "awaiting_response"
        and s.date_envoi is not None
        and (aujourdhui - s.date_envoi).days > jours_relance
    ]
    if dormantes:
        alertes.append(
            AlerteVente(
                "soumissions_dormantes",
                f"{len(dormantes)} soumission(s) en attente depuis plus de {jours_relance} jours "
                f"({sum(s.total for s in dormantes):,.0f} $ dormants) — à relancer".replace(",", " "),
            )
        )

    return RapportVentes(
        debut=debut,
        fin=fin,
        vendeurs=lignes_vendeurs,
        equipe_envoyees_n=len(envoyees_semaine),
        equipe_envoyees_n_precedente=len(envoyees_semaine_prec),
        equipe_vendues_dollars=equipe_vendues_dollars,
        equipe_vendues_dollars_precedente=equipe_vendues_dollars_prec,
        equipe_variation_pct=equipe_variation_pct,
        equipe_taux_closing_hebdo=_taux(len(vendues_semaine), len(envoyees_semaine)),
        equipe_taux_closing_cohorte=equipe_taux_closing_cohorte,
        montant_moyen_vendu=montant_moyen_vendu,
        projection_fin_mois=projection_fin_mois,
        mois_precedent_total=mois_precedent_total,
        objectif_mensuel=objectif_mensuel,
        verdict_projection=verdict_projection,
        alertes=alertes,
    )


def _construire_verdict(projection: float, mois_precedent_total: float, mois_prec_num: int, objectif: float) -> str:
    nom_mois_prec = MOIS_FR[mois_prec_num]
    morceaux = [f"Projection : {projection:,.0f} $".replace(",", " ")]

    var_mois_prec = _variation(projection, mois_precedent_total)
    if var_mois_prec is not None:
        direction = "au-dessus de" if var_mois_prec >= 0 else "en dessous de"
        morceaux.append(f"{abs(var_mois_prec):.0%} {direction} {nom_mois_prec}")
    else:
        morceaux.append(f"{nom_mois_prec} n'a eu aucune vente (rien à comparer)")

    if objectif and objectif > 0:
        var_objectif = _variation(projection, objectif)
        if var_objectif is not None:
            statut = "en avance de" if var_objectif >= 0 else "en retard de"
            morceaux.append(f"{statut} {abs(var_objectif):.0%} sur l'objectif ({objectif:,.0f} $)".replace(",", " "))

    return " — ".join(morceaux)
