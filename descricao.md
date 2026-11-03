# Descoberta de Topologias de Rede com Mininet e Ryu

## Orientações Gerais

O trabalho deverá ser realizado em grupos de dois alunos, ou individualmente caso deseje.

## Objetivos

O objetivo deste trabalho é preparar o ambiente e a base de código para o desenvolvimento de aplicações em Redes Definidas por Software (SDN). Os alunos deverão:

- Familiarizar-se com a emulação de cenários de redes utilizando a API do **Mininet**.
- Familiarizar-se com a programação utilizando a API para desenvolvimento de controladores **Ryu**.
- Familiarizar-se com a biblioteca **NetworkX** para análise de topologias de rede em Python.
- Desenvolver a capacidade de ler um arquivo de topologia em formato de lista de arestas, instanciá-la no Mininet e mapear essa topologia dinamicamente utilizando a API de topologia do Ryu.

---

## Descrição do Trabalho

Neste trabalho, vocês não precisarão calcular rotas ou instalar fluxos complexos. O foco é **leitura, instanciação e descoberta**. O trabalho está dividido nas seguintes etapas:

### 1. Leitura e Instanciação da Topologia

Você deve criar um script Python que leia um arquivo de texto contendo uma lista de arestas. A lista de arestas representa apenas os enlaces entre os switches e considera que cada switch está conectado a um host. O script deve fazer o *parsing* do arquivo e utilizar a API do Mininet para instanciar a rede.

### 2. Descoberta de Switches e Enlaces no Ryu (via API de Topologia)

O controlador da rede deverá ser desenvolvido utilizando o framework Ryu e utilizar o protocolo **OpenFlow versão 1.3**. Baseando-se nas práticas padrão de descoberta de topologia no Ryu, o controlador deve importar os métodos `get_switch` e `get_link` de `ryu.topology.api` para extrair a lista completa de dispositivos e enlaces.

- Para que o Ryu consiga injetar pacotes LLDP e mapear a rede, o controlador deve ser iniciado com o parâmetro `--observe-links`. Exemplo:
  ```bash
  ryu-manager --observe-links topo_discover.py
  ```
- Utilize os eventos `EventSwitchEnter` e `EventLinkAdd` para acionar a chamada das funções `get_switch(self, None)` e `get_link(self, None)`, atualizando as estruturas de dados internas da sua aplicação.

### 3. Descoberta de Hosts usando Gratuitous ARP

Em redes SDN puras, não dependemos de *broadcasts* convencionais para o aprendizado de MAC (*MAC learning*). Para que o controlador descubra os hosts, utilizaremos o **Gratuitous ARP** (ou `arping`).

- **O conceito:** O host enviará uma resposta ARP não solicitada (também chamada de *"gratuitous ARP"*, ou um `arping`) ao conectar para informar à rede o seu endereço MAC e IP.
- **A interceptação:** Quando um switch recebe a requisição ARP, ele envia a requisição para o controlador como uma mensagem `Packet In`, em vez de transmiti-la em *broadcast*.
- **A ação do Controlador:** Você deve adaptar a função `_packet_in_handler()` para tratar os pacotes que não possuem regras instaladas nos switches. O controlador deve ler o pacote ARP, extrair o IP e o MAC da origem, registrar a qual switch e porta o host pertence e armazenar isso em suas estruturas de dados.
- **Impressão da Topologia:** O seu código do controlador deve ser capaz de imprimir no log (console) a topologia completa mapeada, listando explicitamente todos os switches ativos, todos os enlaces entre eles e todos os servidores/hosts conectados às suas respectivas portas.

### 4. Topologias de Teste Fixas

Para facilitar a depuração e garantir que seu código trata adequadamente grafos arbitrários, o teste final deve ser validado em duas topologias fixas que contêm múltiplos caminhos e loops: **Triangle** e **Someloops**.

#### Topologia 1: Triangle

**Diagrama:**
```text
     h2
     |
     s2
    /  \
  s1----s3
  |      |
  h1     h3
```

**Lista de arestas (arquivo `topo_triangle.txt`):**
```text
s1 s2
s2 s3
s1 s3
```

#### Topologia 2: Someloops

**Diagrama:**
```text
 h2       h3
 |        |
s5---s1---s2
 |    |    | \
 |    |    |  s6--h4
 |    |    | /
s3---s4----+
 |
 h1
```

**Lista de arestas (arquivo `topo_someloops.txt`):**
```text
s1 s2
s1 s4
s1 s5
s2 s4
s2 s6
s3 s4
s3 s5
s4 s6
```

---

## Códigos Iniciais

Para otimizar o tempo de desenvolvimento, utilize as estruturas abaixo como ponto de partida.

