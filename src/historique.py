"""
Persistance d'un résumé de chaque semaine (Phase 2 : sert de mémoire pour
l'analyse Claude, qui compare la semaine actuelle aux ~4 précédentes).

Un fichier JSON compact par semaine dans historique/, commité au repo par le
GitHub Action (les runners sont éphémères, donc rien ne survit sans ça).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
DOSSIER_HISTORIQUE = RACINE / "historique"


def _chemin_semaine(debut: date, dossier: Path = DOSSIER_HISTORIQUE) -> Path:
    return dossier / f"semaine_{debut.isoformat()}.json"


def sauvegarder_semaine(payload: dict, dossier: Path = DOSSIER_HISTORIQUE):
    """Sauvegarde le résumé de la semaine (payload produit par analyse_claude.construire_payload)."""
    dossier.mkdir(exist_ok=True)
    debut = date.fromisoformat(payload["debut"])
    _chemin_semaine(debut, dossier).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def charger_semaines_precedentes(
    avant: date, n: int = 4, dossier: Path = DOSSIER_HISTORIQUE
) -> list[dict]:
    """
    Charge jusqu'à n résumés de semaines précédant `avant`, triés du plus
    ancien au plus récent (pratique pour montrer une tendance à Claude).
    """
    if not dossier.exists():
        return []

    candidats = []
    for i in range(1, 27):  # cherche jusqu'à ~6 mois en arrière
        debut_candidat = avant - timedelta(days=7 * i)
        chemin = _chemin_semaine(debut_candidat, dossier)
        if chemin.exists():
            candidats.append(json.loads(chemin.read_text(encoding="utf-8")))
        if len(candidats) >= n:
            break

    return list(reversed(candidats))
