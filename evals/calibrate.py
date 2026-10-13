"""Judge calibration: do the LLM judge's scores agree with a human's?

Step 1 - make a labelling sheet from an eval run (opens in Excel):
    python -m evals.calibrate template --results results/claude.json

Step 2 - fill in the human_* columns yourself (1-5), WITHOUT looking at the
    judge's scores first. Save as CSV.

Step 3 - compare human vs judge:
    python -m evals.calibrate compare --results results/claude.json --labels evals/human_labels.csv

To test a new judge rubric, re-judge the SAME answers you labelled (a new eval
run would produce new answers, so the comparison would no longer be like-for-like):
    python -m evals.calibrate rejudge --results results/claude.json --judge-version v2
    python -m evals.calibrate compare --results results/claude.json results/claude_rejudged_v2.json
"""
import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

from app.rag import Retriever

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = ROOT / "evals" / "human_labels.csv"
# Chosen to cover: a judge-vs-human disagreement (edge-typo), padded answers
# (refund-01, pii-01), a reasoning question (edge-overblock) and a clean answer (price-04).
DEFAULT_IDS = ["edge-typo", "refund-01", "pii-01", "edge-overblock", "price-04"]
CRITERIA = ["faithfulness", "correctness", "conciseness"]
FIELDS = ["id", "question", "retrieved_context", "reference", "answer",
          "human_faithfulness", "human_correctness", "human_conciseness", "notes"]


def _load_results(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["summary"], {c["id"]: c for c in data["cases"]}


def _golden():
    rows = (ROOT / "evals" / "golden.jsonl").read_text(encoding="utf-8").splitlines()
    return {c["id"]: c for c in map(json.loads, filter(str.strip, rows))}


def make_template(results, out, ids):
    summary, cases = _load_results(results)
    golden = _golden()
    top_k = summary.get("config", {}).get("top_k", 2)
    retriever = Retriever()  # deterministic, so this recreates what the model saw
    out = Path(out)
    if out.exists():
        sys.exit(f"{out} already exists - delete it first (it may hold your labels).")
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # -sig: Excel reads UTF-8 correctly
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for cid in ids:
            case = cases.get(cid)
            if not case or case.get("blocked") or "judge" not in case:
                print(f"  skipping {cid}: not in results or not judged")
                continue
            ctx = [h[1] for h in retriever.search(case["question"], top_k=top_k)]
            w.writerow({"id": cid, "question": case["question"],
                        "retrieved_context": "\n---\n".join(ctx),
                        "reference": golden.get(cid, {}).get("reference", ""),
                        "answer": case["answer"],
                        "human_faithfulness": "", "human_correctness": "",
                        "human_conciseness": "", "notes": ""})
    print(f"Wrote {out}. Score each row 1-5 before looking at the judge's scores.")
    print("  faithfulness: claims supported by the context?   correctness: same key facts as reference?")
    print("  conciseness: answers only what was asked?")


def _read_labels(path):
    with Path(path).open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    labels = {}
    for r in rows:
        scores = {}
        for c in CRITERIA:
            v = (r.get(f"human_{c}") or "").strip()
            if v:
                if not v.isdigit() or not 1 <= int(v) <= 5:
                    sys.exit(f"{r['id']}: human_{c} must be 1-5, got {v!r}")
                scores[c] = int(v)
        if scores:
            labels[r["id"]] = {"scores": scores, "notes": r.get("notes", ""),
                               "answer": (r.get("answer") or "").strip()}
    if not labels:
        sys.exit(f"No scores found in {path} - fill in the human_* columns first.")
    return labels


def compare(results, labels_path):
    summary, cases = _load_results(results)
    labels = _read_labels(labels_path)
    version = summary.get("config", {}).get("judge_version", "v1")
    pairs = {c: [] for c in CRITERIA}
    skipped = []
    lines = [f"# Judge calibration ({Path(results).name}, judge {version})", "",
             "| id | criterion | human | judge | diff | judge's reason |", "|---|---|---|---|---|---|"]
    for cid, lab in labels.items():
        judge = cases.get(cid, {}).get("judge")
        if not judge:
            print(f"  {cid}: no judge score in this results file, skipped")
            continue
        if lab["answer"] and lab["answer"] != cases[cid]["answer"].strip():
            print(f"  {cid}: SKIPPED - this results file has a different answer than the one you "
                  f"labelled. Use 'rejudge' to score the labelled answer with another judge.")
            skipped.append(cid)
            continue
        for c, h in lab["scores"].items():
            if c not in judge:
                continue  # e.g. conciseness only exists in judge v2
            j = judge[c]
            pairs[c].append((h, j))
            mark = "" if h == j else ("judge higher" if j > h else "judge lower")
            reason = judge.get("reason", "").replace("|", "/")[:110] if h != j else ""
            lines.append(f"| {cid} | {c} | {h} | {j} | {mark} | {reason} |")

    lines += ["", "| criterion | n | exact agreement | within 1 point | mean judge - human |",
              "|---|---|---|---|---|"]
    print(f"\nJudge {version} vs human ({Path(results).name}):")
    for c, ps in pairs.items():
        if not ps:
            continue
        exact = sum(h == j for h, j in ps) / len(ps)
        within1 = sum(abs(h - j) <= 1 for h, j in ps) / len(ps)
        bias = statistics.mean(j - h for h, j in ps)
        lines.append(f"| {c} | {len(ps)} | {exact:.0%} | {within1:.0%} | {bias:+.2f} |")
        lean = "lenient" if bias > 0 else "strict" if bias < 0 else "neutral"
        print(f"  {c:13s} n={len(ps)}  exact={exact:.0%}  within-1={within1:.0%}  "
              f"bias={bias:+.2f} ({lean})")
    if skipped:
        lines += ["", f"Skipped (answer differs from the labelled one): {', '.join(skipped)}"]
    lines += ["", "Positive bias = the judge scores higher than you (too lenient).",
              "With only a handful of labels, treat this as a sanity check, not a statistic."]
    out = ROOT / "results" / f"calibration_{Path(results).stem}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {out}")


