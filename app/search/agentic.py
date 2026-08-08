"""Bounded agentic retrieval loop.

A complex multi-domain question embeds to a centroid sitting between its
sub-questions and matching none of them well, so a single query -> single
embedding -> single pass is wrong before any orchestration runs. This module
decomposes the question, fans out retrieval per sub-question, and stops on the
first of three conditions rather than running to a fixed depth.

Deliberately not a framework. What makes retrieval agentic is the decision
logic - what to decompose into, how to allocate budget across corpora, when
there is enough evidence. A general-purpose state model supplies edges and a
state dict while every node stays hand-written, and it obstructs the one thing
that has to thread through every node here: budget accounting.

Three properties are load-bearing:

* **Pre-committed budget.** Each node declares ``max_token_cost`` before
  dispatch and the driver refuses any node whose declared cost exceeds the
  remaining ceiling. On a 4GB local provider you cannot discover you are over
  budget after the generation call has already run.
* **Fixpoint termination.** If an iteration surfaces no chunk the accumulated
  evidence does not already hold, the loop stops. This is what prevents burning
  the full budget on a query the retriever has already exhausted.
* **An explicit not-found list.** Sub-questions that finish unsatisfied are
  reported as such. A system that says "nothing in your research notes on this"
  is doing something a chatbot with search cannot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.search.context_builder import token_count

logger = logging.getLogger(__name__)

# Cost the driver charges itself for one decomposition round-trip before it
# runs. Deliberately generous: refusing a node we could have afforded costs a
# little recall, overrunning the context ceiling costs the answer.
_DECOMPOSE_COST_ESTIMATE = 600

# Rough per-chunk token cost, used only for the pre-dispatch budget declaration.
# The driver charges actual token counts once chunks arrive.
_AVG_CHUNK_TOKENS = 180

_DECOMPOSE_PROMPT = """Break the user's question into the smallest set of independent \
sub-questions that together answer it. Return ONLY a JSON array of objects, no prose.

Each object: {{"question": "<sub-question>", "domain": "<one-or-two word topic hint, or null>"}}

Rules:
- If the question is already single-purpose, return exactly one object.
- Never return more than {max_subqueries} objects.
- Sub-questions must be answerable independently by searching a document corpus.

