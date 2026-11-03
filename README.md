# Descoberta Dinâmica de Topologia e Roteamento OSPF / ECMP com Ryu, Mininet e NetworkX

Este projeto implementa uma solução completa de Redes Definidas por Software (SDN) com o controlador **Ryu** (OpenFlow 1.3), **NetworkX** (`nx.DiGraph`) e **Mininet**, contemplando:

1. **Descoberta Dinâmica de Topologia em Tempo de Execução:** Mapeamento reativo de switches e enlaces direcionados via LLDP (`EventSwitchEnter`, `EventSwitchLeave`, `EventLinkAdd`, `EventLinkDelete`) e descoberta de hosts na borda via *Gratuitous ARP* / `PacketIn`.
2. **Roteamento OSPF e ECMP:**
   - **OSPF:** Cálculo do menor caminho via `nx.shortest_path()`.
   - **ECMP (*Equal-Cost Multi-Path*):** Obtenção de todos os caminhos de custo mínimo via `nx.all_shortest_paths()` e seleção determinística por função de dispersão (`get_route_ecmp(switch_in, switch_out, paths)`).
3. **Convergência Dinâmica a Falhas:** Detecção imediata de queda de enlaces (`EventLinkDelete`) ou saída de switches (`EventSwitchLeave`), invalidação automática dos fluxos afetados (`OFPFlowMod`) e recálculo de rotas alternativas em tempo real.
4. **Script Mininet Unificado (`fctmain.py`):** Instanciação parametrizada das topologias **Torus 3x3** (`torus`) e **Spine-Leaf** (`spineleaf`), desabilitação explícita de IPv6, aquecimento via `net.pingAll()` e manutenção da CLI interativa (`CLI(net)`).

---

## Estrutura do Projeto

- `topo_discover.py`: Aplicação do controlador Ryu (OpenFlow 1.3) que mantém o grafo direcionado `nx.DiGraph()`, calcula rotas via **OSPF** ou **ECMP**, instala regras `OFPFlowMod` ao longo do caminho e trata reconvergência dinâmica em falhas de enlaces.
- `fctmain.py`: Script Mininet unificado que recebe `--topology {torus,spineleaf}` e `--routing {ospf,ecmp}`, desabilita IPv6 nos nós, sincroniza LLDP/ARP, executa `net.pingAll()` e abre a `CLI(net)` interativa.
- `topologia.py`: Script Mininet para leitura de topologias arbitrárias em formato de lista de arestas (`topo_*.txt`).
- `topo_torus.txt` / `topo_spineleaf.txt` / `topo_triangle.txt` / `topo_someloops.txt`: Arquivos de listas de arestas das topologias.
- `install.sh` e `requirements.txt`: Scripts para configuração automatizada das dependências de sistema e ambiente virtual Python 3.9 (`.venv`).

---

## 1. Configuração e Instalação

Na raiz do projeto (`/home/administrador/projeto/sdn/Redes---Trabalho-SDN-1`), execute:

```bash
chmod +x install.sh
./install.sh
```

---

## 2. Fluxo de Execução Integrado (`topo_discover.py` + `fctmain.py`)

Abra **dois terminais** na pasta do projeto:

### Passo 1: Iniciar o Controlador Ryu (Terminal 1)

Ative o ambiente virtual `.venv` e inicie o controlador informando o algoritmo desejado (`--routing ospf` ou `--routing ecmp`):

```bash
source .venv/bin/activate

# Executar com OSPF:
python3 topo_discover.py --routing ospf

# OU executar com ECMP:
python3 topo_discover.py --routing ecmp
```

*(Alternativamente, você também pode invocar diretamente via `ryu-manager`:)*
```bash
ryu-manager --observe-links --user-flags topo_discover.py --routing ecmp topo_discover.py
```

### Passo 2: Iniciar a Topologia no Mininet com `fctmain.py` (Terminal 2)

No segundo terminal, execute o `fctmain.py` com `sudo` combinando `--topology` (`torus` ou `spineleaf`) e `--routing` (`ospf` ou `ecmp`):

#### Cenário 1: Topologia Malha 2D (`torus` 3x3)
9 switches (`s1` a `s9`) e 9 hosts (`h1` a `h9`) com fechamento em anel horizontal e vertical:
```bash
sudo python3 fctmain.py --topology torus --routing ospf
# ou:
sudo python3 fctmain.py --topology torus --routing ecmp
```

#### Cenário 2: Topologia Datacenter (`spineleaf`)
2 switches Spine (`s1`, `s2`), 6 switches Leaf (`s3` a `s8`) e 8 hosts (`h1` a `h8`) em topologia bipartida completa entre Leafs e Spines:
```bash
sudo python3 fctmain.py --topology spineleaf --routing ospf
# ou:
sudo python3 fctmain.py --topology spineleaf --routing ecmp
```

---

## 3. Testes Interativos na CLI do Mininet e Simulação de Falhas

Após o `net.pingAll()` inicial de aquecimento, o `fctmain.py` permanecerá aberto no prompt `mininet>`.

### Testes na Topologia Torus 3x3
```text
mininet> h4 ping -c 4 h5
mininet> link s4 s5 down
mininet> h4 ping -c 4 h5
mininet> link s4 s5 up
```
No terminal do controlador Ryu, você observará o evento `[FALHA DE ENLACE]`, a limpeza das regras antigas e a instalação imediata do novo caminho desviando do enlace `(s4, s5)`.

### Testes na Topologia Spine-Leaf
```text
mininet> h3 ping -c 4 h4
mininet> link s3 s1 down
mininet> h3 ping -c 4 h4
mininet> link s3 s1 up
```
Quando o enlace entre o Leaf `s3` e o Spine `s1` cai, o controlador Ryu recalcula automaticamente a rota utilizando o Spine `s2` (`s3 -> s2 -> s4`).

### Encerrando e Limpando o Ambiente
Para sair da CLI do Mininet, digite `exit` (que invocará `net.stop()`). Caso necessário limpar estados residuais do Open vSwitch:
```bash
sudo mn -c
```
