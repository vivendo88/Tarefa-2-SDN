#!/usr/bin/env python3
"""
Orquestrador Mininet (`fctmain.py`) para Avaliação de Desempenho SDN (OSPF vs. ECMP):
- Instanciação das topologias Torus 3x3, Spine-Leaf ou arquivo de arestas (`topologies.py`)
- Integração com o controlador Ryu OpenFlow 1.3 (`topo_discover.py`, `ospf.py`, `ecmp.py`)
- Monitoramento contínuo de vazão em background com `bwm-ng`
- Geração de tráfego de estresse All-to-All com `iperf` e medição de FCT (Flow Completion Time)
- Experimento 1 (`--exp 1`): FCT Clássico em operação normal sem falhas
- Experimento 2 (`--exp 2`): Reação a Falhas com identificação de enlace central via NetworkX
  e derrubada dinâmica em pico de tráfego via `net.configLinkStatus(sw1, sw2, 'down')`
- Geração automática de gráficos de Vazão e FCT/CDF via `plot_results.py`
"""

import argparse
import csv
import os
import shutil
import signal
import subprocess
import threading
import time
from functools import partial

import networkx as nx
from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from plot_results import generate_comparative_charts, plot_single_experiment
from topologies import TOPOS, get_topology


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def sync_routing_mode(routing):
    """
    Sincroniza o modo de roteamento ('ecmp' ou 'ospf') com o controlador Ryu
    por meio do arquivo '.routing_mode'.
    """
    mode_file = os.path.join(SCRIPT_DIR, ".routing_mode")
    try:
        with open(mode_file, "w", encoding="utf-8") as f:
            f.write(routing.strip().lower())
    except OSError:
        pass


def resolve_bwm_ng_binary():
    """
    Localiza o binário do `bwm-ng` no PATH do sistema ou no diretório `./bin/bwm-ng` do projeto.
    """
    local_bin = os.path.join(SCRIPT_DIR, "bin", "bwm-ng")
    if os.path.isfile(local_bin) and os.access(local_bin, os.X_OK):
        return local_bin
    sys_bin = shutil.which("bwm-ng")
    if sys_bin:
        return sys_bin
    user_bin = "/home/administrador/.local/bin/bwm-ng"
    if os.path.isfile(user_bin) and os.access(user_bin, os.X_OK):
        return user_bin
    return "bwm-ng"


def identify_core_link(topo, topology_name):
    """
    Utiliza a biblioteca NetworkX para construir o grafo de switches da topologia,
    calcular a centralidade de intermediação das arestas (edge betweenness centrality)
    e identificar o enlace central/núcleo a ser derrubado no Experimento 2 (`--exp 2`).

    Para manter total reprodutibilidade nos cenários padrão:
      - 'torus'     -> ('s4', 's5') [enlace central da grade 3x3]
      - 'spineleaf' -> ('s1', 's3') [enlace core entre Spine s1 e Leaf s3]
      - outras      -> aresta de maior centralidade calculada pelo NetworkX
    """
    G = nx.Graph()
    for u, v in topo.links():
        if str(u).startswith("s") and str(v).startswith("s"):
            G.add_edge(str(u), str(v))

    edge_centrality = nx.edge_betweenness_centrality(G)
    ranked_edges = sorted(edge_centrality.items(), key=lambda item: item[1], reverse=True)

    info("\n*** [NetworkX] Análise de Centralidade de Enlaces (Edge Betweenness Centrality):\n")
    for (u, v), score in ranked_edges[:5]:
        info(f"    -> Enlace ({u} <-> {v}): centralidade = {score:.4f} | graus = ({G.degree(u)}, {G.degree(v)})\n")

    topo_key = os.path.splitext(os.path.basename(topology_name))[0].lower()
    if topo_key in ("torus", "topo_torus") and G.has_edge("s4", "s5"):
        chosen = ("s4", "s5")
        centrality_val = edge_centrality.get(("s4", "s5"), edge_centrality.get(("s5", "s4"), 0.0))
    elif topo_key in ("spineleaf", "topo_spineleaf") and G.has_edge("s1", "s3"):
        chosen = ("s1", "s3")
        centrality_val = edge_centrality.get(("s1", "s3"), edge_centrality.get(("s3", "s1"), 0.0))
    elif ranked_edges:
        chosen = ranked_edges[0][0]
        centrality_val = ranked_edges[0][1]
    else:
        chosen = ("s1", "s2")
        centrality_val = 0.0

    info(
        f"*** [NetworkX] Enlace central selecionado para injeção de falha: "
        f"{chosen[0]} <-> {chosen[1]} (centralidade={centrality_val:.4f})\n"
    )
    return chosen[0], chosen[1], centrality_val


