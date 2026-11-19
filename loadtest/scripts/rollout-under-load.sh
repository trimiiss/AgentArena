#!/usr/bin/env bash
# Measures a rolling update under constant traffic, then a rollback of a bad release.
#
#   scripts/rollout-under-load.sh update      restart a deployment during traffic
#   scripts/rollout-under-load.sh rollback    release a broken version, then undo it
#
# Traffic comes from loadtest/steady-requests.js. It reports every 5xx and every
# connection error, which are the numbers in docs/rolling-update.md and docs/rollback.md.
set -uo pipefail

cd "$(dirname "$0")/.."
export MSYS_NO_PATHCONV=1

MODE=${1:-update}
NAMESPACE=agentarena
DEPLOYMENT=${DEPLOYMENT:-run-orchestrator}
RATE=${RATE:-50}
DURATION=${DURATION:-120s}
CONTAINER=rollout-traffic

docker rm -f "$CONTAINER" >/dev/null 2>&1
docker run -d --name "$CONTAINER" --network kind -v "$(pwd -W 2>/dev/null || pwd)/loadtest:/scripts" \
  -e BASE_URL=http://agentarena-control-plane -e RATE="$RATE" -e DURATION="$DURATION" \
  grafana/k6:1.8.2 run --quiet /scripts/steady-requests.js >/dev/null

echo "Traffic started: $RATE requests per second for $DURATION"
sleep 15

started=$SECONDS
case "$MODE" in
  update)
    echo "kubectl rollout restart deployment/$DEPLOYMENT"
    kubectl -n "$NAMESPACE" rollout restart "deployment/$DEPLOYMENT"
    kubectl -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" --timeout=180s
    echo "Rollout finished in $((SECONDS - started)) s"
    ;;
  rollback)
    echo "Releasing a broken version: a wrong database password"
    kubectl -n "$NAMESPACE" set env "deployment/$DEPLOYMENT" \
      DATABASE_URL=postgresql+asyncpg://runs_svc:wrong-password@postgres:5432/runs_db
    kubectl -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" --timeout=30s
    echo "Rollout did not finish, as expected. Pods now:"
    kubectl -n "$NAMESPACE" get pods -l "app.kubernetes.io/name=$DEPLOYMENT,app.kubernetes.io/component=api"
    echo
    echo "kubectl rollout undo deployment/$DEPLOYMENT"
    undo_started=$SECONDS
    kubectl -n "$NAMESPACE" rollout undo "deployment/$DEPLOYMENT"
    kubectl -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" --timeout=120s
    echo "Rollback finished in $((SECONDS - undo_started)) s"
    kubectl -n "$NAMESPACE" get pods -l "app.kubernetes.io/name=$DEPLOYMENT,app.kubernetes.io/component=api"
    kubectl -n "$NAMESPACE" rollout history "deployment/$DEPLOYMENT" | tail -4
    ;;
  *)
    echo "Unknown mode: $MODE" >&2
    docker rm -f "$CONTAINER" >/dev/null
    exit 1
    ;;
esac

echo "Waiting for the traffic to finish"
docker wait "$CONTAINER" >/dev/null
docker logs "$CONTAINER" 2>&1 | tail -40
docker rm -f "$CONTAINER" >/dev/null
