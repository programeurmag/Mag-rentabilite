# SPEC — Module 3 : Rapport de chantier quotidien (capture terrain) MAG

Extension du projet existant. Objectif : capturer sur le terrain, en 30-45 secondes par job, les données que Jobber ne capture pas de façon fiable — qui était présent, le temps réel passé sur chaque job, et les matériaux utilisés — puis les intégrer à l'agent de rentabilité comme source primaire.

Principe : la capture est humaine mais minimale (le chauffeur de chaque truck), le traitement est 100% automatique. Les punchs Jobber deviennent une validation croisée, plus la source unique.

---

## 1. Le formulaire (Google Form rempli par le chauffeur)

**Une soumission par job visité** (pas par journée) — un chauffeur qui fait 3 jobs remplit 3 fois le form. C'est la clé : ça donne les heures et les matériaux PAR JOB directement, sans logique de répartition.

Champs :
1. **Chauffeur / Truck** — liste déroulante (Truck Lavage 1, Truck Lavage 2, Truck Sable 1, 2, 3...)
2. **No de job Jobber** — texte court (ex. 198). Champ le plus important pour le croisement.
3. **Client / adresse** — texte court (secours si le no de job est erroné)
4. **Gars présents sur ce job** — cases à cocher, liste complète des employés terrain (incluant ceux qui ne sont pas sur Jobber)
5. **Heure d'arrivée sur le site** / **Heure de départ** — l'agent calcule la durée × nombre de gars = heures-personnes du job
6. **Matériaux utilisés** — champs numériques : sacs de sable polymère, litres de scellant, autres (texte libre : quantité + quoi)
7. **Statut du job** — deux choix seulement, gros et clairs : « ✅ Terminé » / « 🔁 On revient ». C'est tout ce que le gars a à décider. Un job de plusieurs jours = plusieurs soumissions du Form (une par jour de présence), toutes « On revient » sauf la dernière.
8. **Notes / imprévus** — texte optionnel (bris, surprise sur le terrain, extra demandé par le client)

Le Form alimente automatiquement un Google Sheets (comportement natif de Google Forms).

## 2. Accès aux données pour l'agent

Ordre de préférence :
1. **API Google Sheets avec un compte de service** (gratuit, lecture seule) : créer un projet sur console.cloud.google.com, activer l'API Sheets, créer un service account, partager le Sheets de réponses avec le courriel du service account. La clé JSON va dans les secrets GitHub comme les autres credentials.
2. Alternative simple si l'option 1 bloque : publication CSV du Sheets (Fichier → Partager → Publier sur le web → CSV) et lecture par URL. Moins sécurisé (URL obscure mais publique) — acceptable temporairement, à mentionner à Justin.

## 3. Coûts standards des matériaux (config.yaml — ajouts)

```yaml
couts_materiaux:
  sac_polymere: 0        # $ / sac — À REMPLIR PAR JUSTIN (coût réel d'achat)
  scellant_litre: 0      # $ / litre — À REMPLIR
employes_hors_jobber:    # gars sans licence Jobber — noms EXACTEMENT comme dans le Form
  # "Prénom Nom": taux horaire
seuil_ecart_heures: 0.15   # écart Form vs punchs Jobber qui déclenche une alerte (15%)
overhead_quotidien_total: 1340   # $ / jour, tous camions confondus
nb_camions: 5                    # 2 lavage + 3 sable → 268 $/camion/jour
seuil_burn_job_en_cours: 0.60    # alerte si coûts accumulés > 60% de la valeur avant complétion
```
Le chauffeur rapporte des quantités, l'agent convertit en $ avec les coûts standards. Pas de factures par job.

## 4. Logique d'intégration dans le calcul de rentabilité

Nouvelle hiérarchie des sources pour les heures d'un job :
1. **Rapport de chantier (Form)** = source primaire : (départ − arrivée) × nombre de gars présents = heures-personnes, coût = Σ heures × taux de chaque gars présent (taux depuis config, incluant les hors-Jobber)
2. **Punchs Jobber** = validation : comparer les heures Form aux heures punchées par les mêmes gars le même jour. Écart > seuil → alerte dans le rapport hebdo, les deux chiffres affichés.
3. **Fallback** : si aucun rapport de chantier pour un job (oubli du chauffeur), utiliser l'ancienne logique d'attribution (punch direct → répartition) et flaguer le job « données Form manquantes ».

