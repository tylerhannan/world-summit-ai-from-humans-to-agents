# From human to agents: demo

The demo from the talk "From human to agents" at World Summit AI Amsterdam 2026. Software is moving from human speed to agent speed, and this demo shows three engineering practices that matter once agents are your main users:

1. **The medium:** agents arrive concurrently and talk to your data over tools. Here, 50 agents query ClickHouse at once over MCP.
2. **Accountability:** every action is attributed to a user, every step is on the record, and the agent's database user is read-only.
3. **Feedback:** a prompt change is evaluated against known answers before it ships, and a plausible regression is caught.

It's an agentic data stack in about 450 lines of Python:

- Claude plans and writes SQL
- The ClickHouse MCP server runs the SQL on ClickHouse Cloud as a read-only user
- Langfuse traces every step, holds the prompt versions and runs the evals

The dataset is [UK property prices](https://clickhouse.com/docs/get-started/sample-datasets/uk-price-paid): about 31M sales since 1995.

```
agent.py              the agent (Claude + MCP tools + Langfuse tracing)
load_test.py          N agents at once
run_experiment.py     score a prompt version against the eval dataset
setup_langfuse.py     one-time: prompt v1 + eval dataset with ground-truth answers
eval_questions.py     20 questions + ground-truth SQL
prompts/              v1 (production) and v2 (a deliberate regression)
sql/                  user setup, data load, query_log queries
```

## What you need

- A [ClickHouse Cloud](https://clickhouse.com/cloud) service (the free trial is enough)
- A [Langfuse Cloud](https://cloud.langfuse.com) project
- An [Anthropic API key](https://console.anthropic.com)
- Python 3.10+ and [uv](https://docs.astral.sh/uv/) (`brew install uv`), which runs the MCP server

A full run (50-agent load test plus two 20-question experiments) costs well under $1 with the default model, Claude Haiku 4.5.

## Quick setup

Do steps 1 and 2 below, fill in `.env` (`cp .env.example .env`), then run:

```bash
./bootstrap.sh            # checks .env, installs, starts the MCP server, seeds Langfuse, smoke test
./bootstrap.sh baseline   # same, plus the baseline experiment
./bootstrap.sh stop       # stop the MCP server afterwards
```

It generates the MCP auth token for you. Everything below is the manual version of the same steps.

## Manual setup (about 20 minutes)

**1. ClickHouse Cloud.** In the SQL console, run `sql/01_load_uk_price_paid.sql` (the insert takes a minute or two), then `sql/00_create_user.sql` with a real password. If you prefer the CLI, [`clickhousectl`](https://clickhouse.com/docs/cloud) can create the service and run both files.

**2. Langfuse Cloud.** Create a project, then go to **Settings → API Keys** and create a key pair. Use `https://cloud.langfuse.com` (EU) or `https://us.cloud.langfuse.com` (US) as `LANGFUSE_BASE_URL`.

**3. Local environment**

```bash
cp .env.example .env          # fill in everything
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**4. ClickHouse MCP server.** Run it in its own terminal and leave it running. `uvx` gives it its own environment.

```bash
set -a; source .env; set +a
uvx mcp-clickhouse            # serves http://127.0.0.1:8000/mcp
curl localhost:8000/health    # -> OK
```

**5. Seed Langfuse.** This creates prompt v1 (`production`) and the dataset. It computes each expected answer by running the ground-truth SQL on your service.

```bash
python setup_langfuse.py
```

**6. Smoke test**

```bash
python agent.py "What was the average sale price in London in 2023?"
```

The trace appears in Langfuse under **Tracing**, with user `tyler` and session `cli-test`.

**7. Record the baseline**

```bash
python run_experiment.py --label production
```

## The demo in three parts

### 1. Agents under load

```bash
python load_test.py --agents 50
```

50 agent sessions start at once, each with its own question and user. Each `done` line shows the user, how many queries the agent ran, how long it took and its answer. Then see it from ClickHouse's side: run query 1 from `sql/02_demo_query_log.sql` in the SQL console for the query count and p95 latency, and query 2 (as a chart) for queries per second.

If you hit Anthropic rate limits, use `--agents 30`.

### 2. Every step on the record

In Langfuse:

1. Go to **Tracing** and filter by a user, for example `anna`. The tag `load-test` marks the run.
2. Open a trace (sort by latency to find one with several queries). Step through `llm step 1` → `clickhouse.run_query` → `llm step 2`. Each step has its latency, tokens and cost, and the SQL the agent wrote is in the `run_query` span.
3. Each generation is linked to the prompt version that produced it (`uk-property-agent` v1).
4. Open **Sessions** to see the whole run as one conversation.

### 3. Catching a regression

`prompts/v2_candidate_regression.txt` is a deliberate regression, so don't use it as an example of a good prompt. It reads like a reasonable cost cut: run one query on a "1M-row sample" and reply with the answer only. But `LIMIT 1000000` isn't a sample. It returns the first rows ClickHouse reads, which are mostly old sales, so a question about 2023 gets `0`.

1. Create v2 with the `candidate` label, **not** `production`:
   ```bash
   python setup_langfuse.py --candidate
   ```
   Or do it in the UI: **Prompts** → `uk-property-agent` → **New version**. Paste the file, set the config to `{"max_queries": 1}` and add the label `candidate`.
2. Run the eval against it:
   ```bash
   python run_experiment.py --label candidate
   ```
3. In Langfuse, go to **Datasets** → `uk-property-eval` → **Experiments**, select the v1 and v2 runs and compare. Accuracy drops from 100% to about 20%. `used_database` stays at 1, because the agent still queried: it just queried the wrong rows. Latency and cost are flat, so no dashboard would have flagged it. Open a failed item to see `expected 861502, got 0`.
4. Back in **Prompts**, `production` is still on v1. Caught before it shipped.

## Notes

- **Model:** the default is `claude-haiku-4-5-20251001`, which is fast and cheap for 50 parallel agents. Set `MODEL` in `.env` to change it. Each run is bounded by `MAX_STEPS=8` and `max_tokens=1024`.
- **Read-only access:** the agent connects as `mcp_agent`, with `SELECT` on `uk.*` only. The MCP server also runs queries with `readonly=1` by default.
- **Query budget:** a prompt's Langfuse config can set `max_queries`. `agent.py` enforces it: once the budget is used, it tells the model to answer and turns off tool calls.
- **Scoring:** answers are numeric within ±2%, or a name match. The evaluator reads the `FINAL ANSWER:` line the prompt asks for. Ground truth is computed from the live data by `setup_langfuse.py`.
- **First version of the regression:** v2 originally told the agent to answer from general knowledge. Claude Haiku 4.5 ignored it and queried the database anyway, even when told outright not to use the tool.
- **MCP client version:** the client uses the `mcp` 1.x API (pinned `<2`). The 2.x client API is different.
- **ClickHouse Cloud query log:** each replica keeps its own `system.query_log`, so the queries in `sql/02_demo_query_log.sql` use `clusterAllReplicas`.

## License

[Apache 2.0](LICENSE)
