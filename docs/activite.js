// Page Activité — volume brut par vendeur (voir commun.js pour le partagé).

function calculerActivite(opps, soumissions, vendeur) {
  const oppsV = vendeur ? opps.filter((o) => o.vendeur === vendeur) : opps;
  const soumV = vendeur ? soumissions.filter((s) => s.vendeur === vendeur) : soumissions;
  const lost = oppsV.filter((o) => o.statut === "lost");
  const visite = oppsV.filter((o) => o.visite_bookee);

  const nAppels = oppsV.reduce((a, o) => a + o.n_appels_sortants, 0);
  const nAppelsRepondus = oppsV.reduce((a, o) => a + o.n_appels_repondus, 0);
  const nSms = oppsV.reduce((a, o) => a + o.n_sms_sortants, 0);
  const nEmails = oppsV.reduce((a, o) => a + o.n_emails_sortants, 0);

  return {
    nom: vendeur || "Équipe",
    n_leads: oppsV.length,
    n_appels_sortants: nAppels,
    n_appels_repondus: nAppelsRepondus,
    pct_appels_repondus: pct(nAppelsRepondus, nAppels),
    n_sms_sortants: nSms,
    n_emails_sortants: nEmails,
    n_soumissions: soumV.length,
    valeur_soumissions: soumV.reduce((a, s) => a + (s.montant || 0), 0),
    n_visite_bookee: visite.length,
    tentatives_moy_avant_abandon: lost.length ? moyenne(lost.map((o) => o.n_appels_sortants)) : null,
    n_lost: lost.length,
  };
}

function rendreTableauActivite(lignes) {
  const colonnes = [
    ["Vendeur", (l) => l.nom],
    ["Leads", (l) => fmtNombre(l.n_leads)],
    ["Appels faits", (l) => fmtNombre(l.n_appels_sortants)],
    ["Appels répondus", (l) => `${fmtNombre(l.n_appels_repondus)} (${fmtPct(l.pct_appels_repondus)})`],
    ["SMS envoyés", (l) => fmtNombre(l.n_sms_sortants)],
    ["Emails envoyés", (l) => (l.n_emails_sortants > 0 ? fmtNombre(l.n_emails_sortants) : grisee("aucune activité email loggée dans GHL"))],
    ["Soumissions", (l) => `${fmtNombre(l.n_soumissions)} (${fmtDollars(l.valeur_soumissions)})`],
    ["Visites bookées", (l) => fmtNombre(l.n_visite_bookee)],
    [
      "Tentatives moy. avant abandon",
      (l) => (l.n_lost > 0 ? Math.round(l.tentatives_moy_avant_abandon * 10) / 10 : grisee("aucun lead perdu sur la période")),
    ],
  ];
  return `<table><thead><tr>${colonnes.map(([label]) => `<th>${label}</th>`).join("")}</tr></thead>
    <tbody>${lignes.map((l) => `<tr>${colonnes.map(([, fn]) => `<td>${fn(l)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function bucketsActiviteSemaine(opps) {
  const semaines = new Map();
  opps.forEach((o) => {
    const t = semaineDe(versDate(o.date_creation));
    if (!semaines.has(t)) semaines.set(t, { appels: 0, sms: 0, visite: 0, leads: 0 });
    const b = semaines.get(t);
    b.appels += o.n_appels_sortants;
    b.sms += o.n_sms_sortants;
    if (o.visite_bookee) b.visite++;
    b.leads++;
  });
  return Array.from(semaines.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, b]) => b);
}

function render() {
  if (!DONNEES) return;
  const fin = versDate(DONNEES.periode.fin);
  const { debut } = fenetrePreset(presetActif, fin);

  const opps = DONNEES.opportunites_brutes;
  const soum = DONNEES.soumissions_brutes;
  const oppsActuelles = filtrer(opps, "date_creation", debut, fin);
  const soumActuelles = filtrer(soum, "date_envoi", debut, fin);

  majMeta(debut, fin);

  const lignes = [calculerActivite(oppsActuelles, soumActuelles, null), ...DONNEES.vendeurs_config.map((nom) => calculerActivite(oppsActuelles, soumActuelles, nom))];
  document.getElementById("tableau-activite").innerHTML = rendreTableauActivite(lignes);

  const buckets = bucketsActiviteSemaine(opps);
  document.getElementById("graphiques-activite").innerHTML =
    graphiqueLigne("Appels / semaine", buckets, (b) => b.appels, "") +
    graphiqueLigne("SMS / semaine", buckets, (b) => b.sms, "") +
    graphiqueLigne("Visites bookées / semaine", buckets, (b) => b.visite, "");
}

initPresets(render);
chargerDonnees(render);
