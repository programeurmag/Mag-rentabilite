// Page Pipeline — l'argent qui dort. Vue "maintenant", pas de plage de dates
// (pipeline_ouvert_brutes couvre TOUT l'historique ouvert, pas juste 180j).

const JOUR_MS = 86400000;

function rendreValeurParStage(opps) {
  const parStage = new Map();
  opps.forEach((o) => {
    const s = o.stage_nom || "(inconnu)";
    if (!parStage.has(s)) parStage.set(s, { n: 0, valeur: 0 });
    const b = parStage.get(s);
    b.n++;
    b.valeur += o.valeur || 0;
  });
  const lignes = Array.from(parStage.entries()).sort((a, b) => b[1].valeur - a[1].valeur);
  const total = lignes.reduce((a, [, b]) => a + b.valeur, 0);
  return `<table><thead><tr><th>Stage</th><th>Opportunités</th><th>Valeur ouverte</th></tr></thead><tbody>
    ${lignes.map(([s, b]) => `<tr><td>${s}</td><td>${b.n}</td><td>${fmtDollars(b.valeur)}</td></tr>`).join("")}
    <tr style="font-weight:700"><td>Total</td><td>${lignes.reduce((a, [, b]) => a + b.n, 0)}</td><td>${fmtDollars(total)}</td></tr>
  </tbody></table>`;
}

function rendreAgeParStage(opps, maintenant) {
  const parStage = new Map();
  opps.forEach((o) => {
    const s = o.stage_nom || "(inconnu)";
    const age = (maintenant - versDate(o.date_creation)) / JOUR_MS;
    if (!parStage.has(s)) parStage.set(s, []);
    parStage.get(s).push(age);
  });
  const lignes = Array.from(parStage.entries()).sort((a, b) => moyenne(b[1]) - moyenne(a[1]));
  return `<table><thead><tr><th>Stage</th><th>Âge moyen</th><th>Plus vieille</th></tr></thead><tbody>
    ${lignes.map(([s, ages]) => `<tr><td>${s}</td><td>${Math.round(moyenne(ages))} j</td><td>${Math.round(Math.max(...ages))} j</td></tr>`).join("")}
  </tbody></table>`;
}

function rendreJamaisContactes(opps) {
  const lignes = opps
    .filter((o) => o.n_appels_sortants === 0 && o.n_sms_sortants === 0)
    .sort((a, b) => versDate(a.date_creation) - versDate(b.date_creation));
  if (!lignes.length) return "<em>Aucun — tous les leads ouverts ont été contactés au moins une fois.</em>";
  return `<table><thead><tr><th>Nom</th><th>Vendeur</th><th>Stage</th><th>Créé le</th><th>Lien GHL</th></tr></thead><tbody>
    ${lignes
      .map((o) => {
        const lien = ghlLien(o.id);
        return `<tr><td>${o.nom}</td><td>${o.vendeur}</td><td>${o.stage_nom}</td><td>${new Date(o.date_creation).toLocaleDateString("fr-CA")}</td><td>${lien ? `<a class="lien-ghl" href="${lien}" target="_blank" rel="noopener">Ouvrir</a>` : grisee("location id absent")}</td></tr>`;
      })
      .join("")}
  </tbody></table>`;
}

function rendreSoumissionsDormantes(soumissions, maintenant) {
  const lignes = soumissions
    .filter((s) => s.statut === "awaiting_response" && (maintenant - versDate(s.date_envoi)) / JOUR_MS >= 7)
    .map((s) => ({ ...s, jours: Math.round((maintenant - versDate(s.date_envoi)) / JOUR_MS) }))
    .sort((a, b) => b.jours - a.jours);
  if (!lignes.length) return "<em>Aucune — pas de soumission en attente depuis plus de 7 jours.</em>";
  const total = lignes.reduce((a, s) => a + (s.montant || 0), 0);
  return `<p><strong>${lignes.length} soumission(s)</strong> — ${fmtDollars(total)} en attente.</p>
    <table><thead><tr><th>#</th><th>Client</th><th>Vendeur</th><th>Montant</th><th>Envoyée depuis</th></tr></thead><tbody>
    ${lignes.map((s) => `<tr><td>${s.numero}</td><td>${s.client}</td><td>${s.vendeur}</td><td>${fmtDollars(s.montant)}</td><td>${s.jours} j</td></tr>`).join("")}
  </tbody></table>`;
}

function rendreOpportunitesDormantes(opps, maintenant) {
  const lignes = opps
    .map((o) => {
      const derniere = o.derniere_activite || o.updated_at || o.date_creation;
      const jours = Math.round((maintenant - versDate(derniere)) / JOUR_MS);
      return { ...o, jours };
    })
    .filter((o) => o.jours >= 14)
    .sort((a, b) => b.jours - a.jours);
  if (!lignes.length) return "<em>Aucune — tout le pipeline ouvert a bougé dans les 14 derniers jours.</em>";
  return `<table><thead><tr><th>Nom</th><th>Vendeur</th><th>Stage</th><th>Valeur</th><th>Sans activité depuis</th><th>Lien GHL</th></tr></thead><tbody>
    ${lignes
      .map((o) => {
        const lien = ghlLien(o.id);
        return `<tr><td>${o.nom}</td><td>${o.vendeur}</td><td>${o.stage_nom}</td><td>${fmtDollars(o.valeur)}</td><td>${o.jours} j</td><td>${lien ? `<a class="lien-ghl" href="${lien}" target="_blank" rel="noopener">Ouvrir</a>` : grisee()}</td></tr>`;
      })
      .join("")}
  </tbody></table>`;
}

function render() {
  if (!DONNEES) return;
  const maintenant = versDate(DONNEES.genere_le);
  const opps = DONNEES.pipeline_ouvert_brutes || [];
  const soum = DONNEES.soumissions_brutes || [];

  document.getElementById("meta").textContent = `Vue en temps réel — mis à jour ${maintenant.toLocaleString("fr-CA")}`;

  document.getElementById("valeur-par-stage").innerHTML = rendreValeurParStage(opps);
  document.getElementById("age-par-stage").innerHTML = rendreAgeParStage(opps, maintenant);
  document.getElementById("jamais-contactes").innerHTML = rendreJamaisContactes(opps);
  document.getElementById("soumissions-dormantes").innerHTML = rendreSoumissionsDormantes(soum, maintenant);
  document.getElementById("opportunites-dormantes").innerHTML = rendreOpportunitesDormantes(opps, maintenant);
}

chargerDonnees(render);
