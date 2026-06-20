"""
Interface Streamlit — architecture dynamique et évolution des métriques (SDN-GW).

Lancement :
  cd "…/Simulation SDN_GW"
  streamlit run app_simulation.py
"""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any, Dict, List, Mapping, Optional

import numpy as np
import streamlit as st

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    _HAS_PLOTLY = True
except Exception:
    _HAS_PLOTLY = False

from eval_sdn_gw import (
    APPROACH_COLORS,
    AREA_M,
    N_GW,
    NODE_COUNTS,
    R_GW_M,
    SIM_DUR_S,
    T_EMIS_S,
    Approach,
    Scenario,
    aggregate_runs,
    run_single_traced,
)

APPROACH_LABELS: Dict[Approach, str] = {
    "rssi_only": "RSSI-only",
    "score_noh": "Score-noH",
    "sdngw": "SDN-GW",
}
ALL_APPROACHES: List[Approach] = ["rssi_only", "score_noh", "sdngw"]


def _draw_architecture(
    result: Dict[str, Any],
    frame_idx: int,
    max_links: int = 48,
) -> Any:
    import matplotlib.pyplot as plt

    gw_pos = result["gw_positions"]
    xs = result["trace_xs"][frame_idx]
    ys = result["trace_ys"][frame_idx]
    gws = result["trace_gw"][frame_idx]
    n = len(xs)

    fig, ax = plt.subplots(figsize=(8.5, 8))
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(0, AREA_M)
    ax.set_ylim(0, AREA_M)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.grid(True, alpha=0.25)

    gw_colors = ["#e41a1c", "#4daf4a", "#377eb8"]
    for i, (gx, gy) in enumerate(gw_pos):
        ax.scatter(
            [gx],
            [gy],
            s=420,
            marker="^",
            c=gw_colors[i % len(gw_colors)],
            edgecolors="black",
            linewidths=1.0,
            zorder=5,
            label=f"GW{i}",
        )
        circ = plt.Circle((gx, gy), R_GW_M, color=gw_colors[i % len(gw_colors)], alpha=0.06, zorder=1)
        ax.add_patch(circ)

    cx = float(np.mean([p[0] for p in gw_pos]))
    cy = float(np.mean([p[1] for p in gw_pos]))
    ns_x, ns_y = cx, min(cy + 1_200.0, AREA_M - 400.0)
    ax.scatter([ns_x], [ns_y], s=900, marker="s", c="#984ea3", edgecolors="black", linewidths=1.0, zorder=6)
    ax.annotate("NS + GW-CM", (ns_x, ns_y), textcoords="offset points", xytext=(0, 18), ha="center", fontsize=10)
    for i, (gx, gy) in enumerate(gw_pos):
        ax.plot([ns_x, gx], [ns_y, gy], "k--", alpha=0.35, linewidth=1.0, zorder=2)

    node_colors = []
    for j in range(n):
        g = int(gws[j])
        if g < 0:
            node_colors.append("#bdbdbd")
        else:
            node_colors.append(gw_colors[g % N_GW])
    ax.scatter(xs, ys, s=22, c=node_colors, edgecolors="black", linewidths=0.2, zorder=4)

    rng = np.random.default_rng(int(result.get("seed", 0)) + frame_idx)
    if n > 0:
        k = min(max_links, n)
        idx = rng.choice(n, size=k, replace=False)
        for j in idx:
            g = int(gws[j])
            if g < 0:
                continue
            gx, gy = gw_pos[g]
            ax.plot([xs[j], gx], [ys[j], gy], color=node_colors[j], alpha=0.22, linewidth=0.8, zorder=3)

    loads = result["trace_loads"][frame_idx]
    load_txt = " | ".join(f"L{i}={int(loads[i])}" for i in range(min(len(loads), N_GW)))
    t = float(result["trace_times"][frame_idx])
    ax.set_title(f"Topologie — t = {t:.0f} s  ({load_txt})", fontsize=11)

    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return fig


