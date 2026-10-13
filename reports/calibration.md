# LLM-as-a-Judge Calibration

## Why calibrate

The eval suite uses Claude (Sonnet 5.5) as a judge to score answers 1–5. A judge is only useful if its scores agree with a careful human. Before calibration, judge v1 gave 5/5 faithfulness and correctness to almost every answer, which is suspicious: a metric that never moves can't catch a regression.

## Method

1. Picked 5 judged answers from a real-model run (`results/claude.json`) to cover different situations:
   - `edge-typo`: the only case judge v1 marked down
   - `refund-01`, `pii-01`: answers padded with unrequested facts
   - `edge-overblock`: a reasoning question ("can I ignore 429s?")
   - `price-04`: a clean one-sentence answer
2. Scored each answer myself (`evals/human_labels.csv`) on faithfulness, correctness and conciseness, without looking at the judge's scores first.
3. Compared with `python -m evals.calibrate compare`, which reports exact agreement, agreement within 1 point, and bias (mean judge − human; positive = judge too lenient).
4. Re-scored the **same answers** with a revised rubric (judge v2) using `python -m evals.calibrate rejudge`, so the only thing that changed between v1 and v2 is the rubric.

## Round 1: my own labels were inconsistent

My first labels disagreed with judge v1 on every row (0% exact agreement, judge +1.8 on both criteria). Reviewing them showed the problem was mostly mine:

- **Double-counting padding.** I lowered *correctness* for answers that were right but added extra facts (`refund-01`, `edge-overblock`: correctness 2). That flaw belongs in *conciseness*, so it was being penalised twice.
- **Slips.** `price-04` got faithfulness 1 ("key facts invented"), although its only claim is word-for-word in the retrieved context. `edge-typo`, the shortest answer, got conciseness 2.

**Lesson:** a vague rubric confuses humans as much as models. Separate criteria only work if each one asks exactly one question, and labels need written reasons.

## Round 2: results with corrected labels

| Criterion | Judge v1: exact / within 1 / bias | Judge v2: exact / within 1 / bias |
|---|---|---|
| Faithfulness | 20% / 80% / +1.0 | 0% / 80% / +1.2 |
| Correctness | 40% / 100% / +0.6 | 40% / 100% / +0.6 |
| Conciseness | not scored | **60% / 100% / 0.0** |

*(n = 5 labelled answers per criterion)*

### What judge v2 changed

Judge v2 was written in response to two problems found in v1:

1. **v1 docked a point for naming "AcmeCloud".** The retriever drops document titles, so the company name never appears in the retrieved text, and the judge never sees the system prompt that says the assistant works for AcmeCloud. v2 is told who the assistant speaks for.
2. **v1 gave 5/5 to padded answers.** Its rubric only asked about grounding and correctness. v2 adds a **conciseness** score with a description for each level.

## Findings

- **Conciseness is the best-calibrated criterion:** 60% exact agreement, every answer within 1 point, zero bias. It measures something v1 couldn't see at all. Across the full 14-answer v2 run it separated tight answers (5) from small extras (4) and clear padding (3).
- **Correctness is unchanged and reasonable:** every answer within 1 point. The judge scored all five 5; I gave three of them 4.
- **Faithfulness got slightly further from me, for a known reason.** The only exact match under v1 was `edge-typo`, where we both gave 4 for "AcmeCloud". v2 now gives 5, as designed, so that match disappears. This is a policy decision, not a judge error: should naming the company the assistant represents count against faithfulness? I decided **[keep v2 / revert this rule]** because **[reason]**.
- **The remaining gap is a consistent +1, not random disagreement.** Every faithfulness and correctness difference is "human 4, judge 5". Either the judge is mildly lenient or I avoid giving 5s. For `price-04` and `pii-01` I couldn't name a specific flaw, which points to my scoring habit as at least part of the gap.

## Methodology lesson: compare like with like

My first v1-vs-v2 comparison ran a new eval with judge v2. That regenerated the answers: 4 of the 5 labelled answers changed wording, so judge v2 was scoring text I had never seen. The comparison was invalid. The fix:
- `rejudge` re-scores the stored answers without regenerating them.
- `compare` now refuses to compare a label against a different answer.

Because LLM output varies between runs, any judge comparison has to freeze the answers.

## Limitations

- **5 labelled answers, 1 human rater.** This is a sanity check, not a statistic. A real calibration would use 30+ answers and at least two raters, with agreement measured between the raters themselves.
- All 5 answers were good, so the labels only test the top of the scale (3–5). Calibration should also include clearly bad answers (wrong facts, invented details) to check that the judge scores low when it should.
- The judge (Sonnet) and the answering model (Haiku) are from the same model family, which can bias the judge towards that family's style.

## Decision

Use **judge v2** (`JUDGE_VERSION=v2`). It matches my scores as well as v1 on correctness, adds a well-calibrated conciseness signal, and its faithfulness difference comes down to the explicit company-name rule above. Next improvement: add bad answers to the labelled set to test the bottom of the scale.
