"""
Client GHL (GoHighLevel API v2, sub-account MAG) — utilitaires partagés pour
le module Dashboard Ventes (opportunités / contacts / conversations / tâches /
notes). Reprend l'auth et la pagination déjà validées dans
valider_etape2_sync_soumissions.py (endpoints vérifiés contre la doc
officielle HighLevel, marketplace.gohighlevel.com/docs) : Bearer token +
header Version: v3, pagination opportunities/search via meta.startAfter /
meta.startAfterId.

Rate limit GHL (doc officielle) : 100 requêtes / 10s (burst) et 200 000/jour,
par app par location. DELAI_ENTRE_APPELS et la reprise avec backoff sur 429
gardent une marge large sous ces seuils.

Les endpoints conversations/tasks/notes n'ont pas pu être confirmés à 100%
contre des données réelles avant le premier run de explorer_ventes_ghl.py
(doc publique peu détaillée sur ces trois-là) — voir les commentaires
"forme à confirmer" ci-dessous, ajustés une fois le script d'exploration
lancé sur le vrai compte.
"""

from __future__ import annotations

import time

import requests

URL_GHL = "https://services.leadconnectorhq.com"
VERSION_GHL = "v3"
DELAI_ENTRE_APPELS = 0.15  # ~6-7 req/s, cohérent avec sync_soumissions_ghl.py, large marge sous 100/10s
MAX_TENTATIVES_429 = 5


def en_tete_ghl(config: dict) -> dict:
    return {
        "Authorization": f"Bearer {config['GHL_PRIVATE_TOKEN']}",
        "Version": VERSION_GHL,
        "Accept": "application/json",
    }


def appel_ghl(config: dict, methode: str, chemin: str, params: dict | None = None, tentative: int = 0) -> dict:
    """GET/POST générique avec pause anti-rate-limit et reprise avec backoff sur 429."""
    headers = en_tete_ghl(config)
    r = requests.request(methode, f"{URL_GHL}{chemin}", headers=headers, params=params, timeout=30)
    if r.status_code == 429 and tentative < MAX_TENTATIVES_429:
        attente = float(r.headers.get("Retry-After", 2 * (tentative + 1)))
        time.sleep(attente)
        return appel_ghl(config, methode, chemin, params, tentative + 1)
    r.raise_for_status()
    time.sleep(DELAI_ENTRE_APPELS)
    return r.json()


def obtenir_pipelines(config: dict) -> list[dict]:
    """Toutes les pipelines de la location, avec leurs stages (id + name), dans l'ordre GHL."""
    data = appel_ghl(config, "GET", "/opportunities/pipelines", params={"locationId": config["GHL_LOCATION_ID"]})
    return data["pipelines"]


def obtenir_opportunites_pipeline(config: dict, pipeline_id: str) -> list[dict]:
    """Toutes les opportunités (tout statut) d'une pipeline donnée, paginées."""
    opportunites = []
    params = {
        "locationId": config["GHL_LOCATION_ID"],
        "pipelineId": pipeline_id,
        "status": "all",
        "limit": 100,
    }
    while True:
        data = appel_ghl(config, "GET", "/opportunities/search", params=params)
        opportunites.extend(data["opportunities"])
        meta = data.get("meta") or {}
        if not meta.get("startAfter") or len(data["opportunities"]) < params["limit"]:
            break
        params = {**params, "startAfter": meta["startAfter"], "startAfterId": meta["startAfterId"]}
    return opportunites


def obtenir_conversations_contact(config: dict, contact_id: str) -> list[dict]:
    """Forme à confirmer empiriquement (voir explorer_ventes_ghl.py) : la doc publique
    ne détaille pas le corps de la réponse au-delà de "liste de conversations"."""
    data = appel_ghl(
        config,
        "GET",
        "/conversations/search",
        params={"locationId": config["GHL_LOCATION_ID"], "contactId": contact_id, "limit": 20},
    )
    return data.get("conversations", [])


def obtenir_messages_conversation(config: dict, conversation_id: str) -> list[dict]:
    """Forme à confirmer empiriquement : certaines versions de la doc GHL montrent
    un objet imbriqué {"messages": {"messages": [...]}}, d'autres un tableau plat
    {"messages": [...]}. explorer_ventes_ghl.py gère les deux et signale laquelle
    est la vraie sur ce compte."""
    data = appel_ghl(config, "GET", f"/conversations/{conversation_id}/messages", params={"limit": 100})
    brut = data.get("messages")
    if isinstance(brut, dict):
        return brut.get("messages", [])
    return brut or []


def obtenir_taches_contact(config: dict, contact_id: str) -> list[dict]:
    data = appel_ghl(config, "GET", f"/contacts/{contact_id}/tasks")
    return data.get("tasks", [])


def obtenir_notes_contact(config: dict, contact_id: str) -> list[dict]:
    data = appel_ghl(config, "GET", f"/contacts/{contact_id}/notes")
    return data.get("notes", [])


def tester_scope(config: dict, methode: str, chemin: str, params: dict | None = None) -> tuple[bool, str | None]:
    """Teste un endpoint sans lever d'exception — utilisé en préflight pour détecter
    les scopes manquants sur le Private Integration Token (401 'not authorized for
    this scope', différent d'un 401 d'auth invalide) avant de lancer une boucle
    qui planterait sur la première itération."""
    headers = en_tete_ghl(config)
    r = requests.request(methode, f"{URL_GHL}{chemin}", headers=headers, params=params, timeout=30)
    time.sleep(DELAI_ENTRE_APPELS)
    if r.ok:
        return True, None
    try:
        message = r.json().get("message", r.text[:200])
    except ValueError:
        message = r.text[:200]
    return False, f"{r.status_code} — {message}"


_CACHE_UTILISATEURS: dict[str, str] = {}


def obtenir_nom_utilisateur(config: dict, user_id: str | None) -> str:
    """Résout un userId GHL (assignedTo) en nom affichable. Mis en cache (peu
    d'utilisateurs, appelé une fois par vendeur)."""
    if not user_id:
        return "(non assigné)"
    if user_id not in _CACHE_UTILISATEURS:
        try:
            data = appel_ghl(config, "GET", f"/users/{user_id}")
            u = data.get("user") or data
            nom = u.get("name") or f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()
            _CACHE_UTILISATEURS[user_id] = nom or user_id
        except requests.HTTPError:
            _CACHE_UTILISATEURS[user_id] = f"(userId {user_id}, résolution échouée)"
    return _CACHE_UTILISATEURS[user_id]
