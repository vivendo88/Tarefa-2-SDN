#!/usr/bin/env python3
"""
Controlador SDN Ryu com Descoberta Dinâmica de Topologia (LLDP + ARP),
Grafo Direcionado NetworkX (nx.DiGraph) e Roteamento OSPF / ECMP.
"""

import os
import sys
import networkx as nx

from ryu import cfg
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event
from ryu.topology.api import get_switch, get_link
from ryu.lib.packet import packet, ethernet, ether_types, arp, ipv4

from ospf import get_route_ospf
from ecmp import get_route_ecmp, compute_ecmp_route

MODE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".routing_mode")

def get_current_routing_mode(default_mode="ospf"):
    """
    Lê dinamicamente o algoritmo de roteamento definido pelo fctmain.py.
    """
    if os.path.exists(MODE_FILE):
        try:
            with open(MODE_FILE, "r", encoding="utf-8") as f:
                mode = f.read().strip().lower()
                if mode in ["ecmp", "ospf"]:
                    return mode
        except OSError:
            pass
    return default_mode


# Registro de flag de linha de comando (--routing ospf|ecmp) no Ryu CONF
CONF = cfg.CONF
if 'routing' not in CONF:
    try:
        CONF.register_cli_opts([
            cfg.StrOpt(
                'routing',
                default=os.environ.get('ROUTING_ALGO', ''),
                help='Algoritmo de roteamento SDN: ospf ou ecmp'
            ),
        ])
    except Exception:
        pass


