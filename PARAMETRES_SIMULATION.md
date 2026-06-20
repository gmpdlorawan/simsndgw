# Paramètres de simulation SDN-GW

Récapitulatif pour l’article (Tableau 3) et correspondance avec le simulateur `eval_sdn_gw.py`.

---

## TABLEAU 3 — Paramètres de simulation SDN-GW (article)

| Paramètre | Valeur |
|-----------|--------|
| **Outil** | Python 3 / SimPy, EU868 (868 MHz) |
| **BW / PIRE max.** | 125 kHz / 14 dBm |
| **SF / ADR** | SF7–SF12 (DR0–DR5), ADR activé |
| **Zone / GW** | 10 km × 10 km, 3 GW fixes, rayon 3 000 m |
| **Densités** | 10 à 100 nœuds (pas de 10) |
| **Durée / répét.** | 3 600 s / 10 scénarios indépendants |
| **S1 (nœuds fixes)** | 30 % nœuds en zone frontière ≤ 500 m |
| **S2 (mobilité)** | RWP, 50–200 m/pas, pas = 5 s (10–40 m/s) |
| **(α, β, γ, δ)** | (0,35 ; 0,25 ; 0,20 ; 0,20) |
| **H / W / Tmin** | 0,10 / 5 uplinks / 120 s |

---

## Baselines comparées

| Approche | Description |
|----------|-------------|
| **RSSI-only** | Meilleur RSSI instantané à chaque uplink, sans fenêtre glissante |
| **Score-noH** | Score composite (éq. 1), W = 5, H = 0, Tmin = 0 |
| **SDN-GW** | Score composite + hystérésis double garde (éq. 2), H = 0,10, Tmin = 120 s |

---

## GW-CM — Score composite (éq. 1)

\[
S(n, GW_i) = \alpha \tilde{RSSI}_i + \beta \tilde{SNR}_i + \gamma (1 - \tilde{Load}_i) + \delta \, PRR(n, GW_i)
\]

| Symbole | Valeur | Rôle |
|---------|--------|------|
| α | 0,35 | RSSI moyen sur fenêtre W |
| β | 0,25 | SNR moyen sur fenêtre W |
| γ | 0,20 | Charge inversée (pénalise GW surchargées) |
| δ | 0,20 | PRR historique sur fenêtre W |
| W | 5 uplinks | Fenêtre glissante |
| H | 0,10 | Seuil minimal de gain de score pour commuter |
| Tmin | 120 s | Garde temporelle entre deux commutations |

**Décision de commutation (éq. 2)** : commuter vers \(GW_j = \arg\max S\) si  
\(S(GW_j) - S(GW^*) > H\) et \(t - t_{switch}(n) > T_{min}\).

---

## Environnement réseau (implémentation)

| Paramètre | Valeur | Constante code (`eval_sdn_gw.py`) |
|-----------|--------|-----------------------------------|
| Surface | 10 km × 10 km | `AREA_M = 10_000` |
| Nombre de GW | 3 | `N_GW = 3` |
| Rayon de couverture | 3 000 m | `R_GW_M = 3_000` |
| Positions GW (m) | (2500, 5000), (7500, 5000), (5000, 2000) | `GW_POSITIONS` |
| Durée simulation | 3 600 s | `SIM_DUR_S` |
| Répétitions | 10 seeds | `N_SEEDS_DEFAULT = 10` |
| Densités nœuds | 10, 20, …, 100 | `NODE_COUNTS` |

---

## Couche physique LoRa / EU868

| Paramètre | Valeur | Constante code |
|-----------|--------|----------------|
| Bande | 868 MHz (EU868) | — |
| Bande passante | 125 kHz | — |
| PIRE / EIRP | 14 dBm | `EIRP_DBM = 14.0` |
| SF | 7–12 | `SF_MIN`, `SF_MAX` |
| Path-loss | Log-distance, n = 2,7 | `N_PL = 2.7` |
| PL à 1 m | 32 dB | `PL_AT_1M_DB = 32.0` |
| Shadowing | Log-normal, σ = 7,5 dB | `SIGMA_SHADOW_DB = 7.5` |
| Fading rapide | ±6 dB (uniforme) | `FADE_MAX_DB = 6.0` |
| Bruit récepteur | −118,5 dBm (+ jitter) | `NOISE_FLOOR_DBM` |
| Marge choix SF (ADR simplifié) | 8 dB | `PHY_MARGIN_DB = 8.0` |
| Sensibilité / SNR min | Semtech SF7–SF12 | `GW_SENSITIVITY_DBM`, `SNR_DEMOD_MIN_DB` |