def rejudge(results, version, out=None):
    """Score the answers already in a results file with another judge rubric.
    Answers are NOT regenerated, so the comparison with your labels stays fair."""
    from evals import metrics

    summary, cases = _load_results(results)
    golden = _golden()
    top_k = summary.get("config", {}).get("top_k", 2)
    retriever = Retriever()
    judged = 0
    for cid, case in cases.items():
        if "judge" not in case:
            continue  # blocked / refusal cases were never judged
        ctx = [h[1] for h in retriever.search(case["question"], top_k=top_k)]
        new = metrics.llm_judge(case["question"], case["answer"], ctx,
                                golden.get(cid, {}).get("reference", ""), version=version)
        if new is None:
            sys.exit("Judge unavailable (mock mode or no ANTHROPIC_API_KEY) - rejudge needs the real API.")
        case["judge"] = new
        judged += 1
    js = [c["judge"] for c in cases.values() if "judge" in c]
    for k in CRITERIA:
        vals = [j[k] for j in js if k in j]
        summary[f"judge_{k}"] = round(statistics.mean(vals), 3) if vals else None
    summary.setdefault("config", {})["judge_version"] = version
    summary["config"]["rejudged_from"] = Path(results).name
    out = Path(out or Path(results).with_name(f"{Path(results).stem}_rejudged_{version}.json"))
    out.write_text(json.dumps({"summary": summary, "cases": list(cases.values())}, indent=2), encoding="utf-8")
    print(f"Re-judged {judged} answers with judge {version} (answers unchanged) -> {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("template")
    t.add_argument("--results", required=True)
    t.add_argument("--out", default=str(DEFAULT_LABELS))
    t.add_argument("--ids", nargs="*", default=DEFAULT_IDS)
    c = sub.add_parser("compare")
    c.add_argument("--results", required=True, nargs="+")
    c.add_argument("--labels", default=str(DEFAULT_LABELS))
    r = sub.add_parser("rejudge")
    r.add_argument("--results", required=True)
    r.add_argument("--judge-version", default="v2")
    r.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "rejudge":
        rejudge(args.results, args.judge_version, args.out)
    elif args.cmd == "template":
        make_template(args.results, args.out, args.ids)
    else:
        for res in args.results:
            compare(res, args.labels)


if __name__ == "__main__":
    main()
