// Dashboard Ventes GHL — logique interactive (page 2026-08-01).
// Tout le calcul se fait ici, à partir des données brutes de stats_ventes_ghl.json
// (voir dashboard_ventes_ghl.py pour ce qui est déjà pré-calculé côté serveur :
// contacte_meme_jour / visite_bookee / rappel_48h dépendent de l'historique GHL
// des appels/SMS et ne peuvent pas être recalculés dans le navigateur — seul le
// filtrage par date et l'agrégation le sont).

let DONNEES = null;
let presetActif = "30";
let ongletDetail = "leads";
let rechercheTexte = "";
let triDetail = { colonne: "date_creation", asc: false };

function versDate(iso) {
  return new Date(iso);
}

function pct(numerateur, denominateur) {
  return denominateur ? (100 * numerateur) / denominateur : null;
}

function fmtPct(v) {
  return v === null || v === undefined ? "s/o" : Math.round(v) + " %";
}

function fmtDollars(v) {
  return v ? Math.round(v).toLocaleString("fr-CA") + " $" : "s/o";
}

function fmtListe(noms, max = 5) {
  const affiches = noms.slice(0, max).join(", ");
  const reste = noms.length - max;
  return reste > 0 ? `${affiches}, +${reste} autre(s)` : affiches;
}

function couleurSeuil(valeur, seuils) {
  if (valeur === null || valeur === undefined || !seuils) return null;
  if (valeur >= seuils.vert) return "vert";
  if (valeur >= seuils.rouge) return "jaune";
  return "rouge";
}

// --- Fenêtres de dates ------------------------------------------------------

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
    return d > debut && d <= fin;
  });
}

// --- Métriques (port JS de metriques_ventes_ghl.py) -------------------------

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

// --- Conseils actionnables (port JS des fonctions _conseil_* Python) --------

function conseilContact(m, couleur) {
  if (!["rouge", "jaune"].includes(couleur) || !m.leads_non_contactes.length) return null;
  return `${m.leads_non_contactes.length} lead(s) non contacté(s) le jour même : ${fmtListe(m.leads_non_contactes)}`;
}

function conseilVisite(m, couleur) {
  if (!["rouge", "jaune"].includes(couleur)) return null;
  const n = m.n_leads - m.n_visite_bookee;
  if (n <= 0) return null;
  return `${n} lead(s) n'ont pas encore dépassé les tentatives d'appel — relancer en priorité.`;
}

function conseilRappel(m, couleur) {
  if (!["rouge", "jaune"].includes(couleur) || !m.soumissions_sans_rappel.length) return null;
  return `${m.soumissions_sans_rappel.length} soumission(s) sans rappel depuis 48h : ${fmtListe(m.soumissions_sans_rappel)}`;
}

function conseilClosing(m, couleur) {
  if (!["rouge", "jaune"].includes(couleur) || !m.n_soumissions_envoyees) return null;
  return `${m.n_won}/${m.n_soumissions_envoyees} soumissions gagnées — creuser les motifs de perte des autres.`;
}

// --- Rendu : bandeau équipe --------------------------------------------------

function rendreBandeauEquipe(actuelle, precedente) {
  const stats = [
    ["Leads", String(actuelle.n_leads), ...deltaRelatif(actuelle.n_leads, precedente.n_leads)],
    [
      "Contact même jour",
      fmtPct(actuelle.pct_contact_meme_jour),
      ...deltaPoints(actuelle.pct_contact_meme_jour, precedente.pct_contact_meme_jour),
    ],
    [
      "Visite bookée",
      fmtPct(actuelle.pct_visite_bookee),
      ...deltaPoints(actuelle.pct_visite_bookee, precedente.pct_visite_bookee),
    ],
    ["Rappel 48h", fmtPct(actuelle.pct_rappel_48h), ...deltaPoints(actuelle.pct_rappel_48h, precedente.pct_rappel_48h)],
    ["Taux de closing", fmtPct(actuelle.pct_closing), ...deltaPoints(actuelle.pct_closing, precedente.pct_closing)],
    [
      "Valeur moyenne (won)",
      fmtDollars(actuelle.valeur_moyenne_won),
      ...deltaRelatif(actuelle.valeur_moyenne_won || 0, precedente.valeur_moyenne_won || 0),
    ],
  ];
  return stats
    .map(([label, val, deltaTxt, deltaPos]) => {
      const fleche = deltaTxt
        ? ` <span style="color:${deltaPos ? "#1e7a34" : "#c22b2b"};font-size:14px">${deltaPos ? "▲" : "▼"} ${deltaTxt}</span>`
        : "";
      return `<div><div class="stat-equipe-label">${label}</div><div class="stat-equipe-valeur">${val}${fleche}</div></div>`;
    })
    .join("");
}

