#!/usr/bin/env bash
set -e

mkdir -p logs

echo "=========================================================="
echo " Validando permissao sudo para o Mininet...               "
echo "=========================================================="
sudo -v

source .venv/bin/activate

TOPOLOGIAS=("torus" "spineleaf")
ALGORITMOS=("ospf" "ecmp")

for topo in "${TOPOLOGIAS[@]}"; do
    for algo in "${ALGORITMOS[@]}"; do
        png="topologia_${topo}_${algo}_descoberta.png"
        ryu_log="logs/ryu_${topo}_${algo}.log"
        mn_log="logs/mininet_${topo}_${algo}.log"

        echo ""
        echo "=========================================================="
        echo " Testando: Topologia=${topo} | Roteamento=${algo}         "
        echo "=========================================================="

        sudo mn -c > /dev/null 2>&1 || true

        # Inicia o controlador Ryu em background com o algoritmo escolhido
        TOPO_OUTPUT_PNG="${png}" python3 topo_discover.py --routing "${algo}" > "${ryu_log}" 2>&1 &
        RYU_PID=$!
        sleep 2

        # Executa o fctmain.py no Mininet em modo batch (executa até o pingAll e encerra)
        sudo python3 fctmain.py --topology "${topo}" --routing "${algo}" --batch > "${mn_log}" 2>&1 || true

        # Encerra o controlador Ryu
        kill "${RYU_PID}" 2>/dev/null || true
        wait "${RYU_PID}" 2>/dev/null || true

        echo " -> Log do Ryu salvo em:     ${ryu_log}"
        echo " -> Log do Mininet salvo em: ${mn_log}"
        echo " -> Grafo PNG gerado em:     ${png}"
    done
done

sudo mn -c > /dev/null 2>&1 || true
echo ""
echo "=========================================================="
echo " Bateria de testes concluida com sucesso!                 "
echo "=========================================================="
