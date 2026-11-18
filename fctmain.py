#!/usr/bin/env python3
"""
Orquestrador Mininet (fctmain.py) para instanciação das topologias a partir do
módulo dedicado `topologies.py`, integração com o controlador Ryu (OSPF/ECMP)
e controle manual passo a passo dos experimentos da Parte 2 (bwm-ng, iperf,
medição de FCT e injeção de falhas de enlace).
"""

import argparse
import csv
from datetime import datetime
from functools import partial
import os
import shutil
import subprocess
import time

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from topologies import TOPOS, get_topology


DEFAULT_IPERF_PORT = 5001
FAILURE_LINKS = {
    "torus": ("s4", "s5"),
    "spineleaf": ("s3", "s1"),
}


def sync_routing_mode(routing):
    """
    Sincroniza o algoritmo de roteamento escolhido com o controlador Ryu
    por meio do arquivo '.routing_mode' no diretório do projeto.
    """
    mode_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".routing_mode")
    try:
        with open(mode_file, "w", encoding="utf-8") as f:
            f.write(routing.strip().lower())
    except OSError:
        pass


def get_logs_dir():
    logs_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir


def get_bwm_log_path(topology_name, routing_name, exp_num):
    topo_label = os.path.splitext(os.path.basename(topology_name))[0].lower()
    filename = f"bwm_{topo_label}_{routing_name.lower()}_exp{exp_num}.csv"
    return os.path.join(get_logs_dir(), filename)


