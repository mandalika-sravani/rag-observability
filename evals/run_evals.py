"""Run the golden set, score it, and compare against a saved baseline.

    python -m evals.run_evals --label v1 --save-baseline     # first time
    python -m evals.run_evals --label v2 --top-k 1           # try a change
    -> exits with code 1 if any quality metric regressed (this is what fails CI)
"""
import argparse
import json
import os
import statistics
import sys
from pathlib import Path

from app import rag
from app.telemetry import flush
from evals import metrics

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "evals" / "golden.jsonl"
BASELINE = ROOT / "evals" / "baseline.json"            # real model (your Claude numbers)
BASELINE_MOCK = ROOT / "evals" / "baseline_mock.json"  # mock mode (used by CI)
RESULTS = ROOT / "results"

# metric -> max allowed DROP vs baseline (quality) or max allowed RISE ratio (cost)
QUALITY_TOLERANCE = {
    "pass_rate": 0.05,
    "retrieval_hit_rate": 0.05,
    "context_recall": 0.05,
    "keyword_recall": 0.05,
    "semantic_similarity": 0.05,
    "guardrail_accuracy": 0.0,  # safety checks must never regress
    "judge_correctness": 0.25,
}
MAX_COST_INCREASE = 0.20


def load_golden(path=GOLDEN):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def score_case(case: dict, res: rag.RagResult) -> dict:
    row = {"id": case["id"], "category": case["category"], "question": case["question"],
           "stop_reason": res.stop_reason,
           "answer": res.answer, "doc_ids": res.doc_ids, "blocked": res.blocked,
           "latency_ms": round(res.latency_ms, 2), "ttft_ms": round(res.ttft_ms, 2),
           "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
           "cost_usd": res.cost_usd, "trace_id": res.trace_id}

    if case.get("expected_blocked"):
        row["guardrail_ok"] = float(res.blocked)
        row["passed"] = res.blocked
        return row

    ref = case.get("reference", "")
    row["exact_match"] = metrics.exact_match(res.answer, ref)
    row["semantic_similarity"] = round(metrics.semantic_similarity(res.answer, ref), 3)
    row["lexical_similarity"] = round(metrics.lexical_similarity(res.answer, ref), 3)

    if case.get("expected_refusal"):
        refused = rag.REFUSAL.lower().rstrip(".") in res.answer.lower()
        row["passed"] = refused and not res.blocked
        return row

    keywords = case.get("expected_keywords", [])
    row["retrieval_hit"] = metrics.retrieval_hit(res.doc_ids, case.get("expected_doc"))
    row["context_recall"] = round(metrics.context_recall(res.contexts, keywords), 3)
    row["keyword_recall"] = round(metrics.keyword_recall(res.answer, keywords), 3)
    passed = row["retrieval_hit"] == 1.0 and row["keyword_recall"] >= 0.5 and not res.blocked
    # An answer cut off by the max_tokens limit is a failure even if keywords appear before the cut.
    row["truncated"] = res.stop_reason == "max_tokens"
    if row["truncated"]:
        passed = False
    # Where did it break? Retrieval (facts never fetched) vs generation (facts fetched, not used)
    if not passed and not res.blocked:
        row["failure_stage"] = ("generation (truncated)" if row["truncated"]
                                else "retrieval" if row["context_recall"] < 0.5 else "generation")

    if case.get("expected_pii_redacted"):  # PII must never reach the LLM
        leaked = [p for p in case["expected_pii_redacted"] if p in res.sanitized_question]
        row["guardrail_ok"] = float(not leaked)
        passed = passed and not leaked

    judge = metrics.llm_judge(case["question"], res.answer, res.contexts, ref)
    if judge:
        row["judge"] = judge
    row["passed"] = passed
    return row


def _mean(rows, key):
    vals = [r[key] for r in rows if key in r]
    return round(statistics.mean(vals), 4) if vals else None


def _pct(values, p):
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, round(p / 100 * (len(values) - 1)))
    return round(values[idx], 2)


def aggregate(rows):
    lat = [r["latency_ms"] for r in rows]
    ttft = [r["ttft_ms"] for r in rows if r["ttft_ms"] > 0]
    judged = [r["judge"] for r in rows if "judge" in r]
    summary = {
        "cases": len(rows),
        "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 4),
        "retrieval_hit_rate": _mean(rows, "retrieval_hit"),
        "context_recall": _mean(rows, "context_recall"),
        "keyword_recall": _mean(rows, "keyword_recall"),
        "semantic_similarity": _mean(rows, "semantic_similarity"),
        "lexical_similarity": _mean(rows, "lexical_similarity"),
        "guardrail_accuracy": _mean(rows, "guardrail_ok"),
        "judge_correctness": round(statistics.mean(j["correctness"] for j in judged), 3) if judged else None,
        "judge_faithfulness": round(statistics.mean(j["faithfulness"] for j in judged), 3) if judged else None,
        "judge_conciseness": round(statistics.mean(j["conciseness"] for j in judged if "conciseness" in j), 3)
        if any("conciseness" in j for j in judged) else None,
        "latency_p50_ms": _pct(lat, 50),
        "latency_p95_ms": _pct(lat, 95),
        "ttft_avg_ms": round(statistics.mean(ttft), 2) if ttft else None,
        "total_input_tokens": sum(r["input_tokens"] for r in rows),
        "total_output_tokens": sum(r["output_tokens"] for r in rows),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
    }
    summary["cost_per_request_usd"] = round(summary["total_cost_usd"] / len(rows), 8)
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r["passed"])
    summary["pass_rate_by_category"] = {k: round(sum(v) / len(v), 3) for k, v in by_cat.items()}
    return summary


