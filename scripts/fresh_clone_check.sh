#!/usr/bin/env bash
# Week 11-12: "can a stranger follow the README and get a working system?"
# Clones the repo fresh into a temp folder, builds everything, runs unit + e2e tests
# with the real gateway, then cleans up.
#
#   ./scripts/fresh_clone_check.sh                       # main branch of the team repo
#   ./scripts/fresh_clone_check.sh <repo-url> <branch>
#
# WARNING: stops any running containers named app1..3, controller, gateway, prometheus, grafana
#          (the names are fixed in docker-compose.yml, so two stacks cannot run at once).
set -euo pipefail

REPO=${1:-https://github.com/AndrewOh077/Software-project.git}
BRANCH=${2:-main}
WORK=$(mktemp -d)
START=$(date +%s)

cleanup() {
  (cd "$WORK/repo" 2>/dev/null && docker compose down -v --remove-orphans >/dev/null 2>&1) || true
  rm -rf "$WORK"
}
trap cleanup EXIT
step() { printf '\n==> %s\n' "$*"; }

step "check tools"
for t in git docker python3 curl; do
  command -v "$t" >/dev/null || { echo "missing: $t"; exit 1; }
done
docker compose version

step "clone $REPO ($BRANCH)"
git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$WORK/repo"
cd "$WORK/repo"

step "unit tests (no Docker needed)"
python3 -m unittest discover -s tests/unit

step "stop containers with the same names"
docker rm -f app1 app2 app3 controller gateway gateway-mock prometheus grafana >/dev/null 2>&1 || true

step "build and start (first time: several minutes)"
docker compose up -d --build

step "wait for controller and gateway"
for _ in $(seq 1 90); do
  if curl -fsS localhost:9100/healthz >/dev/null 2>&1 && curl -fsS localhost:8080/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS localhost:9100/healthz >/dev/null && curl -fsS localhost:8080/health >/dev/null

step "e2e scenario tests with the real gateway (about 5 minutes)"
REAL_GATEWAY=1 GATEWAY_ADMIN=http://localhost:8080 python3 -m unittest discover -s tests/e2e -v

echo
echo "OK: fresh clone of $BRANCH builds and passes all tests in $(( $(date +%s) - START ))s"