Question: {query}"""


@dataclass
class SubQuery:
    text: str
    domain_hint: str | None = None
    status: str = "pending"  # pending | satisfied | unanswered
    k: int = 5


@dataclass
class Evidence:
    chunk: dict[str, Any]
    folder_tag: str
    subquery: str


@dataclass
class TraceEvent:
    kind: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryState:
    query: str
    subqueries: list[SubQuery] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    iteration: int = 0
    tokens_spent: int = 0
    tokens_ceiling: int = 0
    trace: list[TraceEvent] = field(default_factory=list)
    stop_reason: str = ""

    def evidence_ids(self) -> set[Any]:
        return {e.chunk.get("chunk_id") for e in self.evidence}

    def chunks(self) -> list[dict[str, Any]]:
        """Accumulated evidence as retrieval results, best-scoring first."""
        seen: set[Any] = set()
        out: list[dict[str, Any]] = []
        for e in sorted(self.evidence, key=lambda ev: ev.chunk.get("score", 0.0), reverse=True):
            cid = e.chunk.get("chunk_id")
            if cid in seen:
                continue
            seen.add(cid)
            out.append(e.chunk)
        return out

    def unanswered(self) -> list[str]:
        return [s.text for s in self.subqueries if s.status != "satisfied"]

    def note(self, kind: str, detail: str, **data: Any) -> None:
        self.trace.append(TraceEvent(kind=kind, detail=detail, data=data))

    def can_afford(self, cost: int) -> bool:
        return self.tokens_spent + cost <= self.tokens_ceiling


def _parse_subqueries(raw: str, original: str, max_subqueries: int) -> list[str]:
    """Extract sub-questions from an LLM response.

    Falls back to the original query on anything unexpected. A local 3B model
    will wrap JSON in prose or emit a bare list of strings often enough that
    strict parsing alone would silently disable decomposition.
    """
    if not raw:
        return [original]

    candidate = raw.strip()
    match = re.search(r"\[.*\]", candidate, re.DOTALL)
    if match:
        candidate = match.group(0)

    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        logger.debug("Decomposition response was not JSON; using the original query.")
        return [original]

    if not isinstance(parsed, list):
        return [original]

    questions: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("question") or item.get("text") or "").strip()
        else:
            continue
        if text and text not in questions:
            questions.append(text)

    if not questions:
        return [original]
    return questions[:max_subqueries]


def _parse_domains(raw: str, questions: list[str]) -> dict[str, str | None]:
    """Best-effort domain hints keyed by sub-question text."""
    hints: dict[str, str | None] = dict.fromkeys(questions)
    match = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not match:
        return hints
    try:
        parsed = json.loads(match.group(0))
    except (ValueError, TypeError):
        return hints
    if not isinstance(parsed, list):
        return hints
    for item in parsed:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or item.get("text") or "").strip()
        domain = item.get("domain")
        if text in hints and isinstance(domain, str) and domain.strip():
            hints[text] = domain.strip()
    return hints


async def decompose_node(state: QueryState, llm_client: Any, k: int) -> QueryState:
    """Split the query into sub-questions and allocate each a share of the budget.

    The per-sub-question ``k`` is derived from the remaining ceiling divided by
    the sub-question count, not held constant: four sub-questions at
    ``retrieval_top_k=15`` is 60 chunks, which exceeds the context ceiling
    before the loop runs once. This is the honest version of adaptive-k - it
    replaces the word-count heuristic with an actual budget.
    """
    max_subqueries = max(1, settings.agentic_subquery_max)

    if not state.can_afford(_DECOMPOSE_COST_ESTIMATE):
        state.note("decompose", "Skipped decomposition - insufficient token budget.")
        state.subqueries = [SubQuery(text=state.query, k=k)]
        return state

    prompt = _DECOMPOSE_PROMPT.format(query=state.query, max_subqueries=max_subqueries)
    try:
        raw = await llm_client.generate_raw([{"role": "user", "content": prompt}])
    except Exception as e:
        logger.warning("Decomposition call failed (%s) - falling back to the original query.", e)
        raw = ""

    state.tokens_spent += token_count(prompt) + token_count(raw or "")

    questions = _parse_subqueries(raw or "", state.query, max_subqueries)
    hints = _parse_domains(raw or "", questions)

    # Chunk budget split across sub-questions, floored so a wide decomposition
    # still retrieves something usable per branch.
    per_query_k = max(3, k // max(1, len(questions)))
    state.subqueries = [
        SubQuery(text=q, domain_hint=hints.get(q), k=per_query_k) for q in questions
    ]

    if len(questions) == 1 and questions[0] == state.query:
        state.note("decompose", "Treated as a single question.", subqueries=questions)
    else:
        state.note(
            "decompose",
            f"Split into {len(questions)} sub-question(s).",
            subqueries=questions,
            per_query_k=per_query_k,
        )
    return state


async def fanout_node(
    state: QueryState,
    *,
    retrieve: Any,
    pending: list[SubQuery],
) -> tuple[QueryState, set[Any]]:
    """Retrieve for each pending sub-question concurrently.

    Returns the ids seen this round so the driver can test for fixpoint.
    ``retrieve`` is injected rather than imported to keep this module free of a
    circular dependency on ``retrieval``.
    """
    if not pending:
        return state, set()

    results = await asyncio.gather(
        *(retrieve(sq.text, sq.k) for sq in pending),
        return_exceptions=True,
    )

    round_ids: set[Any] = set()
    for sq, res in zip(pending, results, strict=True):
        if isinstance(res, BaseException):
            logger.warning("Sub-query retrieval failed for %r: %s", sq.text, res)
            state.note("retrieve", f"Retrieval failed for: {sq.text}")
            continue

        chunks = list(res)
        for chunk in chunks:
            round_ids.add(chunk.get("chunk_id"))
            state.evidence.append(
                Evidence(
                    chunk=chunk,
                    folder_tag=chunk.get("folder_tag") or "",
                    subquery=sq.text,
                )
            )
            state.tokens_spent += token_count(chunk.get("text", ""))

        sources = sorted({c.get("folder_tag") or "untagged" for c in chunks})
        state.note(
            "retrieve",
            f"{sq.text} - {len(chunks)} result(s)"
            + (f" from {', '.join(sources)}" if sources else ""),
            subquery=sq.text,
            count=len(chunks),
            sources=sources,
        )

    return state, round_ids


def sufficiency_node(state: QueryState) -> QueryState:
    """Mark sub-questions satisfied when they have evidence above the score floor."""
    floor = settings.agentic_evidence_score_floor
    by_subquery: dict[str, list[Evidence]] = {}
    for e in state.evidence:
        by_subquery.setdefault(e.subquery, []).append(e)

    for sq in state.subqueries:
        hits = by_subquery.get(sq.text, [])
        if any(h.chunk.get("score", 0.0) > floor for h in hits):
            sq.status = "satisfied"
        elif hits:
            # Retrieved something, but nothing clears the floor.
            sq.status = "unanswered"
        else:
            sq.status = "unanswered"
    return state


async def run_agentic_loop(
    query: str,
    *,
    retrieve: Any,
    llm_client: Any,
    k: int,
    tokens_ceiling: int,
) -> QueryState:
    """Drive the bounded loop and return the final state.

    ``retrieve`` is an ``async (text: str, k: int) -> list[dict]`` callable -
    normally a partial over ``hybrid_retrieve`` bound to the caller's db,
    embedding service and filters.
    """
    state = QueryState(query=query, tokens_ceiling=max(0, tokens_ceiling))
    state.note("start", f"Budget: {state.tokens_ceiling} tokens.")

    state = await decompose_node(state, llm_client, k)

    max_iterations = max(1, settings.agentic_max_iterations)
    while True:
        if state.iteration >= max_iterations:
            state.stop_reason = "iteration_cap"
            break

        pending = [sq for sq in state.subqueries if sq.status != "satisfied"]
        if not pending:
            state.stop_reason = "all_satisfied"
            break

        # Pre-commit: declare the round's cost before dispatching it.
        declared = sum(sq.k for sq in pending) * _AVG_CHUNK_TOKENS
        if not state.can_afford(declared):
            state.stop_reason = "budget_exhausted"
            state.note(
                "stop",
                f"Stopped before iteration {state.iteration + 1}: "
                f"declared cost {declared} exceeds the remaining budget.",
            )
            break

        before = state.evidence_ids()
        state, round_ids = await fanout_node(state, retrieve=retrieve, pending=pending)
        state.iteration += 1

        if round_ids and round_ids <= before:
            state.stop_reason = "fixpoint"
            state.note("stop", "Stopped early: this round surfaced nothing new.")
            break
        if not round_ids:
            state.stop_reason = "no_results"
            break

        state = sufficiency_node(state)

    state = sufficiency_node(state)

    missing = state.unanswered()
    if missing:
        state.note(
            "not_found",
            "Searched for but found nothing on: " + "; ".join(missing),
            subqueries=missing,
        )

    if not state.stop_reason:
        state.stop_reason = "complete"
    state.note(
        "done",
        f"Finished after {state.iteration} iteration(s) ({state.stop_reason}); "
        f"{state.tokens_spent}/{state.tokens_ceiling} tokens.",
        stop_reason=state.stop_reason,
        iterations=state.iteration,
        tokens_spent=state.tokens_spent,
    )
    return state


def trace_payload(state: QueryState) -> list[dict[str, Any]]:
    """Serialize the trace for API responses and the stream."""
    return [{"kind": e.kind, "detail": e.detail, **e.data} for e in state.trace]
