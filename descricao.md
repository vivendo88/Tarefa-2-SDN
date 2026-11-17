# Trabalho SDN — Parte 2: Roteamento Dinâmico, Tolerância a Falhas e Análise de Desempenho

## Orientações Gerais

O trabalho deve ser realizado em grupos de dois alunos ou individualmente. Esta etapa consiste na continuação direta da **Parte 1**, que abordou a descoberta de topologias de rede utilizando Mininet e o framework Ryu. O foco desta fase é a consolidação de habilidades voltadas à pesquisa científica em Redes Definidas por Software (SDN), exigindo a concepção de experimentos, coleta automatizada de dados, resiliência estrutural e análise rigorosa de resultados por meio de representações gráficas em padrão acadêmico.

---

## Objetivos Acadêmicos e Metodológicos

O objetivo central é implementar um ambiente automatizado para a execução de dois experimentos distintos:

- **Experimento 1:** Medir o tempo de conclusão de fluxos (*Flow Completion Time* — FCT) em testes de estresse com tráfego "todos para todos".
- **Experimento 2:** Avaliar o tempo de recuperação da rede (*throughput*) frente à queda de enlaces físicos no meio do teste de estresse.

---

## Ferramentas e Conhecimentos Necessários

- **NetworkX:** Utilizada para calcular o menor caminho (OSPF) ou múltiplos menores caminhos (ECMP) entre nós de origem e destino na topologia.
- **Iperf:** Gerador de tráfego ativo para injetar rajadas de dados (estresse) e determinar a largura de banda efetiva.
- **Bwm-ng:** Monitor de interface executado em background para capturar o comportamento da largura de banda.
- **Gnuplot, Matplotlib ou equivalente:** Ferramentas analíticas para gerar gráficos com qualidade de publicação.

---

## Descrição Técnica do Trabalho

### 1. Modelagem em Grafos e Algoritmos de Roteamento

O controlador Ryu deverá utilizar a descoberta dinâmica para instanciar um grafo direcionado no NetworkX em tempo de execução. Com base nesta estrutura, devem ser implementados o **OSPF** (primeiro menor caminho) e o **ECMP** (todos os menores caminhos).

No caso do ECMP, a escolha do caminho deverá ser feita por meio de uma função de hash:

```python
def get_route_ecmp(switch_in, switch_out, paths): 
    if paths: 
        # Escolha a função de hash que desejar, desde que gere um número inteiro
        hash_val = hash(switch_in + switch_out)
        choice = hash_val % len(paths) 
        path = sorted(paths)[choice] 
    return path
```

### 2. Instalação Dinâmica de Regras e Tolerância a Falhas

O controlador deve contemplar as seguintes funções essenciais:

- `find_route(switch_in, switch_out, method)`: Determina a rota baseando-se no método (OSPF ou ECMP).
- `install_path()` e `add_flow()`: Instalam proativamente as regras de fluxo (OpenFlow 1.3) em cada switch.
- **Tratamento de Falhas:** O controlador deve reagir automaticamente ao evento `EventOFPPortStatus`. Ao detectar a queda de um enlace, o controlador deve atualizar o grafo, limpar os fluxos rompidos (`OFPFC_DELETE`) e permitir o recálculo imediato de rotas.

### 3. Automatização dos Experimentos (Script Mininet)

Crie um script Mininet único (ex: `fctmain.py`) que receba via linha de comando a topologia, o roteamento e qual experimento rodar. Exemplo:

```bash
sudo python fctmain.py --topology torus --routing ecmp --exp 1
sudo python fctmain.py --topology spineleaf --routing ospf --exp 2
```

A rotina do script deve seguir os passos abaixo:

1. **Inicialização:** Instanciar a topologia escolhida e desabilitar o tráfego IPv6 nos hosts e switches.
2. **Descoberta:** Aguardar a inicialização e executar um `net.pingAll()` para o controlador aprender todas as rotas.
3. **Monitoramento:** Iniciar a captura passiva de vazão com o BWM-NG em background.
4. **Estresse (Iperf):** Iniciar os servidores e clientes Iperf, configurando transferências "todos-para-todos" com blocos fixos de dados (ex: 5MB).
5. **Execução do Experimento Selecionado (`--exp`):**
   - **Se `--exp 1` (FCT Clássico):** Apenas aguarde a conclusão natural de todos os fluxos. Nenhuma falha é injetada. O foco é obter o tempo total de conclusão.
   - **Se `--exp 2` (Reação a Falhas):** Na metade do teste de estresse (quando a vazão agregada estiver no pico, ex: após 20 segundos), derrube intencionalmente um enlace de núcleo usando comandos nativos da API (ex: `net.configLinkStatus('s1', 's3', 'down')`). O foco é forçar o recálculo de rotas e registrar a queda e a recuperação da vazão.
