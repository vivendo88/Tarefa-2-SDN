#!/usr/bin/env bash
set -e

echo "=========================================================="
echo " 1. Instalando dependências do sistema (Mininet e OVS)    "
echo "=========================================================="

sudo apt-get update
sudo apt-get install -y \
    curl \
    mininet \
    openvswitch-switch \
    openvswitch-common \
    iputils-arping \
    net-tools \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    libffi-dev \
    libssl-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev

echo "=========================================================="
echo " 2. Iniciando o serviço do Open vSwitch                   "
echo "=========================================================="
sudo service openvswitch-switch start || sudo systemctl start openvswitch-switch

echo "=========================================================="
echo " 3. Criando ambiente virtual (.venv) com Python 3.9       "
echo "    (Necessário pois o Ryu 4.34 exige Python <= 3.9)      "
echo "=========================================================="
if ! command -v uv &> /dev/null && [ ! -f "$HOME/.local/bin/uv" ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# Cria a venv .venv baixando automaticamente o Python 3.9 isolado para o projeto
uv venv .venv --python 3.9 --seed

echo "=========================================================="
echo " 4. Instalando pacotes na .venv (requirements.txt)        "
echo "=========================================================="
./.venv/bin/pip install "setuptools<58.0.0" "pip<24.1" wheel
./.venv/bin/pip install --no-build-isolation -r requirements.txt
echo "/usr/lib/python3/dist-packages" > ./.venv/lib/python3.9/site-packages/system_mininet.pth

echo "=========================================================="
echo " Instalação concluída com sucesso!                        "
echo "=========================================================="
echo ""
echo "Como executar o projeto (Fluxo Integrado OSPF / ECMP):"
echo ""
echo "  1. Ative a venv no terminal onde for rodar o Ryu:"
echo "     source .venv/bin/activate"
echo ""
echo "  2. Inicie o controlador Ryu (Terminal 1 - com a venv ativa):"
echo "     python3 topo_discover.py --routing ospf"
echo "     (ou: python3 topo_discover.py --routing ecmp)"
echo "     (ou: ryu-manager --observe-links --user-flags topo_discover.py --routing ecmp topo_discover.py)"
echo ""
echo "  3. Inicie a topologia unificada no Mininet (Terminal 2):"
echo "     sudo python3 fctmain.py --topology torus --routing ospf"
echo "     sudo python3 fctmain.py --topology torus --routing ecmp"
echo "     sudo python3 fctmain.py --topology spineleaf --routing ospf"
echo "     sudo python3 fctmain.py --topology spineleaf --routing ecmp"
