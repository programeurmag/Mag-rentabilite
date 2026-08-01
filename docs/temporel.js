// Page Temporel — heatmap jour/heure des leads entrants + délais médians.
// Le délai médian "premier contact" (minutes) n'est PAS calculable avec les
// données actuellement loggées (on sait seulement si le contact a eu lieu LE
// JOUR MÊME, pas l'heure exacte du premier appel/SMS) — grisé volontairement,
// voir le rapport des données manquantes.

const JOURS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

function rendreHeatmap(opps) {
  const grille = Array.from({ length: 7 }, () => Array(24).fill(0));
  opps.forEach((o) => {
    const d = versDate(o.date_creation);
    const jour = (d.getDay() + 6) % 7; // lundi = 0
    grille[jour][d.getHours()]++;
  });
  const max = Math.max(...grille.flat(), 1);

  const couleur = (n) => {
    if (!n) return "transparent";
    const intensite = 0.15 + 0.85 * (n / max);
    return `rgba(37,99,235,${intensite.toFixed(2)})`;
  };

  let html = '<div class="heatmap-grille">';
  html += '<div class="heatmap-label"></div>';
  for (let h = 0; h < 24; h++) html += `<div class="heatmap-label" style="justify-content:center">${h}</div>`;
  for (let j = 0; j < 7; j++) {
    html += `<div class="heatmap-label">${JOURS_FR[j]}</div>`;
    for (let h = 0; h < 24; h++) {
      const n = grille[j][h];
      html += `<div class="heatmap-cellule" style="background:${couleur(n)}" title="${JOURS_FR[j]} ${h}h : ${n} lead(s)"></div>`;
    }
  }
  html += "</div>";
  html += '<p class="conseil">Heure locale du navigateur. Intensité = volume de leads créés dans cette case, sur la période sélectionnée.</p>';
  return html;
}

function rendreDelais(soum) {
  const avecLeadSoumission = soum.map((s) => s.delai_lead_soumission_heures).filter((v) => v !== null && v !== undefined);
  const avecSoumissionWon = soum.filter((s) => s.won).map((s) => s.delai_soumission_won_heures).filter((v) => v !== null && v !== undefined);

  const blocs = [
    ["Délai médian premier contact", grisee("pas d'horodatage exact du 1er contact loggé — seulement 'même jour ou non'")],
    [
      "Délai médian lead → soumission",
      avecLeadSoumission.length ? fmtDelaiHeures(mediane(avecLeadSoumission)) : grisee("pas assez de soumissions matchées à un lead GHL"),
    ],
    [
      "Délai médian soumission → won",
      avecSoumissionWon.length ? fmtDelaiHeures(mediane(avecSoumissionWon)) : grisee("pas assez de soumissions gagnées sur la période"),
    ],
  ];
  return blocs.map(([label, val]) => `<div><div class="stat-equipe-label">${label}</div><div class="stat-equipe-valeur">${val}</div></div>`).join("");
}

function render() {
  if (!DONNEES) return;
  const fin = versDate(DONNEES.periode.fin);
  const { debut } = fenetrePreset(presetActif, fin);

  const opps = filtrer(DONNEES.opportunites_brutes, "date_creation", debut, fin);
  const soum = filtrer(DONNEES.soumissions_brutes, "date_envoi", debut, fin);

  majMeta(debut, fin);
  document.getElementById("heatmap").innerHTML = rendreHeatmap(opps);
  document.getElementById("bandeau-delais").innerHTML = rendreDelais(soum);
}

initPresets(render);
chargerDonnees(render);
