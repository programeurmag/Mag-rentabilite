// Fonctions communes — Centre de contrôle Ventes GHL (2026-08-01).
// Chargé avant le JS propre à chaque page (voir dashboard_html_ventes.py).
// Toutes les fonctions sont globales (pas de modules ES) pour rester
// autonome/sans build, cohérent avec le reste du projet.

let DONNEES = null;
let presetActif = "30";

function versDate(iso) {
  return iso ? new Date(iso) : null;
}

function pct(numerateur, denominateur) {
  return denominateur ? (100 * numerateur) / denominateur : null;
}

function fmtPct(v) {
  return v === null || v === undefined ? "s/o" : Math.round(v) + " %";
}

function fmtDollars(v) {
  return v ? Math.round(v).toLocaleString("fr-CA") + " $" : v === 0 ? "0 $" : "s/o";
}

function fmtNombre(v) {
  return v === null || v === undefined ? "s/o" : Math.round(v).toLocaleString("fr-CA");
}

function fmtDelaiHeures(h) {
  if (h === null || h === undefined) return "s/o";
  if (h < 1) return Math.round(h * 60) + " min";
  if (h < 48) return Math.round(h) + " h";
  return Math.round(h / 24) + " j";
}

function fmtListe(noms, max = 5) {
  const affiches = noms.slice(0, max).join(", ");
  const reste = noms.length - max;
  return reste > 0 ? `${affiches}, +${reste} autre(s)` : affiches;
}

function grisee(texte = "données insuffisantes") {
  return `<span class="grisee">s/o <span class="badge-manquant">${texte}</span></span>`;
}

