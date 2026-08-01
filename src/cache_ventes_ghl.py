"""Petit utilitaire de cache pour le module Dashboard Ventes GHL (voir
ventes_ghl_source.py — pourquoi ce cache existe et comment il est utilisé)."""

from __future__ import annotations

import json

from env_utils import RACINE

CHEMIN_CACHE = RACINE / "cache_ventes_ghl.json"


def charger_cache() -> dict:
    if CHEMIN_CACHE.exists():
        return json.loads(CHEMIN_CACHE.read_text(encoding="utf-8"))
    return {"opportunites": {}}


def sauvegarder_cache(cache: dict, chemin=CHEMIN_CACHE):
    chemin.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