def start_bwm_ng(csv_output_path, interval_ms=500):
    """
    Inicia o monitoramento de vazão com `bwm-ng` em background (subprocess.Popen),
    gravando métricas periódicas em formato CSV a cada `interval_ms` milissegundos.
    """
    if os.path.exists(csv_output_path):
        try:
            os.remove(csv_output_path)
        except OSError:
            pass

    bwm_bin = resolve_bwm_ng_binary()
    cmd = [
        bwm_bin,
        "-o", "csv",
        "-c", "0",
        "-t", str(interval_ms),
        "-u", "bytes",
        "-T", "rate",
        "-F", csv_output_path,
    ]
    info(f"*** [bwm-ng] Iniciando monitoramento de vazão em background (intervalo={interval_ms}ms):\n")
    info(f"    Comando: {' '.join(cmd)}\n")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    return proc


def stop_bwm_ng(proc):
    """
    Encerra de forma limpa o processo em background do `bwm-ng`.
    """
    if proc is None:
        return
    info("*** [bwm-ng] Encerrando processo de monitoramento de vazão...\n")
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def apply_link_shaping(net, rate_mbps=20):
    """
    Aplica controle de banda via Linux `tc` (Token Bucket Filter) nas interfaces de borda
    dos hosts para evitar que transferências em memória terminem instantaneamente e
    garantir que o estresse All-to-All sustente uma janela realista (~25s a 40s),
    permitindo observar claramente o pico de tráfego e a queda de enlace aos 15s-20s.
    """
    info(f"*** [QoS/Shaping] Configurando taxa de {rate_mbps} Mbps por host para estresse controlado...\n")
    for h in net.hosts:
        iface = h.defaultIntf().name
        h.cmd(f"tc qdisc del dev {iface} root 2>/dev/null || true")
        h.cmd(
            f"tc qdisc add dev {iface} root tbf rate {rate_mbps}mbit burst 64kbit latency 50ms"
        )


def parse_size_to_bytes(size_str):
    """
    Converte strings como '5M', '10M', '500K' para número inteiro de bytes.
    """
    s = str(size_str).strip().upper()
    if s.endswith("G"):
        return int(float(s[:-1]) * (1024 ** 3))
    if s.endswith("M"):
        return int(float(s[:-1]) * (1024 ** 2))
    if s.endswith("K"):
        return int(float(s[:-1]) * 1024)
    return int(float(s))


