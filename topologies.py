"""Definições das topologias Mininet: Torus 3x3, Spine-Leaf e Lista de Arestas."""

import os
from mininet.topo import Topo


def _add_switch_and_host(topo, idx, with_host=True):
    """Cria um switch s<idx> e, opcionalmente, seu host h<idx> conectado a ele."""
    sw = topo.addSwitch(f"s{idx}", dpid=f"{idx:016x}")
    if with_host:
        host = topo.addHost(f"h{idx}", ip=f"10.0.0.{idx}/24", mac=f"00:00:00:00:00:{idx:02x}")
        topo.addLink(host, sw)
    return sw


class TorusTopo(Topo):
    """Malha 2D Torus 3x3 (9 switches e 9 hosts com bordas fechadas em anel)."""

    def build(self):
        sw = {f"s{i}": _add_switch_and_host(self, i, with_host=True) for i in range(1, 10)}
        links = [
            ("s1", "s2"), ("s2", "s3"), ("s3", "s1"),
            ("s4", "s5"), ("s5", "s6"), ("s6", "s4"),
            ("s7", "s8"), ("s8", "s9"), ("s9", "s7"),
            ("s1", "s4"), ("s4", "s7"), ("s7", "s1"),
            ("s2", "s5"), ("s5", "s8"), ("s8", "s2"),
            ("s3", "s6"), ("s6", "s9"), ("s9", "s3"),
        ]
        for u, v in links:
            self.addLink(sw[u], sw[v])


class SpineLeafTopo(Topo):
    """Datacenter Spine-Leaf: 2 Spines (s1, s2) e 6 Leafs (s3..s8) com hosts h3..h8."""

    def build(self):
        sw = {f"s{i}": _add_switch_and_host(self, i, with_host=(i >= 3)) for i in range(1, 9)}
        for leaf in range(3, 9):
            for spine in (1, 2):
                self.addLink(sw[f"s{leaf}"], sw[f"s{spine}"])


class EdgeListTopo(Topo):
    """Topologia genérica carregada a partir de um arquivo de lista de arestas (.txt)."""

    def build(self, edge_list_file):
        edges = []
        with open(edge_list_file, "r", encoding="utf-8") as f:
            for line in f:
                p = line.strip().split()
                if len(p) >= 2 and not p[0].startswith("#"):
                    edges.append((p[0], p[1]))

        nomes = sorted({s for edge in edges for s in edge}, key=lambda s: int(s.lstrip("s")) if s.lstrip("s").isdigit() else 0)
        sw = {}
        for i, nome in enumerate(nomes, start=1):
            num = int(nome.lstrip("s")) if nome.lstrip("s").isdigit() else i
            sw[nome] = _add_switch_and_host(self, num, with_host=True)
        for u, v in edges:
            self.addLink(sw[u], sw[v])


TOPOS = {"torus": TorusTopo, "spineleaf": SpineLeafTopo}
topos = {"torus": lambda: TorusTopo(), "spineleaf": lambda: SpineLeafTopo()}


def get_topology(name_or_path):
    """Retorna a instância da topologia pelo nome ('torus'/'spineleaf') ou arquivo .txt."""
    key = name_or_path.strip().lower()
    if key in TOPOS:
        return TOPOS[key]()
    if os.path.isfile(name_or_path):
        return EdgeListTopo(edge_list_file=name_or_path)
    raise ValueError(f"Topologia inválida: '{name_or_path}'. Use 'torus', 'spineleaf' ou um arquivo válido.")