class TopoDiscover(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    ROUTING_COOKIE = 0x100
    ROUTING_COOKIE_MASK = 0xFFFFFFFFFFFFFFFF
    FLOW_PRIORITY = 10

    def __init__(self, *args, **kwargs):
        super(TopoDiscover, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.net = nx.DiGraph()
        self.datapaths = {}
        self.switches = []
        self.links = []
        self.switch_ports = set()       # Portas inter-switch ativas: {(dpid, port_no)}
        self.ever_switch_ports = set()  # Histórico de portas inter-switch: {(dpid, port_no)}
        self.hosts = {}                 # {ip: {'mac': mac, 'dpid': dpid, 'port': port}}
        self.installed_paths = {}       # {(src_ip, dst_ip): tuple(path)}

        self.cli_routing = getattr(CONF, 'routing', '') or os.environ.get('ROUTING_ALGO', '')
        self.routing_mode = self._resolve_routing_mode()
        self.logger.info(
            "Controlador TopoDiscover inicializado | Algoritmo de roteamento: %s",
            self.routing_mode.upper()
        )

    def _resolve_routing_mode(self):
        """
        Determina o algoritmo de roteamento ativo ('ospf' ou 'ecmp').
        Prioriza a flag de CLI/ambiente do Ryu; caso não informada, lê o arquivo
        de sincronização '.routing_mode' gerado pelo fctmain.py (padrão: 'ospf').
        """
        if self.cli_routing and self.cli_routing.strip().lower() in ('ospf', 'ecmp'):
            return self.cli_routing.strip().lower()

        mode_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.routing_mode')
        if os.path.exists(mode_file):
            try:
                with open(mode_file, 'r', encoding='utf-8') as f:
                    val = f.read().strip().lower()
                if val in ('ospf', 'ecmp'):
                    return val
            except OSError:
                pass
        return 'ospf'

    # =========================================================================
    # GERENCIAMENTO DE DATAPATHS E TABLE-MISS (OpenFlow 1.3)
    # =========================================================================

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id is not None:
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id is not None and datapath.id in self.datapaths:
                del self.datapaths[datapath.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Regra Table-Miss (prioridade 0): envia pacotes desconhecidos ao controlador
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=0,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)

    # =========================================================================
    # DESCOBERTA DINÂMICA DE TOPOLOGIA (Switch Enter/Leave, Link Add/Delete)
    # =========================================================================

    def _sync_topology_graph(self):
        """
        Sincroniza a estrutura do grafo direcionado self.net (nx.DiGraph)
        com os switches e enlaces descobertos via ryu.topology.api.
        """
        switch_list = get_switch(self.topology_api_app, None)
        self.switches = sorted([sw.dp.id for sw in switch_list])
        for sw in switch_list:
            self.datapaths[sw.dp.id] = sw.dp

        link_list = get_link(self.topology_api_app, None)
        self.links = sorted([
            (link.src.dpid, link.dst.dpid, link.src.port_no, link.dst.port_no)
            for link in link_list
        ])

        # Atualiza conjunto de portas inter-switch
        current_sw_ports = set()
        for src_dpid, dst_dpid, src_port, dst_port in self.links:
            current_sw_ports.add((src_dpid, src_port))
            current_sw_ports.add((dst_dpid, dst_port))
        self.switch_ports = current_sw_ports
        self.ever_switch_ports |= current_sw_ports

        # Reconstrói o grafo direcionado nx.DiGraph()
        new_graph = nx.DiGraph()
        for dpid in self.switches:
            new_graph.add_node(dpid)

        for src_dpid, dst_dpid, src_port, dst_port in self.links:
            new_graph.add_edge(
                src_dpid,
                dst_dpid,
                port=src_port,
                src_port=src_port,
                dst_port=dst_port
            )

        self.net = new_graph

        # Remove hosts eventualmente associados a portas inter-switch ou switches inativos
        self.hosts = {
            ip: info for ip, info in self.hosts.items()
            if info['dpid'] in self.switches
            and (info['dpid'], info['port']) not in self.ever_switch_ports
        }

    def _clear_routing_flows(self):
        """
        Remove todas as regras de fluxo de roteamento (identificadas por ROUTING_COOKIE)
        em todos os switches ativos para forçar o recálculo dinâmico de rotas após
        alterações na topologia (queda/adição de enlaces ou switches).
        """
        self.installed_paths.clear()
        for dpid, datapath in list(self.datapaths.items()):
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            mod = parser.OFPFlowMod(
                datapath=datapath,
                cookie=self.ROUTING_COOKIE,
                cookie_mask=self.ROUTING_COOKIE_MASK,
                table_id=ofproto.OFPTT_ALL,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPP_ANY,
                match=parser.OFPMatch()
            )
            datapath.send_msg(mod)

    @set_ev_cls(event.EventSwitchEnter)
    def _event_switch_enter_handler(self, ev):
        self._sync_topology_graph()
        self.logger.info("Switches mapeados atualmente: %s", self.switches)

    @set_ev_cls(event.EventSwitchLeave)
    def _event_switch_leave_handler(self, ev):
        dpid = ev.switch.dp.id
        self.ever_switch_ports = {
            (sw, pt) for (sw, pt) in self.ever_switch_ports if sw != dpid
        }
        self._sync_topology_graph()
        self._clear_routing_flows()
        self.logger.warning(
            "[TOPOLOGIA] Switch s%s saiu da rede. Fluxos limpos para reconvergência. Switches ativos: %s",
            dpid, self.switches
        )

    @set_ev_cls(event.EventLinkAdd)
    def _event_link_add_handler(self, ev):
        link = ev.link
        self._sync_topology_graph()
        self.logger.info(
            "Enlace adicionado: s%s (porta %s) -> s%s (porta %s) | Total de enlaces direcionados: %d",
            link.src.dpid, link.src.port_no, link.dst.dpid, link.dst.port_no, len(self.links)
        )

    @set_ev_cls(event.EventLinkDelete)
    def _event_link_delete_handler(self, ev):
        link = ev.link
        self._sync_topology_graph()
        # Garante a remoção de ambas as direções do enlace que caiu no grafo DiGraph
        if self.net.has_edge(link.src.dpid, link.dst.dpid):
            self.net.remove_edge(link.src.dpid, link.dst.dpid)
        if self.net.has_edge(link.dst.dpid, link.src.dpid):
            self.net.remove_edge(link.dst.dpid, link.src.dpid)

        self._clear_routing_flows()
        self.logger.warning(
            "[FALHA DE ENLACE] Enlace removido: s%s (porta %s) <-> s%s (porta %s) | "
            "Fluxos invalidados para reconvergência dinâmica (%d enlaces ativos).",
            link.src.dpid, link.src.port_no, link.dst.dpid, link.dst.port_no, len(self.links)
        )

    # =========================================================================
    # ALGORITMOS DE ROTEAMENTO: OSPF E ECMP
    # =========================================================================

    def get_route(self, switch_in, switch_out):
        """
        Calcula a rota entre switch_in e switch_out no grafo direcionado self.net
        utilizando o algoritmo selecionado (OSPF ou ECMP) importado dos módulos
        dedicados `ospf.py` e `ecmp.py`.
        """
        self.routing_mode = self._resolve_routing_mode()

        if self.routing_mode == 'ecmp':
            return compute_ecmp_route(self.net, switch_in, switch_out)
        return get_route_ospf(self.net, switch_in, switch_out)

    # =========================================================================
    # INSTALAÇÃO DE FLUXOS E ENCAMINHAMENTO DE PACOTES
    # =========================================================================

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            cookie=self.ROUTING_COOKIE,
            priority=priority,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)

    def install_path_flows(self, path, src_ip, dst_ip):
        """
        Programa as regras OpenFlow (OFPFlowMod) em cada switch ao longo do caminho
        calculado (em ordem reversa, do destino para a origem, evitando perda em trânsito).
        """
        for i in reversed(range(len(path))):
            sw_dpid = path[i]
            datapath = self.datapaths.get(sw_dpid)
            if datapath is None:
                continue

            if i < len(path) - 1:
                next_dpid = path[i + 1]
                out_port = self.net[sw_dpid][next_dpid]['src_port']
            else:
                out_port = self.hosts[dst_ip]['port']

            parser = datapath.ofproto_parser
            actions = [parser.OFPActionOutput(out_port)]

            # Regra para tráfego IPv4 entre (src_ip, dst_ip)
            match_ip = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip
            )
            self.add_flow(datapath, self.FLOW_PRIORITY, match_ip, actions)

            # Regra para tráfego ARP unicast/renovação entre (src_ip, dst_ip)
            match_arp = parser.OFPMatch(
                eth_type=ether_types.ETH_TYPE_ARP,
                arp_spa=src_ip,
                arp_tpa=dst_ip
            )
            self.add_flow(datapath, self.FLOW_PRIORITY, match_arp, actions)

    def _flood_to_edge_ports(self, msg):
        """
        Envia pacotes ARP de descoberta apenas para as portas de borda (conectadas a hosts)
        de todos os switches, evitando loops de broadcast em topologias com ciclos
        como Torus e Spine-Leaf.
        """
        in_dpid = msg.datapath.id
        in_port = msg.match['in_port']
        switch_list = get_switch(self.topology_api_app, None)

        for sw in switch_list:
            dp = sw.dp
            ofproto = dp.ofproto
            parser = dp.ofproto_parser
            actions = []

            for port in sw.ports:
                if port.is_reserved():
                    continue
                port_no = port.port_no
                # Ignora portas entre switches e a própria porta de entrada original
                if (dp.id, port_no) in self.ever_switch_ports or (dp.id, port_no) in self.switch_ports:
                    continue
                if dp.id == in_dpid and port_no == in_port:
                    continue
                actions.append(parser.OFPActionOutput(port_no))

            if actions:
                out = parser.OFPPacketOut(
                    datapath=dp,
                    buffer_id=ofproto.OFP_NO_BUFFER,
                    in_port=ofproto.OFPP_CONTROLLER,
                    actions=actions,
                    data=msg.data
                )
                dp.send_msg(out)

    def _register_host(self, src_ip, src_mac, dpid, in_port):
        """
        Registra um host descoberto se o pacote chegou por uma porta de borda (não inter-switch).
        Retorna True se um novo host foi descoberto ou atualizado.
        """
        if not src_ip or src_ip == '0.0.0.0':
            return False

        if (dpid, in_port) in self.switch_ports or (dpid, in_port) in self.ever_switch_ports:
            return False

        existing = self.hosts.get(src_ip)
        if existing is not None:
            # Se o host já foi aprendido em um switch ativo, não sobrescreve caso o pacote
            # tenha sido interceptado em um switch intermediário durante reconvergência
            if existing['dpid'] in self.switches and existing['dpid'] != dpid:
                return False
            if existing['dpid'] == dpid and existing['port'] == in_port and existing['mac'] == src_mac:
                return False

        # Remove registros antigos na mesma porta de borda deste switch
        self.hosts = {
            ip: info for ip, info in self.hosts.items()
            if not (info['dpid'] == dpid and info['port'] == in_port) and info['dpid'] in self.switches
        }

        self.hosts[src_ip] = {
            'mac': src_mac,
            'dpid': dpid,
            'port': in_port
        }
        return True

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        # Ignora quadros LLDP (usados pela descoberta de topologia) e IPv6
        if eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
            return

        arp_pkt = pkt.get_protocol(arp.arp)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        src_ip = None
        dst_ip = None

        if arp_pkt:
            src_ip = arp_pkt.src_ip
            dst_ip = arp_pkt.dst_ip
            is_new = self._register_host(src_ip, arp_pkt.src_mac, dpid, in_port)
            if is_new:
                self.logger.info(
                    "Host descoberto via ARP: IP %s | MAC %s | Switch s%s (porta %s)",
                    src_ip, arp_pkt.src_mac, dpid, in_port
                )
                if len(self.hosts) >= len(self.switches) and len(self.switches) > 0:
                    self.print_full_topology()
                    self.salvar_grafo_descoberto()
                # Se for Gratuitous ARP (src_ip == dst_ip), não precisa instalar rota unicast
                if src_ip == dst_ip:
                    return
        elif ip_pkt:
            src_ip = ip_pkt.src
            dst_ip = ip_pkt.dst
            self._register_host(src_ip, eth.src, dpid, in_port)
        else:
            return

        # Caso o destino ainda não seja conhecido, faz flood controlado apenas nas portas de borda
        if dst_ip not in self.hosts:
            if arp_pkt:
                self._flood_to_edge_ports(msg)
            return

        if src_ip not in self.hosts:
            return

        switch_in = self.hosts[src_ip]['dpid']
        switch_out = self.hosts[dst_ip]['dpid']

        path = self.get_route(switch_in, switch_out)
        if not path:
            self.logger.warning(
                "[ROTEAMENTO %s] Sem caminho disponível entre s%s (%s) e s%s (%s)!",
                self.routing_mode.upper(), switch_in, src_ip, switch_out, dst_ip
            )
            return

        path_tuple = tuple(path)
        if self.installed_paths.get((src_ip, dst_ip)) != path_tuple:
            self.installed_paths[(src_ip, dst_ip)] = path_tuple
            path_str = " -> ".join(f"s{sw}" for sw in path)
            self.logger.info(
                "[ROTEAMENTO %s] %s (s%s) -> %s (s%s) | Caminho: [%s] (%d saltos)",
                self.routing_mode.upper(),
                src_ip, switch_in,
                dst_ip, switch_out,
                path_str, max(0, len(path) - 1)
            )

        # Instala os fluxos OpenFlow em todos os switches do caminho
        self.install_path_flows(path, src_ip, dst_ip)

        # Entrega imediata do pacote atual para evitar perda durante a instalação do fluxo
        if dpid in path:
            idx = path.index(dpid)
            if idx < len(path) - 1:
                out_port = self.net[dpid][path[idx + 1]]['src_port']
            else:
                out_port = self.hosts[dst_ip]['port']
            target_dp = datapath
        else:
            target_dp = self.datapaths.get(switch_out)
            out_port = self.hosts[dst_ip]['port']

        if target_dp is not None:
            ofproto = target_dp.ofproto
            parser = target_dp.ofproto_parser
            actions = [parser.OFPActionOutput(out_port)]
            out = parser.OFPPacketOut(
                datapath=target_dp,
                buffer_id=ofproto.OFP_NO_BUFFER,
                in_port=ofproto.OFPP_CONTROLLER,
                actions=actions,
                data=msg.data
            )
            target_dp.send_msg(out)

    # =========================================================================
    # VISUALIZAÇÃO E LOG DA TOPOLOGIA
    # =========================================================================

    def salvar_grafo_descoberto(self, nome_arquivo=None):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        if nome_arquivo is None:
            env_output = os.environ.get("TOPO_OUTPUT_PNG")
            if env_output:
                nome_arquivo = env_output
            elif len(self.switches) == 3:
                nome_arquivo = "topologia_triangulo_descoberta.png"
            elif len(self.switches) == 6:
                nome_arquivo = "topologia_someloops_descoberta.png"
            elif len(self.switches) == 8:
                nome_arquivo = "topologia_spineleaf_descoberta.png"
            elif len(self.switches) == 9:
                nome_arquivo = "topologia_torus_descoberta.png"
            else:
                nome_arquivo = "topologia_descoberta.png"

        G = nx.Graph()

        for dpid in self.switches:
            G.add_node(f"s{dpid}", tipo='switch')

        for src_dpid, dst_dpid, _, _ in self.links:
            G.add_edge(f"s{src_dpid}", f"s{dst_dpid}")

        for ip, info in self.hosts.items():
            host_label = f"Host\n{ip}"
            sw_label = f"s{info['dpid']}"
            G.add_node(host_label, tipo='host')
            G.add_edge(host_label, sw_label)

        cores = [
            'lightblue' if G.nodes[n].get('tipo') == 'switch' else 'lightgreen'
            for n in G.nodes()
        ]

        plt.figure(figsize=(8, 6))
        pos = nx.spring_layout(G, seed=42)
        nx.draw(
            G, pos,
            with_labels=True,
            node_color=cores,
            node_size=1000,
            font_size=8,
            font_weight='bold',
            edge_color='gray'
        )
        plt.title(f"Topologia Descoberta (Ryu - {self.routing_mode.upper()})")
        plt.savefig(nome_arquivo, bbox_inches='tight')
        plt.close()
        self.logger.info("Imagem do grafo salva em: %s", nome_arquivo)

    def print_full_topology(self):
        self.logger.info("\n" + "=" * 55)
        self.logger.info(
            "       TOPOLOGIA COMPLETA MAPEADA (MODO: %s)",
            self.routing_mode.upper()
        )
        self.logger.info("=" * 55)

        self.logger.info("[SWITCHES ATIVOS]")
        for dpid in sorted(self.switches):
            self.logger.info(" -> Switch DPID: %s (s%d)", dpid, dpid)

        self.logger.info("\n[ENLACES ENTRE SWITCHES]")
        for src_dpid, dst_dpid, src_port, dst_port in self.links:
            self.logger.info(
                " -> Switch %s (porta %s) <---> Switch %s (porta %s)",
                src_dpid, src_port, dst_dpid, dst_port
            )

        self.logger.info("\n[HOSTS CONECTADOS]")
        for ip, info in sorted(self.hosts.items()):
            self.logger.info(
                " -> IP: %s | MAC: %s | Conectado ao Switch: %s (porta %s)",
                ip, info['mac'], info['dpid'], info['port']
            )

        self.logger.info("=" * 55 + "\n")


if __name__ == '__main__':
    import argparse
    from ryu.cmd.manager import main as ryu_main

    parser = argparse.ArgumentParser(
        description='Inicia o controlador Ryu TopoDiscover com suporte a OSPF e ECMP.'
    )
    parser.add_argument(
        '--routing',
        choices=['ospf', 'ecmp'],
        default='ospf',
        help='Algoritmo de roteamento a ser utilizado pelo controlador (ospf ou ecmp).'
    )
    args, unknown = parser.parse_known_args()

    os.environ['ROUTING_ALGO'] = args.routing
    mode_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.routing_mode')
    try:
        with open(mode_file, 'w', encoding='utf-8') as f:
            f.write(args.routing)
    except OSError:
        pass

    sys.argv = [
        'ryu-manager',
        '--observe-links',
        '--user-flags', os.path.abspath(__file__),
        '--routing', args.routing,
        *unknown,
        os.path.abspath(__file__)
    ]
    ryu_main()
