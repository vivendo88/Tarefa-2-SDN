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
    Sincroniza o modo de roteamento com o controlador Ryu via arquivo temporário.

    Escreve a escolha ('ecmp' ou 'ospf') em um arquivo oculto chamado '.routing_mode'
    no mesmo diretório deste script. O aplicativo Ryu lê esse arquivo em tempo de 
    execução para saber qual lógica de encaminhamento deve ser aplicada.
    """
    mode_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".routing_mode")
    try:
        with open(mode_file, "w", encoding="utf-8") as f:
            f.write(routing.strip().lower())
    except OSError:
        pass


def run_network(topology_name, routing_name, controller_ip="127.0.0.1", controller_port=6633, batch=False):
    """
    Monta, inicializa e gerencia o ciclo de vida completo da rede virtual Mininet.

    Etapas executadas pela função:
    1. Sincroniza o algoritmo de roteamento selecionado (OSPF ou ECMP).
    2. Constrói os nós (hosts e switches OpenFlow 1.3) a partir da topologia especificada.
    3. Conecta a infraestrutura ao controlador Ryu remoto.
    4. Desativa o IPv6 em todos os nós para evitar tráfego de controle desnecessário (como ICMPv6/MLD).
    5. Dispara Gratuitous ARPs para que o controlador descubra a localização (porta/MAC/IP) de cada host.
    6. Executa um 'pingAll' inicial para popular as tabelas de fluxo e testar a conectividade básica.
    7. Abre o console interativo (CLI) do Mininet ou encerra automaticamente (modo batch/automação).
    """
    # 1. Informa ao controlador o modo de roteamento escolhido
    sync_routing_mode(routing_name)

    # 2. Carrega a instância da topologia (ex: Torus, Spine-Leaf ou de arquivo)
    topo = get_topology(topology_name)

    info(f"*** Instanciando topologia '{topology_name.upper()}' com roteamento '{routing_name.upper()}'...\n")
    
    # Força os switches Open vSwitch a utilizarem a versão OpenFlow 1.3
    switch_of13 = partial(OVSKernelSwitch, protocols="OpenFlow13")
    net = Mininet(
        topo=topo,
        controller=None,
        switch=switch_of13,
        autoSetMacs=False
    )
    
    # Conecta ao controlador Ryu externo
    net.addController(
        "c0",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port
    )

    # Inicializa os switches e links
    net.start()

    # Desativa IPv6 em todos os nós para reduzir ruído de broadcast e tráfego excedente
    info("*** Desabilitando tráfego IPv6 em todos os hosts e switches...\n")
    for node in net.hosts + net.switches:
        node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
        node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
        node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")

    # Pausa para que os pacotes LLDP mapeiem os links entre switches no Ryu
    info("*** Aguardando 3s para sincronização do LLDP e descoberta de enlaces no Ryu...\n")
    time.sleep(3)

    # Envia pacotes ARP voluntários (Gratuitous ARP) para que o Ryu popule sua tabela de hosts conhecidos
    info("*** Enviando Gratuitous ARP inicial de todos os hosts para registro no Ryu...\n")
    for h in net.hosts:
        iface = h.defaultIntf().name
        h.cmd(f"arping -U -c 1 -I {iface} {h.IP()}")
        time.sleep(0.1)

    time.sleep(1)

    # Teste inicial de conectividade global (aquece as regras de fluxo nos switches)
    info(f"*** Executando net.pingAll() para aquecimento da rede ({routing_name.upper()})...\n")
    net.pingAll()

    # Se não for execução em lote (batch), entrega o controle ao terminal interativo
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

    # Limpa as interfaces virtuais e desliga a rede ao encerrar
    info("*** Encerrando a rede Mininet...\n")
    net.stop()


def validate_topology_arg(value):
    """
    Função de validação customizada para o argparse.

    Verifica se o valor informado pelo usuário no parâmetro --topology corresponde
    a uma topologia pré-definida no dicionário TOPOS (ex.: 'torus', 'spineleaf')
    ou a um caminho válido de arquivo de texto contendo a definição das arestas.
    Lança ArgumentTypeError caso o valor seja inválido.
    """
    if value.lower() in TOPOS or os.path.isfile(value):
        return value
    valid_choices = ", ".join(sorted(TOPOS.keys()))
    raise argparse.ArgumentTypeError(
        f"Topologia inválida '{value}'. Escolha entre {{{valid_choices}}} ou informe um arquivo existente."
    )


def parse_args():
    """
    Configura e processa os argumentos de linha de comando da aplicação.

    Define flags obrigatórias (--topology, --routing) e opcionais (--controller-ip,
    --controller-port, --batch), retornando o objeto contendo os valores parseados.
    """
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
