#!/usr/bin/env python3
"""
Módulo de roteamento ECMP (Equal-Cost Multi-Path) sobre o grafo direcionado NetworkX.
Pode ser importado pelo controlador principal (`topo_discover.py`) ou executado
diretamente com `python3 ecmp.py` / `ryu-manager --observe-links ecmp.py`.
"""

import os
import sys
import networkx as nx


def get_route_ecmp(switch_in, switch_out, paths):
    """
    Seleciona deterministicamente um caminho dentre os múltiplos caminhos de custo mínimo
    (ECMP) utilizando hash do par (switch_in, switch_out).
    """
    if paths:
        hash_val = hash(f"{switch_in}{switch_out}")
        choice = hash_val % len(paths)
        path = sorted(paths)[choice]
        return path
    return None


def compute_ecmp_route(net, switch_in, switch_out):
    """
    Obtém todos os caminhos mínimos entre switch_in e switch_out via nx.all_shortest_paths()
    e retorna a rota selecionada por get_route_ecmp().
    """
    if switch_in not in net or switch_out not in net:
        return None
    if switch_in == switch_out:
        return [switch_in]
    try:
        paths = list(nx.all_shortest_paths(net, switch_in, switch_out))
        return get_route_ecmp(switch_in, switch_out, paths)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


if __name__ == "__main__":
    os.environ["ROUTING_ALGO"] = "ecmp"
    from ryu.cmd.manager import main as ryu_main

    topo_discover_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "topo_discover.py")
    sys.argv = [
        "ryu-manager",
        "--observe-links",
        "--user-flags", topo_discover_path,
        "--routing", "ecmp",
        *sys.argv[1:],
        topo_discover_path,
    ]
    ryu_main()