function mediane(valeurs) {
  const v = valeurs.filter((x) => x !== null && x !== undefined).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

function moyenne(valeurs) {
  const v = valeurs.filter((x) => x !== null && x !== undefined);
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

function couleurSeuil(valeur, seuils) {
  if (valeur === null || valeur === undefined || !seuils) return null;
  if (valeur >= seuils.vert) return "vert";
  if (valeur >= seuils.rouge) return "jaune";
  return "rouge";
}

// --- Fenêtres de dates -------------------------------------------------------

function fenetrePreset(preset, fin) {
  let debut, debutPrec, finPrec;
  if (preset === "mois") {
    debut = new Date(fin.getFullYear(), fin.getMonth(), 1);
    finPrec = new Date(debut.getTime() - 1);
    debutPrec = new Date(finPrec.getFullYear(), finPrec.getMonth(), 1);
  } else {
    const jours = parseInt(preset, 10);
    debut = new Date(fin.getTime() - jours * 86400000);
    finPrec = debut;
    debutPrec = new Date(debut.getTime() - jours * 86400000);
  }
  return { debut, fin, debutPrec, finPrec };
}

function filtrer(liste, champ, debut, fin) {
  return liste.filter((x) => {
    const d = versDate(x[champ]);
    return d && d > debut && d <= fin;
  });
}

// --- Métriques (port JS de metriques_ventes_ghl.py) --------------------------

function calculerMetriques(opps, soumissions, vendeur) {
  const oppsV = vendeur ? opps.filter((o) => o.vendeur === vendeur) : opps;
  const soumV = vendeur ? soumissions.filter((s) => s.vendeur === vendeur) : soumissions;

  const contactes = oppsV.filter((o) => o.contacte_meme_jour);
  const visite = oppsV.filter((o) => o.visite_bookee);
  const soumGhl = oppsV.filter((o) => o.soumission_envoyee);
  const avecRappel = soumGhl.filter((o) => o.rappel_48h === true);
  const won = soumV.filter((s) => s.won);
  const wonAvecValeur = won.filter((s) => s.montant);

  return {
    nom: vendeur || "Équipe",
    n_leads: oppsV.length,
    n_contactes_meme_jour: contactes.length,
    pct_contact_meme_jour: pct(contactes.length, oppsV.length),
    n_visite_bookee: visite.length,
    pct_visite_bookee: pct(visite.length, oppsV.length),
    n_soumissions_ghl: soumGhl.length,
    n_soumissions_avec_rappel: avecRappel.length,
    pct_rappel_48h: pct(avecRappel.length, soumGhl.length),
    n_soumissions_envoyees: soumV.length,
    n_won: won.length,
    pct_closing: pct(won.length, soumV.length),
    valeur_moyenne_won: wonAvecValeur.length
      ? wonAvecValeur.reduce((a, s) => a + s.montant, 0) / wonAvecValeur.length
      : null,
    leads_non_contactes: oppsV.filter((o) => !o.contacte_meme_jour).map((o) => o.nom),
    soumissions_sans_rappel: soumGhl.filter((o) => o.rappel_48h === false).map((o) => o.nom),
  };
}

function deltaPoints(actuel, precedent) {
  if (actuel === null || actuel === undefined || precedent === null || precedent === undefined) return [null, null];
  const d = actuel - precedent;
  return [`${Math.abs(Math.round(d))} pts`, d >= 0];
}

function deltaRelatif(actuel, precedent) {
  if (!precedent) return [null, null];
  const d = (actuel - precedent) / precedent;
  return [`${Math.abs(Math.round(d * 100))}%`, d >= 0];
}

// --- Semaines / graphiques ----------------------------------------------------

function semaineDe(date) {
  const d = new Date(date);
  const jour = (d.getDay() + 6) % 7; // lundi = 0
  d.setDate(d.getDate() - jour);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function graphiqueLigne(titre, points, accesseur, suffixe = "%") {
  const valeurs = points.map(accesseur).filter((v) => v !== null && v !== undefined);
  if (!valeurs.length) {
    return `<div><div class="graphique-titre">${titre}</div><em>Pas de données</em></div>`;
  }
  const max = Math.max(...valeurs, 1);
  const w = 260,
    h = 90,
    pad = 6;
  const n = points.length;
  const coords = points.map((p, i) => {
    const v = accesseur(p);
    const x = pad + (i / Math.max(n - 1, 1)) * (w - 2 * pad);
    const y = v === null || v === undefined ? null : h - pad - (v / max) * (h - 2 * pad);
    return [x, y];
  });
  const chemin = coords
    .filter((c) => c[1] !== null)
    .map((c, i) => (i === 0 ? "M" : "L") + c[0].toFixed(1) + "," + c[1].toFixed(1))
    .join(" ");
  const dernier = Math.round(valeurs[valeurs.length - 1]);
  return `<div>
    <div class="graphique-titre">${titre} <strong>(${dernier}${suffixe})</strong></div>
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" preserveAspectRatio="none">
      <path d="${chemin}" fill="none" stroke="#2563eb" stroke-width="2"/>
    </svg>
  </div>`;
}

// --- Nav / presets communs -----------------------------------------------------

function initPresets(onChange) {
  const conteneur = document.getElementById("presets");
  if (!conteneur) return;
  conteneur.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-preset]");
    if (!btn) return;
    presetActif = btn.dataset.preset;
    conteneur.querySelectorAll("button").forEach((b) => b.classList.toggle("actif", b === btn));
    onChange();
  });
}

function majMeta(debut, fin) {
  const el = document.getElementById("meta");
  if (!el || !DONNEES) return;
  el.textContent = `${debut.toLocaleDateString("fr-CA")} au ${fin.toLocaleDateString("fr-CA")} — mis à jour ${versDate(DONNEES.genere_le).toLocaleString("fr-CA")}`;
}

function ghlLien(id) {
  if (!DONNEES || !DONNEES.ghl_location_id) return null;
  return `https://app.gohighlevel.com/v2/location/${DONNEES.ghl_location_id}/opportunities/${id}?tab=Opportunity+details`;
}

function chargerDonnees(callback) {
  fetch("stats_ventes_ghl.json")
    .then((r) => r.json())
    .then((d) => {
      DONNEES = d;
      callback();
    })
    .catch((err) => {
      const el = document.getElementById("meta");
      if (el) el.textContent = "Erreur de chargement des données : " + err;
    });
}
