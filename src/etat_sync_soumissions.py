"""
Persistance du timestamp du dernier run du sync soumissions Jobber -> GHL (voir
spec-sync-soumissions-jobber-ghl.md, Flow, point 1 : "Lire le timestamp du
dernier run (fichier d'état commité dans le repo...)").

Même approche que historique.py : un fichier JSON commité au repo par le
GitHub Action (les runners sont éphémères, rien ne survit sans ça).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_ETAT = RACINE / "etat_sync_soumissions.json"


def lire_dernier_run(chemin: Path = CHEMIN_ETAT) -> datetime | None:
    if not chemin.exists():
        return None
    data = json.loads(chemin.read_text(encoding="utf-8"))
    return datetime.fromisoformat(data["dernier_run"])


def sauvegarder_dernier_run(moment: datetime, chemin: Path = CHEMIN_ETAT):
    chemin.write_text(
        json.dumps({"dernier_run": moment.astimezone(timezone.utc).isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )
