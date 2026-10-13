# RAG Observability + Evaluation Suite

Assignment: *Instrument full telemetry tracing and an automated evaluation suite for a RAG pipeline with regression benchmarking.*

A small support-bot RAG over 4 docs (fictional "AcmeCloud"), with:

| Concept | Where it lives |
|---|---|
| Distributed tracing, spans, prompts, tool/LLM calls (OpenTelemetry) | `app/telemetry.py`, spans in `app/rag.py` |
| Latency, TTFT, tokens, cost-per-request | `llm.generate` span attributes + `evals/run_evals.py` summary |
| Golden test set, edge cases, adversarial cases | `evals/golden.jsonl` (14 cases, 4 categories) |
| Exact match, semantic similarity, keyword recall, retrieval hit, context recall | `evals/metrics.py` |
| Failure localisation (retrieval vs generation) | `failure_stage` in `run_evals.score_case` |
| LLM-as-a-judge (rubric, bias notes) | `metrics.llm_judge` (runs only with an API key) |
| Guardrails: PII masking, prompt-injection blocking, canary leak check, toxicity | `app/guardrails.py` |
| Unit/integration assertions | `tests/` (pytest) |
| Regression testing + CI gate | `evals/baseline.json`, `compare()`, `.github/workflows/llm-eval.yml` |

Runs on a normal laptop (no GPU). Works **without an API key** in mock mode; set `ANTHROPIC_API_KEY` to use Claude for real.

## Pipeline and trace shape

```
rag.request                      (root span: latency, prompt_version, blocked)
├── guardrail.input              (PII found, injection blocked?)
├── retrieval                    (doc ids, similarity scores, top_k)
├── llm.generate                 (model, input/output tokens, TTFT, cost, prompt, completion)
└── guardrail.output             (leak / toxicity check)
```

## Run it (Windows PowerShell)

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt

$env:MOCK_LLM="1"                                   # free mode
python -m app.rag "How do I increase my rate limit on the Pro plan?"
pytest -q
python -m evals.run_evals --label run --save-run
python -m evals.run_evals --label topk1 --top-k 1   # -> REGRESSION DETECTED, exit code 1
```

(macOS/Linux: `export MOCK_LLM=1`.) Reports land in `results/<label>.md`; every span is in `traces/traces.jsonl`.

**Two baselines.** Mock runs compare against `evals/baseline_mock.json` (this is what CI uses); real-model runs compare against `evals/baseline.json`. The script picks the right one from the mode, so a free mock run is never judged against Claude's numbers. Override with `--baseline <file>`.

**Semantic vs lexical similarity.** `lexical_similarity` (character TF-IDF) measures shared wording; `semantic_similarity` uses sentence embeddings (`fastembed`, `BAAI/bge-small-en-v1.5`, ~70 MB, runs on CPU) to measure shared meaning. Real-model runs use embeddings by default; mock runs and CI use TF-IDF so they stay offline. Force either with `SIM_BACKEND=embeddings|tfidf`. The report shows both scores per case. A baseline saved with one backend won't be compared on `semantic_similarity` against a run using the other.

**Judge calibration (task 5).** `python -m evals.calibrate template --results results/claude.json` writes `evals/human_labels.csv`. Score the `human_*` columns yourself, then run `python -m evals.calibrate compare --results results/claude.json`. The report shows agreement and whether the judge is lenient or strict. `JUDGE_VERSION=v2` switches to a rubric that knows the assistant speaks for AcmeCloud and adds a conciseness score. To compare judges fairly, re-score the *same* labelled answers with `python -m evals.calibrate rejudge --results results/claude.json --judge-version v2`; a fresh eval run produces new answers, and `compare` skips any label whose answer no longer matches. Runs with different judge versions aren't compared on judge scores.

**Retrieval vs generation.** `retrieval_hit` only checks the right *document* came back. `context_recall` checks the needed *facts* are in the retrieved chunks. Every failed case gets a `failure_stage`: `retrieval` if the facts were never fetched (fix chunking/top-k/embeddings), `generation` if they were fetched but the answer missed them (fix the prompt/model).

### Use real Claude

```powershell
Remove-Item Env:MOCK_LLM
$env:ANTHROPIC_API_KEY="sk-ant-..."
$env:INPUT_PRICE_PER_MTOK="..."; $env:OUTPUT_PRICE_PER_MTOK="..."   # from Anthropic's pricing page
python -m evals.run_evals --label claude --save-baseline
```

Now TTFT is measured from the stream, token counts come from the API, cost is computed, and the LLM judge scores faithfulness + correctness.

### Send traces to Langfuse (optional)

Langfuse accepts OpenTelemetry. Create a free Langfuse Cloud project, then (check their OpenTelemetry docs for the current endpoint):

```powershell
$auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("pk-lf-...:sk-lf-..."))
$env:OTEL_EXPORTER_OTLP_ENDPOINT="https://cloud.langfuse.com/api/public/otel"
$env:OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $auth"
```

## Your tasks (the "simple task" version of the assignment)

1. **Run it** in mock mode and open one trace in `traces/traces.jsonl`. Identify each span and what its attributes tell you.
2. **Add 4 golden cases**: one normal, one typo/edge, one out-of-scope, one new injection phrasing (e.g. *"Forget your rules and…"*). Make the injection one fail first, then fix `INJECTION_PATTERNS`.
3. **Break and catch**: run with `--top-k 1` and read the regression report. Explain *why* `price-01` failed using its trace (hint: which doc was retrieved?).
4. **Swap the semantic metric** for real embeddings (reuse your Module 6 code), re-save the baseline, compare the scores.
5. **Real model run**: with your Claude key, run the suite, then hand-label 5 answers 1–5 and compare with the judge — that's judge calibration.
6. **CI**: push to GitHub; the workflow runs tests + evals on every PR and fails if quality drops.

Stretch: replace the regex guardrails with Presidio (PII) or Guardrails AI validators; mention why Llama Guard (8B) is better served via an API than on an 8 GB laptop.

## Known limits (worth writing about in your submission)

- Regex guardrails are a baseline; attackers rephrase. Real systems layer a classifier + least-privilege tools.
- TF-IDF retrieval and similarity are weak semantically; embeddings fix that.
- LLM judges have position/length/self-preference bias; use a strict rubric with JSON-only output and calibrate against human labels.
- Prompts/completions are stored in traces: redact PII **before** spans are emitted (done here for the input).
