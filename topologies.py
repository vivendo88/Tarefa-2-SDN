#!/usr/bin/env python3
"""
Módulo dedicado às definições de topologias do Mininet (Torus 3x3, Spine-Leaf
e carregamento dinâmico via arquivo de lista de arestas).
"""

import os
from mininet.topo import Topo


class TorusTopo(Topo):
    """
    Topologia 1: Malha 2D (Torus 3x3)
    - 9 switches (s1 a s9)
    - 9 hosts (h1 a h9), onde hi conecta-se diretamente a si
    - Grade 3x3 com fechamento em anel nas bordas horizontais e verticais:
      Horizontal:
        Linha 1: (s1, s2), (s2, s3), (s3, s1)
        Linha 2: (s4, s5), (s5, s6), (s6, s4)
        Linha 3: (s7, s8), (s8, s9), (s9, s7)
      Vertical:
        Coluna 1: (s1, s4), (s4, s7), (s7, s1)
        Coluna 2: (s2, s5), (s5, s8), (s8, s2)
        Coluna 3: (s3, s6), (s6, s9), (s9, s3)
    """

    def build(self):
        switches = {}

        # Cria os 9 switches e 1 host conectado diretamente a cada switch (porta 1)
        for i in range(1, 10):
            s_name = f"s{i}"
            h_name = f"h{i}"
            sw = self.addSwitch(s_name, dpid=f"{i:016x}")
            host = self.addHost(
                h_name,
                ip=f"10.0.0.{i}/24",
                mac=f"00:00:00:00:00:{i:02x}"
            )
            self.addLink(host, sw)
            switches[s_name] = sw

        # Enlaces horizontais (com fechamento em anel)
        horizontal_links = [
            ("s1", "s2"), ("s2", "s3"), ("s3", "s1"),
            ("s4", "s5"), ("s5", "s6"), ("s6", "s4"),
            ("s7", "s8"), ("s8", "s9"), ("s9", "s7"),
        ]

        # Enlaces verticais (com fechamento em anel)
        vertical_links = [
            ("s1", "s4"), ("s4", "s7"), ("s7", "s1"),
            ("s2", "s5"), ("s5", "s8"), ("s8", "s2"),
            ("s3", "s6"), ("s6", "s9"), ("s9", "s3"),
        ]

        for s_orig, s_dest in horizontal_links + vertical_links:
            self.addLink(switches[s_orig], switches[s_dest])


class SpineLeafTopo(Topo):
    """
    Topologia 2: Spine-Leaf (Datacenter Simples)
    - Switches Spine (Núcleo): 2 switches (s1 e s2)
    - Switches Leaf (Borda): 6 switches (s3 a s8)
    - Hosts: 8 hosts (h1 a h8), um em cada switch (hi conectado a si)
    - Enlaces:
      - Conexão total bipartida entre Leafs (s3..s8) e Spines (s1, s2)
      - Sem enlaces diretos entre Spines (s1 e s2)
      - Sem enlaces diretos entre Leafs (s3..s8)
    """

    def build(self):
        switches = {}

        # Cria os 8 switches (s1..s8) e conecta 1 host (h1..h8) diretamente a cada switch
        for i in range(1, 9):
            s_name = f"s{i}"
            h_name = f"h{i}"
            sw = self.addSwitch(s_name, dpid=f"{i:016x}")
            host = self.addHost(
                h_name,
                ip=f"10.0.0.{i}/24",
                mac=f"00:00:00:00:00:{i:02x}"
            )
            self.addLink(host, sw)
            switches[s_name] = sw

        spines = ["s1", "s2"]
        leafs = [f"s{i}" for i in range(3, 9)]

        # Conexão total bipartida entre cada Leaf (s3..s8) e cada Spine (s1, s2)
        for leaf in leafs:
            for spine in spines:
                self.addLink(switches[leaf], switches[spine])


class EdgeListTopo(Topo):
    """
    Topologia carregada dinamicamente a partir de um arquivo de lista de arestas (.txt).
    Cada switch instanciado recebe automaticamente um host correspondente (ex.: s1 -> h1).
    """

    def build(self, edge_list_file):
        if not os.path.isfile(edge_list_file):
            raise FileNotFoundError(f"Arquivo de topologia não encontrado: {edge_list_file}")

        switches = {}
        edges = []

        with open(edge_list_file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2 and not parts[0].startswith("#"):
                    edges.append((parts[0], parts[1]))

        # Ordena os switches pelo índice numérico para atribuição consistente de DPID, IP e MAC
        unique_switches = sorted(
            {sw for edge in edges for sw in edge},
            key=lambda s: int(s.lstrip("s")) if s.lstrip("s").isdigit() else s
        )

        for idx, s_name in enumerate(unique_switches, start=1):
            num = int(s_name.lstrip("s")) if s_name.lstrip("s").isdigit() else idx
            h_name = f"h{num}"
            sw = self.addSwitch(s_name, dpid=f"{num:016x}")
            host = self.addHost(
                h_name,
                ip=f"10.0.0.{num}/24",
                mac=f"00:00:00:00:00:{num:02x}"
            )
            self.addLink(host, sw)
            switches[s_name] = sw

        for s_orig, s_dest in edges:
            self.addLink(switches[s_orig], switches[s_dest])


# Alias para compatibilidade retroativa
Torus3x3Topo = TorusTopo

# Dicionário de mapeamento de nomes para classes de topologia
TOPOS = {
    "torus": TorusTopo,
    "spineleaf": SpineLeafTopo,
}

# Compatibilidade com `mn --custom topologies.py --topo <nome>`
topos = {
    "torus": (lambda: TorusTopo()),
    "spineleaf": (lambda: SpineLeafTopo()),
}


def get_topology(topology_identifier):
    """
    Fábrica de topologias: instancia a classe correspondente ao nome ('torus' ou 'spineleaf')
    ou carrega dinamicamente um arquivo de lista de arestas caso seja informado um caminho válido.
    """
    key = topology_identifier.strip().lower()
    if key in TOPOS:
        return TOPOS[key]()

    if os.path.isfile(topology_identifier):
        return EdgeListTopo(edge_list_file=topology_identifier)

    valid_options = ", ".join(sorted(TOPOS.keys()))
    raise ValueError(
        f"Topologia inválida: '{topology_identifier}'. "
        f"Escolha entre [{valid_options}] ou informe um arquivo de arestas existente."
    )
