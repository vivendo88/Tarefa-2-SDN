#!/usr/bin/env python3
"""Controlador SDN Ryu: Descoberta de Topologia, OSPF/ECMP e Tolerância a Falhas."""

import os
import sys
import networkx as nx

from ryu import cfg
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib.packet import arp, ether_types, ethernet, ipv4, packet
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event
from ryu.topology.api import get_link, get_switch

from ecmp import compute_ecmp_route
from ospf import get_route_ospf

CONF = cfg.CONF
if "routing" not in CONF:
    try:
        CONF.register_cli_opts([cfg.StrOpt("routing", default="", help="ospf ou ecmp")])
    except Exception:
        pass


class TopoDiscover(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    ROUTING_COOKIE = 0x100

    def __init__(self, *args, **kwargs):
        super(TopoDiscover, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.net = nx.DiGraph()
        self.datapaths = {}
        self.switches = []
        self.links = []
        self.switch_ports = set()   # {(dpid, port_no)} das portas entre switches
        self.hosts = {}             # {ip: {'mac': mac, 'dpid': dpid, 'port': port}}
        self.installed_paths = {}   # {(src_ip, dst_ip): tuple(path)}
        self.routing_mode = self._resolve_routing_mode()
        self.logger.info("Controlador iniciado | Algoritmo: %s", self.routing_mode.upper())

    def _resolve_routing_mode(self):
        cli_val = getattr(CONF, "routing", "") or os.environ.get("ROUTING_ALGO", "")
        if cli_val.strip().lower() in ("ospf", "ecmp"):
            return cli_val.strip().lower()
        mode_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".routing_mode")
        if os.path.exists(mode_file):
            with open(mode_file, "r", encoding="utf-8") as f:
                val = f.read().strip().lower()
                if val in ("ospf", "ecmp"):
                    return val
        return "ospf"

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Instala a regra Table-Miss (prioridade 0) ao conectar cada switch."""
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        ofp, parser = dp.ofproto, dp.ofproto_parser
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0, match=parser.OFPMatch(), instructions=inst))

    def _sync_topology_graph(self):
        """Atualiza switches, enlaces, portas inter-switch e o grafo NetworkX."""
        switch_list = get_switch(self.topology_api_app, None)
        self.switches = sorted(sw.dp.id for sw in switch_list)
        for sw in switch_list:
            self.datapaths[sw.dp.id] = sw.dp

        link_list = get_link(self.topology_api_app, None)
        self.links = sorted((l.src.dpid, l.dst.dpid, l.src.port_no, l.dst.port_no) for l in link_list)

        self.switch_ports = set()
        self.net = nx.DiGraph()
        for dpid in self.switches:
            self.net.add_node(dpid)
        for src, dst, src_port, dst_port in self.links:
            self.switch_ports.add((src, src_port))
            self.switch_ports.add((dst, dst_port))
            self.net.add_edge(src, dst, src_port=src_port, dst_port=dst_port)

    def _clear_routing_flows(self):
        """Remove os fluxos de roteamento (OFPFC_DELETE) para forçar recálculo de rotas."""
        self.installed_paths.clear()
        for dp in list(self.datapaths.values()):
            ofp, parser = dp.ofproto, dp.ofproto_parser
            mod = parser.OFPFlowMod(
                datapath=dp,
                cookie=self.ROUTING_COOKIE,
                cookie_mask=0xFFFFFFFFFFFFFFFF,
                table_id=ofp.OFPTT_ALL,
                command=ofp.OFPFC_DELETE,
                out_port=ofp.OFPP_ANY,
                out_group=ofp.OFPP_ANY,
                match=parser.OFPMatch(),
            )
            dp.send_msg(mod)

    @set_ev_cls(event.EventSwitchEnter)
    def _event_switch_enter_handler(self, ev):
        self._sync_topology_graph()
        self.logger.info("Switches ativos: %s", self.switches)

    @set_ev_cls(event.EventLinkAdd)
    def _event_link_add_handler(self, ev):
        self._sync_topology_graph()
        self.logger.info("Enlace adicionado (%d enlaces direcionados).", len(self.links))

    @set_ev_cls(event.EventLinkDelete)
    def _event_link_delete_handler(self, ev):
        u, v = ev.link.src.dpid, ev.link.dst.dpid
        if self.net.has_edge(u, v) or self.net.has_edge(v, u):
            self._sync_topology_graph()
            self._clear_routing_flows()

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status_handler(self, ev):
        """Detecta queda/subida de porta imediatamente, atualiza o grafo e limpa fluxos."""
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        port_no = msg.desc.port_no
        is_down = (
            msg.reason == ofp.OFPPR_DELETE
            or bool(msg.desc.state & ofp.OFPPS_LINK_DOWN)
            or bool(msg.desc.config & ofp.OFPPC_PORT_DOWN)
        )

        if is_down:
            vizinho = None
            if self.net.has_node(dp.id):
                for succ in list(self.net.successors(dp.id)):
                    if self.net[dp.id][succ].get("src_port") == port_no:
                        vizinho = succ
                        break
            if vizinho is not None:
                self.net.remove_edges_from([e for e in [(dp.id, vizinho), (vizinho, dp.id)] if self.net.has_edge(*e)])
                self.links = [l for l in self.links if not ((l[0] == dp.id and l[1] == vizinho) or (l[0] == vizinho and l[1] == dp.id))]
                self.logger.warning("[PORT STATUS DOWN] Enlace s%d <-> s%d caiu (porta %s). Fluxos limpos!", dp.id, vizinho, port_no)
            self._clear_routing_flows()
        else:
            self.logger.info("[PORT STATUS UP] Porta %s no switch s%d ativada.", port_no, dp.id)
            self._sync_topology_graph()
            self._clear_routing_flows()

    def find_route(self, switch_in, switch_out, method=None):
        """Calcula a rota usando OSPF ou ECMP."""
        algo = (method or self._resolve_routing_mode()).lower()
        self.routing_mode = algo
        if algo == "ecmp":
            return compute_ecmp_route(self.net, switch_in, switch_out)
        return get_route_ospf(self.net, switch_in, switch_out)

    def add_flow(self, datapath, priority, match, actions):
        """Envia uma regra OpenFlow 1.3 para o switch."""
        ofp, parser = datapath.ofproto, datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, cookie=self.ROUTING_COOKIE,
            priority=priority, match=match, instructions=inst,
        )
        datapath.send_msg(mod)

    def install_path(self, path, src_ip, dst_ip):
        """Instala regras de fluxo IPv4 e ARP em todos os switches do caminho."""
        for i in reversed(range(len(path))):
            sw = path[i]
            dp = self.datapaths.get(sw)
            if not dp:
                continue
            out_port = self.net[sw][path[i + 1]]["src_port"] if i < len(path) - 1 else self.hosts[dst_ip]["port"]
            parser = dp.ofproto_parser
            actions = [parser.OFPActionOutput(out_port)]
            self.add_flow(dp, 10, parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP, ipv4_src=src_ip, ipv4_dst=dst_ip), actions)
            self.add_flow(dp, 10, parser.OFPMatch(eth_type=ether_types.ETH_TYPE_ARP, arp_spa=src_ip, arp_tpa=dst_ip), actions)

    def _flood_to_edge_ports(self, msg):
        """Envia ARP Request apenas pelas portas de borda (evita loops)."""
        for sw in get_switch(self.topology_api_app, None):
            dp = sw.dp
            ofp, parser = dp.ofproto, dp.ofproto_parser
            actions = [
                parser.OFPActionOutput(p.port_no)
                for p in sw.ports
                if not p.is_reserved()
                and (dp.id, p.port_no) not in self.switch_ports
                and not (dp.id == msg.datapath.id and p.port_no == msg.match["in_port"])
            ]
            if actions:
                dp.send_msg(parser.OFPPacketOut(
                    datapath=dp, buffer_id=ofp.OFP_NO_BUFFER,
                    in_port=ofp.OFPP_CONTROLLER, actions=actions, data=msg.data,
                ))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """Trata pacotes desconhecidos: descobre hosts (ARP/IPv4) e instala rotas."""
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth or eth.ethertype in (ether_types.ETH_TYPE_LLDP, ether_types.ETH_TYPE_IPV6):
            return

        arp_pkt = pkt.get_protocol(arp.arp)
        ip_pkt = pkt.get_protocol(ipv4.ipv4)
        if arp_pkt:
            src_ip, dst_ip, src_mac = arp_pkt.src_ip, arp_pkt.dst_ip, arp_pkt.src_mac
        elif ip_pkt:
            src_ip, dst_ip, src_mac = ip_pkt.src, ip_pkt.dst, eth.src
        else:
            return

        # Aprende host somente se entrou por porta de borda
        if src_ip not in self.hosts and src_ip != "0.0.0.0" and (dp.id, in_port) not in self.switch_ports:
            self.hosts[src_ip] = {"mac": src_mac, "dpid": dp.id, "port": in_port}
            self.logger.info("Host descoberto: %s (%s) -> s%s porta %s", src_ip, src_mac, dp.id, in_port)
            expected_hosts = 6 if len(self.switches) == 8 else len(self.switches)
            if len(self.hosts) >= expected_hosts and self.switches:
                self.print_full_topology()

        if arp_pkt and src_ip == dst_ip:
            return  # Gratuitous ARP

        if dst_ip not in self.hosts:
            if arp_pkt:
                self._flood_to_edge_ports(msg)
            return

        if src_ip not in self.hosts:
            return

        path = self.find_route(self.hosts[src_ip]["dpid"], self.hosts[dst_ip]["dpid"])
        if not path:
            return

        if self.installed_paths.get((src_ip, dst_ip)) != tuple(path):
            self.installed_paths[(src_ip, dst_ip)] = tuple(path)
            self.logger.info("[%s] %s -> %s | Rota: %s", self.routing_mode.upper(), src_ip, dst_ip, " -> ".join(f"s{s}" for s in path))

        self.install_path(path, src_ip, dst_ip)

        # Despacha o pacote atual imediatamente
        target_dp = dp if dp.id in path else self.datapaths.get(self.hosts[dst_ip]["dpid"])
        if target_dp:
            idx = path.index(target_dp.id)
            out_port = self.net[target_dp.id][path[idx + 1]]["src_port"] if idx < len(path) - 1 else self.hosts[dst_ip]["port"]
            ofp, parser = target_dp.ofproto, target_dp.ofproto_parser
            target_dp.send_msg(parser.OFPPacketOut(
                datapath=target_dp, buffer_id=ofp.OFP_NO_BUFFER,
                in_port=ofp.OFPP_CONTROLLER, actions=[parser.OFPActionOutput(out_port)], data=msg.data,
            ))

    def print_full_topology(self):
        self.logger.info("=== TOPOLOGIA (%s) | Switches: %s | Enlaces: %d | Hosts: %d ===",
                         self.routing_mode.upper(), self.switches, len(self.links), len(self.hosts))
        for s, d, sp, dp in self.links:
            self.logger.info("  Enlace: s%s:%s <-> s%s:%s", s, sp, d, dp)
        for ip, info in sorted(self.hosts.items()):
            self.logger.info("  Host %s (%s) -> s%s:%s", ip, info["mac"], info["dpid"], info["port"])


if __name__ == "__main__":
    import argparse
    from ryu.cmd.manager import main as ryu_main

    parser = argparse.ArgumentParser()
    parser.add_argument("--routing", choices=["ospf", "ecmp"], default="ospf")
    args, unknown = parser.parse_known_args()
    os.environ["ROUTING_ALGO"] = args.routing
    sys.argv = ["ryu-manager", "--observe-links", "--user-flags", __file__, "--routing", args.routing, *unknown, __file__]
    ryu_main()
