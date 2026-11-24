"""Roteamento ECMP (múltiplos menores caminhos) utilizando NetworkX e hash."""

import networkx as nx


def get_route_ecmp(switch_in, switch_out, paths):
    """Seleciona deterministicamente um caminho dentre os menores via hash."""
    if paths:
        hash_val = hash(switch_in + switch_out)
        choice = hash_val % len(paths)
        path = sorted(paths)[choice]
        return path
    return None


def compute_ecmp_route(net, switch_in, switch_out):
    """Calcula todos os menores caminhos e escolhe um via get_route_ecmp()."""
    if switch_in == switch_out:
        return [switch_in]
    try:
        paths = list(nx.all_shortest_paths(net, switch_in, switch_out))
        return get_route_ecmp(switch_in, switch_out, paths)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
