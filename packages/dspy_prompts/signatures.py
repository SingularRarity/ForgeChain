"""DSPy Signatures — structured I/O contracts per role and tier.

Each signature defines what goes *in* (input fields) and what comes *out*
(output fields).  DSPy uses these to auto-construct and optimise prompts.

Tier progression changes the output richness:
  junior  → just the code / draft
  mid     → code + self-review notes
  senior  → code + review + architectural rationale
  cto     → full review: code quality, security, architecture, refactor plan
"""

from __future__ import annotations

import dspy
from typing import Literal

Role  = str
Tier  = Literal["junior", "mid", "senior", "cto"]


# ── Junior: Ollama — lean output ─────────────────────────────────────────── #

class JuniorCodeSignature(dspy.Signature):
    """Produce a minimal working code patch for the given task.

    Keep output concise — this runs on a local small model with limited context.
    Output ONLY the changed lines as a unified diff. No explanation unless asked.
    """
    task_description: str = dspy.InputField(
        desc="Short, specific description of what to implement (≤300 words)"
    )
    role_context: str = dspy.InputField(
        desc="One-line role + stack: e.g. 'Python/FastAPI backend engineer'"
    )
    # No existing_context field — keeps prompt small for local models
    reasoning: str = dspy.OutputField(
        desc="Brief step-by-step plan (3-5 steps) before writing any code"
    )
    patch: str = dspy.OutputField(
        desc="Unified diff patch — only changed lines, no surrounding noise"
    )
    confidence: str = dspy.OutputField(
        desc="low | medium | high — be honest; low triggers escalation to Grok"
    )


class JuniorAnalysisSignature(dspy.Signature):
    """Summarise a ticket or document and list the key action items."""
    input_text:   str = dspy.InputField(desc="Raw ticket, document, or description to analyse")
    role_context: str = dspy.InputField(desc="Analyst role context")
    summary:      str = dspy.OutputField(desc="Concise summary (3-5 sentences)")
    action_items: str = dspy.OutputField(desc="Bulleted list of concrete action items")
    confidence:   str = dspy.OutputField(desc="Self-assessed confidence: low | medium | high")


# ── Mid: Grok — adds self-review ─────────────────────────────────────────── #

class MidCodeSignature(dspy.Signature):
    """Produce a code patch with a self-review checklist."""
    task_description: str = dspy.InputField(desc="What needs to be implemented")
    role_context:     str = dspy.InputField(desc="Agent role and stack constraints")
    existing_context: str = dspy.InputField(desc="Relevant existing code snippets or patterns")
    patch:            str = dspy.OutputField(desc="Unified diff patch")
    self_review:      str = dspy.OutputField(
        desc="Self-review checklist: edge cases covered, error handling, test coverage gaps"
    )
    confidence:       str = dspy.OutputField(desc="Self-assessed confidence: low | medium | high")


# ── Senior: Gemini — adds architectural rationale ────────────────────────── #

class SeniorCodeSignature(dspy.Signature):
    """Produce a production-ready patch with architectural rationale and trade-off notes."""
    task_description:   str = dspy.InputField(desc="What needs to be implemented")
    role_context:       str = dspy.InputField(desc="Agent role and stack constraints")
    existing_context:   str = dspy.InputField(desc="Relevant existing code snippets or patterns")
    patch:              str = dspy.OutputField(desc="Unified diff patch")
    self_review:        str = dspy.OutputField(desc="Self-review: edge cases, error handling, tests")
    architecture_notes: str = dspy.OutputField(
        desc="Design decisions made, alternatives considered, trade-offs accepted"
    )
    cto_flag:           str = dspy.OutputField(
        desc="'yes' if this change requires CTO architectural review, else 'no', with reason"
    )
    confidence:         str = dspy.OutputField(desc="Self-assessed confidence: low | medium | high")


# ── CTO: Anthropic — full review mode ────────────────────────────────────── #

