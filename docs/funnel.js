// Page Funnel — Lead -> Contacté -> Visite bookée -> Soumission -> Won.

function calculerFunnel(opps, soumissions, vendeur) {
  const oppsV = vendeur ? opps.filter((o) => o.vendeur === vendeur) : opps;
  const soumV = vendeur ? soumissions.filter((s) => s.vendeur === vendeur) : soumissions;

  const contactes = oppsV.filter((o) => o.n_appels_sortants > 0 || o.n_sms_sortants > 0);
  const visite = oppsV.filter((o) => o.visite_bookee);
  const won = soumV.filter((s) => s.won);

  return [
    { nom: "Lead", n: oppsV.length, valeur: null },
    { nom: "Contacté", n: contactes.length, valeur: null },
    { nom: "Visite bookée", n: visite.length, valeur: null },
    { nom: "Soumission envoyée", n: soumV.length, valeur: soumV.reduce((a, s) => a + (s.montant || 0), 0) },
    { nom: "Won", n: won.length, valeur: won.reduce((a, s) => a + (s.montant || 0), 0) },
  ];
}

function rendreFunnel(etapes) {
  const max = etapes[0].n || 1;
  return etapes
    .map((e, i) => {
      const largeur = Math.max((e.n / max) * 100, e.n > 0 ? 3 : 0);
      const conv = i > 0 && etapes[i - 1].n ? pct(e.n, etapes[i - 1].n) : null;
      const chiffres = `${fmtNombre(e.n)}${e.valeur !== null ? ` — ${fmtDollars(e.valeur)}` : ""}${conv !== null ? ` (${fmtPct(conv)})` : ""}`;
      return `<div class="funnel-etape">
        <div class="funnel-nom">${e.nom}</div>
        <div class="funnel-barre-fond"><div class="funnel-barre" style="width:${largeur}%"></div></div>
        <div class="funnel-chiffres">${chiffres}</div>
      </div>`;
    })
    .join("");
}

function render() {
  if (!DONNEES) return;
  const fin = versDate(DONNEES.periode.fin);
  const { debut } = fenetrePreset(presetActif, fin);

  const opps = filtrer(DONNEES.opportunites_brutes, "date_creation", debut, fin);
  const soum = filtrer(DONNEES.soumissions_brutes, "date_envoi", debut, fin);

  majMeta(debut, fin);

  document.getElementById("funnel-equipe").innerHTML = rendreFunnel(calculerFunnel(opps, soum, null));
  document.getElementById("funnel-vendeurs").innerHTML = DONNEES.vendeurs_config
    .map((nom) => `<div class="carte"><h2>${nom}</h2>${rendreFunnel(calculerFunnel(opps, soum, nom))}</div>`)
    .join("");
}

initPresets(render);
chargerDonnees(render);
