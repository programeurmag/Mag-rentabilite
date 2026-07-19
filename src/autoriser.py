"""
Script interactif à exécuter UNE SEULE FOIS en local pour autoriser l'app
Jobber et obtenir un refresh_token (ensuite réutilisé chaque semaine).

Usage : python3 src/autoriser.py
"""

import webbrowser
from urllib.parse import parse_qs, urlparse

from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_auth import construire_url_autorisation, echanger_code


def _extraire_code(entree_utilisateur: str) -> str:
    """Accepte soit le code brut, soit l'URL complète de redirection collée."""
    entree_utilisateur = entree_utilisateur.strip()
    if entree_utilisateur.startswith("http"):
        query = parse_qs(urlparse(entree_utilisateur).query)
        if "code" not in query:
            raise ValueError("Aucun paramètre 'code' trouvé dans l'URL collée.")
        return query["code"][0]
    return entree_utilisateur


def main():
    config = dotenv_values(CHEMIN_ENV)
    client_id = config["JOBBER_CLIENT_ID"]
    client_secret = config["JOBBER_CLIENT_SECRET"]
    redirect_uri = config["JOBBER_REDIRECT_URI"]

    url_autorisation, state = construire_url_autorisation(client_id, redirect_uri)

    print("Ouverture du navigateur pour autoriser l'app MAG Rentabilité...")
    print(f"Si le navigateur ne s'ouvre pas, copie cette URL manuellement :\n{url_autorisation}\n")
    webbrowser.open(url_autorisation)

    print(
        "Après avoir cliqué 'Allow Access' dans Jobber, tu vas être redirigé vers "
        f"{redirect_uri}?code=...&state=...\n"
        "Cette page peut afficher une erreur (404, etc.) — c'est normal, ce qui compte "
        "c'est l'URL affichée dans la barre d'adresse du navigateur."
    )
    entree = input("\nColle ici l'URL complète de redirection (ou juste le code) : ")
    code = _extraire_code(entree)

    tokens = echanger_code(client_id, client_secret, code, redirect_uri)

    if "refresh_token" not in tokens:
        print("Erreur : pas de refresh_token dans la réponse Jobber :", tokens)
        return

    maj_env("JOBBER_REFRESH_TOKEN", tokens["refresh_token"])
    print("\nRefresh token sauvegardé dans .env avec succès.")
    print("Ajoute aussi cette valeur comme secret GitHub (JOBBER_REFRESH_TOKEN) pour l'étape 4.")


if __name__ == "__main__":
    main()
