"""Clip 11a: start N agent sessions at once against ClickHouse over MCP.
Usage: python load_test.py --agents 50"""

import argparse
import asyncio
import random
import statistics
import time
import uuid

from agent import get_prompt, langfuse, run_agent

TOWNS = ["LONDON", "MANCHESTER", "BIRMINGHAM", "LEEDS", "BRISTOL", "LIVERPOOL",
         "CAMBRIDGE", "OXFORD", "NOTTINGHAM", "SHEFFIELD", "NEWCASTLE UPON TYNE", "CARDIFF"]
TYPES = ["flat", "terraced", "semi-detached", "detached"]
USERS = ["anna", "bram", "chen", "daan", "eva", "femke", "joost", "lotte", "sam", "yara"]
TEMPLATES = [
    "What was the average sale price in {town} in {year}?",
    "How many {ptype} properties were sold in {town} in {year}?",
    "What was the median price of new builds in {town} in {year}?",
    "Which district in {town} had the highest average price in {year}?",
    "How did the number of sales in {town} change between {year} and {next_year}?",
    "What share of sales in {town} were leasehold in {year}?",
    "What was the most expensive {ptype} sold in {town} in {year}?",
]


def make_questions(n: int, seed: int = 7):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        year = rng.randint(2005, 2023)
        out.append(rng.choice(TEMPLATES).format(
            town=rng.choice(TOWNS).title(), ptype=rng.choice(TYPES), year=year, next_year=year + 1))
    return out


async def main(n: int):
    prompt = get_prompt("production")
    questions = make_questions(n)
    run_id = uuid.uuid4().hex[:6]

    print(f"\nStarting {n} agent sessions at once  (prompt v{prompt.version}, run {run_id})\n")
    for i, q in enumerate(questions, 1):
        print(f"  [{i:02d}] {q}")
        await asyncio.sleep(0.03)  # purely so the list scrolls on camera
    print()

    done = 0
    started = time.perf_counter()

    async def one(i, q):
        nonlocal done
        user = USERS[i % len(USERS)]
        try:
            res = await run_agent(q, user_id=user, session_id=f"load-{run_id}-{i:02d}",
                                  prompt=prompt, tags=["load-test"])
            done += 1
            print(f"  done {done:02d}/{n}  {user:<6} {res['queries']} queries  {res['seconds']:>5.1f}s   "
                  f"{(res['final'] or '')[:40]}")
            return res
        except Exception as e:  # keep going if one agent fails
            done += 1
            print(f"  FAIL {done:02d}/{n}  {user:<6} {type(e).__name__}: {e}")
            return None

    results = [r for r in await asyncio.gather(*(one(i, q) for i, q in enumerate(questions))) if r]
    wall = time.perf_counter() - started
    secs = sorted(r["seconds"] for r in results)
    total_q = sum(r["queries"] for r in results)
    p95 = secs[max(0, int(len(secs) * 0.95) - 1)] if secs else 0
    print(f"\n{len(results)}/{n} agents finished in {wall:.1f}s  |  {total_q} ClickHouse queries  |  "
          f"agent p50 {statistics.median(secs) if secs else 0:.1f}s, p95 {p95:.1f}s\n")
    langfuse.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", type=int, default=50)
    asyncio.run(main(ap.parse_args().agents))