def _draw_metrics(result: Dict[str, Any]) -> Any:
    import matplotlib.pyplot as plt

    tt = result["trace_times"]
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle("Évolution des métriques (agrégées)", fontsize=12)

    axes[0, 0].plot(tt, result["trace_cgw"], color="#1b9e77", linewidth=1.8)
    axes[0, 0].set_ylabel("CGW (comm./nœud)")
    axes[0, 0].grid(True, alpha=0.3)

    axes[0, 1].plot(tt, result["trace_pdr"], color="#d95f02", linewidth=1.8)
    axes[0, 1].set_ylabel("PDR (%)")
    axes[0, 1].grid(True, alpha=0.3)

    axes[1, 0].plot(tt, result["trace_ldl"], color="#7570b3", linewidth=1.8)
    axes[1, 0].set_ylabel("LDL (ms)")
    axes[1, 0].set_xlabel("Temps simulé (s)")
    axes[1, 0].grid(True, alpha=0.3)

    axes[1, 1].plot(tt, result["trace_sigma"], color="#e7298a", linewidth=1.8)
    axes[1, 1].set_ylabel("sigma_L")
    axes[1, 1].set_xlabel("Temps simulé (s)")
    axes[1, 1].grid(True, alpha=0.3)

    for ax in axes.ravel():
        ax.set_xlim(left=0.0)

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return fig


def _draw_metrics_compare(by_approach: Mapping[Approach, Dict[str, Any]]) -> Any:
    import matplotlib.pyplot as plt

    colors = {"rssi_only": "#1f78b4", "score_noh": "#33a02c", "sdngw": "#e31a1c"}
    fig, axes = plt.subplots(2, 2, figsize=(11, 7))
    fig.suptitle("Comparaison des 3 approches (mêmes paramètres)", fontsize=12)

    specs = [
        ("trace_cgw", "CGW (comm./nœud)"),
        ("trace_pdr", "PDR (%)"),
        ("trace_ldl", "LDL (ms)"),
        ("trace_sigma", "sigma_L"),
    ]
    for ax, (key, ylabel) in zip(axes.ravel(), specs):
        for ap in ALL_APPROACHES:
            r = by_approach.get(ap)
            if r is None:
                continue
            tt = r["trace_times"]
            ax.plot(tt, r[key], color=colors[ap], linewidth=1.6, label=APPROACH_LABELS[ap])
        ax.set_ylabel(ylabel)
        ax.set_xlabel("Temps simulé (s)")
        ax.grid(True, alpha=0.3)
        ax.set_xlim(left=0.0)

    axes[0, 0].legend(loc="best", fontsize=8)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return fig


def _traces_to_csv(by_approach: Mapping[Approach, Dict[str, Any]]) -> bytes:
    lines: List[str] = [
        "approach,t_s,cgw,pdr_pct,ldl_ms,sigma_L,load_gw0,load_gw1,load_gw2",
    ]
    for ap, r in by_approach.items():
        tt = r["trace_times"]
        loads = r["trace_loads"]
        for i in range(len(tt)):
            row = loads[i]
            l0 = int(row[0]) if len(row) > 0 else 0
            l1 = int(row[1]) if len(row) > 1 else 0
            l2 = int(row[2]) if len(row) > 2 else 0
            lines.append(
                f"{ap},{float(tt[i]):.1f},"
                f"{float(r['trace_cgw'][i]):.6f},"
                f"{float(r['trace_pdr'][i]):.6f},"
                f"{float(r['trace_ldl'][i]):.6f},"
                f"{float(r['trace_sigma'][i]):.6f},"
                f"{l0},{l1},{l2}"
            )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _fig_to_png_bytes(fig: Any) -> bytes:
    import matplotlib.pyplot as plt

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _plotly_density_vs_nodes(
    rows: List[Mapping[str, Any]],
    scenario: Scenario,
) -> Optional[Any]:
    if not _HAS_PLOTLY:
        return None

    titles = ("CGW (comm./nœud/h)", "PDR (%)", "LDL (ms)", "sigma_L")
    keys = ("cgw", "pdr", "ldl_ms", "sigma_l")

    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=titles,
        vertical_spacing=0.14,
        horizontal_spacing=0.08,
    )
    xt = list(range(10, 101, 10))

    for idx, key in enumerate(keys):
        row_i = idx // 2 + 1
        col_i = idx % 2 + 1
        for ap in ALL_APPROACHES:
            sub = [r for r in rows if r["scenario"] == scenario and r["approach"] == ap]
            if not sub:
                continue
            sub_sorted = sorted(sub, key=lambda r: int(r["n_nodes"]))
            x = [int(r["n_nodes"]) for r in sub_sorted]
            y = [float(r[key]) for r in sub_sorted]
            col = APPROACH_COLORS[ap]
            marker_kw = dict(size=8, color=col, line=dict(width=1, color="black"))
            common = dict(
                mode="lines+markers",
                name=APPROACH_LABELS[ap],
                line=dict(color=col, width=2),
                marker=marker_kw,
                legendgroup=ap,
                showlegend=(idx == 0),
            )
            if key == "cgw":
                err = [float(r.get("cgw_std", 0.0)) for r in sub_sorted]
                fig.add_trace(
                    go.Scatter(
                        x=x,
                        y=y,
                        error_y=dict(type="data", array=err, visible=True, color=col, thickness=1.2, width=4),
                        **common,
                    ),
                    row=row_i,
                    col=col_i,
                )
            else:
                fig.add_trace(go.Scatter(x=x, y=y, **common), row=row_i, col=col_i)

        fig.update_xaxes(title_text="Nombre de nœuds", tickmode="array", tickvals=xt, row=row_i, col=col_i)

    sc_label = "S1 (fixes)" if scenario == "S1_fixed" else "S2 (RWP)"
    fig.update_layout(
        height=560,
        margin=dict(l=40, r=30, t=56, b=40),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
            bgcolor="white",
            bordercolor="#cccccc",
            borderwidth=1,
            font=dict(size=11),
        ),
        title=dict(
            text=f"Métriques vs nombre de nœuds (10…100) — {sc_label}",
            x=0.5,
            xanchor="center",
        ),
        showlegend=True,
    )
    return fig


