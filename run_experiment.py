"""Clip 11c: run the eval dataset against a prompt version and score it.
  python run_experiment.py --label production      # baseline (run this before recording)
  python run_experiment.py --label candidate       # the change under test
Then compare the two runs in Langfuse: Datasets > uk-property-eval > Experiments."""

import argparse
import os
import re
import uuid

from langfuse import Evaluation

from agent import final_answer, get_prompt, langfuse, run_agent

DATASET = os.getenv("DATASET_NAME", "uk-property-eval")
TOLERANCE = 0.02  # 2% relative tolerance for numeric answers


def _num(s):
    try:
        return float(re.sub(r"[£$,%\s]", "", str(s)))
    except ValueError:
        return None


def _norm(s):
    return re.sub(r"[^A-Z0-9 ]", "", str(s).upper()).strip()


def accuracy(*, output, expected_output, **kwargs):
    got = final_answer(output["answer"]) if output else None
    expected = expected_output["answer"]
    if got is None:
        return Evaluation(name="accuracy", value=0.0, comment="No FINAL ANSWER line")
    if isinstance(expected, (int, float)):
        g = _num(got)
        ok = g is not None and abs(g - expected) <= max(abs(expected) * TOLERANCE, 0.5)
    else:
        ok = _norm(expected) == _norm(got) or _norm(expected) in _norm(got)
    return Evaluation(name="accuracy", value=1.0 if ok else 0.0,
                      comment=f"expected {expected}, got {got}")


def used_database(*, output, **kwargs):
    q = output["queries"] if output else 0
    return Evaluation(name="used_database", value=1.0 if q > 0 else 0.0, comment=f"{q} queries")


def avg_accuracy(*, item_results, **kwargs):
    vals = [e.value for r in item_results for e in r.evaluations if e.name == "accuracy"]
    avg = sum(vals) / len(vals) if vals else 0.0
    return Evaluation(name="avg_accuracy", value=avg, comment=f"{avg:.0%} correct")


def main(label, version):
    prompt = get_prompt(label=label, version=version)
    tag = f"v{prompt.version}" + (f" ({label})" if version is None else "")
    run_id = uuid.uuid4().hex[:6]

    async def task(*, item, **kwargs):
        return await run_agent(item.input["question"], user_id="eval-bot",
                               session_id=f"eval-{tag}-{run_id}", prompt=prompt,
                               tags=["experiment", tag])

    result = langfuse.get_dataset(DATASET).run_experiment(
        name=f"prompt {tag}",
        run_name=f"prompt {tag} - {run_id}",
        description=f"{os.getenv('PROMPT_NAME', 'uk-property-agent')} {tag}",
        task=task,
        evaluators=[accuracy, used_database],
        run_evaluators=[avg_accuracy],
        max_concurrency=5,
        metadata={"prompt_version": str(prompt.version)},
    )
    print(result.format())
    langfuse.flush()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="production")
    ap.add_argument("--version", type=int, default=None)
    a = ap.parse_args()
    main(a.label, a.version)