// --- Rendu : cartes vendeurs -------------------------------------------------

function rendreCarteVendeur(m, seuils) {
  const c1 = couleurSeuil(m.pct_contact_meme_jour, seuils.contact_meme_jour);
  const c2 = couleurSeuil(m.pct_visite_bookee, seuils.visite_bookee);
  const c3 = couleurSeuil(m.pct_rappel_48h, seuils.rappel_48h);
  const c4 = couleurSeuil(m.pct_closing, seuils.closing);
  const metriques = [
    ["Contact même jour", `${fmtPct(m.pct_contact_meme_jour)} (${m.n_contactes_meme_jour}/${m.n_leads})`, c1, conseilContact(m, c1)],
    ["Visite bookée", `${fmtPct(m.pct_visite_bookee)} (${m.n_visite_bookee}/${m.n_leads})`, c2, conseilVisite(m, c2)],
    [
      "Rappel 48h après soumission",
      `${fmtPct(m.pct_rappel_48h)} (${m.n_soumissions_avec_rappel}/${m.n_soumissions_ghl})`,
      c3,
      conseilRappel(m, c3),
    ],
    ["Taux de closing", `${fmtPct(m.pct_closing)} (${m.n_won}/${m.n_soumissions_envoyees})`, c4, conseilClosing(m, c4)],
    ["Valeur moyenne (won)", fmtDollars(m.valeur_moyenne_won), null, null],
  ];
  const corps = metriques
    .map(
      ([label, val, couleur, conseil]) => `
    <div class="metrique carte-fond-${couleur || "neutre"}">
      <div class="metrique-tete"><span class="metrique-label">${label}</span><span class="metrique-valeur">${val}</span></div>
      ${conseil ? `<div class="conseil">${conseil}</div>` : ""}
    </div>`
    )
    .join("");
  return `<div class="carte"><h2>${m.nom}</h2>${corps}</div>`;
}

// --- Rendu : tendance par semaine (graphiques SVG faits main) ---------------

