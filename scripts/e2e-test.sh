#!/usr/bin/env bash
# End-to-end check used by CI after kind-up.sh:
#   1. wait for the healer to learn a baseline
#   2. ship a bad payment-service release and expect an automatic ROLLBACK
#   3. leak memory in order-service and expect a pod RESTART
set -euo pipefail
NS=self-healing

kubectl -n "$NS" port-forward svc/detector 8090:8090 >/tmp/pf-detector.log 2>&1 &
PF_PID=$!
trap 'kill $PF_PID 2>/dev/null || true' EXIT
sleep 5

wait_for() {  # wait_for <description> <timeout seconds> <command...>
  local what=$1 timeout=$2; shift 2
  local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS - start > timeout )); then echo "Timed out waiting for: $what"; return 1; fi
    sleep 5
  done
  echo "OK: $what ($(( SECONDS - start ))s)"
}

baseline_ready() {
  curl -fs localhost:8090/status | jq -e '.services["payment-service"].ready and .services["order-service"].ready'
}
incident() {  # incident <service> <type> <action>
  curl -fs localhost:8090/incidents | jq -e --arg s "$1" --arg t "$2" --arg a "$3" \
    'any(.[]; .service == $s and .incident_type == $t and .action == $a and .action_success == true)'
}
no_bad_env() {
  ! kubectl -n "$NS" get deploy payment-service -o jsonpath='{.spec.template.spec.containers[0].env[*].name}' \
    | grep -q CHAOS_ERROR_RATE
}

wait_for "healer baseline trained" 420 baseline_ready

echo "==> Bad release of payment-service"
bash scripts/bad-release.sh
wait_for "ERROR_SPIKE detected and rolled back" 300 incident payment-service ERROR_SPIKE ROLLBACK
wait_for "bad env var gone from the deployment" 60 no_bad_env
kubectl -n "$NS" rollout status deployment/payment-service --timeout=180s
kubectl -n "$NS" rollout history deployment/payment-service

echo "==> Memory leak in order-service"
OLD_POD=$(kubectl -n "$NS" get pod -l app=order-service -o jsonpath='{.items[0].metadata.name}')
kubectl -n "$NS" port-forward "pod/$OLD_POD" 18081:8081 >/tmp/pf-order.log 2>&1 &
sleep 4
curl -fs -X POST 'localhost:18081/chaos/memory-leak?mbPerSecond=15'
echo
wait_for "MEMORY_PRESSURE detected and pod restarted" 300 incident order-service MEMORY_PRESSURE RESTART

echo "==> Incidents"
curl -fs localhost:8090/incidents | jq '.[] | {service, incident_type, reason, action, action_detail, recovered, summary}'
