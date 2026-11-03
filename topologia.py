import sys
from time import sleep
from functools import partial
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel

def start_network(edge_list_file, batch=False):
    # Configura os switches para utilizar a versão OpenFlow 1.3
    switch_of13 = partial(OVSKernelSwitch, protocols='OpenFlow13')
    net = Mininet(controller=RemoteController, switch=switch_of13)
    c0 = net.addController('c0', port=6633)

    switches = {}
    hosts = {}

    # Leitura e parsing do arquivo de arestas
    with open(edge_list_file, 'r') as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                s_orig, s_dest = parts[0], parts[1]

                # Instancia os switches caso ainda não existam
                for s_name in (s_orig, s_dest):
                    if s_name not in switches:
                        # Adiciona o switch
                        sw = net.addSwitch(s_name)
                        switches[s_name] = sw

                        # Adiciona um host conectado a este switch (ex: s1 -> h1                                                                                                                                                             )
                        h_name = 'h' + s_name.lstrip('s')
                        host = net.addHost(h_name)
                        hosts[h_name] = host
                        net.addLink(host, sw)

                # Cria o enlace entre os dois switches
                net.addLink(switches[s_orig], switches[s_dest])

    net.start()
    for node in net.hosts + net.switches:
        node.cmd("sysctl -w net.ipv6.conf.all.disable_ipv6=1")
        node.cmd("sysctl -w net.ipv6.conf.default.disable_ipv6=1")
        node.cmd("sysctl -w net.ipv6.conf.lo.disable_ipv6=1")
    print("Aguardando inicialização do controlador...")
    sleep(3)

    print("Enviando Gratuitous ARP de todos os hosts para descoberta pelo Ryu...                                                                                                                                                             ")
    for h in net.hosts:
        # Obtém o nome da interface ativa do host (ex.: h1-eth0)
        iface = h.defaultIntf().name
        # Dispara o Gratuitous ARP especificando a interface correta
        h.cmd(f'arping -U -c 1 -I {iface} {h.IP()}')
        sleep(0.2)

    if batch:
        sleep(2)
    else:
        CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    if len(sys.argv) > 1:
        start_network(sys.argv[1], batch=('--batch' in sys.argv))
    else:
        print("Uso: sudo python topologia.py <arquivo_topologia.txt>")
