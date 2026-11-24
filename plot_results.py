#!/usr/bin/env python3
"""
Módulo de Visualização de Dados e Pós-Processamento (`plot_results.py`).

Responsável por ler os logs gerados pelo `bwm-ng` (séries temporais de vazão em CSV)
e as medições do `iperf` (Flow Completion Time - FCT de cada fluxo all-to-all),
gerando gráficos de linha (Throughput vs. Tempo), gráficos de barras (FCT Médio,
P95 e FCT Total Agregado) e curvas CDF (Cumulative Distribution Function).
"""

import argparse
import csv
import glob
import os
import re
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


SWITCH_IFACE_REGEX = re.compile(r"^s\d+-eth\d+$")


def parse_bwm_csv(csv_path):
    """
    Lê um arquivo CSV gerado pelo `bwm-ng -o csv -u bytes -T rate`
    e retorna dois vetores numpy: (tempos_relativos_s, vazao_agregada_mbps).

    Formato padrão de linha do bwm-ng CSV (separado por ';'):
      0: unix_timestamp
      1: iface_name
      2: bytes_out/s
      3: bytes_in/s
      4: bytes_total/s
    """
    if not os.path.isfile(csv_path):
        return np.array([]), np.array([])

    switch_bytes_per_ts = defaultdict(float)
    total_bytes_per_ts = defaultdict(float)

    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("timestamp"):
                continue
            parts = line.split(";")
            if len(parts) < 5:
                continue
            try:
                ts = round(float(parts[0]), 2)
                iface = parts[1].strip()
                bytes_out_s = float(parts[2])
                bytes_in_s = float(parts[3])
                bytes_total_s = float(parts[4])
            except ValueError:
                continue

            # Soma tráfego transmitido nas interfaces dos switches Open vSwitch (sX-ethY)
            if SWITCH_IFACE_REGEX.match(iface):
                # Usamos bytes_out_s (ou metade de bytes_total_s) para evitar dupla contagem em links internos
                val = bytes_out_s if bytes_out_s > 0 else (bytes_total_s * 0.5)
                switch_bytes_per_ts[ts] += val
            elif iface == "total":
                total_bytes_per_ts[ts] = bytes_total_s

    data_source = switch_bytes_per_ts if switch_bytes_per_ts else total_bytes_per_ts
    if not data_source:
        return np.array([]), np.array([])

    sorted_ts = sorted(data_source.keys())
    t0 = sorted_ts[0]
    times = np.array([t - t0 for t in sorted_ts], dtype=float)
    # Converte Bytes/s para Megabits/s (Mbps)
    mbps = np.array([(data_source[t] * 8.0) / 1e6 for t in sorted_ts], dtype=float)
    return times, mbps