### 1. Script Mininet (`teste_topologia.py`)

```python
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel
from time import sleep
import sys

def start_network(edge_list_file):
    net = Mininet(controller=RemoteController, switch=OVSKernelSwitch)
    c0 = net.addController('c0', port=6633)
    
    # TODO: Fazer o parsing do arquivo 'edge_list_file' para extrair as arestas
    # TODO: Adicionar switches e links usando net.addSwitch e net.addLink baseando-se no arquivo
    # TODO: Adicionar um host para cada switch e conectá-los
    
    net.start()
    print("Aguardando inicialização do controlador...")
    sleep(3)
    
    print("Enviando Gratuitous ARP de todos os hosts para descoberta pelo Ryu...")
    for h in net.hosts:
        h.cmd('arping -U -c 1 ' + h.IP())
        sleep(0.1)
    
    CLI(net)
    net.stop()

if __name__ == '__main__':
    setLogLevel('info')
    if len(sys.argv) > 1:
        start_network(sys.argv[1])
    else:
        print("Uso: sudo python teste_topologia.py <arquivo_topologia.txt>")
```

### 2. Aplicação Ryu (`topo_discover.py`)

```python
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event
from ryu.topology.api import get_switch, get_link
from ryu.lib.packet import packet, arp

class TopoDiscover(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TopoDiscover, self).__init__(*args, **kwargs)
        self.topology_api_app = self
        self.switches = []
        self.links = []
        self.hosts = {} # Estrutura sugerida: {ip: {'mac': mac, 'dpid': dpid, 'port': port}}

    @set_ev_cls(event.EventSwitchEnter)
    def _event_switch_enter_handler(self, ev):
        # Utilizando a API do Ryu para extrair os switches atualizados
        switch_list = get_switch(self.topology_api_app, None)
        self.switches = [switch.dp.id for switch in switch_list]
        self.logger.info("Switches mapeados atualmente: %s", self.switches)

    @set_ev_cls(event.EventLinkAdd)
    def _event_link_add_handler(self, ev):
        # Utilizando a API do Ryu para extrair os enlaces atualizados
        link_list = get_link(self.topology_api_app, None)
        self.links = [(link.src.dpid, link.dst.dpid, link.src.port_no, link.dst.port_no) for link in link_list]
        self.logger.info("Total de enlaces descobertos: %d", len(self.links))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match['in_port']
        
        pkt = packet.Packet(msg.data)
        arp_pkt = pkt.get_protocol(arp.arp)

        if arp_pkt:
            self.logger.info("ARP recebido do IP %s, MAC %s no Switch %s, porta %s", 
                             arp_pkt.src_ip, arp_pkt.src_mac, datapath.id, in_port)
            # TODO: Salvar as informações do host descoberto na estrutura self.hosts
            # TODO: Chamar função para imprimir a topologia completa no log
            self.print_full_topology()
            
    def print_full_topology(self):
        # TODO: Crie a lógica iterativa para imprimir no terminal a topologia completa mapeada de forma amigável
        # (Switches disponíveis, Enlaces registrados e Servidores/Hosts conectados)
        pass
```

---

## Entrega do Trabalho e Pontuação

A entrega deverá ser realizada obrigatoriamente em duas plataformas:

1. **Ambiente Virtual de Aprendizagem (AVA / Moodle):** Envio do pacote compactado (`.zip`) e do relatório (`.pdf`).
2. **Repositório GitHub:** Inserção do link do repositório no texto da entrega do AVA. O repositório deve conter todos os arquivos do projeto.

### O que deve ser entregue

- **Código-fonte comentado:** Todos os scripts Python (`teste_topologia.py` / `topologia.py`, `topo_discover.py`, e arquivos de topologia) devidamente estruturados.
- **Logs da Execução:** Arquivos de texto contendo a saída do terminal do Mininet e a saída do console do Ryu. O log do Ryu deve obrigatoriamente exibir a impressão da topologia completa (switches mapeados, enlaces e servidores descobertos).
- **Relatório Técnico (PDF):** Relatório explicando em detalhes a implementação. O relatório deve conter:
  - A representação gráfica de todos os grafos das topologias usadas nos testes gerada a partir do **NetworkX**.
  - Uma explicação sobre como a flag `--observe-links` e as funções `get_switch`/`get_link` funcionam no Ryu para mapear a rede.
  - Uma explicação sobre como as mensagens `PacketIn` funcionaram na interceptação do Gratuitous ARP e como o controlador processou e armazenou os hosts.
  - Prints dos terminais comprovando a consistência entre a rede instanciada pelo Mininet e a topologia detalhada descoberta pelo Ryu.
