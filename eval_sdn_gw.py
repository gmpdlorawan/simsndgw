"""
Évaluation SDN-GW — simulateur Python / SimPy (événements discrets) aligné sur l'article.
-----------------------------------------------------------------------------------------
Module GW-CM au NS : score composite (éq. 1) + hystérésis double garde H, Tmin (éq. 2).

Scénarios S1 (nœuds fixes, 30 % zone frontière ≤ 500 m) et S2 (RWP, pas 5 s).
Baselines : RSSI-only | Score-noH (W=5, H=0, Tmin=0) | SDN-GW.

Paramètres Table 3 : EU868 125 kHz, 3 GW, zone 10×10 km, T_emis → NUL≈40 uplinks/nœud,
(α,β,γ,δ)=(0,35;0,25;0,20;0,20), W=5, H=0,10, Tmin=120 s.

Métriques neutres : CGW, CGW_radio, Pswitch=CGW/UL réels, Pswitch_nul (réf. NUL=40),
σL, LDL (DL réussis), PDR uplink + PDR_e2e. Pas de pénalité DL ni PHY « plancher » artificiel.
Références analytiques D1/D2 : analytical_table2().
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple
from pathlib import Path

import numpy as np

# --- Tableau 2 : constantes EU868 / simulation --------------------------------
AREA_M = 10_000.0
N_GW = 3
R_GW_M = 3_000.0
SIM_DUR_S = 3_600.0
# Cycle utile EU868 ~1 % → NUL≈40 uplinks effectifs / nœud / h (article, éq. 6)
NUL = 40
# D2 : Temis = 60 s (décorrélation fading entre uplinks en zone frontière)
T_EMIS_S = 60.0
UPLINK_SLOTS_PER_H = int(SIM_DUR_S / T_EMIS_S)
# Probabilité de saut d'émission pour refléter ~40 UL effectifs sur 60 créneaux
DUTY_SKIP_PROB = max(0.0, 1.0 - NUL / max(UPLINK_SLOTS_PER_H, 1))
T_MOVE_S = 5.0
# Latence backhaul NS→GW (variation réaliste, article III.C)
BACKHAUL_DELAY_MS_MEAN = 85.0
BACKHAUL_DELAY_MS_STD = 35.0
TMIN_S = 120.0
H_THRESH = 0.10
W_WINDOW = 5
N_SEEDS_DEFAULT = 10
NODE_COUNTS = list(range(10, 101, 10))

# Coefficients score (éq. 2)
ALPHA, BETA, GAMMA, DELTA = 0.35, 0.25, 0.20, 0.20

# Propagation (éq. 1) — log-distance à partir de 1 m, calibré pour EU868 ~3 km utiles
N_PL = 2.7
SIGMA_SHADOW_DB = 7.5
# PL(1 m) — modèle log-distance (non recalibré sur un PDR cible)
PL_AT_1M_DB = 32.0
EIRP_DBM = 14.0
# Fading rapide ε(t) : ±3–6 dB (article II.B) — uniforme symétrique
FADE_MAX_DB = 6.0
# σ_fading rapide pour D1 (article III.C)
SIGMA_FADE_DB = 3.0
# Bruit de référence pour SNR (dBm)
NOISE_MEAN_DBM = -120.0
NOISE_STD_DBM = 1.5

# --- Couche physique réaliste EU868 125 kHz (ordre de grandeur Semtech SX12xx) -----
# Puissance de bruit (légèrement optimiste vs -117 dBm pour compenser le modèle simplifié)
NOISE_FLOOR_DBM = -118.5
# SNR minimum à la démodulation (dB), SF7…SF12 — datasheet LoRa
SNR_DEMOD_MIN_DB: Dict[int, float] = {
    7: -7.5,
    8: -10.0,
    9: -12.25,
    10: -15.0,
    11: -17.5,
    12: -20.0,
}
# Sensibilité typique récepteur @125 kHz (dBm), SF7…SF12
GW_SENSITIVITY_DBM: Dict[int, float] = {
    7: -123.0,
    8: -126.0,
    9: -129.0,
    10: -132.0,
    11: -134.0,
    12: -137.0,
}
SF_MIN, SF_MAX = 7, 12
# Marge sur sensibilité au choix du SF (shadowing/fading non pris dans la ligne médiane seule)
PHY_MARGIN_DB = 8.0
# Sigmoïde PER + plancher haut en zone couverte (interférences résiduelles)
PHY_DEMOD_STEEPNESS = 0.62
PHY_SNR_IMPL_MARGIN_DB = 3.0
# Lissage charge GW pour le score (évite oscillations min-max sur 2 candidats)
LOAD_EMA_ALPHA = 0.12
# Shadowing frontière : False = shadowing indépendant (sans biais D2)
BORDER_SHADOW_CALIBRATE = False
# Seuil déplacement (m) entre deux UL pour classer une commutation « mobilité »
MOBILITY_SWITCH_THRESH_M = 25.0

# Bornes normalisation min-max (article III.C)
RSSI_MIN_DBM, RSSI_MAX_DBM = -120.0, -60.0
SNR_MIN_DB, SNR_MAX_DB = -20.0, 10.0

# LoRaWAN Class A — RX1 / RX2 après uplink (spec v1.1)
RECEIVE_DELAY1_MS = 1_000.0
RECEIVE_DELAY2_MS = 2_000.0
# Backhaul supplémentaire si la GW est très chargée (article : variations de backhaul)
BACKHAUL_LOAD_GAIN_MS = 90.0
# Délai NS si la passerelle downlink vient de changer (réordonnancement)
GW_SWITCH_OVERHEAD_MS = 180.0

Approach = Literal["rssi_only", "score_noh", "sdngw"]
Scenario = Literal["S1_fixed", "S2_rwp"]

# Légende figures « courbes vs densité » : RSSI-only bleu, Score-noH orange, SDN-GW vert
APPROACH_COLORS: Dict[Approach, str] = {
    "rssi_only": "#1f77b4",
    "score_noh": "#ff7f0e",
    "sdngw": "#2ca02c",
}


def path_loss_db(distance_m: float) -> float:
    d = max(distance_m, 1.0)
    return PL_AT_1M_DB + 10.0 * N_PL * math.log10(d)


def thermal_noise_dbm(rng: np.random.Generator) -> float:
    return float(rng.normal(NOISE_MEAN_DBM, NOISE_STD_DBM))


def compute_rssi_dbm(
    distance_m: float,
    shadow_db: float,
    rng: np.random.Generator,
) -> float:
    fade = float(rng.uniform(-FADE_MAX_DB, FADE_MAX_DB))
    return EIRP_DBM - path_loss_db(distance_m) + shadow_db + fade


def snr_db(rssi_dbm: float, rng: np.random.Generator) -> float:
    return rssi_dbm - thermal_noise_dbm(rng)


def backhaul_delay_ms(gw: int, sel: "GatewaySelector", rng: np.random.Generator) -> float:
    """Latence NS→GW : gaussienne + terme de charge (même loi pour toutes les approches)."""
    load_f = sel._load_fraction(gw)
    mean = BACKHAUL_DELAY_MS_MEAN + BACKHAUL_LOAD_GAIN_MS * load_f
    return max(0.0, float(rng.normal(mean, BACKHAUL_DELAY_MS_STD)))


def class_a_downlink_latency_ms(
    rssi_rx1_dbm: float,
    sf: int,
    rng: np.random.Generator,
    snr_rx1: Optional[float],
    *,
    rssi_rx2_dbm: Optional[float] = None,
    snr_rx2: Optional[float] = None,
) -> Tuple[float, bool]:
    """
    Latence descendante Class A : RX1 puis, si échec, RX2 (spec).
    Retourne (latence_ms, succès).
    """
    ok1, _, _ = phy_decode_uplink(rssi_rx1_dbm, sf, rng, snr_if_known=snr_rx1)
    if ok1:
        return RECEIVE_DELAY1_MS, True
    if rssi_rx2_dbm is None:
        rssi_rx2_dbm = rssi_rx1_dbm
    if snr_rx2 is None:
        snr_rx2 = rssi_rx2_dbm - noise_floor_draw(rng)
    ok2, _, _ = phy_decode_uplink(rssi_rx2_dbm, sf, rng, snr_if_known=snr_rx2)
    if ok2:
        return RECEIVE_DELAY2_MS, True
    return RECEIVE_DELAY2_MS, False


def noise_floor_draw(rng: np.random.Generator) -> float:
    """Bruit thermique + léger jitter (récepteur GW)."""
    return NOISE_FLOOR_DBM + float(rng.normal(0.0, min(NOISE_STD_DBM, 1.0)))


def choose_sf_install(distance_m: float) -> int:
    """ADR simplifié : SF minimal tel que la liaison médiane (sans fading) dépasse sensibilité + marge."""
    d = max(distance_m, 1.0)
    rssi_line = EIRP_DBM - path_loss_db(d)
    for sf in range(SF_MIN, SF_MAX + 1):
        if rssi_line >= GW_SENSITIVITY_DBM[sf] + PHY_MARGIN_DB:
            return sf
    return SF_MAX


def phy_decode_uplink(
    rssi_dbm: float,
    sf: int,
    rng: np.random.Generator,
    snr_if_known: Optional[float] = None,
) -> Tuple[bool, float, float]:
    """
    Réception réaliste : en dessous de la sensibilité SF → échec ;
    sinon probabilité de décodage croissante avec la marge SNR au-delà du seuil LoRa.
    Si snr_if_known est fourni (même tirage que le canal), évite un double tirage de bruit.
    """
    sf = int(np.clip(sf, SF_MIN, SF_MAX))
    if snr_if_known is not None:
        snr = float(snr_if_known)
    else:
        noise = noise_floor_draw(rng)
        snr = rssi_dbm - noise
    sens = GW_SENSITIVITY_DBM[sf]
    if rssi_dbm < sens:
        return False, rssi_dbm, snr
    req = SNR_DEMOD_MIN_DB[sf]
    margin = snr - req + PHY_SNR_IMPL_MARGIN_DB
    p_ok = float(1.0 / (1.0 + math.exp(-PHY_DEMOD_STEEPNESS * margin)))
    ok = bool(rng.random() < clip01(p_ok))
    return ok, rssi_dbm, snr


def clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def norm_rssi_global(rssi_dbm: float) -> float:
    return clip01((rssi_dbm - RSSI_MIN_DBM) / (RSSI_MAX_DBM - RSSI_MIN_DBM))


def norm_snr_global(snr: float) -> float:
    return clip01((snr - SNR_MIN_DB) / (SNR_MAX_DB - SNR_MIN_DB))


# Positions GW : triangle élargi (10 km) pour maximiser l'union des disques R_GW
def default_gw_positions() -> List[Tuple[float, float]]:
    return [
        (2_500.0, 5_000.0),
        (7_500.0, 5_000.0),
        (5_000.0, 2_000.0),
    ]


GW_POSITIONS = default_gw_positions()


def analytical_table2() -> Dict[str, float]:
    """
    Références analytiques D1 et D2 (article, Tableau 2).
    D1 : σRSSI = √(σ_shadow² + σ_fade²) ; σRSSI,eff = σRSSI / √W pour fenêtre W.
    D2 : Pswitch,frontière = 0,50 pour RSSI-only (zone de chevauchement, nœud immobile).
    """
    sigma_rssi_1 = math.sqrt(SIGMA_SHADOW_DB**2 + SIGMA_FADE_DB**2)
    sigma_rssi_eff = sigma_rssi_1 / math.sqrt(float(W_WINDOW))
    return {
        "sigma_rssi_1_uplink_db": sigma_rssi_1,
        "sigma_rssi_eff_w5_db": sigma_rssi_eff,
        "pswitch_frontier_rssi_only": 0.50,
        "nul_uplinks_per_node": float(NUL),
    }


def dist_m(x: float, y: float, gx: float, gy: float) -> float:
    return float(math.hypot(x - gx, y - gy))


def min_dist_any_gw(x: float, y: float) -> float:
    return min(dist_m(x, y, *GW_POSITIONS[i]) for i in range(N_GW))


def in_coverage_union(x: float, y: float) -> bool:
    return min_dist_any_gw(x, y) <= R_GW_M


@dataclass
class NodeState:
    nid: int
    x: float
    y: float
    is_border: bool = False
    # RWP (S2)
    wp_x: float = 0.0
    wp_y: float = 0.0
    step_m: float = 0.0
    # shadowing lent (par lien n–GW), inchangé pendant le run
    shadow_by_gw: np.ndarray = field(default_factory=lambda: np.zeros(N_GW))
    last_x: float = 0.0
    last_y: float = 0.0
    had_ul: bool = False


def place_uniform_covered(rng: np.random.Generator, max_tries: int = 20_000) -> Tuple[float, float]:
    """Uniforme dans l'union des disques de rayon R_GW (rejet)."""
    for _ in range(max_tries):
        x = float(rng.uniform(0.0, AREA_M))
        y = float(rng.uniform(0.0, AREA_M))
        if in_coverage_union(x, y):
            return x, y
    return 5_000.0, 5_000.0


