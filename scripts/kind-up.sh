#!/usr/bin/env bash
# Builds the images, creates a kind cluster and deploys everything.
#   ./scripts/kind-up.sh
#   OPENAI_API_KEY=sk-... ./scripts/kind-up.sh   # optional, for LLM incident summaries
set -euo pipefail

CLUSTER=${CLUSTER:-self-healing}
NS=self-healing
cd "$(dirname "$0")/.."

echo "==> Building images"
docker build -t order-service:local --build-arg SERVICE=order-service services/
docker build -t payment-service:local --build-arg SERVICE=payment-service services/
docker build -t detector:local detector/
docker build -t loadgen:local load/

if ! kind get clusters | grep -qx "$CLUSTER"; then
  echo "==> Creating kind cluster $CLUSTER"
  kind create cluster --name "$CLUSTER" --wait 120s
fi

echo "==> Loading images into the cluster"
for image in order-service:local payment-service:local detector:local loadgen:local; do
  kind load docker-image "$image" --name "$CLUSTER"
done

echo "==> Deploying"
kubectl apply -f k8s/namespace.yaml
kubectl -n "$NS" create configmap grafana-datasources \
  --from-file=grafana/provisioning/datasources/prometheus.yml --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create configmap grafana-dashboard-provider \
  --from-file=grafana/provisioning/dashboards/dashboards.yml --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NS" create configmap grafana-dashboards \
  --from-file=grafana/dashboards/self-healing.json --dry-run=client -o yaml | kubectl apply -f -
if [[ -n "${OPENAI_API_KEY:-}" ]]; then
  kubectl -n "$NS" create secret generic openai --from-literal=api-key="$OPENAI_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -
fi
kubectl apply -f k8s/

echo "==> Waiting for pods"
for d in payment-service order-service prometheus detector grafana loadgen; do
  kubectl -n "$NS" rollout status deployment/"$d" --timeout=300s
done

cat <<EOF

Everything is up. In separate terminals:
  kubectl -n $NS port-forward svc/grafana 3000:3000      # dashboard at http://localhost:3000
  kubectl -n $NS port-forward svc/detector 8090:8090     # http://localhost:8090/incidents
The healer needs about two minutes of healthy traffic to learn its baseline.
Then try ./scripts/bad-release.sh or ./scripts/memory-leak.sh
EOF
