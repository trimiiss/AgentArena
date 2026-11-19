#!/usr/bin/env bash
# Proves the NetworkPolicies are enforced, by trying real connections from inside
# real pods. Allowed paths must connect and forbidden paths must time out.
set -uo pipefail

NAMESPACE=agentarena
failures=0

# Tries a TCP connection from inside a pod, with Python since images have no curl.
attempt() {
  local pod=$1 host=$2 port=$3
  kubectl -n "$NAMESPACE" exec "$pod" -- python -c "
import socket, sys
try:
    socket.create_connection(('$host', $port), timeout=3).close()
except OSError:
    sys.exit(1)
" >/dev/null 2>&1
}

pod_of() {
  kubectl -n "$NAMESPACE" get pods -l "$1" -o jsonpath='{.items[0].metadata.name}'
}

expect() {
  local verdict=$1 description=$2 pod=$3 host=$4 port=$5
  if attempt "$pod" "$host" "$port"; then actual=allowed; else actual=blocked; fi
  if [ "$actual" = "$verdict" ]; then
    printf '  ok      %-8s %s\n' "$actual" "$description"
  else
    printf '  WRONG   %-8s %s (expected %s)\n' "$actual" "$description" "$verdict"
    failures=$((failures + 1))
  fi
}

worker=$(pod_of app.kubernetes.io/component=worker)
scenario=$(pod_of app.kubernetes.io/name=scenario-service)
identity=$(pod_of app.kubernetes.io/name=identity-service)
registry=$(pod_of app.kubernetes.io/name=agent-registry)
sample=$(pod_of app.kubernetes.io/name=sample-agent)

echo "Paths that must work"
expect allowed "worker to postgres" "$worker" postgres 5432
expect allowed "worker to rabbitmq" "$worker" rabbitmq 5672
expect allowed "worker to agent-registry" "$worker" agent-registry 8000
expect allowed "worker to scenario-service" "$worker" scenario-service 8000
expect allowed "worker to sample-agent" "$worker" sample-agent 8000
expect allowed "scenario-service to postgres" "$scenario" postgres 5432
expect allowed "scenario-service to identity-service" "$scenario" identity-service 8000

echo "Paths that must be blocked"
expect blocked "scenario-service to rabbitmq" "$scenario" rabbitmq 5672
expect blocked "scenario-service to agent-registry" "$scenario" agent-registry 8000
expect blocked "scenario-service to sample-agent" "$scenario" sample-agent 8000
expect blocked "identity-service to rabbitmq" "$identity" rabbitmq 5672
expect blocked "identity-service to agent-registry" "$identity" agent-registry 8000
expect blocked "agent-registry to rabbitmq" "$registry" rabbitmq 5672
expect blocked "sample-agent to postgres" "$sample" postgres 5432
expect blocked "sample-agent to rabbitmq" "$sample" rabbitmq 5672
expect blocked "sample-agent to agent-registry" "$sample" agent-registry 8000
expect blocked "worker to the cloud metadata address" "$worker" 169.254.169.254 80
expect blocked "worker to the Kubernetes API" "$worker" kubernetes.default.svc 443

echo
if [ "$failures" -eq 0 ]; then
  echo "All network paths behave as declared"
else
  echo "$failures paths behave differently from the policy"
fi
exit "$failures"
