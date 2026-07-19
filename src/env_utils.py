"""Petit utilitaire pour mettre à jour une clé du fichier .env sans toucher au reste."""

from __future__ import annotations

import re
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CHEMIN_ENV = RACINE / ".env"


def maj_env(cle: str, valeur: str, chemin: Path = CHEMIN_ENV):
    """Met à jour (ou ajoute) une clé dans le fichier .env."""
    lignes = chemin.read_text(encoding="utf-8").splitlines()
    trouve = False
    for i, ligne in enumerate(lignes):
        if re.match(rf"^{re.escape(cle)}=", ligne):
            lignes[i] = f"{cle}={valeur}"
            trouve = True
            break
    if not trouve:
        lignes.append(f"{cle}={valeur}")
    chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
