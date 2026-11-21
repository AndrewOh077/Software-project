#!/usr/bin/env bash
# Everything Person 3 measures, in order, with one command:  make results
#
#   1. Week 5  Mode A (no protection): normal, spike, failure
#   2. Week 5  Mode B (rules):         normal, spike, failure  (+ save metrics for the AI)
#   3. Week 8  teach the AI "normal" from the Mode B normal run
#   4. Week 8  sick run (app-3 fails 30% quietly) + score the AI on spike, failure, sick
#   5. Week 7  save results: mode-baseline.md, ai-report.html, mttr.csv
#
# About 25 minutes at full length. Faster rehearsal: SCENARIO_SCALE=0.3 make results
# Default uses Docker (`make up` first). No Docker? NATIVE=1 make results  (needs a
# `prometheus` binary on your PATH, or PROMETHEUS_BIN=/path/to/prometheus).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
RAW=$ROOT/experiments/results/raw
OUT=$ROOT/experiments/results
PY=${PYTHON:-python}
COMPOSE="docker compose -f $ROOT/docker-compose.dev.yml"
export SCENARIO_SCALE=${SCENARIO_SCALE:-1}
mkdir -p "$RAW"
cd "$ROOT"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
wait_for() {  # url
  for _ in $(seq 60); do curl -fs "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "gave up waiting for $1"; exit 1
}

# ---------------------------------------------------------------- start the stack
PIDS=()
if [[ ${NATIVE:-0} == 1 ]]; then
  export PYTHONPATH=$ROOT EVENTS_FILE=$RAW/events.jsonl
  NAT=$RAW/native && mkdir -p "$NAT"
  trap 'kill ${PIDS[@]+"${PIDS[@]}"} 2>/dev/null || true' EXIT
  for i in 1 2 3; do
    SERVER_ID=app-$i READY_DELAY_SEC=3 BASE_LATENCY_MS=20 \
      $PY -m uvicorn fakes.fake_app:app --port 800$i --log-level warning >"$NAT/app$i.log" 2>&1 &
    PIDS+=($!)
  done
  sed -e 's/gateway:8000/localhost:8000/' -e 's/app-\([123]\):8000/localhost:800\1/g' \
      -e 's/host.docker.internal:8010/localhost:8010/' \
      monitoring/prometheus/prometheus.yml >"$NAT/prometheus.yml"
  ${PROMETHEUS_BIN:-prometheus} --config.file="$NAT/prometheus.yml" \
    --storage.tsdb.path="$NAT/tsdb" --web.listen-address=:9090 >"$NAT/prometheus.log" 2>&1 &
  PIDS+=($!)
  GW_PID=""
  gateway() {  # on|off
    [[ -n $GW_PID ]] && kill "$GW_PID" 2>/dev/null && sleep 1
    BACKENDS="app-1=http://localhost:8001,app-2=http://localhost:8002,app-3=http://localhost:8003" \
      HEALTH_CHECK_INTERVAL_SEC=1 HEALTH_CHECKS=$1 \
      $PY -m uvicorn fakes.stub_gateway:app --port 8000 --log-level warning >"$NAT/gateway.log" 2>&1 &
    GW_PID=$!
    PIDS+=($GW_PID)
  }
  locust_run() {  # scenario-file csv-name [VAR=value ...]
    local file=$1 name=$2; shift 2
    (cd loadtest && env ${@+"$@"} $PY -m locust -f "$file" --headless --host http://localhost:8000 \
      --csv "$RAW/$name" --only-summary)
  }
  CHAOS_KILL=(CHAOS_TARGET=http://localhost:8002 CHAOS_SERVER_ID=app-2)
  CHAOS_SICK=(CHAOS_TARGET=http://localhost:8003 CHAOS_SERVER_ID=app-3)
else
  curl -fs http://localhost:8000/health >/dev/null 2>&1 || $COMPOSE up -d --build
  gateway() { HEALTH_CHECKS=$1 $COMPOSE up -d gateway >/dev/null; }
  locust_run() {
    local file=$1 name=$2; shift 2
    local envs=(); for kv in "$@"; do envs+=(-e "$kv"); done
    $COMPOSE run --rm ${envs[@]+"${envs[@]}"} locust -f "$file" --headless --host http://gateway:8000 \
      --csv "/results/$name" --only-summary
  }
  CHAOS_KILL=(CHAOS_TARGET=http://app-2:8000 CHAOS_SERVER_ID=app-2)
  CHAOS_SICK=(CHAOS_TARGET=http://app-3:8000 CHAOS_SERVER_ID=app-3)
fi

# One run: start the scenario, then (optionally) save its Prometheus numbers for the AI.
run() {  # name scenario-file save-metrics(yes|no) [VAR=value ...]
  local name=$1 file=$2 save=$3; shift 3
  local start; start=$(date +%s)
  say "$name ($file)"
  # Locust exits 1 when any request failed. In these scenarios that is the point, not a crash.
  locust_run "$file" "$name" ${@+"$@"} || true
  sleep 10  # let the last scrape and any recovery land
  if [[ $save == yes ]]; then
    local minutes; minutes=$($PY -c "import time; print(round((time.time() - $start) / 60, 2))")
    $PY -m ai.anomaly collect --minutes "$minutes" --out "$RAW/${name}_metrics.csv"
  fi
  sleep 20  # rate() windows forget this run before the next one starts
}

wait_for http://localhost:9090/-/ready

# ---------------------------------------------------------------- 1 + 2. Week 5 baseline
for mode in A B; do
  [[ $mode == A ]] && gateway off || gateway on
  wait_for http://localhost:8000/health
  sleep 5  # health checks see every server before traffic starts
  save=$([[ $mode == B ]] && echo yes || echo no)
  run "mode$mode-normal" scenario_normal.py "$save"
  run "mode$mode-spike" scenario_spike.py "$save"
  run "mode$mode-failure" scenario_failure.py "$save" "${CHAOS_KILL[@]}"
done

# ---------------------------------------------------------------- 3 + 4. Week 8–9 AI
say "teach the AI what normal looks like"
$PY -m ai.anomaly train "$RAW/modeB-normal_metrics.csv"
run sick scenario_sick.py yes "${CHAOS_SICK[@]}"
for r in modeB-spike modeB-failure sick; do
  say "score the AI on $r"
  $PY -m ai.anomaly evaluate "$RAW/${r}_metrics.csv" "$RAW/events.jsonl" --json "$RAW/${r}_eval.json"
done

# ---------------------------------------------------------------- 5. Week 7 save results
say "save results"
$PY experiments/compare_modes.py "$RAW" --out "$OUT/mode-baseline.md" --json "$RAW/modes.json"
$PY -m ai.report "$OUT/ai-report.html" \
  spike="$RAW/modeB-spike_eval.json" failure="$RAW/modeB-failure_eval.json" sick="$RAW/sick_eval.json"
$PY experiments/mttr.py "$RAW/events.jsonl" --csv "$RAW/mttr.csv" >/dev/null

say "done"
echo "Week 5 table : experiments/results/mode-baseline.md"
echo "AI report    : experiments/results/ai-report.html   (open it in a browser)"
echo "Raw numbers  : experiments/results/raw/"
