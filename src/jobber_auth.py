"""
Authentification OAuth 2.0 avec l'API Jobber (authorization code flow).

Référence : https://developer.getjobber.com/docs/building_your_app/app_authorization

Flux complet :
  1. (une seule fois, manuellement) construire_url_autorisation() -> l'utilisateur
     ouvre l'URL dans son navigateur, se connecte à Jobber, clique "Allow Access".
  2. Jobber redirige vers JOBBER_REDIRECT_URI avec un paramètre ?code=...
  3. echanger_code() échange ce code contre un access_token + refresh_token.
  4. Le refresh_token est stocké (dans .env en local, en secret GitHub en prod) et
     rafraichir_token() sert à obtenir un nouvel access_token à chaque exécution
     du rapport hebdomadaire (l'access_token expire après 60 minutes).
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import requests

URL_AUTORISATION = "https://api.getjobber.com/api/oauth/authorize"
URL_TOKEN = "https://api.getjobber.com/api/oauth/token"


def construire_url_autorisation(client_id: str, redirect_uri: str, state: str | None = None) -> str:
    """Construit l'URL que l'utilisateur doit ouvrir pour autoriser l'app."""
    state = state or secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{URL_AUTORISATION}?{urlencode(params)}", state


def echanger_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    """Échange le code d'autorisation contre un access_token + refresh_token."""
    reponse = requests.post(
        URL_TOKEN,
        headers={"Content-Type": "application/json"},
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    reponse.raise_for_status()
    return reponse.json()


def rafraichir_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """
    Utilise le refresh_token pour obtenir un nouvel access_token.

    Attention : si la rotation des refresh tokens est activée sur l'app Jobber,
    la réponse contient un NOUVEAU refresh_token qu'il faut sauvegarder (l'ancien
    ne doit plus jamais être réutilisé). Voir Refresh Token Rotation dans la doc.
    """
    reponse = requests.post(
        URL_TOKEN,
        headers={"Content-Type": "application/json"},
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    reponse.raise_for_status()
    return reponse.json()
