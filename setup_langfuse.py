"""One-time setup (run before recording):
  1. Creates prompt v1 in Langfuse with the `production` label.
  2. Computes ground-truth answers in ClickHouse and uploads them as a Langfuse dataset.
Optional: --candidate also creates the regression prompt (v2) with the `candidate` label,
if you'd rather not type it into the Langfuse UI on camera."""

import argparse
import hashlib
import os
from decimal import Decimal
from pathlib import Path

import clickhouse_connect
from dotenv import load_dotenv
from langfuse import get_client

from eval_questions import QUESTIONS

load_dotenv()
PROMPT_NAME = os.getenv("PROMPT_NAME", "uk-property-agent")
DATASET = os.getenv("DATASET_NAME", "uk-property-eval")
langfuse = get_client()


def create_prompts(candidate: bool):
    try:
        existing = langfuse.get_prompt(PROMPT_NAME, label="production")
        print(f"Prompt '{PROMPT_NAME}' already has a production version (v{existing.version}), skipping v1.")
    except Exception:
        p = langfuse.create_prompt(
            name=PROMPT_NAME,
            prompt=Path("prompts/v1_production.txt").read_text(),
            labels=["production"],
            commit_message="Baseline: always answer from data",
        )
        print(f"Created {PROMPT_NAME} v{p.version} [production]")
    if candidate:
        p = langfuse.create_prompt(
            name=PROMPT_NAME,
            prompt=Path("prompts/v2_candidate_regression.txt").read_text(),
            labels=["candidate"],
            config={"max_queries": 1},
            commit_message="Cut cost and latency: one query on a 1M-row sample, answer only",
        )
        print(f"Created {PROMPT_NAME} v{p.version} [candidate]")


def normalise(value):
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return value


def create_dataset():
    ch = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        secure=os.getenv("CLICKHOUSE_SECURE", "true").lower() == "true",
    )
    try:
        langfuse.create_dataset(name=DATASET, description="UK property questions with ground-truth answers")
    except Exception:
        pass  # already exists
    for question, sql in QUESTIONS:
        answer = normalise(ch.query(sql).result_rows[0][0])
        item_id = "uk-" + hashlib.sha1(question.encode()).hexdigest()[:12]  # stable id, so reruns upsert
        langfuse.create_dataset_item(
            dataset_name=DATASET,
            id=item_id,
            input={"question": question},
            expected_output={"answer": answer},
            metadata={"sql": sql},
        )
        print(f"  {answer!s:>16}  {question}")
    print(f"Dataset '{DATASET}' has {len(QUESTIONS)} items.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", action="store_true", help="also create the v2 regression prompt")
    args = ap.parse_args()
    create_prompts(args.candidate)
    create_dataset()
    langfuse.flush()