def start_bwm_ng(log_path):
    """
    Inicia o monitor de interfaces `bwm-ng` em background salvando a taxa
    de transferência (bytes/s) de todas as interfaces em formato CSV.
    """
    if shutil.which("bwm-ng") is None:
        info("*** [AVISO] Comando 'bwm-ng' não encontrado no sistema! Instale com: sudo apt-get install bwm-ng\n")
        return None

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    if os.path.exists(log_path):
        os.remove(log_path)

    proc = subprocess.Popen(
        [
            "bwm-ng",
            "-o", "csv",
            "-c", "0",
            "-t", "500",
            "-u", "bytes",
            "-T", "rate",
            "-F", log_path,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    info(f"*** [BWM-NG] Monitoramento iniciado em background (PID {proc.pid}) -> {log_path}\n")
    return proc


def stop_bwm_ng(bwm_proc):
    """
    Encerra o processo `bwm-ng` com segurança.
    """
    if bwm_proc is None:
        return
    if bwm_proc.poll() is None:
        bwm_proc.terminate()
        try:
            bwm_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            bwm_proc.kill()
            bwm_proc.wait()
        info(f"*** [BWM-NG] Monitoramento encerrado (PID {bwm_proc.pid}).\n")


def start_iperf_servers(net, port=DEFAULT_IPERF_PORT):
    """
    Inicia servidores `iperf -s` em background em todos os hosts da topologia.
    """
    for h in net.hosts:
        h.cmd("killall -9 iperf 2>/dev/null || true")
        h.cmd(f"iperf -s -p {port} > /dev/null 2>&1 &")
    time.sleep(0.5)
    info(f"*** [IPERF] Servidores iniciados na porta {port} em {len(net.hosts)} hosts.\n")


def start_iperf_all_to_all(net, transfer_size="5M", port=DEFAULT_IPERF_PORT):
    """
    Dispara fluxos `iperf` de estresse "todos-para-todos" com blocos fixos
    de dados (ex: 5M) entre todos os pares de hosts (src != dst).
    Retorna (client_procs, t_start).
    """
    client_procs = []
    t_start = time.time()

    for src in net.hosts:
        for dst in net.hosts:
            if src != dst:
                proc = src.popen(
                    ["iperf", "-c", dst.IP(), "-p", str(port), "-n", str(transfer_size)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                client_procs.append((src.name, dst.name, proc))

    info(
        f"*** [IPERF] {len(client_procs)} fluxos todos-para-todos disparados "
        f"(bloco={transfer_size}, porta={port}).\n"
    )
    return client_procs, t_start


def record_fct_result(topology_name, routing_name, exp_num, transfer_size, fct_seconds, total_flows, failed_flows):
    """
    Registra o tempo total de conclusão de fluxos (FCT) em `logs/fct_results.csv`.
    """
    fct_file = os.path.join(get_logs_dir(), "fct_results.csv")
    file_exists = os.path.isfile(fct_file)
    topo_label = os.path.splitext(os.path.basename(topology_name))[0].lower()

    with open(fct_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "timestamp",
                "topology",
                "routing",
                "exp",
                "transfer_size",
                "fct_seconds",
                "total_flows",
                "failed_flows",
            ])
        writer.writerow([
            datetime.now().isoformat(timespec="seconds"),
            topo_label,
            routing_name.lower(),
            exp_num,
            transfer_size,
            f"{fct_seconds:.4f}",
            total_flows,
            failed_flows,
        ])
    info(f"*** [FCT] Resultado registrado em: {fct_file}\n")


def check_or_wait_iperf(
    client_procs,
    t_start,
    topology_name,
    routing_name,
    exp_num,
    transfer_size,
    wait_all=False,
    already_recorded=False,
):
    """
    Verifica o andamento dos fluxos `iperf` ou aguarda a conclusão de todos.
    Quando todos finalizam, exibe e salva o Flow Completion Time (FCT).
    Retorna True se todos os fluxos já concluíram.
    """
    if not client_procs or t_start is None:
        info("*** [IPERF] Nenhum fluxo todos-para-todos foi disparado ainda.\n")
        return False

    if wait_all:
        info("*** [IPERF] Aguardando conclusão de todos os fluxos...\n")
        while any(proc.poll() is None for _, _, proc in client_procs):
            time.sleep(0.05)

    running = 0
    succeeded = 0
    failed = 0
    for _, _, proc in client_procs:
        ret = proc.poll()
        if ret is None:
            running += 1
        elif ret == 0:
            succeeded += 1
        else:
            failed += 1

    elapsed = time.time() - t_start
    total = len(client_procs)

    info(
        f"*** [IPERF STATUS] Total: {total} | Concluídos OK: {succeeded} | "
        f"Falhas: {failed} | Em andamento: {running} | Tempo decorrido: {elapsed:.2f}s\n"
    )

    if running == 0:
        info(f"*** [FCT FINAL] Todos os {total} fluxos concluídos em {elapsed:.2f}s!\n")
        if not already_recorded:
            record_fct_result(
                topology_name=topology_name,
                routing_name=routing_name,
                exp_num=exp_num,
                transfer_size=transfer_size,
                fct_seconds=elapsed,
                total_flows=total,
                failed_flows=failed,
            )
        return True

    return False


def trigger_link_failure(net, topology_name, status="down"):
    """
    Altera o estado do enlace de núcleo padrão do Experimento 2 usando
    `net.configLinkStatus()` ('down' ou 'up').
    """
    key = topology_name.strip().lower()
    if key not in FAILURE_LINKS:
        info(
            f"*** [AVISO] Topologia '{topology_name}' não possui enlace de falha padrão mapeado.\n"
            "*** Utilize a opção [9] (CLI do Mininet) e execute manualmente: link <sw1> <sw2> down\n"
        )
        return False

    sw1, sw2 = FAILURE_LINKS[key]
    info(f"*** [FALHA DE ENLACE] Alterando status do enlace {sw1} <-> {sw2} para '{status.upper()}'...\n")
    net.configLinkStatus(sw1, sw2, status)
    return True


def cleanup_processes(net, bwm_proc=None):
    """
    Encerra o monitor `bwm-ng` e limpa processos `iperf` residuais em todos os hosts.
    """
    stop_bwm_ng(bwm_proc)
    for h in net.hosts:
        h.cmd("killall -9 iperf 2>/dev/null || true")
    info("*** [LIMPEZA] Processos residuais de iperf/bwm-ng finalizados.\n")


def run_guided_experiment(net, topology_name, routing_name, exp_num, transfer_size):
    """
    Executa o fluxo completo do Experimento 1 ou 2 de forma guiada,
    pausando e pedindo [ENTER] antes de cada etapa para inspeção manual.
    """
    bwm_log = get_bwm_log_path(topology_name, routing_name, exp_num)
    bwm_proc = None
    client_procs = []
    t_start = None

    try:
        info("\n" + "=" * 65 + "\n")
        info(f" MODO GUIADO PASSO A PASSO | Topo: {topology_name.upper()} | Rota: {routing_name.upper()} | Exp: {exp_num}\n")
        info("=" * 65 + "\n")

        input("\n[Passo 1/6] Pressione [ENTER] para executar net.pingAll() (descoberta de rotas)...")
        net.pingAll()

        input(f"\n[Passo 2/6] Pressione [ENTER] para iniciar o monitoramento bwm-ng ({bwm_log})...")
        bwm_proc = start_bwm_ng(bwm_log)

        input("\n[Passo 3/6] Pressione [ENTER] para subir servidores iperf -s em todos os hosts...")
        start_iperf_servers(net)

        input(f"\n[Passo 4/6] Pressione [ENTER] para iniciar o estresse todos-para-todos (bloco={transfer_size})...")
        client_procs, t_start = start_iperf_all_to_all(net, transfer_size=transfer_size)

        if exp_num == 2:
            sw1, sw2 = FAILURE_LINKS.get(topology_name.strip().lower(), ("s?", "s?"))
            input(
                f"\n[Passo 5/6 - EXP 2] Tráfego em curso! Quando quiser injetar a falha no enlace "
                f"{sw1} <-> {sw2}, pressione [ENTER]..."
            )
            trigger_link_failure(net, topology_name, status="down")
        else:
            info("\n[Passo 5/6 - EXP 1] Nenhuma falha programada (FCT Clássico).\n")

        input("\n[Passo 6/6] Pressione [ENTER] para aguardar o término dos fluxos e registrar o FCT...")
        check_or_wait_iperf(
            client_procs=client_procs,
            t_start=t_start,
            topology_name=topology_name,
            routing_name=routing_name,
            exp_num=exp_num,
            transfer_size=transfer_size,
            wait_all=True,
            already_recorded=False,
        )
    finally:
        cleanup_processes(net, bwm_proc)


def interactive_manual_menu(net, topology_name, routing_name, exp_num, transfer_size):
    """
    Menu interativo de controle manual para testar e validar cada etapa
    individualmente antes da automação completa.
    """
    bwm_log = get_bwm_log_path(topology_name, routing_name, exp_num)
    bwm_proc = None
    client_procs = []
    t_start = None
    fct_recorded = False
    fail_pair = FAILURE_LINKS.get(topology_name.strip().lower(), ("sw1", "sw2"))

    try:
        while True:
            bwm_status = "ATIVO" if (bwm_proc is not None and bwm_proc.poll() is None) else "PARADO"
            if client_procs:
                running = sum(1 for _, _, p in client_procs if p.poll() is None)
                iperf_status = f"{len(client_procs) - running}/{len(client_procs)} concluídos"
            else:
                iperf_status = "NÃO INICIADO"

            print("\n" + "=" * 68)
            print(
                f" CONTROLE MANUAL SDN | Topo: {topology_name.upper()} | "
                f"Rota: {routing_name.upper()} | Exp alvo: {exp_num} | Bloco: {transfer_size}"
            )
            print(f" Estado -> bwm-ng: [{bwm_status}] | Fluxos Iperf: [{iperf_status}]")
            print("=" * 68)
            print("  [1] Executar net.pingAll() (aquecimento e descoberta de rotas)")
            print(f"  [2] Iniciar/Parar bwm-ng em background ({os.path.basename(bwm_log)})")
            print("  [3] Iniciar servidores iperf (-s) em todos os hosts")
            print(f"  [4] Disparar estresse iperf Todos-para-Todos (bloco={transfer_size})")
            print(f"  [5] Derrubar enlace de núcleo ({fail_pair[0]} <-> {fail_pair[1]} DOWN)")
            print(f"  [6] Restaurar enlace de núcleo ({fail_pair[0]} <-> {fail_pair[1]} UP)")
            print("  [7] Checar status dos fluxos iperf (ou [7w] para aguardar todos e salvar FCT)")
            print("  [8] Executar Passo a Passo Guiado (pausando com ENTER a cada etapa)")
            print("  [9] Abrir CLI do Mininet (mininet>) para debug manual (ping, dpctl, etc.)")
            print("  [0] Limpar processos, encerrar Mininet e sair")
            print("=" * 68)

            choice = input("Escolha uma opção: ").strip().lower()

            if choice == "1":
                info(f"*** Executando net.pingAll() ({routing_name.upper()})...\n")
                net.pingAll()

            elif choice == "2":
                if bwm_proc is not None and bwm_proc.poll() is None:
                    stop_bwm_ng(bwm_proc)
                    bwm_proc = None
                else:
                    bwm_proc = start_bwm_ng(bwm_log)

            elif choice == "3":
                start_iperf_servers(net)

            elif choice == "4":
                client_procs, t_start = start_iperf_all_to_all(net, transfer_size=transfer_size)
                fct_recorded = False

            elif choice == "5":
                trigger_link_failure(net, topology_name, status="down")

            elif choice == "6":
                trigger_link_failure(net, topology_name, status="up")

            elif choice in ("7", "7w"):
                wait_flag = (choice == "7w")
                done = check_or_wait_iperf(
                    client_procs=client_procs,
                    t_start=t_start,
                    topology_name=topology_name,
                    routing_name=routing_name,
                    exp_num=exp_num,
                    transfer_size=transfer_size,
                    wait_all=wait_flag,
                    already_recorded=fct_recorded,
                )
                if done:
                    fct_recorded = True

            elif choice == "8":
                # Encerra captura manual eventual antes do modo guiado
                stop_bwm_ng(bwm_proc)
                bwm_proc = None
                run_guided_experiment(net, topology_name, routing_name, exp_num, transfer_size)

            elif choice == "9":
                info("*** Entrando na CLI do Mininet. Digite 'exit' para voltar ao menu manual.\n")
                CLI(net)

            elif choice == "0":
                break

            else:
                print("Opção inválida. Tente novamente.")
    finally:
        cleanup_processes(net, bwm_proc)


def run_network(
    topology_name,
    routing_name,
    exp_num=1,
    transfer_size="5M",
    controller_ip="127.0.0.1",
    controller_port=6633,
    batch=False,
):
    sync_routing_mode(routing_name)

    topo = get_topology(topology_name)

    info(f"*** Instanciando topologia '{topology_name.upper()}' com roteamento '{routing_name.upper()}'...\n")
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

    if batch:
        info(f"*** Executando net.pingAll() em modo batch ({routing_name.upper()})...\n")
        net.pingAll()
    else:
        interactive_manual_menu(
            net=net,
            topology_name=topology_name,
            routing_name=routing_name,
            exp_num=exp_num,
            transfer_size=transfer_size,
        )

    info("*** Encerrando a rede Mininet...\n")
    net.stop()


def validate_topology_arg(value):
    """
    Valida se o argumento de topologia corresponde a uma chave registrada em TOPOS
    (ex.: 'torus', 'spineleaf') ou a um caminho de arquivo de arestas válido.
    """
    if value.lower() in TOPOS or os.path.isfile(value):
        return value
    valid_choices = ", ".join(sorted(TOPOS.keys()))
    raise argparse.ArgumentTypeError(
        f"Topologia inválida '{value}'. Escolha entre {{{valid_choices}}} ou informe um arquivo existente."
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Orquestrador Mininet (Torus 3x3, Spine-Leaf ou arquivo de arestas) integrado ao controlador Ryu (OSPF/ECMP)."
    )
    parser.add_argument(
        "--topology",
        required=True,
        type=validate_topology_arg,
        metavar="{torus,spineleaf,<arquivo.txt>}",
        help="Topologia da rede a ser instanciada: 'torus', 'spineleaf' ou caminho de arquivo de arestas.",
    )
    parser.add_argument(
        "--routing",
        required=True,
        choices=["ecmp", "ospf"],
        help="Algoritmo de roteamento no controlador: 'ecmp' ou 'ospf'.",
    )
    parser.add_argument(
        "--exp",
        type=int,
        choices=[1, 2],
        default=1,
        help="Experimento alvo: 1 (FCT Clássico) ou 2 (Reação a Falhas). Padrão: 1.",
    )
    parser.add_argument(
        "--size",
        default="5M",
        help="Tamanho do bloco de dados por fluxo iperf todos-para-todos (ex: 5M, 20M, 50M). Padrão: 5M.",
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
        help="Executa apenas até o pingAll() e encerra sem abrir o menu manual (para testes rápidos).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    setLogLevel("info")
    args = parse_args()
    run_network(
        topology_name=args.topology,
        routing_name=args.routing,
        exp_num=args.exp,
        transfer_size=args.size,
        controller_ip=args.controller_ip,
        controller_port=args.controller_port,
        batch=args.batch,
    )
