"""Evaluation metrics, from cheapest/strictest to most flexible.

  exact_match          - deterministic, strict (good for refusals / fixed strings)
  keyword_recall       - deterministic, tolerant of wording
  lexical_similarity   - shared wording (character TF-IDF); cheap but brittle
  semantic_similarity  - shared MEANING (sentence embeddings via fastembed);
                         falls back to lexical if embeddings are unavailable
  retrieval_hit        - did the retriever fetch the right document?
  llm_judge            - rubric scored by Claude (only when an API key is set)
"""
import json
import os
import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s$%.,-]", "", text.lower())).strip()


def exact_match(answer: str, reference: str) -> float:
    return float(_norm(answer) == _norm(reference))


def keyword_recall(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    a = answer.lower()
    return sum(k.lower() in a for k in keywords) / len(keywords)


def lexical_similarity(answer: str, reference: str) -> float:
    """Character n-gram TF-IDF cosine: measures shared WORDING, not meaning.
    Penalises correct answers that add detail or rephrase."""
    if not answer.strip() or not reference.strip():
        return 0.0
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5)).fit([answer, reference])
    m = vec.transform([answer, reference])
    return float(cosine_similarity(m[0], m[1])[0][0])


# ---- Embedding similarity (meaning-level) ---------------------------------
# fastembed runs a small ONNX model on CPU (~70 MB, no PyTorch, no GPU) -
# fine on an 8 GB laptop. Downloaded once to .cache/fastembed.
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
_EMBEDDER = None
_EMBED_FAILED = False


def similarity_backend() -> str:
    """'embeddings' or 'tfidf'. Default: tfidf in mock mode (offline, fast, CI),
    embeddings in real mode. Override with SIM_BACKEND."""
    choice = os.getenv("SIM_BACKEND")
    if choice in ("embeddings", "tfidf"):
        wanted = choice
    else:
        mock = os.getenv("MOCK_LLM") == "1" or not os.getenv("ANTHROPIC_API_KEY")
        wanted = "tfidf" if mock else "embeddings"
    if wanted == "embeddings" and _load_embedder() is None:
        return "tfidf"
    return wanted


def _load_embedder():
    global _EMBEDDER, _EMBED_FAILED
    if _EMBEDDER is not None or _EMBED_FAILED:
        return _EMBEDDER
    try:
        from pathlib import Path

        from fastembed import TextEmbedding

        cache = Path(__file__).resolve().parent.parent / ".cache" / "fastembed"
        _EMBEDDER = TextEmbedding(EMBED_MODEL, cache_dir=str(cache))
    except Exception as exc:  # not installed / no network: fall back, don't crash
        _EMBED_FAILED = True
        print(f"  [embeddings unavailable ({type(exc).__name__}: {exc}); using TF-IDF]")
    return _EMBEDDER


def embedding_similarity(answer: str, reference: str) -> float:
    import numpy as np

    a, b = list(_load_embedder().embed([answer, reference]))
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def semantic_similarity(answer: str, reference: str) -> float:
    if not answer.strip() or not reference.strip():
        return 0.0
    if similarity_backend() == "embeddings":
        return embedding_similarity(answer, reference)
    return lexical_similarity(answer, reference)


def retrieval_hit(doc_ids: list[str], expected_doc: str | None) -> float:
    """Coarse: was the right DOCUMENT retrieved? (Blind to wrong paragraph.)"""
    return 1.0 if expected_doc is None else float(expected_doc in doc_ids)


def context_recall(contexts: list[str], keywords: list[str]) -> float:
    """Fine-grained: fraction of the facts needed for the answer that are
    actually present in the retrieved chunks. If this is low, the LLM never
    had a chance - the failure is in retrieval, not generation."""
    if not keywords:
        return 1.0
    text = " ".join(contexts).lower()
    return sum(k.lower() in text for k in keywords) / len(keywords)


