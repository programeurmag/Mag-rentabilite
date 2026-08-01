// Page Accueil — Dashboard Ventes GHL (voir commun.js pour les fonctions partagées).

let ongletDetail = "leads";
let rechercheTexte = "";
let triDetail = { colonne: "date_creation", asc: false };

// --- Conseils actionnables ------------------------------------------------------

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

// --- Rendu : podium du mois ------------------------------------------------------

function rendrePodium() {
  const maintenant = versDate(DONNEES.periode.fin);
  const { debut, fin } = fenetrePreset("mois", maintenant);
  const oppsMois = filtrer(DONNEES.opportunites_brutes, "date_creation", debut, fin);
  const soumMois = filtrer(DONNEES.soumissions_brutes, "date_envoi", debut, fin);
  const classement = DONNEES.vendeurs_config
    .map((nom) => calculerMetriques(oppsMois, soumMois, nom))
    .filter((m) => m.n_soumissions_envoyees > 0)
    .sort((a, b) => (b.pct_closing || 0) - (a.pct_closing || 0));

  if (!classement.length) {
    document.getElementById("podium").innerHTML = "<em>Aucune soumission envoyée ce mois-ci pour l'instant.</em>";
    return;
  }

  const medailles = ["🥇", "🥈", "🥉"];
  document.getElementById("podium").innerHTML = `<div style="display:flex;gap:16px;flex-wrap:wrap">
    ${classement
      .map(
        (m, i) => `
      <div style="flex:1;min-width:180px;text-align:center;padding:14px;border-radius:10px;background:${i === 0 ? "#fdf3e0" : "#f7f7f8"}">
        <div style="font-size:28px">${medailles[i] || "🎖️"}</div>
        <div style="font-weight:700;margin-top:4px">${m.nom}</div>
        <div style="font-size:22px;font-weight:700;color:#1e7a34">${fmtPct(m.pct_closing)}</div>
        <div style="font-size:12px;color:#6b7280">${m.n_won}/${m.n_soumissions_envoyees} soumissions</div>
      </div>`
      )
      .join("")}
  </div>`;
}

// --- Rendu : bandeau équipe --------------------------------------------------

function rendreBandeauEquipe(actuelle, precedente) {
  const stats = [
    ["Leads", String(actuelle.n_leads), ...deltaRelatif(actuelle.n_leads, precedente.n_leads)],
    ["Contact même jour", fmtPct(actuelle.pct_contact_meme_jour), ...deltaPoints(actuelle.pct_contact_meme_jour, precedente.pct_contact_meme_jour)],
    ["Visite bookée", fmtPct(actuelle.pct_visite_bookee), ...deltaPoints(actuelle.pct_visite_bookee, precedente.pct_visite_bookee)],
    ["Rappel 48h", fmtPct(actuelle.pct_rappel_48h), ...deltaPoints(actuelle.pct_rappel_48h, precedente.pct_rappel_48h)],
    ["Taux de closing", fmtPct(actuelle.pct_closing), ...deltaPoints(actuelle.pct_closing, precedente.pct_closing)],
    ["Valeur moyenne (won)", fmtDollars(actuelle.valeur_moyenne_won), ...deltaRelatif(actuelle.valeur_moyenne_won || 0, precedente.valeur_moyenne_won || 0)],
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
    ["Rappel 48h après soumission", `${fmtPct(m.pct_rappel_48h)} (${m.n_soumissions_avec_rappel}/${m.n_soumissions_ghl})`, c3, conseilRappel(m, c3)],
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

// --- Rendu : tendance, source, leaderboard, détail (inchangés) --------------

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
      pct_contact: pct(b.contactes, b.leads),
      pct_visite: pct(b.visite, b.leads),
      pct_closing: pct(b.won, b.soum),
      leads: b.leads,
    }));
}

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
    ${lignes.map(([src, b]) => `<tr><td>${src}</td><td>${b.n}</td><td>${fmtPct(pct(b.contactes, b.n))}</td><td>${fmtPct(pct(b.visite, b.n))}</td></tr>`).join("")}
  </tbody></table>`;
}

function rendreLeaderboard(vendeursMetriques) {
  const tries = [...vendeursMetriques].sort((a, b) => (b.pct_closing || 0) - (a.pct_closing || 0));
  return `<table><thead><tr>
      <th>Vendeur</th><th>Leads</th><th>Contact même jour</th><th>Visite bookée</th><th>Rappel 48h</th><th>Closing</th><th>Valeur moy. won</th>
    </tr></thead><tbody>
    ${tries.map((m) => `<tr><td>${m.nom}</td><td>${m.n_leads}</td><td>${fmtPct(m.pct_contact_meme_jour)}</td><td>${fmtPct(m.pct_visite_bookee)}</td><td>${fmtPct(m.pct_rappel_48h)}</td><td>${fmtPct(m.pct_closing)}</td><td>${fmtDollars(m.valeur_moyenne_won)}</td></tr>`).join("")}
  </tbody></table>`;
}

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
    let av = a[triDetail.colonne] ?? "";
    let bv = b[triDetail.colonne] ?? "";
    if (av < bv) return triDetail.asc ? -1 : 1;
    if (av > bv) return triDetail.asc ? 1 : -1;
    return 0;
  });

  const total = lignes.length;
  const affichees = lignes.slice(0, 300);

  conteneur.innerHTML = `<table><thead><tr>
      ${colonnes.map(([cle, label]) => `<th data-col="${cle}">${label}${triDetail.colonne === cle ? (triDetail.asc ? " ▲" : " ▼") : ""}</th>`).join("")}
    </tr></thead>
    <tbody>${affichees.map((l) => `<tr>${colonnes.map(([cle]) => `<td>${formaterCellule(l[cle], cle)}</td>`).join("")}</tr>`).join("")}</tbody></table>
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

  majMeta(debut, fin);
  rendrePodium();

  document.getElementById("bandeau-equipe").innerHTML = rendreBandeauEquipe(equipeActuelle, equipePrecedente);
  document.getElementById("grille-vendeurs").innerHTML = vendeursMetriques.map((m) => rendreCarteVendeur(m, DONNEES.seuils)).join("");

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

initPresets(render);
chargerDonnees(render);
