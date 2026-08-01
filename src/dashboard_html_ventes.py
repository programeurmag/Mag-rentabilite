"""
Rendu HTML statique — Module Dashboard Ventes GHL. Ne contient aucune logique
métier (seuils, conseils) : reçoit des valeurs déjà formatées/calculées par
dashboard_ventes_ghl.py et ne fait que construire le balisage. Page unique,
autonome (CSS inline, pas de dépendance externe), sobre, lisible sur une TV de
bureau comme sur mobile.
"""

from __future__ import annotations

from dataclasses import dataclass

COULEURS = {
    "vert": "#1e7a34",
    "jaune": "#b8860b",
    "rouge": "#c22b2b",
    None: "#6b7280",
}
FONDS = {
    "vert": "#eaf6ec",
    "jaune": "#fdf3e0",
    "rouge": "#fbe9e9",
    None: "#f1f2f4",
}


@dataclass
class MetriqueAffichee:
    label: str
    valeur_texte: str
    couleur: str | None  # "vert" | "jaune" | "rouge" | None (pas de seuil applicable)
    conseil: str | None = None


@dataclass
class CarteVendeur:
    nom: str
    metriques: list[MetriqueAffichee]


def _badge(m: MetriqueAffichee) -> str:
    fond, couleur = FONDS[m.couleur], COULEURS[m.couleur]
    conseil_html = f'<div class="conseil">{m.conseil}</div>' if m.conseil else ""
    return f"""
    <div class="metrique" style="background:{fond}">
      <div class="metrique-tete">
        <span class="metrique-label">{m.label}</span>
        <span class="metrique-valeur" style="color:{couleur}">{m.valeur_texte}</span>
      </div>
      {conseil_html}
    </div>"""


def _carte(c: CarteVendeur) -> str:
    return f"""
    <div class="carte">
      <h2>{c.nom}</h2>
      {''.join(_badge(m) for m in c.metriques)}
    </div>"""


def _tendance(label: str, valeur_texte: str, delta_texte: str | None, delta_positif: bool | None) -> str:
    if delta_texte is None:
        fleche = ""
    else:
        couleur = "#1e7a34" if delta_positif else "#c22b2b"
        symbole = "▲" if delta_positif else "▼"
        fleche = f'<span style="color:{couleur}"> {symbole} {delta_texte}</span>'
    return f"""
    <div class="stat-equipe">
      <div class="stat-equipe-label">{label}</div>
      <div class="stat-equipe-valeur">{valeur_texte}{fleche}</div>
    </div>"""


def generer_page(
    genere_le_texte: str,
    periode_texte: str,
    stats_equipe: list[tuple[str, str, str | None, bool | None]],  # (label, valeur, delta_texte, delta_positif)
    cartes: list[CarteVendeur],
) -> str:
    blocs_equipe = "".join(_tendance(*s) for s in stats_equipe)
    blocs_cartes = "".join(_carte(c) for c in cartes)

    return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="600">
<title>Dashboard Ventes MAG</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: clamp(16px, 2vw, 40px);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: #f7f7f8;
    color: #1a1a1a;
  }}
  header {{
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: clamp(16px, 1.5vw, 28px);
  }}
  h1 {{ font-size: clamp(22px, 2.4vw, 34px); margin: 0; }}
  .meta {{ color: #6b7280; font-size: clamp(13px, 1vw, 16px); }}
  .bandeau-equipe {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: clamp(14px, 1.6vw, 24px);
    margin-bottom: clamp(20px, 2vw, 36px);
  }}
  .stat-equipe-label {{ font-size: clamp(12px, 0.9vw, 14px); color: #6b7280; margin-bottom: 4px; }}
  .stat-equipe-valeur {{ font-size: clamp(20px, 2vw, 28px); font-weight: 600; }}
  .grille-vendeurs {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: clamp(14px, 1.6vw, 22px);
  }}
  .carte {{
    background: #ffffff;
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: clamp(16px, 1.6vw, 24px);
  }}
  .carte h2 {{ font-size: clamp(18px, 1.6vw, 22px); margin: 0 0 14px; }}
  .metrique {{ border-radius: 10px; padding: 10px 14px; margin-bottom: 10px; }}
  .metrique:last-child {{ margin-bottom: 0; }}
  .metrique-tete {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }}
  .metrique-label {{ font-size: clamp(13px, 1vw, 15px); color: #374151; }}
  .metrique-valeur {{ font-size: clamp(17px, 1.4vw, 21px); font-weight: 700; white-space: nowrap; }}
  .conseil {{ font-size: clamp(12px, 0.9vw, 14px); color: #4b5563; margin-top: 4px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #111214; color: #e8e8ea; }}
    .bandeau-equipe, .carte {{ background: #1a1b1e; border-color: #2c2d31; }}
    .meta, .stat-equipe-label, .metrique-label, .conseil {{ color: #9aa0a6; }}
  }}
</style>
</head>
<body>
<header>
  <h1>Dashboard Ventes — MAG</h1>
  <div class="meta">{periode_texte}<br>Mis à jour : {genere_le_texte}</div>
</header>
<section class="bandeau-equipe">
  {blocs_equipe}
</section>
<section class="grille-vendeurs">
  {blocs_cartes}
</section>
</body>
</html>
"""