function semaineDe(date) {
  const d = new Date(date);
  const jour = (d.getDay() + 6) % 7; // lundi = 0
  d.setDate(d.getDate() - jour);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

function bucketsSemaine(opps, soumissions) {
  const semaines = new Map();
  const obtenir = (t) => {
    if (!semaines.has(t)) semaines.set(t, { leads: 0, contactes: 0, visite: 0, soum: 0, won: 0 });
    return semaines.get(t);
  };
  opps.forEach((o) => {
    const b = obtenir(semaineDe(versDate(o.date_creation)));
    b.leads++;
    if (o.contacte_meme_jour) b.contactes++;
    if (o.visite_bookee) b.visite++;
  });
  soumissions.forEach((s) => {
    const b = obtenir(semaineDe(versDate(s.date_envoi)));
    b.soum++;
    if (s.won) b.won++;
  });
  return Array.from(semaines.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([t, b]) => ({
      semaine: new Date(t),
      pct_contact: pct(b.contactes, b.leads),
      pct_visite: pct(b.visite, b.leads),
      pct_closing: pct(b.won, b.soum),
      leads: b.leads,
    }));
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

// --- Rendu : répartition par source ------------------------------------------

function rendreTableSource(opps) {
  const parSource = new Map();
  opps.forEach((o) => {
    const s = o.source || "(inconnue)";
    if (!parSource.has(s)) parSource.set(s, { n: 0, contactes: 0, visite: 0 });
    const b = parSource.get(s);
    b.n++;
    if (o.contacte_meme_jour) b.contactes++;
    if (o.visite_bookee) b.visite++;
  });
  const lignes = Array.from(parSource.entries()).sort((a, b) => b[1].n - a[1].n);
  if (!lignes.length) return "<em>Aucune donnée sur cette période.</em>";
  return `<table><thead><tr><th>Source</th><th>Leads</th><th>% Contact même jour</th><th>% Visite bookée</th></tr></thead><tbody>
    ${lignes
      .map(
        ([src, b]) =>
          `<tr><td>${src}</td><td>${b.n}</td><td>${fmtPct(pct(b.contactes, b.n))}</td><td>${fmtPct(pct(b.visite, b.n))}</td></tr>`
      )
      .join("")}
  </tbody></table>`;
}

// --- Rendu : classement des vendeurs -----------------------------------------

function rendreLeaderboard(vendeursMetriques) {
  const tries = [...vendeursMetriques].sort((a, b) => (b.pct_closing || 0) - (a.pct_closing || 0));
  return `<table><thead><tr>
      <th>Vendeur</th><th>Leads</th><th>Contact même jour</th><th>Visite bookée</th><th>Rappel 48h</th><th>Closing</th><th>Valeur moy. won</th>
    </tr></thead><tbody>
    ${tries
      .map(
        (m) =>
          `<tr><td>${m.nom}</td><td>${m.n_leads}</td><td>${fmtPct(m.pct_contact_meme_jour)}</td><td>${fmtPct(m.pct_visite_bookee)}</td><td>${fmtPct(m.pct_rappel_48h)}</td><td>${fmtPct(m.pct_closing)}</td><td>${fmtDollars(m.valeur_moyenne_won)}</td></tr>`
      )
      .join("")}
  </tbody></table>`;
}

// --- Rendu : tableau détaillé (leads / soumissions) --------------------------

const COLONNES_LEADS = [
  ["nom", "Nom"],
  ["vendeur", "Vendeur"],
  ["source", "Source"],
  ["statut", "Statut"],
  ["date_creation", "Créé le"],
  ["contacte_meme_jour", "Contact même jour"],
  ["visite_bookee", "Visite bookée"],
];
const COLONNES_SOUMISSIONS = [
  ["numero", "#"],
  ["client", "Client"],
  ["vendeur", "Vendeur"],
  ["montant", "Montant"],
  ["won", "Gagné"],
  ["date_envoi", "Envoyée le"],
];

function formaterCellule(v, cle) {
  if (typeof v === "boolean") return v ? "✓" : "—";
  if (cle === "montant") return fmtDollars(v);
  if (cle === "date_creation" || cle === "date_envoi") return new Date(v).toLocaleDateString("fr-CA");
  return v ?? "";
}

function rendreDetail(oppsFiltrees, soumFiltrees) {
  const conteneur = document.getElementById("tableau-detail");
  const colonnes = ongletDetail === "leads" ? COLONNES_LEADS : COLONNES_SOUMISSIONS;
  let lignes = ongletDetail === "leads" ? oppsFiltrees : soumFiltrees;

  if (rechercheTexte) {
    const q = rechercheTexte.toLowerCase();
    lignes = lignes.filter((l) => JSON.stringify(l).toLowerCase().includes(q));
  }

  lignes = [...lignes].sort((a, b) => {
    let av = a[triDetail.colonne];
    let bv = b[triDetail.colonne];
    if (av === undefined || av === null) av = "";
    if (bv === undefined || bv === null) bv = "";
    if (av < bv) return triDetail.asc ? -1 : 1;
    if (av > bv) return triDetail.asc ? 1 : -1;
    return 0;
  });

  const total = lignes.length;
  const affichees = lignes.slice(0, 300);

  conteneur.innerHTML = `<table><thead><tr>
      ${colonnes
        .map(
          ([cle, label]) =>
            `<th data-col="${cle}">${label}${triDetail.colonne === cle ? (triDetail.asc ? " ▲" : " ▼") : ""}</th>`
        )
        .join("")}
    </tr></thead>
    <tbody>${affichees
      .map((l) => `<tr>${colonnes.map(([cle]) => `<td>${formaterCellule(l[cle], cle)}</td>`).join("")}</tr>`)
      .join("")}</tbody></table>
    ${total > 300 ? `<p class="conseil">${total} résultats — 300 premiers affichés, affine ta recherche.</p>` : ""}`;

  conteneur.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const col = th.dataset.col;
      if (triDetail.colonne === col) {
        triDetail.asc = !triDetail.asc;
      } else {
        triDetail.colonne = col;
        triDetail.asc = false;
      }
      render();
    });
  });
}

