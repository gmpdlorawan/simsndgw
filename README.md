# SDN-GW — Simulation LoRaWAN multi-passerelles

Simulateur Python pour l’évaluation de **SDN-GW** : sélection stabilisée de la passerelle downlink par score composite (RSSI, SNR, charge, PRR) et hystérésis à double garde (*H*, *Tmin*), module **GW-CM** au serveur réseau.

**Auteurs** — Djièta Ouindé Aboul Fatas BAMOGO, Laboratoire LAMDI, Bobo-Dioulasso, Burkina Faso

---

## Installation

```bash
git clone https://github.com/gmpdlorawan/simsndgw.git
cd simsndgw
python -m pip install -r requirements.txt
```

Dépôt : [github.com/gmpdlorawan/simsndgw](https://github.com/gmpdlorawan/simsndgw)

**Prérequis :** Python 3.10+, NumPy, Matplotlib, Streamlit, Plotly (optionnel)

---

## Utilisation

**Évaluation batch** (CSV + graphiques dans `plots/`) :

```bash
python eval_sdn_gw.py --scenarios S1,S2 --seeds 10
```

Sous Windows : `lancer.bat eval`

**Interface web** : `lancer.bat` → [http://127.0.0.1:8501](http://127.0.0.1:8501)

---

## Paramètres

Voir **[PARAMETRES_SIMULATION.md](PARAMETRES_SIMULATION.md)** (Tableau 3 : EU868, 3 GW, S1/S2, α/β/γ/δ, H, W, Tmin).

---

## Structure

```
eval_sdn_gw.py          # Simulateur + GW-CM
app_simulation.py       # Interface Streamlit
PARAMETRES_SIMULATION.md
requirements.txt
lancer.bat
plots/                  # Graphiques (générés par eval)
```

**Baselines :** RSSI-only · Score-noH · SDN-GW  
**Métriques :** CGW, Pswitch, σL, LDL, PDR, PDR_e2e

---

## Licence

MIT — voir [LICENSE](LICENSE).
