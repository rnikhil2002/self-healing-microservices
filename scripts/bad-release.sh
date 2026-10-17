#!/usr/bin/env bash
# Ships a "bad release" of payment-service that fails 60% of requests.
# The healer should spot the error spike, see the fresh rollout and roll it back.
set -euo pipefail
NS=self-healing

kubectl -n "$NS" set env deployment/payment-service CHAOS_ERROR_RATE=0.6 APP_VERSION=1.1.0-bad
kubectl -n "$NS" rollout status deployment/payment-service --timeout=180s
echo "Bad release is live. Watch it get rolled back:"
echo "  kubectl -n $NS rollout history deployment/payment-service"
echo "  curl -s localhost:8090/incidents | jq '.[0] | {incident_type, action, action_detail, summary}'"