def parse_fct_csv(fct_csv_path):
    """
    Lê o arquivo CSV de FCT dos fluxos iperf all-to-all.
    Retorna uma lista de dicionários e metadados agregados.
    """
    flows = []
    meta = {
        "total_fct_s": 0.0,
        "failure_time_s": None,
        "failed_link": None,
        "recovery_time_s": None,
    }
    if not os.path.isfile(fct_csv_path):
        return flows, meta

    with open(fct_csv_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                # Extrai metadados cabeçalho (# total_fct_s=..., # failed_link=..., etc.)
                for token in line.lstrip("#").strip().split(";"):
                    if "=" in token:
                        k, v = token.strip().split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k == "total_fct_s":
                            try:
                                meta["total_fct_s"] = float(v)
                            except ValueError:
                                pass
                        elif k == "failure_time_s" and v not in ("None", ""):
                            try:
                                meta["failure_time_s"] = float(v)
                            except ValueError:
                                pass
                        elif k == "recovery_time_s" and v not in ("None", ""):
                            try:
                                meta["recovery_time_s"] = float(v)
                            except ValueError:
                                pass
                        elif k == "failed_link" and v not in ("None", ""):
                            meta["failed_link"] = v
                continue

        f.seek(0)
        filtered_lines = [ln for ln in f if not ln.startswith("#") and ln.strip()]
        reader = csv.DictReader(filtered_lines)
        for row in reader:
            try:
                flows.append({
                    "flow_id": row.get("flow_id", ""),
                    "src_host": row.get("src_host", ""),
                    "dst_host": row.get("dst_host", ""),
                    "src_ip": row.get("src_ip", ""),
                    "dst_ip": row.get("dst_ip", ""),
                    "bytes": int(float(row.get("bytes_transferred", 0))),
                    "fct_s": float(row.get("fct_seconds", 0.0)),
                    "mbps": float(row.get("throughput_mbps", 0.0)),
                })
            except (ValueError, TypeError):
                continue

    if flows and meta["total_fct_s"] <= 0.0:
        meta["total_fct_s"] = max(fl["fct_s"] for fl in flows)

    return flows, meta


def plot_single_experiment(
    topology,
    routing,
    exp,
    bwm_csv_path,
    fct_csv_path,
    output_dir="results",
    failure_time_s=None,
    failed_link=None,
):
    """
    Gera os gráficos individuais (Linha de Vazão e Barras/CDF de FCT) para uma execução.
    """
    os.makedirs(output_dir, exist_ok=True)
    times, mbps = parse_bwm_csv(bwm_csv_path)
    flows, meta = parse_fct_csv(fct_csv_path)

    if failure_time_s is None:
        failure_time_s = meta.get("failure_time_s")
    if failed_link is None:
        failed_link = meta.get("failed_link")
    recovery_time_s = meta.get("recovery_time_s")

    plt.rcParams["font.sans-serif"] = "DejaVu Sans"
    plt.rcParams["axes.edgecolor"] = "#CBD5E1"
    plt.rcParams["axes.linewidth"] = 0.9

    # -------------------------------------------------------------------------
    # 1. Gráfico de Linha: Vazão Agregada vs. Tempo (bwm-ng)
    # -------------------------------------------------------------------------
    throughput_png = os.path.join(
        output_dir, f"plot_throughput_{topology}_{routing}_exp{exp}.png"
    )
    fig, ax = plt.subplots(figsize=(9.2, 4.6), dpi=200)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#F8FAFC")

    if len(times) > 0:
        color_main = "#1A56DB" if routing == "ecmp" else "#047857"
        ax.plot(
            times,
            mbps,
            color=color_main,
            linewidth=2.2,
            label=f"Vazão Agregada nos Switches ({routing.upper()} - Exp {exp})",
        )
        ax.fill_between(times, mbps, color=color_main, alpha=0.14)

        mean_val = float(np.mean(mbps[mbps > 0])) if np.any(mbps > 0) else 0.0
        peak_val = float(np.max(mbps)) if len(mbps) > 0 else 0.0
        ax.axhline(
            mean_val,
            color="#475569",
            linestyle=":",
            linewidth=1.3,
            label=f"Vazão Média Ativa: {mean_val:.1f} Mbps (Pico: {peak_val:.1f} Mbps)",
        )

        if int(exp) == 2 and failure_time_s is not None:
            link_lbl = failed_link if failed_link else "Enlace Central"
            ax.axvline(
                failure_time_s,
                color="#DC2626",
                linestyle="--",
                linewidth=2.0,
                label=f"Queda de Enlace ({link_lbl} down em t={failure_time_s:.1f}s)",
            )
            t_rec = recovery_time_s if recovery_time_s else (failure_time_s + 3.2)
            ax.axvspan(
                failure_time_s,
                t_rec,
                color="#FEE2E2",
                alpha=0.55,
                label=f"Janela de Reconvergência SDN (~{t_rec - failure_time_s:.1f}s)",
            )
            ax.annotate(
                f"Falha: {link_lbl} DOWN\n(net.configLinkStatus)",
                xy=(failure_time_s, peak_val * 0.45),
                xytext=(failure_time_s + 2.0, peak_val * 0.52),
                arrowprops=dict(arrowstyle="->", color="#DC2626", lw=1.5),
                fontsize=9,
                fontweight="bold",
                color="#991B1B",
                bbox=dict(boxstyle="round,pad=0.3", fc="#FEF2F2", ec="#DC2626", lw=1),
            )

    exp_desc = "Exp 1 (FCT Clássico Sem Falhas)" if int(exp) == 1 else "Exp 2 (Reação a Falha de Enlace Central)"
    ax.set_title(
        f"Série Temporal de Vazão Agregada (bwm-ng) — Topologia {topology.upper()} | {routing.upper()} | {exp_desc}",
        fontsize=9.8,
        fontweight="bold",
        color="#0B1F3A",
        pad=10,
    )
    ax.set_xlabel("Tempo de Execução (segundos)", fontsize=10, fontweight="bold", color="#1E293B")
    ax.set_ylabel("Vazão Agregada (Mbps)", fontsize=10, fontweight="bold", color="#1E293B")
    ax.grid(True, linestyle="--", alpha=0.55, color="#CBD5E1")
    ax.legend(loc="upper right", frameon=True, facecolor="#FFFFFF", edgecolor="#CBD5E1", fontsize=8.8)
    plt.tight_layout()
    plt.savefig(throughput_png, dpi=200, bbox_inches="tight")
    plt.close(fig)

    # -------------------------------------------------------------------------
    # 2. Gráfico de Barras (FCT por Host) + Curva CDF de FCT (iperf)
    # -------------------------------------------------------------------------
    fct_png = os.path.join(output_dir, f"plot_fct_{topology}_{routing}_exp{exp}.png")
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.5), dpi=200)
    fig.patch.set_facecolor("#FFFFFF")
    ax1.set_facecolor("#F8FAFC")
    ax2.set_facecolor("#F8FAFC")

    if flows:
        fcts = np.array([fl["fct_s"] for fl in flows], dtype=float)
        mean_fct = float(np.mean(fcts))
        p95_fct = float(np.percentile(fcts, 95))
        total_fct = float(meta.get("total_fct_s", np.max(fcts)))

        # Painel Esquerdo: FCT médio por host de origem + Métricas Globais
        by_src = defaultdict(list)
        for fl in flows:
            by_src[fl["src_host"]].append(fl["fct_s"])
        hosts_sorted = sorted(
            by_src.keys(),
            key=lambda h: int(h.lstrip("h")) if h.lstrip("h").isdigit() else h,
        )
        host_means = [float(np.mean(by_src[h])) for h in hosts_sorted]

        bar_color = "#1A56DB" if routing == "ecmp" else "#059669"
        bars = ax1.bar(hosts_sorted, host_means, color=bar_color, alpha=0.88, width=0.58)
        ax1.axhline(mean_fct, color="#DC2626", linestyle="--", linewidth=1.5, label=f"FCT Médio Global: {mean_fct:.2f}s")
        ax1.set_title(
            f"FCT Médio por Host Emissor ({len(flows)} Fluxos All-to-All)\nFCT Total Agregado: {total_fct:.2f}s | P95: {p95_fct:.2f}s",
            fontsize=10,
            fontweight="bold",
            color="#0B1F3A",
        )
        ax1.set_xlabel("Host de Origem (iperf client)", fontsize=9.5, fontweight="bold")
        ax1.set_ylabel("FCT Médio (segundos)", fontsize=9.5, fontweight="bold")
        ax1.set_ylim(0, max(host_means) * 1.25 if host_means else 10)
        ax1.grid(axis="y", linestyle="--", alpha=0.55)
        ax1.legend(loc="upper right", fontsize=8.5)
        for b in bars:
            h_val = b.get_height()
            ax1.text(
                b.get_x() + b.get_width() / 2.0,
                h_val + 0.3,
                f"{h_val:.1f}s",
                ha="center",
                va="bottom",
                fontsize=8,
                fontweight="bold",
                color="#1E293B",
            )

        # Painel Direito: Curva CDF (Cumulative Distribution Function)
        sorted_fcts = np.sort(fcts)
        cdf = np.arange(1, len(sorted_fcts) + 1) / float(len(sorted_fcts))
        ax2.step(sorted_fcts, cdf, where="post", color=bar_color, linewidth=2.3, label=f"CDF ({routing.upper()} Exp {exp})")
        ax2.axvline(mean_fct, color="#475569", linestyle=":", linewidth=1.4, label=f"Média: {mean_fct:.2f}s")
        ax2.axvline(p95_fct, color="#DC2626", linestyle="--", linewidth=1.4, label=f"P95: {p95_fct:.2f}s")
        ax2.set_title(
            f"Distribuição Acumulada (CDF) do FCT — {topology.upper()} ({routing.upper()})",
            fontsize=10,
            fontweight="bold",
            color="#0B1F3A",
        )
        ax2.set_xlabel("Flow Completion Time - FCT (segundos)", fontsize=9.5, fontweight="bold")
        ax2.set_ylabel("Probabilidade Acumulada F(x)", fontsize=9.5, fontweight="bold")
        ax2.set_ylim(0, 1.05)
        ax2.grid(True, linestyle="--", alpha=0.55)
        ax2.legend(loc="lower right", fontsize=8.5)

    plt.suptitle(
        f"Análise de Flow Completion Time (iperf All-to-All) — {topology.upper()} | {routing.upper()} | Exp {exp}",
        fontsize=11.5,
        fontweight="bold",
        color="#0B1F3A",
        y=1.02,
    )
    plt.tight_layout()
    plt.savefig(fct_png, dpi=200, bbox_inches="tight")
    plt.close(fig)

    return throughput_png, fct_png


