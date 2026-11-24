#!/usr/bin/env python3
"""Orquestrador Mininet enxuto (fctmain.py) com fluxo sequencial manual/automático da Parte 2."""

import argparse
import csv
from functools import partial
import os
import shutil
import subprocess
import time

from mininet.cli import CLI
from mininet.log import info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from topologies import get_topology

FAILURE_LINKS = {"torus": ("s4", "s5"), "spineleaf": ("s3", "s1")}


def step_pause(net, msg, auto=False):
    """Exibe a etapa atual e aguarda [ENTER] (ou 'cli' para debug) no modo manual."""
    info(f"\n>>> {msg}\n")
    if auto:
        return
    while True:
        resp = input("  [ENTER] avançar | digite 'cli' para abrir o Mininet: ").strip().lower()
        if resp == "cli":
            CLI(net)
        else:
            break


def save_fct(topo, routing, exp, size, fct_seconds):
    """Salva o tempo total de conclusão de fluxos (FCT) em logs/fct_results.csv."""
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", "fct_results.csv")
    exists = os.path.isfile(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["topology", "routing", "exp", "size", "fct_seconds"])
        w.writerow([topo, routing, exp, size, f"{fct_seconds:.4f}"])
    info(f"*** [FCT] Total: {fct_seconds:.2f}s (salvo em {path})\n")


def run_experiment(args):
    # Sincroniza o algoritmo com o controlador Ryu
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".routing_mode"), "w") as f:
        f.write(args.routing)

    # 1. Inicialização: instancia a topologia e desabilita IPv6
    sw = partial(OVSKernelSwitch, protocols="OpenFlow13")
    net = Mininet(topo=get_topology(args.topology), controller=None, switch=sw, autoSetMacs=False)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6633)
    net.start()

    for node in net.hosts + net.switches:
        node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1")

    time.sleep(3)
    for h in net.hosts:
        h.cmd(f"arping -U -c 1 -I {h.defaultIntf().name} {h.IP()}")
        time.sleep(0.1)

    bwm_proc = None
    try:
        # 2. Descoberta: pingAll() para o controlador aprender todas as rotas
        step_pause(net, "Passo 2: Executar net.pingAll() para descoberta inicial", args.auto)
        net.pingAll()

        # 3. Monitoramento: bwm-ng em background
        os.makedirs("logs", exist_ok=True)
        bwm_log = f"logs/bwm_{os.path.basename(args.topology)}_{args.routing}_exp{args.exp}.csv"
        step_pause(net, f"Passo 3: Iniciar bwm-ng em background ({bwm_log})", args.auto)
        if shutil.which("bwm-ng"):
            bwm_proc = subprocess.Popen(
                ["bwm-ng", "-o", "csv", "-c", "0", "-t", "500", "-u", "bytes", "-T", "rate", "-F", bwm_log],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

        # 4. Estresse (Iperf): servidores + clientes todos-para-todos
        step_pause(net, f"Passo 4: Subir servidores Iperf e disparar estresse todos-para-todos ({args.size})", args.auto)
        for h in net.hosts:
            h.cmd("iperf -s -p 5001 > /dev/null 2>&1 &")
        time.sleep(0.5)

        t_start = time.time()
        procs = [
            src.popen(["iperf", "-c", dst.IP(), "-p", "5001", "-n", args.size], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for src in net.hosts for dst in net.hosts if src != dst
        ]
        info(f"*** {len(procs)} fluxos Iperf disparados.\n")

        # 5. Execução do Experimento 1 (FCT) ou Experimento 2 (Falha de Enlace)
        if args.exp == 2 and args.topology.lower() in FAILURE_LINKS:
            u, v = FAILURE_LINKS[args.topology.lower()]
            if args.auto:
                time.sleep(args.fail_delay)
            else:
                step_pause(net, f"Passo 5 (EXP 2): Pressione [ENTER] para derrubar o enlace {u} <-> {v}", False)
            info(f"*** Derrubando enlace {u} <-> {v}...\n")
            net.configLinkStatus(u, v, "down")

        info("*** Aguardando conclusão de todos os fluxos Iperf...\n")
        for p in procs:
            p.wait()
        save_fct(args.topology, args.routing, args.exp, args.size, time.time() - t_start)

        if not args.auto:
            step_pause(net, "Passo 6: Finalizado! Digite 'cli' se quiser inspecionar ou [ENTER] para encerrar", False)
    finally:
        # 6. Finalização: encerrar bwm-ng, iperf e parar o Mininet
        if bwm_proc and bwm_proc.poll() is None:
            bwm_proc.terminate()
            bwm_proc.wait()
        for h in net.hosts:
            h.cmd("killall -9 iperf 2>/dev/null || true")
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    parser = argparse.ArgumentParser(description="Experimentos SDN Mininet (Parte 2).")
    parser.add_argument("--topology", required=True, help="torus, spineleaf ou arquivo .txt")
    parser.add_argument("--routing", required=True, choices=["ospf", "ecmp"], help="Algoritmo de roteamento")
    parser.add_argument("--exp", type=int, choices=[1, 2], default=1, help="Experimento 1 (FCT) ou 2 (Falha)")
    parser.add_argument("--size", default="5M", help="Tamanho por fluxo Iperf (padrão: 5M)")
    parser.add_argument("--fail-delay", type=int, default=10, help="Segundos antes da falha no modo --auto (padrão: 10)")
    parser.add_argument("--auto", "--batch", dest="auto", action="store_true", help="Executa sem pausas manuais")
    run_experiment(parser.parse_args())
