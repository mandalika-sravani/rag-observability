"""Unit tests for metrics - especially the case doc-level hit rate misses."""
from evals import metrics


def test_context_recall_catches_right_doc_wrong_chunk():
    # Same document ("pricing"), but the retrieved paragraph lacks the needed fact.
    contexts = ["Annual billing gives a 20% discount on the Pro plan."]
    assert metrics.retrieval_hit(["pricing"], "pricing") == 1.0      # looks fine...
    assert metrics.context_recall(contexts, ["$29", "month"]) == 0.0  # ...but isn't


def test_context_recall_partial():
    contexts = ["The Pro plan costs $29 per month."]
    assert metrics.context_recall(contexts, ["$29", "annual"]) == 0.5


def test_keyword_recall_case_insensitive():
    assert metrics.keyword_recall("Returns HTTP 429", ["429", "http"]) == 1.0


def test_embedding_path_uses_cosine(monkeypatch):
    """Exercise the embedding code path without downloading a model."""
    import numpy as np

    class FakeEmbedder:
        def embed(self, texts):
            # identical meaning -> identical vectors, regardless of wording
            return [np.array([1.0, 0.0]) if "429" in t else np.array([0.0, 1.0]) for t in texts]

    monkeypatch.setattr(metrics, "_EMBEDDER", FakeEmbedder())
    monkeypatch.setenv("SIM_BACKEND", "embeddings")
    a, ref = "HTTP status 429 (Too Many Requests).", "The API returns HTTP 429 Too Many Requests."
    assert metrics.similarity_backend() == "embeddings"
    assert metrics.semantic_similarity(a, ref) == 1.0
    assert metrics.lexical_similarity(a, ref) < 0.6  # wording differs, meaning doesn't


def test_falls_back_to_tfidf_when_embeddings_unavailable(monkeypatch):
    monkeypatch.setattr(metrics, "_EMBEDDER", None)
    monkeypatch.setattr(metrics, "_EMBED_FAILED", True)
    monkeypatch.setenv("SIM_BACKEND", "embeddings")
    assert metrics.similarity_backend() == "tfidf"


def test_judge_skips_thinking_blocks(monkeypatch):
    """Regression test: a response starting with a thinking block crashed the judge."""
    import types

    import anthropic

    thinking = types.SimpleNamespace(type="thinking", thinking="let me check the context...")
    text = types.SimpleNamespace(type="text", text='{"faithfulness": 5, "correctness": 4, "reason": "ok"}')
    fake_msg = types.SimpleNamespace(content=[thinking, text], stop_reason="end_turn")

    class FakeClient:
        messages = types.SimpleNamespace(create=lambda **kw: fake_msg)

    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient())
    monkeypatch.delenv("MOCK_LLM", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("JUDGE_VERSION", "v1")  # don't depend on the user's .env setting
    result = metrics.llm_judge("q", "a", ["ctx"], "ref")
    assert result["faithfulness"] == 5 and result["correctness"] == 4


def test_judge_v2_returns_conciseness(monkeypatch):
    import types

    import anthropic

    text = types.SimpleNamespace(type="text", text='{"faithfulness": 5, "correctness": 5, "conciseness": 3, "reason": "padded"}')
    fake_msg = types.SimpleNamespace(content=[text], stop_reason="end_turn")

    class FakeClient:
        messages = types.SimpleNamespace(create=lambda **kw: fake_msg)

    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient())
    monkeypatch.delenv("MOCK_LLM", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("JUDGE_VERSION", "v2")
    assert metrics.llm_judge("q", "a", ["ctx"], "ref")["conciseness"] == 3


def test_truncated_answer_fails_even_with_keywords():
    """Regression test: an answer cut off at max_tokens passed because '429' appeared before the cut."""
    from app.rag import RagResult
    from evals.run_evals import score_case

    case = {"id": "edge-overblock", "category": "edge", "question": "Can I ignore 429s?",
            "expected_doc": "rate_limits", "expected_keywords": ["429"], "reference": "No."}
    res = RagResult(question=case["question"],
                    answer="No. The docs say clients should retry 429 responses with ex",
                    doc_ids=["rate_limits"], contexts=["Clients should retry 429 responses."],
                    stop_reason="max_tokens")
    row = score_case(case, res)
    assert row["truncated"] and not row["passed"]
    assert row["failure_stage"] == "generation (truncated)"