Matériaux : Σ quantités × coûts standards, soustraits dans la marge du job (colonne Matériaux maintenant automatique au lieu de manuelle).

### 4b. Overhead (frais généraux)

- Overhead total : **1 340 $ / jour**, réparti également sur les **5 camions** (2 lavage + 3 sable) = **268 $ par camion par jour**.
- Charge d'overhead d'un job = pour chaque rapport de chantier : 268 $ × (heures du camion sur ce job ÷ heures totales du camion cette journée-là). Un camion qui passe la journée complète sur un job charge 268 $ à ce job; s'il fait 2 jobs, l'overhead se répartit au prorata du temps.
- Note importante sur les camions de lavage : ils exécutent l'étape 1 (nettoyage) des remises à neuf et « produisent » rarement du revenu seuls — leur overhead et leurs heures se chargent quand même au job, ce qui donne le vrai coût complet d'une remise à neuf (lavage + sable). Ne pas exclure leur coût sous prétexte qu'ils ne facturent pas directement.
- La marge d'un job devient : revenu − coût MO − matériaux − overhead allloué. Afficher la marge avant et après overhead dans le rapport (deux colonnes) pour que Justin voie les deux niveaux.

### 4c. Jobs sur plusieurs jours

- L'agent regroupe TOUTES les soumissions du Form portant le même no de job, peu importe la date ou le camion. Heures, matériaux et overhead s'accumulent au fil des jours.
- Un job est considéré **en cours** tant que sa dernière soumission dit « On revient » et qu'il n'est pas fermé dans Jobber. Il est considéré **complété** quand une soumission dit « Terminé » OU que Jobber le montre fermé (si contradiction entre les deux, alerte).
- La marge finale se calcule seulement à la complétion. Entre-temps, le rapport hebdo affiche une section « Jobs en cours » : coûts accumulés à date vs valeur du job, avec un % de burn (ex. « Job #198 : 62% de la valeur déjà dépensée en coûts, pas terminé »).
- **Alerte burn** : job en cours dont les coûts accumulés dépassent le seuil configuré (défaut 60% de la valeur) → drapeau rouge avant que la job devienne non rentable, pas après.

## 5. Alertes ajoutées au rapport hebdo

- Jobs fermés dans Jobber cette semaine SANS rapport de chantier (par truck — pour savoir quel chauffeur oublie)
- Rapports de chantier dont le no de job ne matche rien dans Jobber (faute de frappe → montrer le champ client/adresse pour correction)
- Écart heures Form vs punchs Jobber au-dessus du seuil
- Même employé rapporté présent sur deux trucks en même temps
- Job marqué « On revient » sans visite de suivi cédulée dans Jobber
- Job en cours dont le burn dépasse le seuil (coûts accumulés vs valeur)
- Contradiction : Form dit « On revient » mais Jobber montre le job fermé (ou l'inverse)

## 6. Checklist de mise en place pour Justin

1. [ ] Créer le Google Form avec les 8 champs (15 min) — garder les noms de champs EXACTEMENT comme la spec pour que le parsing soit prévisible
2. [ ] Lier le Form à un Google Sheets de réponses (bouton natif dans l'onglet Réponses)
3. [ ] Remplir les coûts standards des matériaux et la liste des gars hors Jobber avec leurs taux
4. [ ] Mettre le lien du Form en raccourci sur l'écran d'accueil du téléphone de chaque chauffeur
5. [ ] Annoncer la règle aux chauffeurs : un rapport par job, avant de quitter le site. C'est court, c'est non négociable, et c'est ça qui calcule la rentabilité (et éventuellement les bonus d'équipe)

## 7. Ordre de travail pour Claude Code

1. Lire le Sheets de réponses (service account) et parser les soumissions
2. Croiser avec les jobs Jobber par no de job (fallback : matching flou sur client/adresse, à confirmer manuellement via alerte)
3. Remplacer la source des heures dans le calcul de rentabilité selon la hiérarchie de la section 4 — garder l'ancienne logique comme fallback
4. Calculer les matériaux automatiquement
5. Ajouter les alertes de la section 5 au rapport hebdo existant
6. Test de bout en bout avec 2-3 soumissions de form remplies par Justin lui-même avant le déploiement aux chauffeurs
