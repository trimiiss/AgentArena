#!/usr/bin/env bash
# Creates a local kind cluster and deploys AgentArena to it.
#
#   scripts/deploy-kind.sh           create or update the cluster and deploy
#   scripts/deploy-kind.sh --delete  remove the cluster
#
# Needs docker, kubectl and kind on PATH, or kind in .tools/. The app is then at
# http://127.0.0.1:8088. Stop the Compose stack first if memory is tight.
set -euo pipefail

CLUSTER=agentarena
ROOT=$(cd "$(dirname "$0")/.." && pwd)
INGRESS_VERSION=controller-v1.15.1
METRICS_VERSION=v0.9.1
IMAGES="identity-service agent-registry scenario-service run-orchestrator evaluation-service sample-agent frontend"
INFRA_IMAGES="postgres:17.11-alpine3.24 rabbitmq:4.3.7-management-alpine prom/prometheus:v3.16.0 grafana/grafana:12.4.14 jaegertracing/jaeger:2.23.0 otel/opentelemetry-collector-contrib:0.164.0"

cd "$ROOT"
export PATH="$ROOT/.tools:$PATH"

for tool in docker kubectl kind; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. See docs/deployment.md." >&2; exit 1; }
done

if [ "${1:-}" = "--delete" ]; then
  kind delete cluster --name "$CLUSTER"
  exit 0
fi

random_hex() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
random_key() { head -c 32 /dev/urandom | base64 | tr -d '\n'; }

write_secrets() {
  local target=infrastructure/kind/overlay/secrets.env
  [ -f "$target" ] && return
  echo "Generating new secrets in $target"
  {
    echo "POSTGRES_PASSWORD=$(random_hex)"
    echo "IDENTITY_DB_PASSWORD=$(random_hex)"
    echo "AGENTS_DB_PASSWORD=$(random_hex)"
    echo "SCENARIOS_DB_PASSWORD=$(random_hex)"
    echo "RUNS_DB_PASSWORD=$(random_hex)"
    echo "EVALUATIONS_DB_PASSWORD=$(random_hex)"
    echo "RABBITMQ_USER=agentarena"
    echo "RABBITMQ_PASSWORD=$(random_hex)"
    echo "INTERNAL_SERVICE_TOKEN=$(random_hex)"
    echo "IDENTITY_KEY_ENCRYPTION_KEY=$(random_key)"
    echo "CREDENTIALS_ENCRYPTION_KEY=$(random_key)"
    echo "GRAFANA_ADMIN_PASSWORD=$(random_hex)"
    echo "SAMPLE_AGENT_API_KEY=$(random_hex)"
    echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}"
  } > "$target"
}

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --config infrastructure/kind/kind-config.yaml --wait 120s
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

echo "Installing ingress-nginx and metrics-server"
kubectl apply -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/$INGRESS_VERSION/deploy/static/provider/kind/deploy.yaml" >/dev/null
kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/$METRICS_VERSION/components.yaml" >/dev/null
# kind's kubelets use self-signed certificates.
kubectl -n kube-system patch deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]' >/dev/null 2>&1 || true

if [ -z "${SKIP_BUILD:-}" ]; then
  echo "Building images"
  docker compose --env-file .env.example build
fi

echo "Loading images into the cluster"
for image in $IMAGES; do
  kind load docker-image "agentarena/$image:local" --name "$CLUSTER"
done
# An optimisation only: multi-platform images can fail to load, and then the node
# simply pulls them from the registry.
for image in $INFRA_IMAGES; do
  if docker image inspect "$image" >/dev/null 2>&1; then
    kind load docker-image "$image" --name "$CLUSTER" >/dev/null 2>&1 || echo "The node will pull $image itself"
  fi
done

write_secrets
already_deployed=$(kubectl -n agentarena get deployments --no-headers 2>/dev/null | wc -l)
kubectl apply -k infrastructure/kind/overlay

# Images keep the :local tag, so without this a redeploy leaves the old code running.
if [ "$already_deployed" -gt 0 ]; then
  kubectl -n agentarena rollout restart deployment
fi

kubectl -n ingress-nginx wait --for=condition=available deployment/ingress-nginx-controller --timeout=180s
kubectl -n agentarena rollout status statefulset/postgres --timeout=180s
kubectl -n agentarena rollout status statefulset/rabbitmq --timeout=180s
for deployment in $(kubectl -n agentarena get deployments -o name); do
  kubectl -n agentarena rollout status "$deployment" --timeout=240s
done
kubectl -n agentarena-observability wait --for=condition=available deployment --all --timeout=120s

echo
kubectl -n agentarena get pods
echo
echo "AgentArena is running at http://127.0.0.1:8088"
echo "Grafana:    kubectl -n agentarena-observability port-forward svc/grafana 3000"
echo "Jaeger:     kubectl -n agentarena-observability port-forward svc/jaeger 16686"
echo "Prometheus: kubectl -n agentarena-observability port-forward svc/prometheus 9090"