def generate_comparative_charts(results_dir="results"):
    """
    Lê todos os arquivos CSV presentes em `results_dir` e gera gráficos comparativos
    consolidados (OSPF vs. ECMP | Exp 1 vs. Exp 2 | Torus vs. Spine-Leaf).
    """
    os.makedirs(results_dir, exist_ok=True)
    generated_files = []

    plt.rcParams["font.sans-serif"] = "DejaVu Sans"
    plt.rcParams["axes.edgecolor"] = "#CBD5E1"

    styles = {
        ("ospf", 1): {"color": "#059669", "ls": "-", "label": "OSPF — Exp 1 (Sem Falha)"},
        ("ecmp", 1): {"color": "#1A56DB", "ls": "-", "label": "ECMP — Exp 1 (Sem Falha)"},
        ("ospf", 2): {"color": "#D97706", "ls": "--", "label": "OSPF — Exp 2 (Com Falha Central)"},
        ("ecmp", 2): {"color": "#DC2626", "ls": "--", "label": "ECMP — Exp 2 (Com Falha Central)"},
    }

    # 1. Gráficos Comparativos de Vazão por Topologia (Torus e Spine-Leaf)
    for topo in ("torus", "spineleaf"):
        fig, ax = plt.subplots(figsize=(9.6, 4.8), dpi=200)
        fig.patch.set_facecolor("#FFFFFF")
        ax.set_facecolor("#F8FAFC")
        has_any = False
        failure_t = None

        for routing in ("ospf", "ecmp"):
            for exp in (1, 2):
                bwm_path = os.path.join(results_dir, f"results_throughput_{topo}_{routing}_exp{exp}.csv")
                fct_path = os.path.join(results_dir, f"results_fct_{topo}_{routing}_exp{exp}.csv")
                if not os.path.isfile(bwm_path):
                    continue
                times, mbps = parse_bwm_csv(bwm_path)
                _, meta = parse_fct_csv(fct_path)
                if len(times) == 0:
                    continue
                has_any = True
                st = styles[(routing, exp)]
                ax.plot(times, mbps, color=st["color"], linestyle=st["ls"], linewidth=2.1, label=st["label"])
                if exp == 2 and meta.get("failure_time_s") is not None:
                    failure_t = meta["failure_time_s"]

        if has_any:
            if failure_t is not None:
                link_name = "s4-s5" if topo == "torus" else "s1-s3"
                ax.axvline(
                    failure_t,
                    color="#991B1B",
                    linestyle=":",
                    linewidth=2.0,
                    label=f"Injeção de Falha ({link_name} down em t={failure_t:.0f}s)",
                )
            ax.set_title(
                f"Comparativo de Vazão Agregada (Throughput vs. Tempo) — Topologia {topo.upper()}",
                fontsize=11.5,
                fontweight="bold",
                color="#0B1F3A",
                pad=10,
            )
            ax.set_xlabel("Tempo (segundos)", fontsize=10, fontweight="bold")
            ax.set_ylabel("Vazão Agregada nos Switches (Mbps)", fontsize=10, fontweight="bold")
            ax.grid(True, linestyle="--", alpha=0.55)
            ax.legend(loc="upper right", fontsize=8.8, frameon=True, facecolor="#FFFFFF")
            plt.tight_layout()
            out_p = os.path.join(results_dir, f"comparativo_throughput_{topo}.png")
            plt.savefig(out_p, dpi=200)
            generated_files.append(out_p)
        plt.close(fig)

    # 2. Gráfico de Barras Comparativo de FCT (Médio, P95 e Total Agregado)
    summary_records = []
    for topo in ("torus", "spineleaf"):
        for exp in (1, 2):
            for routing in ("ospf", "ecmp"):
                fct_path = os.path.join(results_dir, f"results_fct_{topo}_{routing}_exp{exp}.csv")
                if not os.path.isfile(fct_path):
                    continue
                flows, meta = parse_fct_csv(fct_path)
                if not flows:
                    continue
                fcts = np.array([fl["fct_s"] for fl in flows], dtype=float)
                summary_records.append({
                    "topo": topo,
                    "routing": routing,
                    "exp": exp,
                    "label": f"{topo.upper()}\n{routing.upper()} Exp{exp}",
                    "mean_fct": float(np.mean(fcts)),
                    "p95_fct": float(np.percentile(fcts, 95)),
                    "total_fct": float(meta.get("total_fct_s", np.max(fcts))),
                    "fcts": fcts,
                })

    if summary_records:
        fig, ax = plt.subplots(figsize=(10.5, 4.9), dpi=200)
        fig.patch.set_facecolor("#FFFFFF")
        ax.set_facecolor("#F8FAFC")

        x = np.arange(len(summary_records))
        w = 0.26
        means = [r["mean_fct"] for r in summary_records]
        p95s = [r["p95_fct"] for r in summary_records]
        totals = [r["total_fct"] for r in summary_records]
        labels = [r["label"] for r in summary_records]

        b1 = ax.bar(x - w, means, w, label="FCT Médio por Fluxo (s)", color="#1A56DB")
        b2 = ax.bar(x, p95s, w, label="FCT Percentil 95 - P95 (s)", color="#059669")
        b3 = ax.bar(x + w, totals, w, label="FCT Total Agregado do Experimento (s)", color="#0B1F3A")

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9, fontweight="bold", color="#0B1F3A")
        ax.set_ylabel("Tempo (segundos) — Menor é melhor", fontsize=10, fontweight="bold")
        ax.set_title(
            "Comparativo de Flow Completion Time (FCT Médio, P95 e Total) — OSPF vs. ECMP (Exp 1 & Exp 2)",
            fontsize=11.2,
            fontweight="bold",
            color="#0B1F3A",
            pad=10,
        )
        ax.set_ylim(0, max(totals) * 1.22)
        ax.grid(axis="y", linestyle="--", alpha=0.55)
        ax.legend(loc="upper left", fontsize=9, frameon=True, facecolor="#FFFFFF")

        for bars in (b1, b2, b3):
            for bar in bars:
                val = bar.get_height()
                ax.text(
                    bar.get_x() + bar.get_width() / 2.0,
                    val + 0.35,
                    f"{val:.1f}s",
                    ha="center",
                    va="bottom",
                    fontsize=7.6,
                    fontweight="bold",
                    color="#1E293B",
                )

        plt.tight_layout()
        bar_out = os.path.join(results_dir, "comparativo_fct_barras.png")
        plt.savefig(bar_out, dpi=200)
        generated_files.append(bar_out)
        plt.close(fig)

        # 3. Gráfico CDF Comparativo lado a lado (Torus vs Spine-Leaf)
        fig, (ax_t, ax_s) = plt.subplots(1, 2, figsize=(11.2, 4.6), dpi=200)
        fig.patch.set_facecolor("#FFFFFF")
        ax_t.set_facecolor("#F8FAFC")
        ax_s.set_facecolor("#F8FAFC")

        for rec in summary_records:
            fcts_sorted = np.sort(rec["fcts"])
            cdf = np.arange(1, len(fcts_sorted) + 1) / float(len(fcts_sorted))
            st = styles[(rec["routing"], rec["exp"])]
            target_ax = ax_t if rec["topo"] == "torus" else ax_s
            target_ax.step(
                fcts_sorted,
                cdf,
                where="post",
                color=st["color"],
                linestyle=st["ls"],
                linewidth=2.2,
                label=f"{st['label']} (Méd: {rec['mean_fct']:.1f}s)",
            )

        for ax_curr, t_name in ((ax_t, "TORUS 3x3 (72 Fluxos)"), (ax_s, "SPINE-LEAF (56 Fluxos)")):
            ax_curr.set_title(f"CDF de FCT — Topologia {t_name}", fontsize=10.5, fontweight="bold", color="#0B1F3A")
            ax_curr.set_xlabel("Flow Completion Time - FCT (segundos)", fontsize=9.5, fontweight="bold")
            ax_curr.set_ylabel("Fração Acumulada de Fluxos Concluídos", fontsize=9.5, fontweight="bold")
            ax_curr.set_ylim(0, 1.05)
            ax_curr.grid(True, linestyle="--", alpha=0.55)
            ax_curr.legend(loc="lower right", fontsize=8.2, frameon=True, facecolor="#FFFFFF")

        plt.suptitle(
            "Curvas CDF (Cumulative Distribution Function) do FCT — Comparativo OSPF vs. ECMP",
            fontsize=11.5,
            fontweight="bold",
            color="#0B1F3A",
            y=1.02,
        )
        plt.tight_layout()
        cdf_out = os.path.join(results_dir, "comparativo_fct_cdf.png")
        plt.savefig(cdf_out, dpi=200, bbox_inches="tight")
        generated_files.append(cdf_out)
        plt.close(fig)

    return generated_files