**Note ADR** : l’article indique « ADR activé » ; le code utilise un **choix de SF initial** selon la distance (ADR simplifié au déploiement), sans adaptation dynamique pendant la simulation.

---

## Trafic uplink et cycle utile

| Paramètre | Valeur | Constante code |
|-----------|--------|----------------|
| Intervalle d’émission \(T_{emis}\) | 60 s | `T_EMIS_S = 60.0` |
| Uplinks de référence (article, éq. 6) | NUL ≈ 40 / nœud / h | `NUL = 40` |
| Créneaux théoriques / h | 60 | `UPLINK_SLOTS_PER_H` |
| Probabilité de saut (≈ cycle 1 %) | ~33 % | `DUTY_SKIP_PROB` |

---

## Scénarios d’évaluation

### S1 — Nœuds fixes
- 30 % des nœuds placés en **zone frontière** : écart ≤ 500 m entre la 1re et la 2e GW la plus proche.
- 70 % répartis uniformément dans l’union des couvertures.
- Aucun déplacement.
- Calibration shadowing frontière (validation D2) : **désactivée** par défaut (`BORDER_SHADOW_CALIBRATE = False`).

### S2 — Mobilité RWP (Random Waypoint)
- Pas de déplacement : 50–200 m toutes les **5 s** (équivalent 10–40 m/s selon l’article).
- Destinations dans l’union des disques de couverture.
- Tous les nœuds mobiles.

---

## Downlink LoRaWAN Classe A

| Paramètre | Valeur | Constante code |
|-----------|--------|----------------|
| Délai RX1 | 1 000 ms | `RECEIVE_DELAY1_MS` |
| Délai RX2 | 2 000 ms | `RECEIVE_DELAY2_MS` |
| Backhaul NS→GW (moyenne / écart-type) | 85 ± 35 ms | `BACKHAUL_DELAY_MS_MEAN`, `_STD` |
| Surcoût backhaul vs charge GW | +90 ms × fraction de charge | `BACKHAUL_LOAD_GAIN_MS` |
| Surcoût changement de GW | 180 ms | `GW_SWITCH_OVERHEAD_MS` |
| Lissage charge (score) | α_EMA = 0,12 | `LOAD_EMA_ALPHA` |

---

## Métriques collectées

| Métrique | Définition |
|----------|------------|
| **CGW** | Nombre moyen de changements de passerelle downlink par nœud (sur 3 600 s) |
| **CGW_radio** | Commutations classées « radio » (hors mobilité forcée / GW absente) |
| **Pswitch** | CGW / nombre moyen d’uplinks réellement émis par nœud |
| **Pswitch_nul** | CGW / NUL (NUL = 40, référence article éq. 6) |
| **Pswitch_frontière** | Commutations / uplinks (nœuds frontière S1, RSSI-only) |
| **σL** | Écart-type de la répartition de charge entre les 3 GW |
| **LDL** | Latence descendante moyenne (RX1 ou RX2 + backhaul + surcoûts) / tentative DL |
| **PDR** | Taux de réception uplink au NS : `uplink_rx / uplink_tx` (%) |
| **PDR_e2e** | Uplink OK et downlink OK : `e2e_ok / uplink_tx` (%) |

---

## Références analytiques (Tableau 2 — article)

| Référence | Formule / valeur |
|-----------|------------------|
| **D1** | \(\sigma_{RSSI} = \sqrt{7{,}5^2 + 3^2} \approx 8{,}08\) dB ; \(\sigma_{RSSI,eff} = \sigma_{RSSI}/\sqrt{W} \approx 3{,}61\) dB |
| **D2** | \(P_{switch,frontière}^{RSSI-only} = 0{,}50\) (zone de chevauchement, nœud immobile) |

Fonction dans le code : `analytical_table2()`.

---

## Exécution

```bash
# Évaluation complète (CSV + graphiques)
python eval_sdn_gw.py --scenarios S1,S2 --seeds 10

# Ou sous Windows
lancer.bat eval
```

Fichiers produits : `sdn_gw_eval_results.csv`, `plots/comparison_*.png`.

---

## Écarts article ↔ code (à mentionner en discussion)

| Point article | Implémentation actuelle |
|-------------|-------------------------|
| SimPy | Boucle à événements discrets (pas de 1 s), Python 3 + NumPy |
| ADR activé | SF fixé au déploiement (ADR simplifié) |
| PDR > 97,9 % | Dépend du modèle PHY (~88–91 % selon runs) |
| Interférences / cycle utile 1 % | Saut probabiliste + capture par GW |

---

*Fichier généré pour le projet « Simulation SDN_GW » — aligné sur le Tableau 3 et `eval_sdn_gw.py`.*