6. **Finalização:** Aguardar o término de todos os fluxos, encerrar os processos residuais (Iperf, bwm-ng) e parar o Mininet.

### 4. Topologias de Teste Fixas

Os dois experimentos deverão ser rodados sobre as 2 topologias descritas abaixo, testadas sob OSPF e ECMP, totalizando 8 execuções no total (2 topologias × 2 métodos × 2 experimentos). Você pode gerá-las via NetworkX no seu script Mininet ou descrevê-las estaticamente.

#### Topologia 1: Malha 2D (Torus 3x3)
- **Características:** 9 Nós. Representa uma grade onde as extremidades se conectam (loop fechado), garantindo redundância.
- **Falha no Experimento 2:** Derrubar o enlace entre `s4` e `s5`.

*(As extremidades verticais e horizontais conectam-se entre si formando um anel)*
```text
 s1  --- s2  --- s3
 |       |       |
 s4  --- s5  --- s6
 |       |       |
 s7  --- s8  --- s9
```

#### Topologia 2: Spine-Leaf (Datacenter Simples)
- **Características:** 2 switches Spine (núcleo, `s1` e `s2`) e 6 switches Leaf (borda, `s3` a `s8`). Cada switch Leaf possui uma conexão direta com ambos os switches Spine, garantindo dois caminhos principais de mesma distância. Não há conexões diretas entre os Spines nem entre os Leafs.
- **Falha no Experimento 2:** Derrubar o enlace entre um Leaf (ex: `s3`) e o primeiro Spine (`s1`).

**Diagrama:**
```text
      [ s1 ]       [ s2 ]       (Spines)
     /  |  \ \     / /  |  \
   s3  s4  s5  s6  s7  s8       (Leafs)
   |   |   |   |   |   |
  h3  h4  h5  h6  h7  h8        (Hosts)
```

---

## Critérios de Avaliação e Entregáveis (Total: 40 Pontos)

O trabalho exige a entrega conjunta via repositório no GitHub e arquivo compactado contendo os códigos, além de uma arguição técnica final.

- **15 pontos (Código Controlador):** Lógica do controlador Ryu, mapeamento dinâmico da rede, correta instalação das regras OpenFlow (OSPF e ECMP) e tratamento dinâmico de falhas (`PortStatus` e limpeza de fluxos).
- **5 pontos (Código Mininet):** Automação completa recebendo os parâmetros, instanciando rigorosamente as 2 topologias e cobrindo os passos dos dois experimentos (injeção condicional de falhas e log Bwm-ng).
- **10 pontos (Relatório Técnico e GitHub):** Documentação formal (PDF) consolidando as análises. Deve obrigatoriamente apresentar:
  - **Gráficos de Linha (Ambos os Experimentos):** Gráficos de linha de "Vazão vs. Tempo", incluindo todos os switches plotados simultaneamente no mesmo gráfico, para ilustrar a distribuição da carga em cada um dos cenários testados.
  - **Gráficos do Experimento 1 (FCT):** Gráfico de barras sumarizando e contrastando o tempo final de conclusão de fluxo (eixo Y) das 4 combinações de topologia/roteamento (eixo X).
  - **Análise do Experimento 2 (Reação a Falhas):** Nos gráficos de linha de "Vazão vs. Tempo" do Experimento 2, a queda do enlace no meio do teste deve ser claramente visível, evidenciando o momento da falha e a subsequente curva de convergência/recuperação da rede.
  - **Discussão acadêmica:** Dissecando os gargalos, o comportamento do OSPF vs ECMP e a eficiência do controlador na recuperação de rotas.
  - **Link público:** Para o repositório no GitHub contendo os códigos e scripts comentados.
- **10 pontos (Entrevista/Arguição Técnica):** Arguição individual para atestar o domínio da base teórica sobre SDN, compreensão da lógica de grafos e fluência sobre o fluxo de tratamento de pacotes implementado.

---
---

# Parte 1: Descoberta de Topologias de Rede com Mininet e Ryu (Contexto Base)

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
