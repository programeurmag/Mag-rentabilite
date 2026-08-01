// Page Sources — performance par source de lead.

function calculerParSource(opps, soumissions) {
  const parSource = new Map();
  const obtenir = (s) => {
    if (!parSource.has(s)) parSource.set(s, { opps: [], soumissions: [] });
    return parSource.get(s);
  };
  opps.forEach((o) => obtenir(o.source || "(inconnue)").opps.push(o));
  soumissions.forEach((s) => {
    if (s.source_lead) obtenir(s.source_lead).soumissions.push(s);
  });

  return Array.from(parSource.entries())
    .map(([source, d]) => {
      const contactes = d.opps.filter((o) => o.contacte_meme_jour);
      const visite = d.opps.filter((o) => o.visite_bookee);
      const won = d.soumissions.filter((s) => s.won);
      const wonAvecValeur = won.filter((s) => s.montant);
      const delaisWon = won.map((s) => s.delai_lead_won_heures).filter((v) => v !== null && v !== undefined);
      return {
        source,
        n_leads: d.opps.length,
        pct_contact: pct(contactes.length, d.opps.length),
        pct_visite: pct(visite.length, d.opps.length),
        n_soumissions_matchees: d.soumissions.length,
        n_won: won.length,
        pct_closing: pct(won.length, d.soumissions.length),
        valeur_moyenne_won: wonAvecValeur.length ? wonAvecValeur.reduce((a, s) => a + s.montant, 0) / wonAvecValeur.length : null,
        delai_median_won_heures: delaisWon.length ? mediane(delaisWon) : null,
        n_delais_dispo: delaisWon.length,
      };
    })
    .sort((a, b) => b.n_leads - a.n_leads);
}

function rendreTableauSources(lignes) {
  return `<table><thead><tr>
      <th>Source</th><th>Leads</th><th>% Contact</th><th>% Visite bookée</th><th>% Closing</th><th>Valeur moy. won</th><th>Délai médian lead→won</th>
    </tr></thead><tbody>
    ${lignes
      .map((l) => {
        const closing = l.n_soumissions_matchees > 0 ? fmtPct(l.pct_closing) : grisee("pas de soumission matchée à cette source");
        const delai = l.n_delais_dispo > 0 ? fmtDelaiHeures(l.delai_median_won_heures) : grisee("pas assez de ventes matchées");
        return `<tr><td>${l.source}</td><td>${l.n_leads}</td><td>${fmtPct(l.pct_contact)}</td><td>${fmtPct(l.pct_visite)}</td><td>${closing}</td><td>${fmtDollars(l.valeur_moyenne_won)}</td><td>${delai}</td></tr>`;
      })
      .join("")}
  </tbody></table>
  <p class="conseil">Le closing et le délai par source dépendent du matching Jobber↔GHL (cascade email/téléphone) — pas garanti à 100 %, voir le rapport des données manquantes.</p>`;
}

function render() {
  if (!DONNEES) return;
  const fin = versDate(DONNEES.periode.fin);
  const { debut } = fenetrePreset(presetActif, fin);

  const opps = filtrer(DONNEES.opportunites_brutes, "date_creation", debut, fin);
  const soum = filtrer(DONNEES.soumissions_brutes, "date_envoi", debut, fin);

  majMeta(debut, fin);
  document.getElementById("tableau-sources").innerHTML = rendreTableauSources(calculerParSource(opps, soum));
}

initPresets(render);
chargerDonnees(render);
