"""
Client GraphQL générique pour l'API Jobber.

Gère automatiquement le rafraîchissement de l'access_token via le refresh_token
(l'access_token expire après 60 minutes, voir jobber_auth.py).
"""

from __future__ import annotations

import time

import requests

from jobber_auth import rafraichir_token

URL_GRAPHQL = "https://api.getjobber.com/api/graphql"

# Dernière version documentée au moment d'écrire ce code (voir changelog Jobber) :
# https://developer.getjobber.com/docs/using_jobbers_api/api_versioning
# À vérifier périodiquement et mettre à jour au besoin.
VERSION_API = "2025-04-16"


class ClientJobber:
    """
    Enveloppe l'API GraphQL de Jobber avec gestion automatique du token.

    Rotation des refresh tokens : DÉSACTIVÉE sur l'app MAG, confirmé
    empiriquement le 19 juillet 2026 (deux appels successifs à
    /oauth/token avec le même refresh_token d'origine renvoient chacun
    un access_token valide et le MÊME refresh_token, pas un nouveau).
    Un commentaire précédent affirmait le contraire ("rotation confirmée
    active") — c'était erroné ; voir README.md, qui a toujours dit
    "rotation désactivée côté Jobber — token stable".

    sur_nouveau_refresh_token reste géré par précaution (si Jobber active un
    jour la rotation, ou pour un autre compte où elle le serait) : dans le cas
    normal actuel, il ne sera simplement jamais appelé.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        sur_nouveau_refresh_token=None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = None
        self.sur_nouveau_refresh_token = sur_nouveau_refresh_token
        self._rafraichir()

    def _rafraichir(self):
        tokens = rafraichir_token(self.client_id, self.client_secret, self.refresh_token)
        self.access_token = tokens["access_token"]
        nouveau_refresh_token = tokens.get("refresh_token")
        if nouveau_refresh_token and nouveau_refresh_token != self.refresh_token:
            self.refresh_token = nouveau_refresh_token
            if self.sur_nouveau_refresh_token:
                self.sur_nouveau_refresh_token(nouveau_refresh_token)

    def _poster(self, requete: str, variables: dict | None):
        return requests.post(
            URL_GRAPHQL,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
                "X-JOBBER-GRAPHQL-VERSION": VERSION_API,
            },
            json={"query": requete, "variables": variables or {}},
            timeout=60,
        )

    def executer(self, requete: str, variables: dict | None = None, tentative: int = 0) -> dict:
        """
        Exécute une requête (ou mutation) GraphQL et retourne les données.

        Gère automatiquement : le rafraîchissement du token sur 401, et un
        backoff simple sur le throttling par coût de requête (voir
        https://developer.getjobber.com/docs/using_jobbers_api/api_rate_limits).
        """
        reponse = self._poster(requete, variables)

        # Un token expiré donne un 401 : on rafraîchit une fois et on réessaie.
        if reponse.status_code == 401:
            self._rafraichir()
            reponse = self._poster(requete, variables)

        reponse.raise_for_status()
        corps = reponse.json()

        if "errors" in corps:
            codes = {e.get("extensions", {}).get("code") for e in corps["errors"]}
            if "THROTTLED" in codes and tentative < 3:
                time.sleep(2 * (tentative + 1))
                return self.executer(requete, variables, tentative + 1)
            raise RuntimeError(f"Erreur GraphQL Jobber : {corps['errors']}")

        return corps["data"]