def _matplotlib_density_vs_nodes(
    rows: List[Mapping[str, Any]],
    scenario: Scenario,
) -> Any:
    import matplotlib.pyplot as plt

    xt = list(range(10, 101, 10))
    metric_specs = [
        ("cgw", "CGW (commutations / nœud)"),
        ("pdr", "PDR (%)"),
        ("ldl_ms", "LDL (ms)"),
        ("sigma_l", "sigma_L (écart-type charge)"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    sc_label = "S1 (fixes)" if scenario == "S1_fixed" else "S2 (RWP)"
    fig.suptitle(f"Métriques vs nombre de nœuds (10, 20, …, 100) — {sc_label}", fontsize=13)

    for ax, (key, ylabel) in zip(axes.ravel(), metric_specs):
        for ap in ALL_APPROACHES:
            sub = [r for r in rows if r["scenario"] == scenario and r["approach"] == ap]
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
                    label=APPROACH_LABELS[ap],
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
                    label=APPROACH_LABELS[ap],
                )
        ax.set_xlabel("Nombre de nœuds")
        ax.set_ylabel(ylabel)
        ax.set_xticks(xt)
        ax.set_xlim(5, 105)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=9, frameon=True, fancybox=False, edgecolor="#cccccc")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return fig


def _density_rows_to_csv(rows: List[Mapping[str, Any]], scenario: Scenario) -> bytes:
    lines = [
        "scenario,approach,n_nodes,cgw,cgw_radio,cgw_std,pswitch,pswitch_nul,"
        "n_ul_mean,pdr,pdr_e2e,ldl_ms,sigma_l,pswitch_border"
    ]
    for r in sorted(rows, key=lambda x: (str(x["approach"]), int(x["n_nodes"]))):
        if r["scenario"] != scenario:
            continue
        lines.append(
            f'{r["scenario"]},{r["approach"]},{int(r["n_nodes"])},'
            f'{r["cgw"]:.6f},{r.get("cgw_radio", 0):.6f},{r["cgw_std"]:.6f},'
            f'{r.get("pswitch", 0):.6f},{r.get("pswitch_nul", 0):.6f},'
            f'{r.get("n_ul_mean", 0):.6f},{r["pdr"]:.6f},{r.get("pdr_e2e", 0):.6f},'
            f'{r["ldl_ms"]:.6f},{r["sigma_l"]:.6f},{r.get("pswitch_border", 0):.6f}'
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


def _plotly_metrics_panel(
    by_approach: Mapping[Approach, Dict[str, Any]],
    *,
    compare: bool,
) -> Optional[Any]:
    if not _HAS_PLOTLY:
        return None

    colors = {"rssi_only": "#1f78b4", "score_noh": "#33a02c", "sdngw": "#e31a1c"}
    titles = ("CGW (comm./nœud/h)", "PDR (%)", "LDL (ms)", "sigma_L")
    keys = ("trace_cgw", "trace_pdr", "trace_ldl", "trace_sigma")

    fig = make_subplots(
        rows=2,
        cols=2,
        subplot_titles=titles,
        vertical_spacing=0.12,
        horizontal_spacing=0.08,
    )

    approaches_order = ALL_APPROACHES if compare else list(by_approach.keys())
    for idx, key in enumerate(keys):
        row = idx // 2 + 1
        col = idx % 2 + 1
        for _, ap in enumerate(approaches_order):
            r = by_approach.get(ap)
            if r is None:
                continue
            tt = r["trace_times"]
            yy = r[key]
            fig.add_trace(
                go.Scatter(
                    x=tt,
                    y=yy,
                    mode="lines",
                    name=APPROACH_LABELS[ap],
                    line=dict(color=colors.get(ap, "#333333"), width=2),
                    legendgroup=ap,
                    showlegend=(idx == 0),
                ),
                row=row,
                col=col,
            )

    fig.update_xaxes(title_text="Temps simulé (s)")
    show_leg = len(approaches_order) > 1
    fig.update_layout(
        height=520,
        margin=dict(l=40, r=30, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        title="Évolution des métriques (interactif)",
        showlegend=show_leg,
    )
    return fig


def _synth_sdngw_vs_rssi(by_approach: Mapping[Approach, Dict[str, Any]]) -> Optional[Dict[str, float]]:
    if "rssi_only" not in by_approach or "sdngw" not in by_approach:
        return None
    br = by_approach["rssi_only"]["final"]
    bs = by_approach["sdngw"]["final"]

    def pct_improve(baseline: float, newv: float, *, lower_is_better: bool) -> float:
        if baseline == 0:
            return 0.0
        if lower_is_better:
            return float((baseline - newv) / abs(baseline) * 100.0)
        return float((newv - baseline) / abs(baseline) * 100.0)

    return {
        "cgw_pct": pct_improve(br["cgw"], bs["cgw"], lower_is_better=True),
        "sigma_pct": pct_improve(br["sigma_l"], bs["sigma_l"], lower_is_better=True),
        "ldl_pct": pct_improve(br["ldl_ms"], bs["ldl_ms"], lower_is_better=True),
        "pdr_pct": pct_improve(br["pdr"], bs["pdr"], lower_is_better=False),
    }


def main() -> None:
    import matplotlib.pyplot as plt

    st.set_page_config(page_title="SDN-GW — Simulation", layout="wide")
    st.title("SDN-GW — Architecture dynamique et métriques")
    st.info(
        "**Accès à l’interface** — Si vous ne voyez pas cette page après `lancer.bat`, ouvrez manuellement "
        "dans Chrome ou Edge : [http://127.0.0.1:8501](http://127.0.0.1:8501) "
        "— Puis cliquez **Lancer / recalculer** dans la barre latérale pour afficher la simulation."
    )

    with st.sidebar:
        st.header("Paramètres")
        scenario_label = st.selectbox("Scénario", ["S1 (nœuds fixes)", "S2 (mobilité RWP)"])
        scenario: Scenario = "S1_fixed" if scenario_label.startswith("S1") else "S2_rwp"

        compare_three = st.checkbox(
            "Comparer les 3 approches",
            value=False,
            help="Lance RSSI-only, Score-noH et SDN-GW avec les mêmes nœuds (seed) et superpose les courbes.",
        )

        approach_label = st.selectbox(
            "Topologie / détail (approche affichée)",
            ["RSSI-only", "Score-noH", "SDN-GW"],
        )
        approach_map = {"RSSI-only": "rssi_only", "Score-noH": "score_noh", "SDN-GW": "sdngw"}
        approach: Approach = approach_map[approach_label]  # type: ignore[assignment]

        n_nodes = st.slider("Nombre de nœuds", min_value=10, max_value=100, value=40, step=5)
        seed = st.number_input("Seed", min_value=0, max_value=99999, value=42, step=1)
        duration = st.slider(
            "Durée simulée (s)",
            min_value=int(T_EMIS_S),
            max_value=int(SIM_DUR_S),
            value=min(1_800, int(SIM_DUR_S)),
            step=int(T_EMIS_S),
        )
        trace_iv = st.selectbox("Pas d'échantillonnage (s)", [60, 120, 300, 600], index=1)

        use_plotly = st.checkbox(
            "Graphiques interactifs (Plotly)",
            value=_HAS_PLOTLY,
            disabled=not _HAS_PLOTLY,
            help="Zoom / survol sur les courbes de métriques. `pip install plotly` si désactivé.",
        )

        run = st.button("Lancer / recalculer", type="primary")

    st.markdown(
        """
**Architecture affichée** : Network Server + module GW-CM (carré violet), passerelles (triangles),
nœuds (couleur = passerelle downlink assignée), disques de couverture (légers), liaisons
échantillonnées nœud→GW. Les courbes montrent l’évolution **cumulée** des métriques pendant la simulation.
        """
    )

    with st.expander(
        "Courbes de comparaison des métriques vs nombre de nœuds (10, 20, …, 100)",
        expanded=False,
    ):
        st.caption(
            f"Pour chaque densité dans **{NODE_COUNTS[0]}…{NODE_COUNTS[-1]}** (pas 10), moyenne sur plusieurs "
            "seeds — même modèle que `eval_sdn_gw.py` (sans trace temporelle)."
        )
        dens_seeds = st.number_input(
            "Nombre de seeds (moyenne)",
            min_value=1,
            max_value=30,
            value=5,
            step=1,
            key="dens_seeds",
        )
        if st.button("Calculer les courbes vs densité", key="dens_go"):
            seeds_list = list(range(int(dens_seeds)))
            total_steps = len(NODE_COUNTS) * len(ALL_APPROACHES)
            dens_rows: List[Dict[str, Any]] = []
            prog_d = st.progress(0, text="Calcul agrégés…")
            step = 0
            for n in NODE_COUNTS:
                for ap in ALL_APPROACHES:
                    dens_rows.append(aggregate_runs(scenario, ap, int(n), seeds_list))
                    step += 1
                    prog_d.progress(
                        step / total_steps,
                        text=f"n={n} — {APPROACH_LABELS[ap]} ({step}/{total_steps})",
                    )
            prog_d.empty()
            st.session_state["density_rows"] = dens_rows
            st.session_state["density_scenario"] = scenario

        dens_rows_st = st.session_state.get("density_rows")
        dens_sc = st.session_state.get("density_scenario", scenario)
        if dens_rows_st:
            if dens_sc != scenario:
                st.caption(
                    f"Résultats affichés pour le scénario **{dens_sc}**. "
                    "Relancez le calcul si vous avez changé le scénario dans la barre latérale."
                )
            plotly_d = bool(use_plotly) and _HAS_PLOTLY
            fig_d_pl = _plotly_density_vs_nodes(dens_rows_st, dens_sc)
            if plotly_d and fig_d_pl is not None:
                st.plotly_chart(fig_d_pl, use_container_width=True)
            else:
                fig_d = _matplotlib_density_vs_nodes(dens_rows_st, dens_sc)
                st.pyplot(fig_d, clear_figure=True)
                plt.close(fig_d)

            dc1, dc2 = st.columns(2)
            with dc1:
                st.download_button(
                    label="Télécharger CSV (agrégats par (n, approche))",
                    data=_density_rows_to_csv(dens_rows_st, dens_sc),
                    file_name=f"sdn_gw_metrics_vs_nodes_{dens_sc}.csv",
                    mime="text/csv",
                    key="dens_dl_csv",
                )
            with dc2:
                fig_png = _matplotlib_density_vs_nodes(dens_rows_st, dens_sc)
                png_b = _fig_to_png_bytes(fig_png)
                st.download_button(
                    label="Télécharger PNG (comparaison 10…100)",
                    data=png_b,
                    file_name=f"sdn_gw_metrics_vs_nodes_{dens_sc}.png",
                    mime="image/png",
                    key="dens_dl_png",
                )

    if run:
        with st.spinner("Simulation en cours…"):
            if compare_three:
                by_ap: Dict[Approach, Dict[str, Any]] = {}
                prog = st.progress(0, text="Préparation…")
                for i, ap in enumerate(ALL_APPROACHES):
                    by_ap[ap] = run_single_traced(
                        scenario,
                        ap,
                        int(n_nodes),
                        int(seed),
                        trace_interval_s=float(trace_iv),
                        sim_duration_s=float(duration),
                    )
                    prog.progress(
                        (i + 1) / len(ALL_APPROACHES),
                        text=f"{APPROACH_LABELS[ap]} — terminé ({i + 1}/{len(ALL_APPROACHES)})",
                    )
                prog.empty()
                st.session_state["sim_by_approach"] = by_ap
                st.session_state["sim_compare"] = True
            else:
                st.session_state["sim_by_approach"] = {
                    approach: run_single_traced(
                        scenario,
                        approach,
                        int(n_nodes),
                        int(seed),
                        trace_interval_s=float(trace_iv),
                        sim_duration_s=float(duration),
                    )
                }
                st.session_state["sim_compare"] = False

    if "sim_by_approach" not in st.session_state:
        st.info("Configurez les paramètres puis cliquez sur **Lancer / recalculer**.")
        return

    by_approach = st.session_state["sim_by_approach"]
    if not st.session_state.get("sim_compare") and len(by_approach) == 1:
        only_ap = next(iter(by_approach))
        if approach != only_ap:
            st.info(
                f"Le dernier calcul est pour **{APPROACH_LABELS[only_ap]}** uniquement. "
                "Relancez après avoir choisi une autre approche dans la barre latérale."
            )
        approach = only_ap
    result: Dict[str, Any] = by_approach[approach]
    times: np.ndarray = result["trace_times"]
    n_frames = len(times)
    do_compare_chart = bool(st.session_state.get("sim_compare")) and len(by_approach) >= 3

    st.download_button(
        label="Télécharger les traces (CSV)",
        data=_traces_to_csv(by_approach),
        file_name="sdn_gw_traces.csv",
        mime="text/csv",
        help="Une ligne par instant et par approche : t, CGW, PDR, LDL, sigma_L, charges GW.",
    )

    c1, c2 = st.columns((1.05, 1.0))
    frame = max(0, n_frames - 1)
    with c1:
        st.subheader("Instantané topologique")
        play = st.checkbox(
            "Lecture auto. (topologie)",
            value=False,
            help="Nécessite Streamlit ≥ 1.33 (fragment). Sinon utilisez le curseur ci-dessous.",
        )
        frag = getattr(st, "fragment", None)
        if not play:
            frame = st.slider(
                "Instant (échantillon)",
                min_value=0,
                max_value=max(0, n_frames - 1),
                value=max(0, n_frames - 1),
                step=1,
                help="Chaque pas correspond à un instant enregistré (pas d'échantillonnage).",
            )
            t_sel = float(times[frame])
            st.caption(f"Temps simulé affiché : **{t_sel:.0f} s** — approche : **{APPROACH_LABELS[approach]}**")
            fig_map = _draw_architecture(result, int(frame))
            st.pyplot(fig_map, clear_figure=True)
            plt.close(fig_map)
        elif play and frag is not None and n_frames > 1:

            @frag(run_every=timedelta(milliseconds=450))
            def _topology_anim() -> None:
                i0 = int(st.session_state.get("_topo_anim_i", 0)) % n_frames
                st.session_state["_topo_anim_i"] = (i0 + 1) % n_frames
                t_a = float(result["trace_times"][st.session_state["_topo_anim_i"]])
                st.caption(
                    f"Lecture auto — t = {t_a:.0f} s — **{APPROACH_LABELS[approach]}** "
                    "(décochez pour repasser au curseur manuel)"
                )
                f_a = _draw_architecture(result, int(st.session_state["_topo_anim_i"]))
                st.pyplot(f_a, clear_figure=True)
                plt.close(f_a)

            _topology_anim()
        else:
            st.caption("Mettez à jour Streamlit (`pip install -U streamlit`) pour activer la lecture automatique.")
            frame = st.slider(
                "Instant (échantillon)",
                min_value=0,
                max_value=max(0, n_frames - 1),
                value=max(0, n_frames - 1),
                step=1,
            )
            fig_map = _draw_architecture(result, int(frame))
            st.pyplot(fig_map, clear_figure=True)
            plt.close(fig_map)

    with c2:
        st.subheader("Métriques finales (sur la durée)")
        if st.session_state.get("sim_compare"):
            rows = []
            for ap in ALL_APPROACHES:
                f = by_approach[ap]["final"]
                rows.append(
                    {
                        "Approche": APPROACH_LABELS[ap],
                        "CGW": f["cgw"],
                        "PDR %": f["pdr"],
                        "LDL ms": f["ldl_ms"],
                        "sigma_L": f["sigma_l"],
                        "Switch total": f["switches_total"],
                    }
                )
            st.dataframe(rows, use_container_width=True, hide_index=True)
        else:
            fin = result["final"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("CGW (moy.)", f"{fin['cgw']:.3f}")
            m2.metric("PDR (moy.)", f"{fin['pdr']:.2f} %")
            m3.metric("LDL (moy.)", f"{fin['ldl_ms']:.1f} ms")
            m4.metric("sigma_L", f"{fin['sigma_l']:.3f}")

        st.subheader("Évolution dans le temps")
        plotly_on = bool(use_plotly) and _HAS_PLOTLY
        fig_pl = _plotly_metrics_panel(
            by_approach,
            compare=do_compare_chart,
        )
        if plotly_on and fig_pl is not None:
            st.plotly_chart(fig_pl, use_container_width=True)
        else:
            if do_compare_chart:
                fig_met = _draw_metrics_compare(by_approach)
            else:
                fig_met = _draw_metrics(result)
            st.pyplot(fig_met, clear_figure=True)
            plt.close(fig_met)

        loads = result["trace_loads"]
        st.subheader("Charge par passerelle (instant sélectionné)")
        fi = int(frame)
        if play and frag is not None:
            fi = int(st.session_state.get("_topo_anim_i", fi))
        if loads is not None and len(loads) > fi:
            row = loads[fi]
            cols = st.columns(N_GW)
            for i in range(min(N_GW, len(row))):
                cols[i].metric(f"GW{i} (nœuds assignés)", f"{int(row[i])}")

    viz_fi = int(frame)
    if play and frag is not None:
        viz_fi = int(st.session_state.get("_topo_anim_i", viz_fi))

    synth = _synth_sdngw_vs_rssi(by_approach) if st.session_state.get("sim_compare") else None
    if synth is not None:
        with st.expander("Synthèse : SDN-GW vs RSSI-only (métriques finales)", expanded=False):
            st.markdown(
                f"| Indicateur | Variation (SDN-GW vs RSSI-only) |\n"
                f"|---|---:|\n"
                f"| **CGW** (baisse souhaitée) | **{synth['cgw_pct']:+.1f} %** |\n"
                f"| **sigma_L** (baisse souhaitée) | **{synth['sigma_pct']:+.1f} %** |\n"
                f"| **LDL** (baisse souhaitée) | **{synth['ldl_pct']:+.1f} %** |\n"
                f"| **PDR** (hausse souhaitée) | **{synth['pdr_pct']:+.1f} %** |\n"
            )
            st.caption(
                "Pourcentages : (baseline − SDN-GW) / baseline × 100 pour CGW, sigma_L et LDL ; "
                "(SDN-GW − baseline) / baseline × 100 pour la PDR."
            )

    st.divider()
    st.subheader("Exports graphiques (instant courant)")
    e1, e2 = st.columns(2)
    fig_topo_export = _draw_architecture(result, viz_fi)
    if do_compare_chart:
        fig_met_export = _draw_metrics_compare(by_approach)
    else:
        fig_met_export = _draw_metrics(result)
    with e1:
        st.download_button(
            label="Télécharger topologie (PNG)",
            data=_fig_to_png_bytes(fig_topo_export),
            file_name=f"sdn_gw_topo_t{int(result['trace_times'][viz_fi])}_{approach}.png",
            mime="image/png",
        )
    with e2:
        st.download_button(
            label="Télécharger courbes métriques (PNG)",
            data=_fig_to_png_bytes(fig_met_export),
            file_name="sdn_gw_metrics.png",
            mime="image/png",
        )


if __name__ == "__main__":
    main()