def compare(current: dict, baseline: dict) -> list[str]:
    problems = []
    cur_backend = current.get("config", {}).get("similarity_backend")
    base_backend = baseline.get("config", {}).get("similarity_backend", "tfidf")
    for key, tol in QUALITY_TOLERANCE.items():
        if key.startswith("judge_") and current.get("config", {}).get("judge_version", "v1") != \
                baseline.get("config", {}).get("judge_version", "v1"):
            print(f"  [skipping {key}: judge rubric changed - re-save the baseline to compare]")
            continue
        if key == "semantic_similarity" and cur_backend != base_backend:
            print(f"  [skipping semantic_similarity: baseline used {base_backend}, this run used "
                  f"{cur_backend} - re-save the baseline to compare]")
            continue
        cur, base = current.get(key), baseline.get(key)
        if cur is None or base is None:
            continue
        if cur < base - tol:
            problems.append(f"{key}: {base} -> {cur} (allowed drop {tol})")
    # Per-request, not total: adding golden cases must not look like a cost regression.
    base_cost = baseline.get("cost_per_request_usd") or 0
    cur_cost = current.get("cost_per_request_usd") or 0
    if base_cost > 0 and cur_cost > base_cost * (1 + MAX_COST_INCREASE):
        problems.append(f"cost_per_request_usd: {base_cost} -> {cur_cost} (> +{MAX_COST_INCREASE:.0%})")
    return problems


def write_report(label, summary, rows, problems, baseline):
    lines = [f"# Eval report: {label}", "", "| metric | current | baseline |", "|---|---|---|"]
    for k, v in summary.items():
        if k != "pass_rate_by_category":
            lines.append(f"| {k} | {v} | {baseline.get(k, '-') if baseline else '-'} |")
    lines += ["", f"Pass rate by category: {summary['pass_rate_by_category']}", "", "## Cases", "",
              "| id | category | passed | failed at | context recall | semantic / lexical | docs | answer |",
              "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ans = r["answer"].replace("|", "/").replace("\n", " ")[:90]
        lines.append(f"| {r['id']} | {r['category']} | {'✅' if r['passed'] else '❌'} | "
                     f"{r.get('failure_stage', '')} | {r.get('context_recall', '-')} | "
                     f"{r.get('semantic_similarity', '-')} / {r.get('lexical_similarity', '-')} | "
                     f"{','.join(r['doc_ids'])} | {ans} |")
    lines += ["", "## Regression check", ""]
    lines += [f"- ❌ {p}" for p in problems] if problems else ["- ✅ no regressions vs baseline"]
    (RESULTS / f"{label}.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="run")
    ap.add_argument("--top-k", type=int, default=2)
    ap.add_argument("--prompt-version", default="v1")
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--baseline", default=None,
                    help="defaults to evals/baseline.json (real) or evals/baseline_mock.json (mock)")
    args = ap.parse_args(argv)
    if args.baseline is None:  # never compare a mock run against real-model numbers
        args.baseline = str(BASELINE_MOCK if rag.use_mock() else BASELINE)

    RESULTS.mkdir(exist_ok=True)
    if rag.use_mock():
        why = "MOCK_LLM=1 is set" if os.getenv("MOCK_LLM") == "1" else "ANTHROPIC_API_KEY not found"
        print(f"MODE: MOCK ({why})\n", file=sys.stderr)
    else:
        print(f"MODE: REAL  model={rag.MODEL}  prices=${rag.INPUT_PRICE}/${rag.OUTPUT_PRICE} per MTok\n",
              file=sys.stderr)
    cases = load_golden()
    rows = [score_case(c, rag.answer(c["question"], top_k=args.top_k, prompt_version=args.prompt_version))
            for c in cases]
    flush()
    summary = aggregate(rows)
    summary["config"] = {"top_k": args.top_k, "prompt_version": args.prompt_version,
                         "model": "mock" if rag.use_mock() else rag.MODEL,
                         "similarity_backend": metrics.similarity_backend(),
                         "judge_version": metrics.judge_version()}

    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text())["summary"] if baseline_path.exists() else {}
    problems = compare(summary, baseline) if baseline and not args.save_baseline else []

    (RESULTS / f"{args.label}.json").write_text(json.dumps({"summary": summary, "cases": rows}, indent=2))
    write_report(args.label, summary, rows, problems, baseline)

    print(json.dumps(summary, indent=2))
    for r in rows:
        if not r["passed"]:
            stage = f" [failed at: {r['failure_stage']}, context_recall={r['context_recall']}]" \
                if "failure_stage" in r else ""
            print(f"FAILED {r['id']}{stage}: {r['answer'][:100]}")

    if args.save_baseline:
        baseline_path.write_text(json.dumps({"summary": summary}, indent=2))
        print(f"\nSaved baseline -> {baseline_path}")
        return 0
    if problems:
        print("\nREGRESSION DETECTED:\n  " + "\n  ".join(problems))
        return 1
    print("\nNo regressions vs baseline." if baseline else "\nNo baseline yet (run with --save-baseline).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