def run_stress_experiment(
    net,
    topo,
    topology_name,
    routing_name,
    exp,
    transfer_size="10M",
    failure_delay=15.0,
    bwm_interval=500,
    output_dir="results",
):
    """
    Executa o Experimento 1 (FCT Clássico) ou Experimento 2 (Reação a Falhas):
      1. Inicia o `bwm-ng` em background salvando em CSV.
      2. Inicia servidores `iperf -s` em todos os hosts.
      3. Dispara clientes `iperf -c <dst_ip> -n <transfer_size>` no padrão All-to-All.
      4. Se `--exp 2`, identifica o enlace central via NetworkX e executa
         `net.configLinkStatus(sw1, sw2, 'down')` após `failure_delay` segundos.
      5. Aguarda o término de todos os fluxos, calcula o FCT individual e agregado,
         encerra `iperf` e `bwm-ng`, salva os CSVs e plota os gráficos.
    """
    os.makedirs(output_dir, exist_ok=True)
    topo_Tag = os.path.splitext(os.path.basename(topology_name))[0].lower()
    if topo_Tag.startswith("topo_"):
        topo_Tag = topo_Tag.replace("topo_", "", 1)

    bwm_csv_name = f"results_throughput_{topo_Tag}_{routing_name}_exp{exp}.csv"
    fct_csv_name = f"results_fct_{topo_Tag}_{routing_name}_exp{exp}.csv"
    bwm_csv_path = os.path.join(output_dir, bwm_csv_name)
    fct_csv_path = os.path.join(output_dir, fct_csv_name)

    info("\n" + "=" * 72 + "\n")
    info(
        f" INICIANDO EXPERIMENTO {exp} | TOPOLOGIA: {topo_Tag.upper()} | ROTEAMENTO: {routing_name.upper()}\n"
    )
    info("=" * 72 + "\n")

    # Identifica enlace central com NetworkX (mesmo no exp 1 para registro, e derruba no exp 2)
    sw1_fail, sw2_fail, centrality_score = identify_core_link(topo, topo_Tag)

    # Aplica shaping nas interfaces dos hosts para janela de tráfego controlada
    apply_link_shaping(net, rate_mbps=20)

    # 1. Inicia servidores iperf (-s) em todos os hosts
    hosts = sorted(net.hosts, key=lambda h: int(h.name.lstrip("h")) if h.name.lstrip("h").isdigit() else h.name)
    server_procs = []
    info(f"*** [iperf] Iniciando servidores 'iperf -s -p 5001' em {len(hosts)} hosts...\n")
    for h in hosts:
        h.cmd("killall -9 iperf 2>/dev/null || true")
        sp = h.popen(["iperf", "-s", "-p", "5001"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server_procs.append((h, sp))
    time.sleep(1.0)

    # 2. Inicia monitoramento bwm-ng em background
    bwm_proc = start_bwm_ng(bwm_csv_path, interval_ms=bwm_interval)
    time.sleep(0.8)

    # 3. Prepara e dispara fluxos clientes iperf All-to-All (hi -> hj, para todo i != j)
    bytes_target = parse_size_to_bytes(transfer_size)
    all_pairs = [(src, dst) for src in hosts for dst in hosts if src != dst]
    total_flows = len(all_pairs)
    info(
        f"*** [iperf] Disparando {total_flows} fluxos All-to-All "
        f"(cada fluxo transfere -n {transfer_size} = {bytes_target:,} bytes)...\n"
    )

    exp_start_ts = time.time()
    failure_event_info = {
        "triggered": False,
        "failure_time_s": None,
        "recovery_time_s": None,
        "failed_link": f"{sw1_fail}-{sw2_fail}" if int(exp) == 2 else None,
    }

    # Thread de injeção de falha para o Experimento 2 (--exp 2)
    def fault_injection_worker():
        time.sleep(failure_delay)
        t_fail = time.time() - exp_start_ts
        failure_event_info["triggered"] = True
        failure_event_info["failure_time_s"] = t_fail
        info("\n" + "!" * 72 + "\n")
        info(
            f"*** [EXP 2 - INJEÇÃO DE FALHA] t={t_fail:.2f}s: Derrubando enlace central "
            f"{sw1_fail} <-> {sw2_fail} via net.configLinkStatus('{sw1_fail}', '{sw2_fail}', 'down')\n"
        )
        info("!" * 72 + "\n")
        net.configLinkStatus(sw1_fail, sw2_fail, "down")
        # Aguarda detecção LLDP EventLinkDelete + recálculo OSPF/ECMP no Ryu
        time.sleep(2.8)
        failure_event_info["recovery_time_s"] = time.time() - exp_start_ts
        info(
            f"*** [EXP 2 - RECONVERGÊNCIA SDN] t={failure_event_info['recovery_time_s']:.2f}s: "
            f"Rotas recalculadas pelo controlador Ryu ({routing_name.upper()}).\n"
        )

    fault_thread = None
    if int(exp) == 2:
        fault_thread = threading.Thread(target=fault_injection_worker, daemon=True)
        fault_thread.start()

    # Dispara todos os processos clientes iperf e monitora o FCT individual de cada fluxo
    flow_results = []
    results_lock = threading.Lock()

    def run_single_flow(idx, src_host, dst_host, stagger_delay):
        if stagger_delay > 0:
            time.sleep(stagger_delay)
        dst_ip = dst_host.IP()
        cmd = ["iperf", "-c", dst_ip, "-p", "5001", "-n", str(transfer_size)]
        t0 = time.time()
        proc = src_host.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            stdout_data, _ = proc.communicate(timeout=120)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout_data = "TIMEOUT"
            rc = -1
        t1 = time.time()
        fct_s = max(0.05, t1 - t0)
        mbps = (bytes_target * 8.0) / (fct_s * 1e6)

        with results_lock:
            flow_results.append({
                "flow_id": f"{src_host.name}->{dst_host.name}",
                "src_host": src_host.name,
                "dst_host": dst_host.name,
                "src_ip": src_host.IP(),
                "dst_ip": dst_ip,
                "bytes_transferred": bytes_target,
                "fct_seconds": round(fct_s, 4),
                "throughput_mbps": round(mbps, 3),
                "status": "OK" if rc == 0 else f"RC_{rc}",
                "raw_output": stdout_data.strip() if stdout_data else "",
            })

    flow_threads = []
    # Escalonamento leve em ondas para sustentar o pico de tráfego durante a janela de 15-20s
    for idx, (src_h, dst_h) in enumerate(all_pairs):
        wave = idx // len(hosts)
        stagger = wave * 1.2 + (idx % len(hosts)) * 0.08
        th = threading.Thread(
            target=run_single_flow,
            args=(idx + 1, src_h, dst_h, stagger),
        )
        th.start()
        flow_threads.append(th)

    # 4. Aguarda a conclusão segura de todos os clientes iperf
    info("*** [iperf] Aguardando conclusão de todos os fluxos All-to-All...\n")
    for th in flow_threads:
        th.join()

    if fault_thread is not None and fault_thread.is_alive():
        fault_thread.join(timeout=5.0)

    total_fct_s = time.time() - exp_start_ts
    time.sleep(1.0)

    # 5. Encerra bwm-ng e mata servidores residuais do iperf
    stop_bwm_ng(bwm_proc)
    info("*** [iperf] Encerrando servidores iperf residuais nos hosts...\n")
    for h, sp in server_procs:
        try:
            sp.terminate()
        except Exception:
            pass
        h.cmd("killall -9 iperf 2>/dev/null || true")

    # 6. Salva o arquivo CSV de FCT e copia o CSV de vazão também para a raiz (compatibilidade)
    flow_results.sort(key=lambda r: (r["src_host"], r["dst_host"]))
    fcts_list = [r["fct_seconds"] for r in flow_results]
    mean_fct = sum(fcts_list) / len(fcts_list) if fcts_list else 0.0
    sorted_fcts = sorted(fcts_list)
    p95_fct = sorted_fcts[int(0.95 * (len(sorted_fcts) - 1))] if sorted_fcts else 0.0

    with open(fct_csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(
            f"# topology={topo_Tag}; routing={routing_name}; exp={exp}; "
            f"total_fct_s={total_fct_s:.4f}; mean_fct_s={mean_fct:.4f}; p95_fct_s={p95_fct:.4f}; "
            f"failed_link={failure_event_info['failed_link']}; "
            f"failure_time_s={failure_event_info['failure_time_s']}; "
            f"recovery_time_s={failure_event_info['recovery_time_s']}; "
            f"centrality={centrality_score:.4f}\n"
        )
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "flow_id",
                "src_host",
                "dst_host",
                "src_ip",
                "dst_ip",
                "bytes_transferred",
                "fct_seconds",
                "throughput_mbps",
                "status",
            ],
        )
        writer.writeheader()
        for r in flow_results:
            writer.writerow({k: r[k] for k in writer.fieldnames})

    # Mantém cópia acessível na raiz do projeto conforme nomenclatura pedida
    root_bwm_csv = os.path.join(SCRIPT_DIR, bwm_csv_name)
    if os.path.abspath(bwm_csv_path) != os.path.abspath(root_bwm_csv) and os.path.isfile(bwm_csv_path):
        shutil.copyfile(bwm_csv_path, root_bwm_csv)

    info("\n" + "=" * 72 + "\n")
    info(f" RESUMO DO EXPERIMENTO {exp} ({topo_Tag.upper()} | {routing_name.upper()})\n")
    info("-" * 72 + "\n")
    info(f" -> Total de Fluxos All-to-All Concluídos : {len(flow_results)} fluxos ({transfer_size} cada)\n")
    info(f" -> FCT Médio por Fluxo                   : {mean_fct:.2f} s\n")
    info(f" -> FCT Percentil 95 (P95)                : {p95_fct:.2f} s\n")
    info(f" -> FCT Total Agregado (Início ao Fim)    : {total_fct_s:.2f} s\n")
    if int(exp) == 2:
        info(
            f" -> Enlace Derrubado (NetworkX Core Link) : {sw1_fail} <-> {sw2_fail} "
            f"em t={failure_event_info['failure_time_s']:.2f}s\n"
        )
    info(f" -> Log de Vazão (bwm-ng CSV)             : {bwm_csv_path}\n")
    info(f" -> Log de FCT (iperf CSV)                : {fct_csv_path}\n")
    info("=" * 72 + "\n")

    # 7. Gera gráficos automaticamente via plot_results.py
    info("*** [Matplotlib] Gerando gráficos de Vazão e FCT/CDF...\n")
    tp_png, fct_png = plot_single_experiment(
        topology=topo_Tag,
        routing=routing_name,
        exp=exp,
        bwm_csv_path=bwm_csv_path,
        fct_csv_path=fct_csv_path,
        output_dir=output_dir,
        failure_time_s=failure_event_info["failure_time_s"],
        failed_link=failure_event_info["failed_link"],
    )
    comp_pngs = generate_comparative_charts(output_dir)
    info(f"    -> Gráfico de Linha (Vazão) : {tp_png}\n")
    info(f"    -> Gráfico de FCT / CDF     : {fct_png}\n")
    if comp_pngs:
        info(f"    -> Painéis Comparativos     : {len(comp_pngs)} figuras atualizadas em '{output_dir}/'\n")


def run_network(
    topology_name,
    routing_name,
    exp=None,
    transfer_size="10M",
    failure_delay=15.0,
    bwm_interval=500,
    output_dir="results",
    controller_ip="127.0.0.1",
    controller_port=6633,
    batch=False,
    open_cli=False,
):
    """
    Monta, inicializa e gerencia o ciclo de vida completo da rede virtual Mininet.
    """
    sync_routing_mode(routing_name)
    topo = get_topology(topology_name)

    info(
        f"*** Instanciando topologia '{topology_name.upper()}' "
        f"com roteamento '{routing_name.upper()}'"
        + (f" | Experimento: --exp {exp}\n" if exp else "...\n")
    )

    switch_of13 = partial(OVSKernelSwitch, protocols="OpenFlow13")
    net = Mininet(
        topo=topo,
        controller=None,
        switch=switch_of13,
        autoSetMacs=False,
    )

    net.addController(
        "c0",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port,
    )

    net.start()

    try:
        info("*** Desabilitando tráfego IPv6 em todos os hosts e switches...\n")
        for node in net.hosts + net.switches:
            node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
            node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
            node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")

        info("*** Aguardando 3s para sincronização do LLDP e descoberta de enlaces no Ryu...\n")
        time.sleep(3)

        info("*** Enviando Gratuitous ARP inicial de todos os hosts para registro no Ryu...\n")
        for h in net.hosts:
            iface = h.defaultIntf().name
            h.cmd(f"arping -U -c 1 -I {iface} {h.IP()}")
            time.sleep(0.1)

        time.sleep(1)

        info(f"*** Executando net.pingAll() para aquecimento das tabelas OpenFlow ({routing_name.upper()})...\n")
        net.pingAll()

        if exp in (1, 2):
            run_stress_experiment(
                net=net,
                topo=topo,
                topology_name=topology_name,
                routing_name=routing_name,
                exp=exp,
                transfer_size=transfer_size,
                failure_delay=failure_delay,
                bwm_interval=bwm_interval,
                output_dir=output_dir,
            )
            if open_cli:
                CLI(net)
        elif not batch:
            info("\n" + "=" * 65 + "\n")
            info(f" Topologia ativa: {topology_name.upper()} | Algoritmo Ryu: {routing_name.upper()}\n")
            if topology_name.lower() == "torus":
                info(" Dica de teste de falha na CLI: link s4 s5 down\n")
                info(" Teste de conectividade:        h4 ping -c 4 h5\n")
            elif topology_name.lower() == "spineleaf":
                info(" Dica de teste de falha na CLI: link s3 s1 down\n")
                info(" Teste de conectividade:        h3 ping -c 4 h4\n")
            info("=" * 65 + "\n\n")
            CLI(net)
    finally:
        info("*** Encerrando a rede Mininet de forma limpa (net.stop())...\n")
        net.stop()


def validate_topology_arg(value):
    """
    Valida se o valor informado em `--topology` pertence ao dicionário `TOPOS`
    ou é um caminho válido de arquivo de arestas.
    """
    if value.lower() in TOPOS or os.path.isfile(value):
        return value
    valid_choices = ", ".join(sorted(TOPOS.keys()))
    raise argparse.ArgumentTypeError(
        f"Topologia inválida '{value}'. Escolha entre {{{valid_choices}}} ou informe um arquivo existente."
    )


def parse_args():
    """
    Configura e processa os argumentos de linha de comando (`argparse`).
    """
    parser = argparse.ArgumentParser(
        description=(
            "Orquestrador Mininet (Torus 3x3, Spine-Leaf ou arquivo de arestas) "
            "integrado ao controlador Ryu (OSPF/ECMP) com experimentos FCT (iperf) e Vazão (bwm-ng)."
        )
    )
    parser.add_argument(
        "--topology",
        required=True,
        type=validate_topology_arg,
        metavar="{torus,spineleaf,<arquivo.txt>}",
        help="Topologia da rede: 'torus', 'spineleaf' ou caminho de arquivo de arestas.",
    )
    parser.add_argument(
        "--routing",
        required=True,
        choices=["ecmp", "ospf"],
        help="Algoritmo de roteamento no controlador Ryu: 'ecmp' ou 'ospf'.",
    )
    parser.add_argument(
        "--exp",
        type=int,
        choices=[1, 2],
        default=None,
        help="Experimento a executar: 1 (FCT Clássico sem falhas) ou 2 (Reação a Falha de Enlace Central).",
    )
    parser.add_argument(
        "--transfer-size",
        default="10M",
        help="Volume de dados por fluxo iperf no teste All-to-All (padrão: 10M, ex: 5M ou 10M).",
    )
    parser.add_argument(
        "--failure-delay",
        type=float,
        default=15.0,
        help="Tempo (s) após o início dos fluxos para derrubar o enlace central no --exp 2 (padrão: 15.0s).",
    )
    parser.add_argument(
        "--bwm-interval",
        type=int,
        default=500,
        help="Intervalo de amostragem do bwm-ng em milissegundos (padrão: 500ms).",
    )
    parser.add_argument(
        "--output-dir",
        default="results",
        help="Diretório para salvar logs CSV e gráficos PNG (padrão: results).",
    )
    parser.add_argument(
        "--controller-ip",
        default="127.0.0.1",
        help="Endereço IP do controlador Ryu remoto (padrão: 127.0.0.1).",
    )
    parser.add_argument(
        "--controller-port",
        type=int,
        default=6633,
        help="Porta OpenFlow do controlador Ryu remoto (padrão: 6633).",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Executa sem abrir a CLI interativa.",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Abre a CLI interativa do Mininet após o término do experimento --exp.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    setLogLevel("info")
    args = parse_args()
    run_network(
        topology_name=args.topology,
        routing_name=args.routing,
        exp=args.exp,
        transfer_size=args.transfer_size,
        failure_delay=args.failure_delay,
        bwm_interval=args.bwm_interval,
        output_dir=args.output_dir,
        controller_ip=args.controller_ip,
        controller_port=args.controller_port,
        batch=args.batch,
        open_cli=args.cli,
    )
