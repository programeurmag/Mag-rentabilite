"""
Calcul des 5 métriques du Dashboard Ventes GHL, par vendeur et pour l'équipe.

Deux sources combinées (décision de Justin, 2026-08-01) :
  - Métriques 1-3 (contact même jour, visite bookée, rappel 48h) : GHL, à
    partir d'OpportuniteVente (voir ventes_ghl_source.py).
  - Métriques 4-5 (taux de closing, valeur moyenne won) : Jobber, à partir de
    SoumissionJobber (voir ventes_jobber_source.py) — le passage en pipeline
    GHL CLOSING ne veut pas dire qu'une vraie soumission chiffrée a été
    envoyée, ce qui écrasait artificiellement le taux de closing.

Ne fait aucun appel réseau — pure logique de calcul, testable sans API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ventes_ghl_source import OpportuniteVente
from ventes_jobber_source import SoumissionJobber

MAX_NOMS_CONSEIL = 10


@dataclass
class MetriquesVendeur:
    nom: str
    n_leads: int
    n_contactes_meme_jour: int
    pct_contact_meme_jour: float | None
    n_visite_bookee: int
    pct_visite_bookee: float | None
    n_soumissions_ghl: int  # opportunités en CLOSING (GHL) — base du rappel 48h seulement
    n_soumissions_avec_rappel: int
    pct_rappel_48h: float | None
    n_soumissions_envoyees: int  # soumissions Jobber envoyées (sentAt) — base du closing
    n_won: int
    pct_closing: float | None
    valeur_moyenne_won: float | None
    leads_non_contactes: list[str] = field(default_factory=list)
    soumissions_sans_rappel: list[str] = field(default_factory=list)


def _pct(numerateur: int, denominateur: int) -> float | None:
    return 100 * numerateur / denominateur if denominateur else None


def calculer_metriques(
    opps: list[OpportuniteVente], soumissions: list[SoumissionJobber], nom_vendeur: str | None = None
) -> MetriquesVendeur:
    """nom_vendeur=None -> agrégat équipe (toutes les données passées)."""
    opps_pertinentes = opps if nom_vendeur is None else [o for o in opps if o.vendeur == nom_vendeur]
    soum_pertinentes = soumissions if nom_vendeur is None else [s for s in soumissions if s.vendeur == nom_vendeur]

    contactes = [o for o in opps_pertinentes if o.contacte_meme_jour]
    visite = [o for o in opps_pertinentes if o.visite_bookee]
    soumissions_ghl = [o for o in opps_pertinentes if o.soumission_envoyee]
    soumissions_avec_rappel = [o for o in soumissions_ghl if o.rappel_48h]

    won = [s for s in soum_pertinentes if s.won]

    return MetriquesVendeur(
        nom=nom_vendeur or "Équipe",
        n_leads=len(opps_pertinentes),
        n_contactes_meme_jour=len(contactes),
        pct_contact_meme_jour=_pct(len(contactes), len(opps_pertinentes)),
        n_visite_bookee=len(visite),
        pct_visite_bookee=_pct(len(visite), len(opps_pertinentes)),
        n_soumissions_ghl=len(soumissions_ghl),
        n_soumissions_avec_rappel=len(soumissions_avec_rappel),
        pct_rappel_48h=_pct(len(soumissions_avec_rappel), len(soumissions_ghl)),
        n_soumissions_envoyees=len(soum_pertinentes),
        n_won=len(won),
        pct_closing=_pct(len(won), len(soum_pertinentes)),
        valeur_moyenne_won=(sum(s.montant for s in won) / len(won)) if won else None,
        leads_non_contactes=[o.nom for o in opps_pertinentes if not o.contacte_meme_jour][:MAX_NOMS_CONSEIL],
        soumissions_sans_rappel=[o.nom for o in soumissions_ghl if o.rappel_48h is False][:MAX_NOMS_CONSEIL],
    )


def couleur_seuil(valeur: float | None, seuils: dict) -> str | None:
    """"vert" si >= seuils['vert'], "rouge" si < seuils['rouge'], "jaune" entre les deux, None si pas de valeur."""
    if valeur is None:
        return None
    if valeur >= seuils["vert"]:
        return "vert"
    if valeur >= seuils["rouge"]:
        return "jaune"
    return "rouge"


def calculer_metriques_equipe_et_vendeurs(
    opps: list[OpportuniteVente], soumissions: list[SoumissionJobber], noms_vendeurs: list[str]
) -> tuple[MetriquesVendeur, list[MetriquesVendeur]]:
    equipe = calculer_metriques(opps, soumissions, None)
    vendeurs = [calculer_metriques(opps, soumissions, nom) for nom in noms_vendeurs]
    return equipe, vendeurs