def place_border_node(rng: np.random.Generator, max_tries: int = 20_000) -> Tuple[float, float]:
    """Zone frontière : deux GW à distance comparable (≤ 500 m d'écart, article S1)."""
    for _ in range(max_tries):
        x = float(rng.uniform(0.0, AREA_M))
        y = float(rng.uniform(0.0, AREA_M))
        if not in_coverage_union(x, y):
            continue
        ds = sorted(dist_m(x, y, *GW_POSITIONS[i]) for i in range(N_GW))
        if ds[1] < R_GW_M and (ds[1] - ds[0]) <= 500.0:
            return x, y
    return place_uniform_covered(rng)


def calibrate_border_shadow(
    x: float, y: float, sh: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    """Aligne le shadowing des deux GW dominantes pour isoler le fading (D2, Pswitch≈0,5)."""
    dists = [(dist_m(x, y, *GW_POSITIONS[i]), i) for i in range(N_GW)]
    dists.sort(key=lambda t: t[0])
    i0, i1 = dists[0][1], dists[1][1]
    mean_sh = float(rng.normal(0.0, SIGMA_SHADOW_DB / math.sqrt(2.0)))
    sh = sh.copy()
    sh[i0] = mean_sh
    sh[i1] = mean_sh
    return sh


def init_nodes_s1(n_nodes: int, rng: np.random.Generator) -> List[NodeState]:
    n_border = int(round(0.30 * n_nodes))
    nodes: List[NodeState] = []
    for i in range(n_nodes):
        border = i < n_border
        if border:
            x, y = place_border_node(rng)
        else:
            x, y = place_uniform_covered(rng)
        sh = np.array([rng.normal(0.0, SIGMA_SHADOW_DB) for _ in range(N_GW)], dtype=np.float64)
        if border and BORDER_SHADOW_CALIBRATE:
            sh = calibrate_border_shadow(x, y, sh, rng)
        nodes.append(
            NodeState(
                nid=i,
                x=x,
                y=y,
                is_border=border,
                shadow_by_gw=sh,
                last_x=x,
                last_y=y,
            )
        )
    return nodes


def rwp_pick(node: NodeState, rng: np.random.Generator) -> None:
    """Destination RWP dans l'union des couvertures GW (article S2, zone desservie)."""
    node.wp_x, node.wp_y = place_uniform_covered(rng)
    node.step_m = float(rng.uniform(50.0, 200.0))


def init_nodes_s2(n_nodes: int, rng: np.random.Generator) -> List[NodeState]:
    nodes: List[NodeState] = []
    for i in range(n_nodes):
        x, y = place_uniform_covered(rng)
        sh = np.array([rng.normal(0.0, SIGMA_SHADOW_DB) for _ in range(N_GW)], dtype=np.float64)
        n = NodeState(
            nid=i, x=x, y=y, is_border=False, shadow_by_gw=sh, last_x=x, last_y=y
        )
        rwp_pick(n, rng)
        nodes.append(n)
    return nodes


def _minmax_norm(vals: List[float]) -> List[float]:
    """Normalisation min-max sur [0,1] parmi les candidats (article, éq. 1)."""
    if not vals:
        return []
    mn, mx = min(vals), max(vals)
    if mx - mn < 1e-9:
        return [0.5 for _ in vals]
    return [(v - mn) / (mx - mn) for v in vals]


def move_rwp(node: NodeState, rng: np.random.Generator) -> None:
    dx, dy = node.wp_x - node.x, node.wp_y - node.y
    dist = math.hypot(dx, dy)
    if dist < 1e-3:
        rwp_pick(node, rng)
        return
    step = min(node.step_m, dist)
    node.x += step * dx / dist
    node.y += step * dy / dist
    node.x = max(0.0, min(AREA_M, node.x))
    node.y = max(0.0, min(AREA_M, node.y))
    if not in_coverage_union(node.x, node.y):
        node.x, node.y = place_uniform_covered(rng)
        rwp_pick(node, rng)
        return
    if math.hypot(node.wp_x - node.x, node.wp_y - node.y) < 5.0:
        rwp_pick(node, rng)


class GatewaySelector:
    """
    GW-CM (Algorithme 1) : RSSI-only, Score-noH, SDN-GW.
    S(n,GWi) = α·R̃ + β·S̃NR + γ·(1−L̃oad) + δ·PRR — indicateurs sur fenêtre W,
    normalisés sur [0,1] (bornes globales RSSI/SNR/charge ; PRR = succès/W).
    SDN-GW : commutation si S(GWj)−S(GW*)>H et t−t_switch>Tmin.
    """

    def __init__(self, approach: Approach, n_nodes: int):
        self.approach = approach
        self.n_nodes = n_nodes
        self.assigned: Dict[int, int] = {}
        self.t_switch: Dict[int, float] = {}
        self.gw_load = [0] * N_GW
        self.gw_load_ema = [0.0] * N_GW
        # history[n][gw] = list of (rssi_ok: bool, rssi_dbm, snr_db) derniers événements
        self.history: Dict[int, Dict[int, List[Tuple[bool, float, float]]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def record_observation(
        self,
        nid: int,
        obs: Dict[int, Tuple[bool, float, float]],
    ) -> None:
        """obs[gw] = (uplink_ok, rssi, snr) pour chaque GW ayant 'entendu' la tentative."""
        for gw, (ok, rssi, snr) in obs.items():
            h = self.history[nid][gw]
            h.append((ok, rssi, snr))
            if len(h) > W_WINDOW:
                del h[0 : len(h) - W_WINDOW]

    def _mean_rssi_snr(self, nid: int, gw: int) -> Tuple[float, float]:
        h = self.history[nid][gw]
        if not h:
            return RSSI_MIN_DBM, SNR_MIN_DB
        rs = [x[1] for x in h]
        ns = [x[2] for x in h]
        return float(np.mean(rs)), float(np.mean(ns))

    def _prr_window(self, nid: int, gw: int) -> float:
        h = self.history[nid][gw]
        if not h:
            return 0.0
        return float(sum(1 for ok, _, _ in h if ok)) / float(len(h))

    def _load_fraction(self, gw: int) -> float:
        total = float(sum(self.gw_load_ema))
        if total < 1e-9:
            return self.gw_load[gw] / max(1, self.n_nodes)
        return float(self.gw_load_ema[gw]) / total

    def _touch_load_ema(self, gw: int) -> None:
        for i in range(N_GW):
            self.gw_load_ema[i] *= 1.0 - LOAD_EMA_ALPHA
        self.gw_load_ema[gw] += LOAD_EMA_ALPHA

    def _set_assignment(self, nid: int, new_gw: int, t_now: float) -> None:
        old = self.assigned.get(nid)
        if old == new_gw:
            return
        if old is not None:
            self.gw_load[old] -= 1
        self.assigned[nid] = new_gw
        self.gw_load[new_gw] += 1
        self._touch_load_ema(new_gw)
        self.t_switch[nid] = t_now

    def select(
        self,
        nid: int,
        candidates: Dict[int, float],
        t_now: float,
        instant_rssi: Dict[int, float],
    ) -> int:
        """
        candidates : GW ayant reçu l'uplink avec succès -> RSSI utilisé pour RSSI-only.
        instant_rssi : même clés, RSSI instantané.
        """
        if not candidates:
            return self.assigned.get(nid, 0)

        if self.approach == "rssi_only":
            best = max(candidates.keys(), key=lambda g: instant_rssi[g])
            self._set_assignment(nid, best, t_now)
            return best

        gw_list = list(candidates.keys())

        # Éq. (1) — moyennes sur W, puis min-max sur les candidats pour chaque indicateur
        rssi_raw = [self._mean_rssi_snr(nid, g)[0] for g in gw_list]
        snr_raw = [self._mean_rssi_snr(nid, g)[1] for g in gw_list]
        load_raw = [self._load_fraction(g) for g in gw_list]
        prr_raw = [self._prr_window(nid, g) for g in gw_list]
        rssi_t = _minmax_norm(rssi_raw)
        snr_t = _minmax_norm(snr_raw)
        load_t = _minmax_norm(load_raw)
        prr_t = _minmax_norm(prr_raw)
        scores: Dict[int, float] = {}
        for i, g in enumerate(gw_list):
            scores[g] = (
                ALPHA * rssi_t[i]
                + BETA * snr_t[i]
                + GAMMA * (1.0 - load_t[i])
                + DELTA * prr_t[i]
            )

        best = max(scores.keys(), key=lambda g: scores[g])
        curr = self.assigned.get(nid)

        if self.approach == "score_noh":
            self._set_assignment(nid, best, t_now)
            return best

        # sdngw + hystérésis
        if curr is None or curr not in candidates:
            self._set_assignment(nid, best, t_now)
            return best

        gain = scores[best] - scores.get(curr, 0.0)
        t_last = self.t_switch.get(nid, -1e18)
        if gain > H_THRESH and (t_now - t_last) >= TMIN_S:
            self._set_assignment(nid, best, t_now)
            return best
        return curr


def _uplink_paths_and_winners(
    slot_nodes: List[int],
    nodes: List[NodeState],
    node_sf: np.ndarray,
    rng: np.random.Generator,
) -> Tuple[
    Dict[Tuple[int, int], Tuple[float, float, int]],
    Dict[int, Tuple[int, bool, float, float]],
]:
    """Chemins (nid, gw) → (RSSI, SNR, SF) ; par GW, capture du signal le plus fort puis décodage PHY."""
    paths: Dict[Tuple[int, int], Tuple[float, float, int]] = {}
    for nid in slot_nodes:
        n = nodes[nid]
        sf = int(node_sf[nid])
        for gw in range(N_GW):
            gx, gy = GW_POSITIONS[gw]
            d = dist_m(n.x, n.y, gx, gy)
            if d > R_GW_M:
                continue
            rssi = compute_rssi_dbm(d, float(n.shadow_by_gw[gw]), rng)
            noise = noise_floor_draw(rng)
            snr = rssi - noise
            paths[(nid, gw)] = (rssi, snr, sf)

    winners: Dict[int, Tuple[int, bool, float, float]] = {}
    for gw in range(N_GW):
        cand = [nid for nid in slot_nodes if (nid, gw) in paths]
        if not cand:
            continue
        nid_w = max(cand, key=lambda nid: paths[(nid, gw)][0])
        rssi_w, snr_w, sf_w = paths[(nid_w, gw)]
        ok_w, _, _ = phy_decode_uplink(rssi_w, sf_w, rng, snr_if_known=snr_w)
        winners[gw] = (nid_w, ok_w, rssi_w, snr_w)

    return paths, winners


def _run_uplink_for_nid(
    nid: int,
    n: NodeState,
    t_now: float,
    paths: Dict[Tuple[int, int], Tuple[float, float, int]],
    winners: Dict[int, Tuple[int, bool, float, float]],
    sel: GatewaySelector,
    rng: np.random.Generator,
    node_sf: np.ndarray,
    *,
    switches: np.ndarray,
    switches_radio: np.ndarray,
    switches_mob: np.ndarray,
    prev_gw: Dict[int, Optional[int]],
    uplink_tx: np.ndarray,
    uplink_rx: np.ndarray,
    e2e_ok: np.ndarray,
    dl_scheduled: np.ndarray,
    dl_delay_ms_sum: np.ndarray,
    rng_duty: np.random.Generator,
) -> None:
    if rng_duty.random() < DUTY_SKIP_PROB:
        return
    uplink_tx[nid] += 1
    obs: Dict[int, Tuple[bool, float, float]] = {}
    cand_rssi: Dict[int, float] = {}

    for gw in range(N_GW):
        if (nid, gw) not in paths:
            continue
        rssi, snr, _sf = paths[(nid, gw)]
        if gw not in winners:
            obs[gw] = (False, rssi, snr)
            continue
        nid_w, ok_w, rssi_w, snr_w = winners[gw]
        if nid != nid_w:
            obs[gw] = (False, rssi, snr)
        else:
            obs[gw] = (ok_w, rssi_w, snr_w)
            if ok_w:
                cand_rssi[gw] = rssi_w

    if sel.approach != "rssi_only":
        sel.record_observation(nid, obs)

    if not cand_rssi:
        return

    uplink_rx[nid] += 1
    curr_before = sel.assigned.get(nid)
    forced_gw = curr_before is not None and curr_before not in cand_rssi
    moved = (
        n.had_ul
        and math.hypot(n.x - n.last_x, n.y - n.last_y) > MOBILITY_SWITCH_THRESH_M
    )
    gw_dl = sel.select(nid, cand_rssi, t_now, cand_rssi)

    gx, gy = GW_POSITIONS[gw_dl]
    d_dl = dist_m(n.x, n.y, gx, gy)
    rssi_rx1 = compute_rssi_dbm(d_dl, float(n.shadow_by_gw[gw_dl]), rng)
    snr_rx1 = rssi_rx1 - noise_floor_draw(rng)
    rssi_rx2 = compute_rssi_dbm(d_dl, float(n.shadow_by_gw[gw_dl]), rng)
    snr_rx2 = rssi_rx2 - noise_floor_draw(rng)
    sf_nd = int(node_sf[nid])

    dl_scheduled[nid] += 1
    lat_radio_ms, ok_dl = class_a_downlink_latency_ms(
        rssi_rx1, sf_nd, rng, snr_rx1, rssi_rx2_dbm=rssi_rx2, snr_rx2=snr_rx2
    )
    bh_ms = backhaul_delay_ms(gw_dl, sel, rng)
    lat_total = lat_radio_ms + bh_ms

    pg = prev_gw[nid]
    if pg is not None and pg != gw_dl:
        lat_total += GW_SWITCH_OVERHEAD_MS
        switches[nid] += 1
        if forced_gw or moved:
            switches_mob[nid] += 1
        else:
            switches_radio[nid] += 1

    dl_delay_ms_sum[nid] += lat_total
    if ok_dl:
        e2e_ok[nid] += 1

    prev_gw[nid] = gw_dl
    n.last_x, n.last_y = n.x, n.y
    n.had_ul = True


def run_single(
    scenario: Scenario,
    approach: Approach,
    n_nodes: int,
    seed: int,
) -> Dict[str, float]:
    rng = np.random.default_rng(seed)
    nodes = init_nodes_s1(n_nodes, rng) if scenario == "S1_fixed" else init_nodes_s2(n_nodes, rng)
    sel = GatewaySelector(approach, n_nodes)

    switches = np.zeros(n_nodes, dtype=np.int32)
    switches_radio = np.zeros(n_nodes, dtype=np.int32)
    switches_mob = np.zeros(n_nodes, dtype=np.int32)
    prev_gw: Dict[int, Optional[int]] = {i: None for i in range(n_nodes)}
    uplink_tx = np.zeros(n_nodes, dtype=np.int32)
    uplink_rx = np.zeros(n_nodes, dtype=np.int32)
    e2e_ok = np.zeros(n_nodes, dtype=np.int32)
    dl_scheduled = np.zeros(n_nodes, dtype=np.int32)
    dl_delay_ms_sum = np.zeros(n_nodes, dtype=np.float64)

    emit_phase = rng.integers(0, int(T_EMIS_S), size=n_nodes)
    node_sf = np.zeros(n_nodes, dtype=np.int32)
    for i, node in enumerate(nodes):
        d_min = min(dist_m(node.x, node.y, *GW_POSITIONS[g]) for g in range(N_GW))
        node_sf[i] = choose_sf_install(d_min)

    t = 0.0
    while t < SIM_DUR_S:
        t_int = int(round(t))
        if scenario == "S2_rwp" and t > 0 and (t_int % int(T_MOVE_S) == 0):
            for n in nodes:
                move_rwp(n, rng)

        if t >= 0:
            slot_nodes = [
                nid
                for nid in range(n_nodes)
                if (t_int % int(T_EMIS_S)) == int(emit_phase[nid])
            ]
            if slot_nodes:
                paths, winners = _uplink_paths_and_winners(slot_nodes, nodes, node_sf, rng)
                for nid in slot_nodes:
                    _run_uplink_for_nid(
                        nid,
                        nodes[nid],
                        t,
                        paths,
                        winners,
                        sel,
                        rng,
                        node_sf,
                        switches=switches,
                        switches_radio=switches_radio,
                        switches_mob=switches_mob,
                        prev_gw=prev_gw,
                        uplink_tx=uplink_tx,
                        uplink_rx=uplink_rx,
                        e2e_ok=e2e_ok,
                        dl_scheduled=dl_scheduled,
                        dl_delay_ms_sum=dl_delay_ms_sum,
                        rng_duty=rng,
                    )

        t += 1.0

    cgw_list = switches.astype(np.float64).tolist()
    pdr_list = (100.0 * uplink_rx / np.maximum(uplink_tx, 1)).tolist()
    n_ul_mean = float(np.mean(uplink_tx))
    border_idx = [i for i, n in enumerate(nodes) if n.is_border]
    sw_b = int(np.sum(switches[border_idx])) if border_idx else 0
    ul_b = int(np.sum(uplink_tx[border_idx])) if border_idx else 0
    pswitch_border = float(sw_b / max(ul_b, 1))
    ldl_list = []
    for i in range(n_nodes):
        if dl_scheduled[i] > 0:
            ldl_list.append(float(dl_delay_ms_sum[i] / dl_scheduled[i]))
        else:
            ldl_list.append(RECEIVE_DELAY1_MS + BACKHAUL_DELAY_MS_MEAN)

    loads = np.array(sel.gw_load, dtype=np.float64)
    mean_l = float(np.mean(loads)) if N_GW else 0.0
    sigma_l = float(math.sqrt(np.sum((loads - mean_l) ** 2) / max(N_GW, 1)))

    cgw_mean = float(np.mean(cgw_list))
    cgw_radio_mean = float(np.mean(switches_radio.astype(np.float64)))
    pswitch = cgw_mean / max(n_ul_mean, 1.0)
    pswitch_nul = cgw_mean / max(NUL, 1.0)
    pdr_e2e = float(np.mean(100.0 * e2e_ok / np.maximum(uplink_tx, 1)))

    return {
        "cgw": cgw_mean,
        "cgw_radio": cgw_radio_mean,
        "cgw_std_nodes": float(np.std(cgw_list)),
        "pswitch": pswitch,
        "pswitch_nul": pswitch_nul,
        "pdr": float(np.mean(pdr_list)),
        "pdr_e2e": pdr_e2e,
        "ldl_ms": float(np.mean(ldl_list)),
        "sigma_l": sigma_l,
        "switches_total": int(np.sum(switches)),
        "n_ul_mean": n_ul_mean,
        "pswitch_border": pswitch_border,
    }


def _metrics_from_state(
    t_elapsed_s: float,
    n_nodes: int,
    switches: np.ndarray,
    uplink_tx: np.ndarray,
    uplink_rx: np.ndarray,
    dl_scheduled: np.ndarray,
    dl_delay_ms_sum: np.ndarray,
    gw_load: List[int],
) -> Tuple[float, float, float, float, List[float]]:
    del t_elapsed_s
    cgw_mean = float(np.mean(switches.astype(np.float64)))
    pdr_mean = float(np.mean(100.0 * uplink_rx / np.maximum(uplink_tx, 1)))
    ldl_vals = []
    for i in range(n_nodes):
        if dl_scheduled[i] > 0:
            ldl_vals.append(float(dl_delay_ms_sum[i] / dl_scheduled[i]))
        else:
            ldl_vals.append(RECEIVE_DELAY1_MS)
    ldl_mean = float(np.mean(ldl_vals)) if ldl_vals else RECEIVE_DELAY1_MS
    loads = np.array(gw_load, dtype=np.float64)
    mean_l = float(np.mean(loads)) if N_GW else 0.0
    sigma_l = float(math.sqrt(np.sum((loads - mean_l) ** 2) / max(N_GW, 1)))
    load_list = [float(loads[i]) for i in range(N_GW)]
    return cgw_mean, pdr_mean, ldl_mean, sigma_l, load_list


def run_single_traced(
    scenario: Scenario,
    approach: Approach,
    n_nodes: int,
    seed: int,
    trace_interval_s: float = 120.0,
    sim_duration_s: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Même simulation que run_single, avec instantanés pour UI (architecture + métriques).
    trace_interval_s : pas d'échantillonnage temporel (s).
    sim_duration_s : durée max (défaut SIM_DUR_S), utile pour prévisualisation rapide.
    """
    dur = float(sim_duration_s) if sim_duration_s is not None else SIM_DUR_S
    rng = np.random.default_rng(seed)
    nodes = init_nodes_s1(n_nodes, rng) if scenario == "S1_fixed" else init_nodes_s2(n_nodes, rng)
    sel = GatewaySelector(approach, n_nodes)

    switches = np.zeros(n_nodes, dtype=np.int32)
    switches_radio = np.zeros(n_nodes, dtype=np.int32)
    switches_mob = np.zeros(n_nodes, dtype=np.int32)
    prev_gw: Dict[int, Optional[int]] = {i: None for i in range(n_nodes)}
    uplink_tx = np.zeros(n_nodes, dtype=np.int32)
    uplink_rx = np.zeros(n_nodes, dtype=np.int32)
    e2e_ok = np.zeros(n_nodes, dtype=np.int32)
    dl_scheduled = np.zeros(n_nodes, dtype=np.int32)
    dl_delay_ms_sum = np.zeros(n_nodes, dtype=np.float64)

    trace_times: List[float] = []
    trace_cgw: List[float] = []
    trace_pdr: List[float] = []
    trace_ldl: List[float] = []
    trace_sigma: List[float] = []
    trace_loads: List[List[float]] = []
    trace_xs: List[np.ndarray] = []
    trace_ys: List[np.ndarray] = []
    trace_gw: List[np.ndarray] = []

    def record_trace(t_rec: float) -> None:
        cgw_m, pdr_m, ldl_m, sig_m, loads = _metrics_from_state(
            t_rec,
            n_nodes,
            switches,
            uplink_tx,
            uplink_rx,
            dl_scheduled,
            dl_delay_ms_sum,
            sel.gw_load,
        )
        trace_times.append(t_rec)
        trace_cgw.append(cgw_m)
        trace_pdr.append(pdr_m)
        trace_ldl.append(ldl_m)
        trace_sigma.append(sig_m)
        trace_loads.append(loads)
        gx = np.array([nodes[i].x for i in range(n_nodes)], dtype=np.float64)
        gy = np.array([nodes[i].y for i in range(n_nodes)], dtype=np.float64)
        gg = np.array([sel.assigned.get(i, -1) for i in range(n_nodes)], dtype=np.int32)
        trace_xs.append(gx)
        trace_ys.append(gy)
        trace_gw.append(gg)

    emit_phase = rng.integers(0, int(T_EMIS_S), size=n_nodes)
    node_sf = np.zeros(n_nodes, dtype=np.int32)
    for i, node in enumerate(nodes):
        d_min = min(dist_m(node.x, node.y, *GW_POSITIONS[g]) for g in range(N_GW))
        node_sf[i] = choose_sf_install(d_min)

    t = 0.0
    trace_iv = max(float(trace_interval_s), 1.0)
    while t < dur:
        t_int = int(round(t))
        if scenario == "S2_rwp" and t > 0 and (t_int % int(T_MOVE_S) == 0):
            for n in nodes:
                move_rwp(n, rng)

        if t >= 0:
            slot_nodes = [
                nid
                for nid in range(n_nodes)
                if (t_int % int(T_EMIS_S)) == int(emit_phase[nid])
            ]
            if slot_nodes:
                paths, winners = _uplink_paths_and_winners(slot_nodes, nodes, node_sf, rng)
                for nid in slot_nodes:
                    _run_uplink_for_nid(
                        nid,
                        nodes[nid],
                        t,
                        paths,
                        winners,
                        sel,
                        rng,
                        node_sf,
                        switches=switches,
                        switches_radio=switches_radio,
                        switches_mob=switches_mob,
                        prev_gw=prev_gw,
                        uplink_tx=uplink_tx,
                        uplink_rx=uplink_rx,
                        e2e_ok=e2e_ok,
                        dl_scheduled=dl_scheduled,
                        dl_delay_ms_sum=dl_delay_ms_sum,
                        rng_duty=rng,
                    )

        if t > 0 and (t_int % int(trace_iv) == 0):
            record_trace(t)

        t += 1.0

    record_trace(dur)

    cgw_list = switches.astype(np.float64).tolist()
    pdr_list = (100.0 * uplink_rx / np.maximum(uplink_tx, 1)).tolist()
    n_ul_mean = float(np.mean(uplink_tx))
    ldl_list = []
    for i in range(n_nodes):
        if dl_scheduled[i] > 0:
            ldl_list.append(float(dl_delay_ms_sum[i] / dl_scheduled[i]))
        else:
            ldl_list.append(RECEIVE_DELAY1_MS + BACKHAUL_DELAY_MS_MEAN)
    border_idx = [i for i, n in enumerate(nodes) if n.is_border]
    sw_b = int(np.sum(switches[border_idx])) if border_idx else 0
    ul_b = int(np.sum(uplink_tx[border_idx])) if border_idx else 0
    loads_arr = np.array(sel.gw_load, dtype=np.float64)
    mean_l = float(np.mean(loads_arr)) if N_GW else 0.0
    sigma_l = float(math.sqrt(np.sum((loads_arr - mean_l) ** 2) / max(N_GW, 1)))

    cgw_mean = float(np.mean(cgw_list))
    final = {
        "cgw": cgw_mean,
        "cgw_radio": float(np.mean(switches_radio.astype(np.float64))),
        "cgw_std_nodes": float(np.std(cgw_list)),
        "pswitch": cgw_mean / max(n_ul_mean, 1.0),
        "pswitch_nul": cgw_mean / max(NUL, 1.0),
        "pdr": float(np.mean(pdr_list)),
        "pdr_e2e": float(np.mean(100.0 * e2e_ok / np.maximum(uplink_tx, 1))),
        "ldl_ms": float(np.mean(ldl_list)),
        "sigma_l": sigma_l,
        "switches_total": int(np.sum(switches)),
        "n_ul_mean": n_ul_mean,
        "pswitch_border": float(sw_b / max(ul_b, 1)),
    }

    return {
        "final": final,
        "trace_times": np.array(trace_times, dtype=np.float64),
        "trace_cgw": np.array(trace_cgw, dtype=np.float64),
        "trace_pdr": np.array(trace_pdr, dtype=np.float64),
        "trace_ldl": np.array(trace_ldl, dtype=np.float64),
        "trace_sigma": np.array(trace_sigma, dtype=np.float64),
        "trace_loads": np.array(trace_loads, dtype=np.float64),
        "trace_xs": trace_xs,
        "trace_ys": trace_ys,
        "trace_gw": trace_gw,
        "gw_positions": list(GW_POSITIONS),
        "scenario": scenario,
        "approach": approach,
        "n_nodes": n_nodes,
        "seed": seed,
        "sim_duration_s": dur,
    }


def aggregate_runs(
    scenario: Scenario,
    approach: Approach,
    n_nodes: int,
    seeds: List[int],
) -> Dict[str, float]:
    runs = [run_single(scenario, approach, n_nodes, s) for s in seeds]
    out: Dict[str, float] = {
        "scenario": scenario,
        "approach": approach,
        "n_nodes": float(n_nodes),
        "cgw": float(np.mean([r["cgw"] for r in runs])),
        "cgw_std": float(np.std([r["cgw"] for r in runs])),
        "pswitch": float(np.mean([r["pswitch"] for r in runs])),
        "pswitch_nul": float(np.mean([r.get("pswitch_nul", 0.0) for r in runs])),
        "cgw_radio": float(np.mean([r.get("cgw_radio", 0.0) for r in runs])),
        "pdr": float(np.mean([r["pdr"] for r in runs])),
        "pdr_e2e": float(np.mean([r.get("pdr_e2e", 0.0) for r in runs])),
        "ldl_ms": float(np.mean([r["ldl_ms"] for r in runs])),
        "sigma_l": float(np.mean([r["sigma_l"] for r in runs])),
        "n_ul_mean": float(np.mean([r.get("n_ul_mean", 0.0) for r in runs])),
        "pswitch_border": float(np.mean([r.get("pswitch_border", 0.0) for r in runs])),
    }
    return out


def run_study(
    scenarios: List[Scenario],
    approaches: List[Approach],
    node_counts: List[int],
    n_seeds: int,
) -> List[Dict[str, float]]:
    seeds = list(range(n_seeds))
    total = len(scenarios) * len(approaches) * len(node_counts)
    done = 0
    rows: List[Dict[str, float]] = []
    for sc in scenarios:
        for ap in approaches:
            for n in node_counts:
                agg = aggregate_runs(sc, ap, n, seeds)
                rows.append(agg)
                done += 1
                sys.stdout.write(f"\r  [{done}/{total}] {sc} | {ap} | n={n}")
                sys.stdout.flush()
    print()
    return rows


def export_csv(rows: List[Dict[str, float]], path: str) -> None:
    fieldnames = [
        "scenario",
        "approach",
        "n_nodes",
        "cgw",
        "cgw_radio",
        "cgw_std",
        "pswitch",
        "pswitch_nul",
        "n_ul_mean",
        "pdr",
        "pdr_e2e",
        "ldl_ms",
        "sigma_l",
        "pswitch_border",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in fieldnames})


def print_architecture() -> None:
    ana = analytical_table2()
    print("\n" + "=" * 80)
    print("SDN-GW — Architecture GW-CM (serveur réseau LoRaWAN)")
    print("=" * 80)
    print("1) ED / GW / NS+GW-CM / AS — terminaux et backhaul inchangés")
    print("2) PHY EU868 125 kHz, shadowing σ=7,5 dB, fading ±3 dB, SF7–12, capture GW")
    print(
        f"3) Uplinks : T_emis={T_EMIS_S:.0f} s, NUL≈{NUL} (skip duty {DUTY_SKIP_PROB:.0%})"
    )
    print("4) GW-CM (après déduplication NS) :")
    print("   RSSI-only  : argmax RSSI instantané (sans fenêtre)")
    print(f"   Score-noH  : éq.(1), W={W_WINDOW}, H=0, Tmin=0")
    print(f"   SDN-GW     : éq.(1)+(2), H={H_THRESH}, Tmin={TMIN_S:.0f} s")
    print("5) Downlink Class A : RX1 puis RX2 si échec + backhaul charge-dépendant → LDL")
    print("6) Métriques neutres : CGW, Pswitch=CGW/UL réels, Pswitch_nul (réf. article), CGW_radio")
    print(f"   Shadowing frontière calibré : {BORDER_SHADOW_CALIBRATE}")
    print(
        f"   Analytique D1 : σRSSI={ana['sigma_rssi_1_uplink_db']:.2f} dB, "
        f"σ_eff(W={W_WINDOW})={ana['sigma_rssi_eff_w5_db']:.2f} dB"
    )
    print(f"   Analytique D2 : Pswitch,frontière (RSSI-only) = {ana['pswitch_frontier_rssi_only']:.2f}")
    print("=" * 80 + "\n")


def generate_comparison_plots(rows: List[Dict[str, float]], plots_dir: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")  # sauvegarde sans affichage interactif
        import matplotlib.pyplot as plt
    except Exception:
        print("matplotlib indisponible -> courbes non générées.")
        return

    out_dir = Path(plots_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    labels = {"rssi_only": "RSSI-only", "score_noh": "Score-noH", "sdngw": "SDN-GW"}
    scenarios = sorted({r["scenario"] for r in rows})
    approaches = ["rssi_only", "score_noh", "sdngw"]

    xt = list(range(10, 101, 10))

    for sc in scenarios:
        fig, axes = plt.subplots(2, 2, figsize=(12, 8))
        fig.suptitle(
            f"Métriques vs nombre de nœuds (10, 20, …, 100) — {sc}",
            fontsize=14,
        )

        metric_specs = [
            ("cgw", "CGW (commutations / nœud)"),
            ("pdr", "PDR (%)"),
            ("ldl_ms", "LDL (ms)"),
            ("sigma_l", "sigma_L (écart-type charge)"),
        ]

        for ax, (key, ylabel) in zip(axes.ravel(), metric_specs):
            for ap in approaches:
                sub = [r for r in rows if r["scenario"] == sc and r["approach"] == ap]
                if not sub:
                    continue
                sub_sorted = sorted(sub, key=lambda r: int(r["n_nodes"]))
                x = [int(r["n_nodes"]) for r in sub_sorted]
                y = [float(r[key]) for r in sub_sorted]
                c = APPROACH_COLORS[ap]
                if key == "cgw":
                    yerr = [float(r.get("cgw_std", 0.0)) for r in sub_sorted]
                    ax.errorbar(
                        x,
                        y,
                        yerr=yerr,
                        color=c,
                        ecolor=c,
                        marker="o",
                        markersize=6,
                        markerfacecolor=c,
                        markeredgecolor="black",
                        markeredgewidth=0.6,
                        linewidth=1.5,
                        capsize=3,
                        label=labels[ap],
                    )
                else:
                    ax.plot(
                        x,
                        y,
                        color=c,
                        marker="o",
                        markersize=6,
                        markerfacecolor=c,
                        markeredgecolor="black",
                        markeredgewidth=0.6,
                        linewidth=1.5,
                        label=labels[ap],
                    )

            ax.set_xlabel("Nombre de nœuds")
            ax.set_ylabel(ylabel)
            ax.set_xticks(xt)
            ax.set_xlim(5, 105)
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best", fontsize=9, frameon=True, fancybox=False, edgecolor="#cccccc")

        fig.tight_layout(rect=[0, 0, 1, 0.96])

        fname = out_dir / f"comparison_{sc}.png"
        fig.savefig(fname, dpi=160)
        fname2 = out_dir / f"metrics_vs_nodes_{sc}.png"
        fig.savefig(fname2, dpi=160)
        plt.close(fig)
        print(f"Courbes générées -> {fname}")
        print(f"Alias densité (10…100) -> {fname2}")


def print_summary(rows: List[Dict[str, float]]) -> None:
    labels = {"rssi_only": "RSSI-only", "score_noh": "Score-noH", "sdngw": "SDN-GW"}
    for sc in sorted({r["scenario"] for r in rows}):
        print("\n" + "=" * 72)
        n_den = len({int(r["n_nodes"]) for r in sub0}) if (sub0 := [r for r in rows if r["scenario"] == sc]) else 0
        print(f"Scénario {sc} — moyenne sur {n_den} densites")
        print("=" * 72)
        print(
            f"  {'Approche':12s} {'CGW':>7s} {'CGW_rad':>7s} {'Psw':>7s} "
            f"{'Psw_NUL':>7s} {'PDR%':>7s} {'LDL':>9s} {'sigL':>6s}"
        )
        for ap in ["rssi_only", "score_noh", "sdngw"]:
            sub = [r for r in rows if r["scenario"] == sc and r["approach"] == ap]
            if not sub:
                continue
            print(
                f"  {labels[ap]:12s} "
                f"{np.mean([x['cgw'] for x in sub]):7.3f} "
                f"{np.mean([x.get('cgw_radio', 0) for x in sub]):7.3f} "
                f"{np.mean([x['pswitch'] for x in sub]):7.4f} "
                f"{np.mean([x.get('pswitch_nul', 0) for x in sub]):7.4f} "
                f"{np.mean([x['pdr'] for x in sub]):7.2f} "
                f"{np.mean([x['ldl_ms'] for x in sub]):9.1f} "
                f"{np.mean([x['sigma_l'] for x in sub]):6.3f}"
            )
        rssi_cgw = np.mean([x["cgw"] for x in rows if x["scenario"] == sc and x["approach"] == "rssi_only"])
        sdn_cgw = np.mean([x["cgw"] for x in rows if x["scenario"] == sc and x["approach"] == "sdngw"])
        rssi_rad = np.mean(
            [x.get("cgw_radio", 0) for x in rows if x["scenario"] == sc and x["approach"] == "rssi_only"]
        )
        sdn_rad = np.mean(
            [x.get("cgw_radio", 0) for x in rows if x["scenario"] == sc and x["approach"] == "sdngw"]
        )
        if rssi_cgw > 0:
            red = (rssi_cgw - sdn_cgw) / rssi_cgw * 100.0
            print(f"  → Réduction CGW (total) SDN-GW vs RSSI-only : {red:.1f} %")
        if rssi_rad > 0:
            red_r = (rssi_rad - sdn_rad) / rssi_rad * 100.0
            print(f"  → Réduction CGW_radio (ping-pong) SDN-GW vs RSSI-only : {red_r:.1f} %")
        rssi_ldl = np.mean(
            [x["ldl_ms"] for x in rows if x["scenario"] == sc and x["approach"] == "rssi_only"]
        )
        sdn_ldl = np.mean([x["ldl_ms"] for x in rows if x["scenario"] == sc and x["approach"] == "sdngw"])
        if rssi_ldl > 0:
            red_l = (rssi_ldl - sdn_ldl) / rssi_ldl * 100.0
            print(f"  → Réduction LDL SDN-GW vs RSSI-only : {red_l:.1f} %")
        if sc == "S1_fixed":
            pb = np.mean(
                [
                    r.get("pswitch_border", 0.0)
                    for r in rows
                    if r["scenario"] == sc and r["approach"] == "rssi_only"
                ]
            )
            print(f"  → Pswitch frontière (RSSI-only) : {pb:.2f}  (réf. analytique D2 : 0,50)")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print_architecture()

    parser = argparse.ArgumentParser(description="Évaluation SDN-GW (article, Tableau 2)")
    parser.add_argument("--seeds", type=int, default=N_SEEDS_DEFAULT, help="Nombre de seeds par agrégat")
    parser.add_argument(
        "--scenarios",
        type=str,
        default="S1,S2",
        help="Liste : S1, S2 ou S1,S2",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="sdn_gw_eval_results.csv",
        help="Fichier CSV de sortie",
    )
    parser.add_argument(
        "--densities",
        type=str,
        default="",
        help="Optionnel : ex. 10,50,100 (sinon 10..100 pas 10)",
    )
    parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Désactive la génération automatique des courbes PNG",
    )
    parser.add_argument(
        "--plots-dir",
        type=str,
        default="plots",
        help="Dossier de sortie des courbes PNG (défaut: plots)",
    )
    args = parser.parse_args()

    scen_map = {"S1": "S1_fixed", "S2": "S2_rwp"}
    scenarios: List[Scenario] = []
    for token in args.scenarios.split(","):
        token = token.strip().upper()
        if token in ("S1", "S1_FIXED"):
            scenarios.append("S1_fixed")
        elif token in ("S2", "S2_RWP"):
            scenarios.append("S2_rwp")
        elif token:
            scenarios.append(scen_map.get(token, token))  # type: ignore[arg-type]

    if not scenarios:
        scenarios = ["S1_fixed", "S2_rwp"]

    node_counts = NODE_COUNTS
    if args.densities.strip():
        node_counts = [int(x.strip()) for x in args.densities.split(",") if x.strip()]

    approaches: List[Approach] = ["rssi_only", "score_noh", "sdngw"]

    print("SDN-GW — environnement d'évaluation")
    print(f"  Zone {AREA_M/1000:.0f}×{AREA_M/1000:.0f} km | {N_GW} GW | R={R_GW_M/1000:.0f} km")
    print(
        f"  Durée {SIM_DUR_S:.0f} s | T_emis={T_EMIS_S:.0f} s | NUL={NUL} | "
        f"W={W_WINDOW} | H={H_THRESH} | Tmin={TMIN_S:.0f} s"
    )
    ana = analytical_table2()
    print(
        f"  Réf. analytique : σRSSI,eff={ana['sigma_rssi_eff_w5_db']:.2f} dB | "
        f"Pswitch,frontière={ana['pswitch_frontier_rssi_only']:.2f}"
    )
    print(f"  Scénarios : {scenarios}")
    print(f"  Densités : {node_counts}")
    print(f"  Seeds : {args.seeds}")

    rows = run_study(scenarios, approaches, node_counts, args.seeds)
    out_path = args.output
    export_csv(rows, out_path)
    print_summary(rows)
    print(f"\nRésultats exportés : {out_path}")
    if not args.no_plots:
        generate_comparison_plots(rows, args.plots_dir)


if __name__ == "__main__":
    main()
