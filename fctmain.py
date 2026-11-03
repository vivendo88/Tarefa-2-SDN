#!/usr/bin/env python3
"""
Orquestrador Mininet (fctmain.py) para instanciação das topologias a partir do
módulo dedicado `topologies.py` e integração com o controlador Ryu (OSPF/ECMP).
"""

import argparse
import os
import time
from functools import partial

from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info

from topologies import TOPOS, get_topology


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


def run_network(topology_name, routing_name, controller_ip="127.0.0.1", controller_port=6633, batch=False):
    sync_routing_mode(routing_name)

    topo = get_topology(topology_name)

    info(f"*** Instanciando topologia '{topology_name.upper()}' com roteamento '{routing_name.upper()}'...\n")
    switch_of13 = partial(OVSKernelSwitch, protocols="OpenFlow13")
    net = Mininet(
        topo=topo,
        controller=None,
        switch=switch_of13,
        autoSetMacs=False
    )
    net.addController(
        "c0",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port
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

    info(f"*** Executando net.pingAll() para aquecimento da rede ({routing_name.upper()})...\n")
    net.pingAll()

    if not batch:
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
        help="Topologia da rede a ser instanciada: 'torus', 'spineleaf' ou caminho de arquivo de arestas."
    )
    parser.add_argument(
        "--routing",
        required=True,
        choices=["ecmp", "ospf"],
        help="Algoritmo de roteamento no controlador: 'ecmp' ou 'ospf'."
    )
    parser.add_argument(
        "--controller-ip",
        default="127.0.0.1",
        help="Endereço IP do controlador Ryu remoto (padrão: 127.0.0.1)."
    )
    parser.add_argument(
        "--controller-port",
        type=int,
        default=6633,
        help="Porta OpenFlow do controlador Ryu remoto (padrão: 6633)."
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Executa apenas até o pingAll() e encerra sem abrir a CLI interativa (para testes automatizados)."
    )
    return parser.parse_args()


if __name__ == "__main__":
    setLogLevel("info")
    args = parse_args()
    run_network(
        topology_name=args.topology,
        routing_name=args.routing,
        controller_ip=args.controller_ip,
        controller_port=args.controller_port,
        batch=args.batch
    )
