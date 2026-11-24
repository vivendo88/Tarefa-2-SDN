#!/usr/bin/env bash
set -e

mkdir -p logs results

echo "=========================================================="
echo " Validando permissao sudo para o Mininet...               "
echo "=========================================================="
sudo -v

source .venv/bin/activate

TOPOLOGIAS=("torus" "spineleaf")
ALGORITMOS=("ospf" "ecmp")
EXPERIMENTOS=(1 2)

for topo in "${TOPOLOGIAS[@]}"; do
    for algo in "${ALGORITMOS[@]}"; do
        for exp in "${EXPERIMENTOS[@]}"; do
            png="results/topologia_${topo}_${algo}_exp${exp}_descoberta.png"
            ryu_log="logs/ryu_${topo}_${algo}_exp${exp}.log"
            mn_log="logs/mininet_${topo}_${algo}_exp${exp}.log"

            echo ""
            echo "=================================================================="
            echo " Executando: Topologia=${topo} | Roteamento=${algo} | Exp=${exp}  "
            echo "=================================================================="

            sudo mn -c > /dev/null 2>&1 || true

            # Inicia o controlador Ryu em background com o algoritmo escolhido
            TOPO_OUTPUT_PNG="${png}" python3 topo_discover.py --routing "${algo}" > "${ryu_log}" 2>&1 &
            RYU_PID=$!
            sleep 2

            # Executa o fctmain.py com --exp 1 ou --exp 2
            sudo python3 fctmain.py \
                --topology "${topo}" \
                --routing "${algo}" \
                --exp "${exp}" \
                --transfer-size 10M \
                --failure-delay 15 \
                --output-dir results > "${mn_log}" 2>&1 || true

            # Encerra o controlador Ryu
            kill "${RYU_PID}" 2>/dev/null || true
            wait "${RYU_PID}" 2>/dev/null || true

            echo " -> Log do Ryu salvo em:     ${ryu_log}"
            echo " -> Log do Mininet salvo em: ${mn_log}"
            echo " -> Grafo PNG salvo em:      ${png}"
        done
    done
done

echo ""
echo "=================================================================="
echo " Consolidando graficos comparativos (plot_results.py)...          "
echo "=================================================================="
python3 plot_results.py --results-dir results

sudo mn -c > /dev/null 2>&1 || true
echo ""
echo "=================================================================="
echo " Bateria de experimentos (Exp 1 e Exp 2) concluida com sucesso!   "
echo "=================================================================="
