"""Roteamento OSPF (menor caminho único) utilizando NetworkX."""

import networkx as nx


def get_route_ospf(net, switch_in, switch_out):
    """Retorna o menor caminho entre switch_in e switch_out no grafo `net`."""
    if switch_in == switch_out:
        return [switch_in]
    try:
        return nx.shortest_path(net, switch_in, switch_out)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