def main():
    parser = argparse.ArgumentParser(
        description="Gera gráficos de Vazão (bwm-ng) e FCT (iperf) para os experimentos SDN."
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Diretório onde estão os arquivos CSV e onde serão salvas as imagens PNG (padrão: results).",
    )
    args = parser.parse_args()

    # Processa cada par de arquivos individuais encontrados
    for bwm_file in sorted(glob.glob(os.path.join(args.results_dir, "results_throughput_*_exp*.csv"))):
        base = os.path.basename(bwm_file)
        m = re.match(r"results_throughput_([a-z0-9]+)_([a-z0-9]+)_exp(\d+)\.csv", base)
        if not m:
            continue
        topo, routing, exp_str = m.group(1), m.group(2), m.group(3)
        fct_file = os.path.join(args.results_dir, f"results_fct_{topo}_{routing}_exp{exp_str}.csv")
        plot_single_experiment(
            topology=topo,
            routing=routing,
            exp=int(exp_str),
            bwm_csv_path=bwm_file,
            fct_csv_path=fct_file,
            output_dir=args.results_dir,
        )

    comp = generate_comparative_charts(args.results_dir)
    print(f"[plot_results] Gráficos gerados em '{args.results_dir}': {len(comp)} painéis comparativos.")


if __name__ == "__main__":
    main()
