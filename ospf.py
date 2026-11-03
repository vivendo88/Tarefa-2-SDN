#!/usr/bin/env python3
"""
Módulo de roteamento OSPF (Shortest Path First) sobre o grafo direcionado NetworkX.
Pode ser importado pelo controlador principal (`topo_discover.py`) ou executado
diretamente com `python3 ospf.py` / `ryu-manager --observe-links ospf.py`.
"""

import os
import sys
import networkx as nx


def get_route_ospf(net, switch_in, switch_out):
    """
    Calcula o caminho mais curto (OSPF) entre switch_in e switch_out
    no grafo direcionado `net` (nx.DiGraph) utilizando nx.shortest_path().
    """
    if switch_in not in net or switch_out not in net:
        return None
    if switch_in == switch_out:
        return [switch_in]
    try:
        return nx.shortest_path(net, switch_in, switch_out)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


if __name__ == "__main__":
    os.environ["ROUTING_ALGO"] = "ospf"
    from ryu.cmd.manager import main as ryu_main

    topo_discover_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topo_discover.py")
    sys.argv = [
        "ryu-manager",
        "--observe-links",
        "--user-flags", topo_discover_path,
        "--routing", "ospf",
        *sys.argv[1:],
        topo_discover_path,
    ]
    ryu_main()