JUDGE_RUBRICS = {
    # v1: the original rubric.
    "v1": """You are grading a support assistant's answer.

Question: {question}
Retrieved context: {context}
Reference answer: {reference}
Assistant answer: {answer}

Score each criterion 1-5:
- faithfulness: every claim in the answer is supported by the context (5 = fully grounded, 1 = invented facts)
- correctness: the answer agrees with the reference answer (5 = same facts, 1 = contradicts it)

Reply with ONLY JSON: {{"faithfulness": int, "correctness": int, "reason": "one sentence"}}""",

    # v2: changes driven by calibration findings (see evals/calibrate.py):
    #  - the judge knows who the assistant speaks for, so naming the company
    #    isn't treated as an unsupported claim (v1 docked edge-typo for "AcmeCloud")
    #  - a conciseness score, because v1 gave 5/5 to answers padded with
    #    facts nobody asked for
    #  - anchors for each score level, so 3 vs 4 means something
    "v2": """You are grading an answer from AcmeCloud's customer-support assistant.
The assistant was told to answer ONLY from the retrieved context, in under 60 words.
Referring to the company as "AcmeCloud" or to "the docs" is expected and is NOT an unsupported claim.

Question: {question}
Retrieved context: {context}
Reference answer: {reference}
Assistant answer: {answer}

Score each criterion 1-5:
- faithfulness: are the answer's factual claims supported by the context?
  5 = all supported; 3 = one minor unsupported detail; 1 = key facts invented
- correctness: does it give the same key facts as the reference answer?
  5 = same key facts; 3 = partially right or missing a key fact; 1 = wrong or contradicts it
- conciseness: does it answer only what was asked?
  5 = only what was asked; 4 = one small helpful extra; 3 = noticeable unrequested detail; 1 = mostly padding

Reply with ONLY JSON: {{"faithfulness": int, "correctness": int, "conciseness": int, "reason": "one sentence"}}""",
}
JUDGE_RUBRIC = JUDGE_RUBRICS["v1"]  # kept for backwards compatibility


def judge_version() -> str:
    v = os.getenv("JUDGE_VERSION", "v1")
    return v if v in JUDGE_RUBRICS else "v1"


def llm_judge(question, answer, contexts, reference, model=None, version=None) -> dict | None:
    """LLM-as-a-judge. Returns None in mock mode so CI stays free/deterministic.

    Bias notes: judges prefer longer answers and their own model family's style.
    Calibrate by hand-labelling cases and comparing (python -m evals.calibrate).
    Consistency comes from a strict rubric + JSON-only output (the current SDK
    no longer takes a `temperature` argument).
    """
    if os.getenv("MOCK_LLM") == "1" or not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic

    version = version or judge_version()
    keys = ["faithfulness", "correctness"] + (["conciseness"] if version == "v2" else [])
    zeros = {k: 0 for k in keys}
    try:
        msg = anthropic.Anthropic().messages.create(
            model=model or os.getenv("JUDGE_MODEL", "claude-sonnet-5-5"),
            max_tokens=1024,  # headroom: any thinking tokens count toward this limit
            messages=[{"role": "user", "content": JUDGE_RUBRICS[version].format(
                question=question, context="\n".join(contexts), reference=reference, answer=answer)}],
        )
    except Exception as exc:  # a judge outage shouldn't crash the whole eval run
        print(f"  [judge skipped: {type(exc).__name__}: {exc}]")
        return None
    # The response can contain several blocks (e.g. a "thinking" block before the
    # answer). Keep only the text blocks instead of assuming content[0] is text.
    raw = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    if not raw.strip():
        return {**zeros, "reason": f"judge returned no text (stop_reason={msg.stop_reason})"}
    try:
        data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        return {**{k: int(data[k]) for k in keys}, "reason": data.get("reason", "")}
    except (ValueError, KeyError):
        return {**zeros, "reason": f"unparseable judge output: {raw[:80]}"}