class CTOReviewSignature(dspy.Signature):
    """Perform a CTO-level review of a patch produced by the Senior engineer."""
    task_description:  str = dspy.InputField(desc="Original task description")
    senior_patch:      str = dspy.InputField(desc="Patch produced by the Senior engineer")
    senior_notes:      str = dspy.InputField(desc="Architectural notes from the Senior engineer")
    verdict:           str = dspy.OutputField(
        desc="'approve' | 'revise' | 'reject' with one-line rationale"
    )
    code_quality:      str = dspy.OutputField(
        desc="Code quality assessment: naming, complexity, immutability, error handling"
    )
    security_flags:    str = dspy.OutputField(
        desc="Security concerns (injection, auth, secrets, data exposure) — 'none' if clean"
    )
    refactor_plan:     str = dspy.OutputField(
        desc="Concrete refactoring steps if verdict is 'revise', else 'n/a'"
    )
    architecture_guidance: str = dspy.OutputField(
        desc="Long-term architectural guidance for the team"
    )


# ── Role-specific analysis signatures ────────────────────────────────────── #

class BATicketSignature(dspy.Signature):
    """Review a JIRA ticket as a Business Analyst."""
    jira_body:           str = dspy.InputField(desc="Full JIRA ticket body")
    project_context:     str = dspy.InputField(desc="Project background and tech stack")
    summary:             str = dspy.OutputField(desc="Executive summary (2-3 sentences)")
    acceptance_criteria: str = dspy.OutputField(desc="Numbered acceptance criteria list")
    edge_cases:          str = dspy.OutputField(desc="Edge cases the engineering team must handle")
    open_questions:      str = dspy.OutputField(desc="Ambiguities requiring clarification")
    risks:               str = dspy.OutputField(desc="Technical and business risks identified")
    confidence:          str = dspy.OutputField(desc="Self-assessed confidence: low | medium | high")


class QATestSignature(dspy.Signature):
    """Generate a pytest test suite for a backend feature."""
    feature_description: str = dspy.InputField(desc="What the feature does")
    implementation_hint: str = dspy.InputField(desc="Key functions/classes/endpoints to test")
    test_file:           str = dspy.OutputField(desc="Complete pytest test file content")
    coverage_estimate:   str = dspy.OutputField(desc="Estimated coverage % and what is not covered")
    confidence:          str = dspy.OutputField(desc="Self-assessed confidence: low | medium | high")


# ── Registry ─────────────────────────────────────────────────────────────── #

_SIG_MAP: dict[tuple[str, str], type[dspy.Signature]] = {
    # (role_category, tier) → signature class
    ("code",     "junior"):  JuniorCodeSignature,
    ("code",     "mid"):     MidCodeSignature,
    ("code",     "senior"):  SeniorCodeSignature,
    ("code",     "cto"):     CTOReviewSignature,
    ("analysis", "junior"):  JuniorAnalysisSignature,
    ("analysis", "mid"):     JuniorAnalysisSignature,   # reuse with richer LM
    ("ba",       "junior"):  BATicketSignature,
    ("ba",       "mid"):     BATicketSignature,
    ("qa",       "junior"):  QATestSignature,
    ("qa",       "mid"):     QATestSignature,
    ("qa",       "senior"):  QATestSignature,
}

_ROLE_CATEGORY: dict[str, str] = {
    "ba":           "ba",
    "qa_backend":   "qa",
    "frontend_dev": "code",
    "backend_dev":  "code",
    "db_eng":       "code",
    "ai_eng":       "code",
    "sre":          "code",
}


def get_signature(role: str, tier: Tier) -> type[dspy.Signature]:
    category = _ROLE_CATEGORY.get(role, "code")
    sig = _SIG_MAP.get((category, tier)) or _SIG_MAP.get(("code", tier))
    if sig is None:
        return JuniorCodeSignature
    return sig