// --- Orchestration ------------------------------------------------------------

function render() {
  if (!DONNEES) return;
  const fin = versDate(DONNEES.periode.fin);
  const { debut, debutPrec, finPrec } = fenetrePreset(presetActif, fin);

  const opps = DONNEES.opportunites_brutes;
  const soum = DONNEES.soumissions_brutes;

  const oppsActuelles = filtrer(opps, "date_creation", debut, fin);
  const oppsPrecedentes = filtrer(opps, "date_creation", debutPrec, finPrec);
  const soumActuelles = filtrer(soum, "date_envoi", debut, fin);
  const soumPrecedentes = filtrer(soum, "date_envoi", debutPrec, finPrec);

  const equipeActuelle = calculerMetriques(oppsActuelles, soumActuelles, null);
  const equipePrecedente = calculerMetriques(oppsPrecedentes, soumPrecedentes, null);
  const vendeursMetriques = DONNEES.vendeurs_config.map((nom) => calculerMetriques(oppsActuelles, soumActuelles, nom));

  document.getElementById("meta").textContent =
    `${debut.toLocaleDateString("fr-CA")} au ${fin.toLocaleDateString("fr-CA")} — mis à jour ${fin.toLocaleString("fr-CA")}`;

  document.getElementById("bandeau-equipe").innerHTML = rendreBandeauEquipe(equipeActuelle, equipePrecedente);
  document.getElementById("grille-vendeurs").innerHTML = vendeursMetriques
    .map((m) => rendreCarteVendeur(m, DONNEES.seuils))
    .join("");

  const buckets = bucketsSemaine(opps, soum); // historique complet (180j), indépendant du preset
  document.getElementById("graphiques-tendance").innerHTML =
    graphiqueLigne("Contact même jour", buckets, (b) => b.pct_contact) +
    graphiqueLigne("Visite bookée", buckets, (b) => b.pct_visite) +
    graphiqueLigne("Taux de closing", buckets, (b) => b.pct_closing) +
    graphiqueLigne("Leads / semaine", buckets, (b) => b.leads, "");

  document.getElementById("tableau-source").innerHTML = rendreTableSource(oppsActuelles);
  document.getElementById("leaderboard").innerHTML = rendreLeaderboard(vendeursMetriques);
  rendreDetail(oppsActuelles, soumActuelles);
}

document.getElementById("presets").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-preset]");
  if (!btn) return;
  presetActif = btn.dataset.preset;
  document.querySelectorAll("#presets button").forEach((b) => b.classList.toggle("actif", b === btn));
  render();
});

document.getElementById("tabs-detail").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-tab]");
  if (!btn) return;
  ongletDetail = btn.dataset.tab;
  triDetail = { colonne: ongletDetail === "leads" ? "date_creation" : "date_envoi", asc: false };
  document.querySelectorAll("#tabs-detail button").forEach((b) => b.classList.toggle("actif", b === btn));
  render();
});

document.getElementById("recherche").addEventListener("input", (e) => {
  rechercheTexte = e.target.value;
  render();
});

fetch("stats_ventes_ghl.json")
  .then((r) => r.json())
  .then((d) => {
    DONNEES = d;
    render();
  })
  .catch((err) => {
    document.getElementById("meta").textContent = "Erreur de chargement des données : " + err;
  });
