"""
Phase 2 — analyse Claude de la semaine (spec section "Phase 2 — Le cerveau").

Reçoit le résumé chiffré de la semaine + jusqu'à 4 semaines précédentes, et
produit 3-5 constats + des recommandations concrètes, en français québécois.

Dégradation gracieuse : si la clé API est absente, invalide, ou que l'appel
échoue pour n'importe quelle raison (réseau, quota, etc.), la fonction
retourne None et le reste du rapport (Slack + Excel) part normalement sans
la section analyse. Un rapport en retard ou incomplet est pire qu'un rapport
sans analyse IA.
"""

from __future__ import annotations

from rapport import RapportSemaine

MODELE = "claude-opus-4-8"

PROMPT_SYSTEME = (
    "Tu es l'analyste opérations de MAG Lavage À Pression (remise à neuf de "
    "pavé uni, Montréal). Ton lecteur est le proprio. Sois direct, concis, "
    "en français québécois, chiffres à l'appui. Priorise : jobs non "
    "rentables, dérive des coûts de main-d'œuvre, problèmes de qualité de "
    "données qui faussent les chiffres. Compare la semaine actuelle aux "
    "semaines précédentes fournies pour repérer des tendances (semaine sur "
    "semaine), si des données précédentes sont disponibles."
)

SCHEMA_SORTIE = {
    "type": "object",
    "properties": {
        "constats": {
            "type": "array",
            "description": "3 à 5 constats courts et directs, chiffres à l'appui",
            "items": {"type": "string"},
        },
        "recommandations": {
            "type": "array",
            "description": "Recommandations concrètes (pricing, dispatch, discipline de punch)",
            "items": {"type": "string"},
        },
    },
    "required": ["constats", "recommandations"],
    "additionalProperties": False,
}


def construire_payload(rapport: RapportSemaine) -> dict:
    """Résumé chiffré compact de la semaine, servant à la fois d'historique et de contexte Claude."""
    return {
        "debut": rapport.debut.isoformat(),
        "fin": rapport.fin.isoformat(),
        "revenu_ferme_total": round(rapport.revenu_ferme_total, 2),
        "heures_fermes_total": round(rapport.heures_fermes_total, 2),
        "dollars_heure_global": round(rapport.dollars_heure_global, 2),
        "cout_mo_ferme_total": round(rapport.cout_mo_ferme_total, 2),
        "pct_main_doeuvre": round(rapport.pct_main_doeuvre, 4),
        "heures_punchees_total": round(rapport.heures_punchees_total, 2),
        "heures_non_attribuees_total": round(rapport.heures_non_attribuees_total, 2),
        "jobs_fermes": [
            {
                "numero": l.numero,
                "client": l.client,
                "etape": l.etape,
                "revenu": round(l.revenu, 2),
                "heures": round(l.heures_attribuees, 2),
                "dollars_heure": round(l.dollars_heure, 2),
                "marge": round(l.marge, 2),
            }
            for l in rapport.jobs_fermes
        ],
        "alertes": [{"type": a.type, "message": a.message} for a in rapport.alertes],
    }


def analyser_semaine(
    api_key: str | None, semaine_actuelle: dict, semaines_precedentes: list[dict]
) -> dict | None:
    """
    Retourne {"constats": [...], "recommandations": [...]} ou None si l'analyse
    n'a pas pu être produite (clé absente/invalide, erreur API, etc.).
    """
    if not api_key or not api_key.strip():
        print("Analyse IA : pas de clé ANTHROPIC_API_KEY configurée, section ignorée.")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)

        contenu_utilisateur = (
            "Voici le résumé de la semaine actuelle, puis (si disponibles) des "
            "semaines précédentes, du plus ancien au plus récent, en JSON.\n\n"
            f"SEMAINE ACTUELLE:\n{semaine_actuelle}\n\n"
            f"SEMAINES PRÉCÉDENTES ({len(semaines_precedentes)}):\n{semaines_precedentes}"
        )

        reponse = client.messages.create(
            model=MODELE,
            max_tokens=2000,
            system=PROMPT_SYSTEME,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA_SORTIE}},
            messages=[{"role": "user", "content": contenu_utilisateur}],
        )

        if reponse.stop_reason == "refusal":
            print("Analyse IA : réponse refusée par Claude, section ignorée.")
            return None

        for bloc in reponse.content:
            if bloc.type == "text":
                import json

                return json.loads(bloc.text)

        print("Analyse IA : aucune réponse texte trouvée, section ignorée.")
        return None

    except Exception as e:  # noqa: BLE001 — toute erreur ne doit jamais bloquer le rapport
        print(f"Analyse IA : échec ({type(e).__name__}: {e}), section ignorée.")
        return None
