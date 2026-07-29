"""
Sync soumissions Jobber -> GHL — étape 1 : auth Jobber + query des soumissions
envoyées des 30 derniers jours, affichage seulement (voir
spec-sync-soumissions-jobber-ghl.md, Ordre de travail, point 1).

Aucune écriture nulle part (ni GHL, ni fichier) : ce script sert uniquement à
valider le schéma GraphQL et regarder les données réelles avant de coder le
matching GHL (étape 2).

Champs vérifiés par introspection contre l'API Jobber en direct (le spec ne
donnait que des noms indicatifs) :
  - Quote.amounts.subtotal existe tel quel.
  - Client.emails et Client.phones ne sont PAS des listes de scalaires : ce
    sont des listes d'objets (Email { address primary }, ClientPhoneNumber
    { number normalizedPhoneNumber primary }).
  - Il n'existe aucun statut "sent" dans QuoteStatusTypeEnum (valeurs réelles :
    draft, awaiting_response, archived, approved, converted,
    changes_requested). Une soumission "envoyée" se détecte par `sentAt` non
    nul, pas par un statut nommé.
  - QuoteFilterAttributes expose bien un filtre `sentAt`, mais il est CASSÉ
    côté serveur : `quotes(filter: { sentAt: { after, before } })` retourne
    systématiquement `totalCount: 0`, même sur une fenêtre où l'on sait (via
    `updatedAt`/`createdAt`) qu'il existe des soumissions envoyées. Vérifié
    sur les deux versions d'API valides testées (2025-01-20 et 2026-04-16,
    la plus récente acceptée par l'endpoint en ce moment) : même résultat
    sur les deux. Contournement utilisé ci-dessous : filtrer par `updatedAt`
    (confirmé fonctionnel) puis filtrer `sentAt` côté client. C'est
    équivalent mathématiquement pour une fenêtre glissante, puisque
    `updatedAt >= sentAt` est toujours vrai (envoyer une soumission MET À
    JOUR la soumission) : toute soumission avec `sentAt` dans les 30
    derniers jours a nécessairement `updatedAt` dans les 30 derniers jours
    aussi.

Usage : python3 src/valider_etape1_sync_soumissions.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from env_utils import CHEMIN_ENV, maj_env
from jobber_client import ClientJobber

FUSEAU_MAG = ZoneInfo("America/Montreal")
N_JOURS = 30


def _vers_iso(jour: date, fin_de_journee: bool = False) -> str:
    heure = "23:59:59" if fin_de_journee else "00:00:00"
    dt_local = datetime.fromisoformat(f"{jour.isoformat()}T{heure}").replace(tzinfo=FUSEAU_MAG)
    return dt_local.astimezone(ZoneInfo("UTC")).isoformat()


def _vers_date_locale(iso_datetime: str | None) -> date | None:
    if not iso_datetime:
        return None
    dt = datetime.fromisoformat(iso_datetime.replace("Z", "+00:00"))
    return dt.astimezone(FUSEAU_MAG).date()


REQUETE_QUOTES_ENVOYEES = """
query QuotesEnvoyees($debut: ISO8601DateTime!, $fin: ISO8601DateTime!, $after: String) {
  quotes(filter: { updatedAt: { after: $debut, before: $fin } }, first: 50, after: $after) {
    nodes {
      quoteNumber
      quoteStatus
      sentAt
      updatedAt
      amounts { subtotal }
      client {
        firstName
        lastName
        emails { address primary }
        phones { number normalizedPhoneNumber primary }
      }
      property { address { postalCode } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _paginer(client: ClientJobber, requete: str, variables: dict, chemin: list):
    tous_les_nodes = []
    apres = None
    while True:
        data = client.executer(requete, {**variables, "after": apres})
        for cle in chemin:
            data = data[cle]
        tous_les_nodes.extend(data["nodes"])
        if not data["pageInfo"]["hasNextPage"]:
            break
        apres = data["pageInfo"]["endCursor"]
    return tous_les_nodes


def _valeur_primaire(items: list[dict], cle: str) -> str:
    """Retourne la valeur primary=True, ou la première dispo, ou ''."""
    if not items:
        return ""
    for item in items:
        if item.get("primary"):
            return item.get(cle, "") or ""
    return items[0].get(cle, "") or ""


def obtenir_quotes_envoyees(client: ClientJobber, debut: date, fin: date) -> list[dict]:
    """
    Soumissions dont le SENTAT tombe dans [debut, fin] (bornes incluses, jours
    locaux MAG). Filtre server-side sur `updatedAt` (le filtre `sentAt` est
    cassé côté API Jobber, voir docstring du module), puis filtre `sentAt`
    côté client sur le résultat.

    Utilisée pour les fenêtres fixes (étapes 1-3, tests manuels, --since /
    backfill). Pour le polling incrémental en continu (cron), voir
    obtenir_quotes_maj_depuis ci-dessous — la différence compte : une
    soumission envoyée il y a 2 mois mais approuvée hier a un `sentAt` hors
    fenêtre mais doit quand même être captée par le cron.
    """
    variables = {"debut": _vers_iso(debut), "fin": _vers_iso(fin, fin_de_journee=True)}
    nodes = _paginer(client, REQUETE_QUOTES_ENVOYEES, variables, ["quotes"])
    return [n for n in nodes if n["sentAt"] and debut <= _vers_date_locale(n["sentAt"]) <= fin]


def obtenir_quotes_maj_depuis(client: ClientJobber, depuis: datetime) -> list[dict]:
    """
    Soumissions déjà envoyées (sentAt non nul, peu importe quand) dont
    `updatedAt` est postérieur à `depuis`. C'est la requête du cron
    incrémental (spec, Flow point 2 : "soumissions avec updatedAt > dernier
    run et statut envoyé/approuvé") — contrairement à obtenir_quotes_envoyees,
    elle capte aussi les changements de statut (approbation, conversion) sur
    une soumission envoyée bien avant `depuis`.
    """
    variables = {"debut": depuis.astimezone(ZoneInfo("UTC")).isoformat(), "fin": _vers_iso(date.today() + timedelta(days=1), fin_de_journee=True)}
    nodes = _paginer(client, REQUETE_QUOTES_ENVOYEES, variables, ["quotes"])
    return [n for n in nodes if n["sentAt"]]


def main():
    config = dotenv_values(CHEMIN_ENV)
    client = ClientJobber(
        config["JOBBER_CLIENT_ID"],
        config["JOBBER_CLIENT_SECRET"],
        config["JOBBER_REFRESH_TOKEN"],
        sur_nouveau_refresh_token=lambda t: maj_env("JOBBER_REFRESH_TOKEN", t),
    )

    fin = date.today()
    debut = fin - timedelta(days=N_JOURS)

    print("=" * 100)
    print(f"SYNC SOUMISSIONS — étape 1 — soumissions envoyées du {debut} au {fin} ({N_JOURS} jours)")
    print("=" * 100)

    quotes = obtenir_quotes_envoyees(client, debut, fin)
    print(f"\n{len(quotes)} soumission(s) envoyée(s) trouvée(s).\n")

    if not quotes:
        return

    lignes = []
    for q in quotes:
        c = q.get("client") or {}
        nom = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
        email = _valeur_primaire(c.get("emails") or [], "address")
        telephone = _valeur_primaire(c.get("phones") or [], "normalizedPhoneNumber") or _valeur_primaire(
            c.get("phones") or [], "number"
        )
        sous_total = q["amounts"]["subtotal"] or 0.0
        lignes.append(
            {
                "numero": q["quoteNumber"],
                "client": nom,
                "email": email,
                "telephone": telephone,
                "sous_total": sous_total,
                "statut": q["quoteStatus"],
                "date_envoi": _vers_date_locale(q["sentAt"]),
            }
        )

    largeur_client = max(len(l["client"]) for l in lignes)
    largeur_client = max(largeur_client, len("Client"))
    largeur_email = max(len(l["email"]) for l in lignes)
    largeur_email = max(largeur_email, len("Email"))

    entete = (
        f"  {'#':<8}  {'Client':<{largeur_client}}  {'Email':<{largeur_email}}  "
        f"{'Téléphone':<15}  {'Sous-total':>12}  {'Statut':<20}  {'Envoyée le':<10}"
    )
    print(entete)
    print("  " + "-" * (len(entete) - 2))
    for l in lignes:
        print(
            f"  {l['numero']:<8}  {l['client']:<{largeur_client}}  {l['email']:<{largeur_email}}  "
            f"{l['telephone']:<15}  {l['sous_total']:>12,.2f}  {l['statut']:<20}  {str(l['date_envoi']):<10}"
        )

    print(f"\nTotal sous-total : {sum(l['sous_total'] for l in lignes):,.2f} $")


if __name__ == "__main__":
    main()
