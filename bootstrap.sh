#!/usr/bin/env bash
# One-command setup for the demo.
#   ./bootstrap.sh            check .env, install, start MCP server, seed Langfuse, smoke test
#   ./bootstrap.sh baseline   ...and also run the baseline experiment (prompt v1)
#   ./bootstrap.sh stop       stop the background MCP server
set -euo pipefail
cd "$(dirname "$0")"

PIDFILE=.mcp.pid
LOG=.mcp.log
ok()   { printf "  \033[32mok\033[0m    %s\n" "$1"; }
fail() { printf "  \033[31mFAIL\033[0m  %s\n" "$1"; exit 1; }
step() { printf "\n\033[1m%s\033[0m\n" "$1"; }

if [[ "${1:-}" == "stop" ]]; then
  [[ -f $PIDFILE ]] && kill "$(cat $PIDFILE)" 2>/dev/null && rm -f $PIDFILE && echo "MCP server stopped." || echo "No MCP server running."
  exit 0
fi

step "1/6  Checking .env"
[[ -f .env ]] || { cp .env.example .env; fail "Created .env from .env.example. Fill it in and rerun."; }
if ! grep -q '^CLICKHOUSE_MCP_AUTH_TOKEN=[0-9a-f]\{64\}$' .env; then
  TOKEN=$(openssl rand -hex 32)
  if grep -q '^CLICKHOUSE_MCP_AUTH_TOKEN=' .env; then
    sed -i.bak "s/^CLICKHOUSE_MCP_AUTH_TOKEN=.*/CLICKHOUSE_MCP_AUTH_TOKEN=$TOKEN/" .env && rm -f .env.bak
  else
    echo "CLICKHOUSE_MCP_AUTH_TOKEN=$TOKEN" >> .env
  fi
  ok "generated CLICKHOUSE_MCP_AUTH_TOKEN"
fi
set -a; source .env; set +a
for v in CLICKHOUSE_HOST CLICKHOUSE_USER CLICKHOUSE_PASSWORD LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_BASE_URL ANTHROPIC_API_KEY; do
  val="${!v:-}"
  [[ -z "$val" || "$val" == *"your-"* || "$val" == *"change-me"* || "$val" == *"..." ]] && fail "$v is not set in .env"
done
ok "all required values present"

step "2/6  Installing Python dependencies"
command -v uvx >/dev/null || fail "uv is not installed. Run: brew install uv   (or: pip install uv)"
[[ -d .venv ]] || python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip >/dev/null
pip install -q -r requirements.txt
ok "dependencies installed in .venv"

step "3/6  Starting the ClickHouse MCP server"
if curl -sf localhost:8000/health >/dev/null 2>&1; then
  ok "already running"
else
  CLICKHOUSE_MCP_SERVER_TRANSPORT=http nohup uvx mcp-clickhouse >"$LOG" 2>&1 &
  echo $! > $PIDFILE
  for _ in $(seq 1 45); do curl -sf localhost:8000/health >/dev/null 2>&1 && break; sleep 2; done
  curl -sf localhost:8000/health >/dev/null 2>&1 || { tail -20 "$LOG"; fail "MCP server did not become healthy (log above, full log in $LOG)"; }
  ok "running on http://127.0.0.1:8000/mcp (log: $LOG, stop with ./bootstrap.sh stop)"
fi

step "4/6  Seeding Langfuse (prompt v1 + eval dataset)"
python setup_langfuse.py || fail "setup_langfuse.py failed (check ClickHouse and Langfuse credentials)"
ok "Langfuse seeded"

step "5/6  Smoke test: one agent run"
python agent.py "What was the average sale price in London in 2023?" || fail "agent run failed"
ok "check Langfuse > Tracing for a trace from user 'tyler'"

if [[ "${1:-}" == "baseline" ]]; then
  step "6/6  Baseline experiment on prompt v1 (production)"
  python run_experiment.py --label production
  ok "baseline recorded"
else
  step "6/6  Skipped baseline. Run ./bootstrap.sh baseline when the smoke test looks good."
fi

printf "\nReady. Record with:\n  source .venv/bin/activate\n  python load_test.py --agents 50          # clip 11a\n  python run_experiment.py --label candidate  # clip 11c\n\n"
