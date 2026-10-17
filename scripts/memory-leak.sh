#!/usr/bin/env bash
# Starts a memory leak in order-service. The healer should restart the pod.
set -euo pipefail
NS=self-healing
POD=$(kubectl -n "$NS" get pod -l app=order-service -o jsonpath='{.items[0].metadata.name}')

kubectl -n "$NS" port-forward "pod/$POD" 18081:8081 >/dev/null 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 3

curl -fs -X POST 'localhost:18081/chaos/memory-leak?mbPerSecond=15'
echo
echo "Leak started in $POD. Watch for it to be replaced:"
echo "  kubectl -n $NS get pods -l app=order-service -w"
