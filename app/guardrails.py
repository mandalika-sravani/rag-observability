"""Lightweight guardrails (no GPU, no extra models).

These are deliberately simple regex/rule checks so the project runs on any
laptop and in CI. Each function has the same shape you would keep when you
swap in a stronger tool:
  - redact_pii      -> Microsoft Presidio / Guardrails AI `detect_pii`
  - detect_injection -> Llama Prompt Guard / NeMo Guardrails jailbreak rail
  - check_output     -> Llama Guard / Guardrails AI `toxic_language`
"""
import re
from dataclasses import dataclass, field

# Canary token hidden in the system prompt. If it ever shows up in an
# answer, the model leaked its instructions (a prompt-injection success).
CANARY = "sys-canary-7731"

PII_PATTERNS = [  # order matters: card before phone
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("PHONE", re.compile(r"(?:\+\d{1,3}[ -]?)?\b\d{5}[ -]?\d{5}\b|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b")),
]

INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above) (instructions|rules)",
    r"disregard (your|the|all) (instructions|rules|system prompt)",
    r"(reveal|print|show|repeat) (me )?(your|the) (system prompt|instructions|hidden prompt)",
    r"(forget|drop|abandon|override) (all |about )?(your|the|any|previous|prior) (rules|instructions|guidelines|restrictions)",
    r"(what|which) (instructions|rules|system prompt) (you were|you've been|were you|have you been) given",
    r"you are now (?!able)",
    r"\bdeveloper mode\b",
    r"\bDAN\b",
]
_INJECTION_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

BLOCKLIST = ["idiot", "stupid", "hate you"]  # toy toxicity list


@dataclass
class GuardResult:
    text: str
    blocked: bool = False
    reason: str | None = None
    findings: list = field(default_factory=list)


def redact_pii(text: str) -> GuardResult:
    findings = []
    for label, pattern in PII_PATTERNS:
        if pattern.search(text):
            findings.append(label)
            text = pattern.sub(f"<{label}>", text)
    return GuardResult(text=text, findings=findings)


def detect_injection(text: str) -> GuardResult:
    for pattern in _INJECTION_RE:
        if pattern.search(text):
            return GuardResult(text=text, blocked=True, reason="prompt_injection",
                               findings=[pattern.pattern])
    return GuardResult(text=text)


def check_input(text: str) -> GuardResult:
    """Input rail: mask PII first, then block injection attempts."""
    redacted = redact_pii(text)
    inj = detect_injection(redacted.text)
    return GuardResult(text=redacted.text, blocked=inj.blocked, reason=inj.reason,
                       findings=redacted.findings + inj.findings)


def check_output(text: str) -> GuardResult:
    """Output rail: block system-prompt leaks and toxic text, mask PII."""
    if CANARY in text:
        return GuardResult(text=text, blocked=True, reason="system_prompt_leak")
    lowered = text.lower()
    for word in BLOCKLIST:
        if word in lowered:
            return GuardResult(text=text, blocked=True, reason="toxic_output", findings=[word])
    return redact_pii(text)
