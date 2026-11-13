"""Compiled knowledge state and entity-view reconciliation for corpusnote."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import re
from typing import Iterable

from .errors import MissingPage, RejectedWrite
from .layout import slugify_project
from .models import Capture, CompileResult, CompiledClaim, Corpus, KnowledgeDigest, Page, TopicClash
from .pages import _now_iso, contains_instruction_injection, read_page, write_page
from .scrub import scrub_text

VALID_PILLARS = ("pattern", "fact", "operator_view", "stakeholder_view", "agent_hypothesis")

DEFAULT_STANCE_BY_PILLAR = {
    "pattern": "proven",
    "fact": "verified",
    "operator_view": "preference",
    "stakeholder_view": "priority",
    "agent_hypothesis": "hypothesis",
}

DEFAULT_ENTITY_BY_PILLAR = {
    "pattern": "system",
    "fact": "system",
    "operator_view": "dawa",
    "stakeholder_view": "stakeholder",
    "agent_hypothesis": "agent",
}

VALID_SCOPES = ("project", "client", "general")

MAX_CLAIMS_PER_PAGE = 25
MAX_DEAD_ENDS_PER_PAGE = 5

_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "then", "there", "these", "this", "to", "was", "were", "will", "with", "we",
    "our", "should", "need", "needs", "make", "made", "update", "updated", "fix",
    "fixed", "add", "added", "implement", "implemented", "use", "using", "used",
    "test", "tests", "session", "goal", "run", "running", "work", "new", "when",
})

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]{1,30}")
_ABS_PATH_RE = re.compile(r"(?:/Users/|/home/|/var/folders/|/private/var/)[A-Za-z0-9._/-]+")


def _significant_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for m in _WORD_RE.finditer(text.lower()):
        tok = m.group(0).strip("_-")
        if len(tok) < 2 or tok in _STOPWORDS or tok.isdigit():
            continue
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)
    return tokens


def infer_topic(text: str, fallback: str = "general") -> str:
    """Infer a normalized topic slug from a goal, title, or statement."""
    tokens = _significant_tokens(text)
    if not tokens:
        return slugify_project(fallback)
    return slugify_project("-".join(tokens[:4]))


def _topics_match(existing_topic: str, incoming_topic: str, existing_stmt: str, incoming_stmt: str) -> bool:
    """Determine if two claims address the same domain topic."""
    t1 = slugify_project(existing_topic)
    t2 = slugify_project(incoming_topic)
    if t1 == t2:
        return True

    toks1 = set(_significant_tokens(existing_topic.replace("-", " ")))
    toks2 = set(_significant_tokens(incoming_topic.replace("-", " ")))
    if toks1 and toks2:
        overlap = len(toks1 & toks2)
        smaller = min(len(toks1), len(toks2))
        if smaller >= 2 and overlap / smaller >= 0.75:
            return True

    stmt_toks1 = set(_significant_tokens(existing_stmt)[:8])
    stmt_toks2 = set(_significant_tokens(incoming_stmt)[:8])
    if toks1 and toks2 and (toks1 & toks2) and stmt_toks1 and stmt_toks2:
        union = len(stmt_toks1 | stmt_toks2)
        if union > 0 and len(stmt_toks1 & stmt_toks2) / union >= 0.6:
            return True

    return False


def _claim_id(pillar: str, entity: str, topic: str) -> str:
    raw = f"{pillar}:{entity}:{topic}".encode("utf-8")
    return "clm-" + hashlib.sha256(raw).hexdigest()[:12]


def _state_relpath_for(project: str | None, scope: str = "project", *, is_commons: bool = False) -> str:
    if is_commons:
        return "state/commons.md"
    if project and scope in ("project", "general"):
        return f"projects/{slugify_project(project)}/state.md"
    return "state/collection.md"


def _claim_from_dict(data: dict) -> CompiledClaim:
    pillar = str(data.get("pillar") or "fact")
    if pillar not in VALID_PILLARS:
        pillar = "agent_hypothesis"
    entity = str(data.get("entity") or DEFAULT_ENTITY_BY_PILLAR[pillar])
    stance = str(data.get("stance") or DEFAULT_STANCE_BY_PILLAR[pillar])
    topic = slugify_project(str(data.get("topic") or "general"))
    cid = str(data.get("id") or _claim_id(pillar, entity, topic))
    prov = data.get("provenance")
    prov_list = [str(p) for p in prov if p] if isinstance(prov, list) else []
    try:
        ev_count = max(1, int(data.get("evidence_count") or 1))
    except (TypeError, ValueError):
        ev_count = 1
    return CompiledClaim(
        id=cid,
        topic=topic,
        pillar=pillar,
        entity=entity,
        stance=stance,
        statement=str(data.get("statement") or "").strip(),
        scope=str(data.get("scope") or "project"),
        project=str(data.get("project") or ""),
        project_id=str(data.get("project_id") or ""),
        provenance=prov_list,
        previous=str(data.get("previous") or "").strip(),
        updated_at=str(data.get("updated_at") or ""),
        evidence_count=ev_count,
    )


def read_compiled_claims(corpus: Corpus, relpath: str) -> list[CompiledClaim]:
    """Read structured compiled claims from a state page. Returns [] if absent."""
    try:
        page = read_page(corpus, relpath)
    except MissingPage:
        return []
    raw_claims = page.frontmatter.get("claims")
    if not isinstance(raw_claims, list):
        return []
    claims: list[CompiledClaim] = []
    for item in raw_claims:
        if isinstance(item, dict) and item.get("statement"):
            c = _claim_from_dict(item)
            if not contains_instruction_injection(c.statement):
                claims.append(c)
    return claims


def render_compiled_markdown(claims: Iterable[CompiledClaim], *, title: str = "Compiled State") -> str:
    """Render structured claims into a compact, human-readable Markdown view.

    Proven Patterns ("The Line Ahead") are rendered first, sorted by evidence_count
    descending so agents focus on canonical positive patterns rather than negative hazards.
    """
    claim_list = list(claims)
    if not claim_list:
        return f"# {title}\n\n_No compiled knowledge recorded yet._\n"

    patterns = sorted(
        [c for c in claim_list if c.pillar == "pattern"],
        key=lambda c: (-c.evidence_count, c.topic),
    )
    facts = [c for c in claim_list if c.pillar == "fact"]
    operator_views = [c for c in claim_list if c.pillar == "operator_view"]
    stakeholder_views = [c for c in claim_list if c.pillar == "stakeholder_view"]
    hypotheses = [c for c in claim_list if c.pillar == "agent_hypothesis"]

    lines: list[str] = [f"# {title}"]

    def _format_bullet(c: CompiledClaim, include_entity: bool = True) -> str:
        ev_suffix = f" · {c.evidence_count}x" if c.evidence_count > 1 else ""
        tag = f"[{c.entity} | {c.stance}{ev_suffix}]" if include_entity else f"[{c.stance}{ev_suffix}]"
        prov = f" (refs: {', '.join(c.provenance[:3])})" if c.provenance else ""
        line = f"- **{c.topic}** {tag}: {c.statement}{prov}"
        if c.previous:
            line += f"\n  - _Previously: {c.previous}_"
        return line

    if patterns:
        lines.append("\n### Proven Patterns (The Line Ahead)")
        for c in patterns:
            lines.append(_format_bullet(c, include_entity=False))

    if facts:
        lines.append("\n### Accepted Facts")
        for c in facts:
            lines.append(_format_bullet(c, include_entity=False))

    if operator_views:
        lines.append("\n### Operator View")
        for c in operator_views:
            lines.append(_format_bullet(c, include_entity=True))

    if stakeholder_views:
        lines.append("\n### Stakeholder Views")
        for c in stakeholder_views:
            lines.append(_format_bullet(c, include_entity=True))

    if hypotheses:
        lines.append("\n### Open Agent Hypotheses & Local Hazards")
        for c in hypotheses:
            lines.append(_format_bullet(c, include_entity=True))

    return "\n".join(lines) + "\n"


def _compact_claims(
    claims: list[CompiledClaim],
    max_claims: int = MAX_CLAIMS_PER_PAGE,
    max_dead_ends: int = MAX_DEAD_ENDS_PER_PAGE,
) -> list[CompiledClaim]:
    """Keep claims bounded under max_claims, capping dead-ends and pruning oldest hypotheses first."""
    # 1. Cap dead_ends first so negative obstructions never accumulate unboundedly
    dead_ends = [c for c in claims if c.pillar == "agent_hypothesis" and c.stance == "dead_end"]
    if len(dead_ends) > max_dead_ends:
        drop_ids = {c.id for c in dead_ends[:-max_dead_ends]}
        claims = [c for c in claims if c.id not in drop_ids]

    if len(claims) <= max_claims:
        return claims

    # 2. Separate remaining dead_ends, other hypotheses, and authoritative claims
    rem_dead = [c for c in claims if c.pillar == "agent_hypothesis" and c.stance == "dead_end"]
    other_hyps = [c for c in claims if c.pillar == "agent_hypothesis" and c.stance != "dead_end"]
    auth = [c for c in claims if c.pillar != "agent_hypothesis"]

    while len(auth) + len(other_hyps) + len(rem_dead) > max_claims and rem_dead:
        rem_dead.pop(0)
    while len(auth) + len(other_hyps) > max_claims and other_hyps:
        other_hyps.pop(0)

    combined = auth + other_hyps + rem_dead
    if len(combined) > max_claims:
        combined = combined[-max_claims:]
    return combined


def _write_state_page(
    corpus: Corpus,
    relpath: str,
    claims: list[CompiledClaim],
    *,
    title: str,
    project: str = "",
    project_id: str = "",
    extra_secrets: tuple[str, ...] = (),
) -> Page:
    compacted = _compact_claims(claims)
    serialized = [asdict(c) for c in compacted]
    body = render_compiled_markdown(compacted, title=title)
    fm = {
        "title": title,
        "kind": "state",
        "status": "active",
        "source": "compiler",
        "project": project,
        "project_id": project_id,
        "claims": serialized,
    }
    return write_page(
        corpus,
        relpath,
        frontmatter=fm,
        body=body,
        extra_secrets=extra_secrets,
    )


def deidentify_general_statement(
    statement: str,
    *,
    corpus_id: str = "",
    project: str = "",
    redact_terms: tuple[str, ...] = (),
    extra_secrets: tuple[str, ...] = (),
) -> str:
    """Scrub secrets, absolute paths, and proprietary client/project names for general commons."""
    scrubbed, _ = scrub_text(statement, extra_secrets=extra_secrets)
    cleaned = _ABS_PATH_RE.sub("<path>", scrubbed)

    terms_to_strip = list(redact_terms)
    if corpus_id and corpus_id not in ("personal", "general"):
        terms_to_strip.append(corpus_id)
    if project:
        terms_to_strip.append(project)

    for term in terms_to_strip:
        clean_term = term.strip()
        if len(clean_term) >= 3:
            pattern = re.compile(re.escape(clean_term), re.IGNORECASE)
            cleaned = pattern.sub("<project>", cleaned)

    return cleaned.strip()


def compile_claim(
    corpus: Corpus,
    *,
    statement: str,
    pillar: str = "fact",
    entity: str | None = None,
    stance: str | None = None,
    topic: str | None = None,
    scope: str = "project",
    project: str | None = None,
    project_id: str | None = None,
    provenance: list[str] | None = None,
    commons_corpus: Corpus | None = None,
    redact_terms: tuple[str, ...] = (),
    extra_secrets: tuple[str, ...] = (),
    is_commons: bool = False,
) -> CompileResult:
    """Evaluate and compile a single claim into the corpus state (and commons if scope == 'general')."""
    if pillar not in VALID_PILLARS:
        raise RejectedWrite("bad_kind")
    if scope not in VALID_SCOPES:
        scope = "project"

    raw_stmt = (statement or "").strip()
    if not raw_stmt:
        raise RejectedWrite("empty_statement")

    if contains_instruction_injection(raw_stmt) or (topic and contains_instruction_injection(topic)):
        raise RejectedWrite("instruction_in_state")

    resolved_entity = (entity or DEFAULT_ENTITY_BY_PILLAR[pillar]).strip()
    resolved_stance = (stance or DEFAULT_STANCE_BY_PILLAR[pillar]).strip()

    # Authority enforcement: an agent cannot directly assert an unverified fact or pattern
    if pillar == "fact" and resolved_entity.startswith("agent:") and resolved_stance not in ("verified", "accepted"):
        pillar = "agent_hypothesis"
        resolved_stance = "hypothesis"
    elif pillar == "pattern" and resolved_entity.startswith("agent:") and resolved_stance not in ("proven", "canonical", "verified"):
        pillar = "agent_hypothesis"
        resolved_stance = "candidate_pattern"

    scrubbed_stmt, _ = scrub_text(raw_stmt, extra_secrets=extra_secrets)
    resolved_topic = slugify_project(topic) if topic and topic.strip() else infer_topic(scrubbed_stmt)
    now = _now_iso()
    prov_list = [str(p) for p in (provenance or []) if p]

    relpath = _state_relpath_for(project, scope, is_commons=is_commons)
    existing_claims = read_compiled_claims(corpus, relpath)

    resolved_ids: list[str] = []
    action = "added"
    updated_claim: CompiledClaim | None = None
    new_claims: list[CompiledClaim] = []

    if pillar in ("pattern", "fact"):
        # 1. Check if an existing pattern/fact on the same topic exists -> supersede or reinforce (incrementing evidence_count)
        # 2. Graduate/clear any open agent_hypothesis (including dead_end, candidate_pattern, hypothesis) or operator_view(intention) on the same topic
        graduated_prev = ""
        graduated_prov: list[str] = []

        for c in existing_claims:
            matched = _topics_match(c.topic, resolved_topic, c.statement, scrubbed_stmt)
            if not matched:
                new_claims.append(c)
                continue

            if c.pillar == "agent_hypothesis":
                resolved_ids.append(c.id)
                for p in c.provenance:
                    if p not in graduated_prov:
                        graduated_prov.append(p)
                if not graduated_prev:
                    if c.stance == "dead_end":
                        graduated_prev = f"Cleared dead-end ({c.entity}): {c.statement[:160]}"
                    elif c.stance == "candidate_pattern":
                        graduated_prev = f"Graduated candidate pattern ({c.entity}): {c.statement[:160]}"
                    else:
                        graduated_prev = f"Confirmed hypothesis ({c.entity}): {c.statement[:160]}"
                continue

            if c.pillar == "operator_view" and c.stance == "intention":
                resolved_ids.append(c.id)
                for p in c.provenance:
                    if p not in graduated_prov:
                        graduated_prov.append(p)
                if not graduated_prev:
                    graduated_prev = f"Fulfilled intention ({c.entity}): {c.statement[:160]}"
                continue

            if c.pillar == pillar and updated_claim is None:
                merged_prov = list(dict.fromkeys(c.provenance + graduated_prov + prov_list))
                if c.statement.strip().lower() == scrubbed_stmt.strip().lower():
                    action = "reinforced"
                    updated_claim = CompiledClaim(
                        id=c.id,
                        topic=c.topic,
                        pillar=pillar,
                        entity=resolved_entity,
                        stance=resolved_stance,
                        statement=c.statement,
                        scope=scope,
                        project=project or c.project,
                        project_id=project_id or c.project_id,
                        provenance=merged_prov,
                        previous=c.previous or graduated_prev,
                        updated_at=now,
                        evidence_count=c.evidence_count + 1,
                    )
                else:
                    action = "superseded"
                    updated_claim = CompiledClaim(
                        id=c.id,
                        topic=resolved_topic,
                        pillar=pillar,
                        entity=resolved_entity,
                        stance=resolved_stance,
                        statement=scrubbed_stmt,
                        scope=scope,
                        project=project or c.project,
                        project_id=project_id or c.project_id,
                        provenance=merged_prov,
                        previous=c.statement[:200],
                        updated_at=now,
                        evidence_count=c.evidence_count + 1 if pillar == "pattern" else 1,
                    )
                new_claims.append(updated_claim)
            else:
                new_claims.append(c)

        if updated_claim is None:
            if resolved_ids:
                action = "graduated"
            merged_prov = list(dict.fromkeys(graduated_prov + prov_list))
            updated_claim = CompiledClaim(
                id=_claim_id(pillar, resolved_entity, resolved_topic),
                topic=resolved_topic,
                pillar=pillar,
                entity=resolved_entity,
                stance=resolved_stance,
                statement=scrubbed_stmt,
                scope=scope,
                project=project or "",
                project_id=project_id or "",
                provenance=merged_prov,
                previous=graduated_prev,
                updated_at=now,
                evidence_count=1,
            )
            new_claims.append(updated_claim)

    elif pillar in ("operator_view", "stakeholder_view"):
        # Same entity on same topic evolves/supersedes their own view.
        # Different entity on same topic coexists so divergent perspectives are preserved.
        other_entity_on_topic = False

        for c in existing_claims:
            matched = _topics_match(c.topic, resolved_topic, c.statement, scrubbed_stmt)
            if not matched:
                new_claims.append(c)
                continue

            if c.pillar == pillar and c.entity.lower() == resolved_entity.lower() and updated_claim is None:
                merged_prov = list(dict.fromkeys(c.provenance + prov_list))
                if c.statement.strip().lower() == scrubbed_stmt.strip().lower() and c.stance == resolved_stance:
                    action = "reinforced"
                    updated_claim = CompiledClaim(
                        id=c.id,
                        topic=c.topic,
                        pillar=pillar,
                        entity=resolved_entity,
                        stance=resolved_stance,
                        statement=c.statement,
                        scope=scope,
                        project=project or c.project,
                        project_id=project_id or c.project_id,
                        provenance=merged_prov,
                        previous=c.previous,
                        updated_at=now,
                        evidence_count=c.evidence_count + 1,
                    )
                else:
                    action = "superseded"
                    updated_claim = CompiledClaim(
                        id=c.id,
                        topic=resolved_topic,
                        pillar=pillar,
                        entity=resolved_entity,
                        stance=resolved_stance,
                        statement=scrubbed_stmt,
                        scope=scope,
                        project=project or c.project,
                        project_id=project_id or c.project_id,
                        provenance=merged_prov,
                        previous=f"[{c.stance}] {c.statement[:180]}",
                        updated_at=now,
                        evidence_count=1,
                    )
                new_claims.append(updated_claim)
            else:
                if c.pillar in ("operator_view", "stakeholder_view"):
                    other_entity_on_topic = True
                new_claims.append(c)

        if updated_claim is None:
            action = "coexisting" if other_entity_on_topic else "added"
            updated_claim = CompiledClaim(
                id=_claim_id(pillar, resolved_entity, resolved_topic),
                topic=resolved_topic,
                pillar=pillar,
                entity=resolved_entity,
                stance=resolved_stance,
                statement=scrubbed_stmt,
                scope=scope,
                project=project or "",
                project_id=project_id or "",
                provenance=prov_list,
                previous="",
                updated_at=now,
                evidence_count=1,
            )
            new_claims.append(updated_claim)

    else:  # agent_hypothesis
        # Cannot overwrite patterns, facts, operator_view, or stakeholder_view.
        for c in existing_claims:
            matched = _topics_match(c.topic, resolved_topic, c.statement, scrubbed_stmt)
            if not matched:
                new_claims.append(c)
                continue

            if c.pillar == "agent_hypothesis" and updated_claim is None:
                merged_prov = list(dict.fromkeys(c.provenance + prov_list))
                if c.statement.strip().lower() == scrubbed_stmt.strip().lower():
                    action = "reinforced"
                    updated_claim = CompiledClaim(
                        id=c.id,
                        topic=c.topic,
                        pillar="agent_hypothesis",
                        entity=resolved_entity,
                        stance=resolved_stance,
                        statement=c.statement,
                        scope=scope,
                        project=project or c.project,
                        project_id=project_id or c.project_id,
                        provenance=merged_prov,
                        previous=c.previous,
                        updated_at=now,
                        evidence_count=c.evidence_count + 1,
                    )
                else:
                    action = "superseded"
                    updated_claim = CompiledClaim(
                        id=c.id,
                        topic=resolved_topic,
                        pillar="agent_hypothesis",
                        entity=resolved_entity,
                        stance=resolved_stance,
                        statement=scrubbed_stmt,
                        scope=scope,
                        project=project or c.project,
                        project_id=project_id or c.project_id,
                        provenance=merged_prov,
                        previous=c.statement[:180],
                        updated_at=now,
                        evidence_count=1,
                    )
                new_claims.append(updated_claim)
            else:
                new_claims.append(c)

        if updated_claim is None:
            updated_claim = CompiledClaim(
                id=_claim_id("agent_hypothesis", resolved_entity, resolved_topic),
                topic=resolved_topic,
                pillar="agent_hypothesis",
                entity=resolved_entity,
                stance=resolved_stance,
                statement=scrubbed_stmt,
                scope=scope,
                project=project or "",
                project_id=project_id or "",
                provenance=prov_list,
                previous="",
                updated_at=now,
                evidence_count=1,
            )
            new_claims.append(updated_claim)

    title = (
        "Compiled General Knowledge"
        if is_commons
        else (f"Compiled State: {project}" if project and scope == "project" else f"Compiled State: {corpus.id}")
    )
    _write_state_page(
        corpus,
        relpath,
        new_claims,
        title=title,
        project=(project or "") if not is_commons else "",
        project_id=(project_id or "") if not is_commons else "",
        extra_secrets=extra_secrets,
    )

    promoted_general: CompiledClaim | None = None
    if (
        not is_commons
        and scope == "general"
        and pillar in ("pattern", "fact", "operator_view")
        and commons_corpus is not None
    ):
        general_stmt = deidentify_general_statement(
            scrubbed_stmt,
            corpus_id=corpus.id,
            project=project or "",
            redact_terms=redact_terms,
            extra_secrets=extra_secrets,
        )
        if general_stmt and not contains_instruction_injection(general_stmt):
            prom_res = compile_claim(
                commons_corpus,
                statement=general_stmt,
                pillar=pillar,
                entity=resolved_entity,
                stance=resolved_stance,
                topic=resolved_topic,
                scope="general",
                project="",
                project_id="",
                provenance=prov_list,
                extra_secrets=extra_secrets,
                is_commons=True,
            )
            promoted_general = prom_res.claim

    return CompileResult(
        action=action,
        claim=updated_claim,
        state_relpath=relpath,
        resolved_ids=resolved_ids,
        promoted_general=promoted_general,
    )


def _summarize_capture_outcome(goal: str, summary: str) -> str:
    """Distill a concise 1-2 sentence statement from a session goal and summary."""
    clean_goal = " ".join((goal or "").strip().splitlines()).strip()
    clean_summary = " ".join((summary or "").strip().splitlines()).strip()

    if clean_summary and clean_goal:
        if clean_goal.lower() in clean_summary.lower():
            distilled = clean_summary
        else:
            distilled = f"{clean_goal[:100]} -> {clean_summary}"
    else:
        distilled = clean_summary or clean_goal

    return distilled[:260].strip()


_PATTERN_LINE_RE = re.compile(r"^\s*(?:proven\s+)?pattern\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def compile_capture(
    corpus: Corpus,
    item: Capture,
    *,
    agent_provider: str | None = None,
    residual_risks: list[str] | None = None,
    proven_pattern: str | None = None,
    commons_corpus: Corpus | None = None,
    redact_terms: tuple[str, ...] = (),
    extra_secrets: tuple[str, ...] = (),
) -> list[CompileResult]:
    """Compile a completed session Capture into the project/collection compiled state.

    - Verified sessions (verification.passed is True) compile into Accepted Facts (and graduate
      matching hypotheses, dead_ends, or intentions). If a proven_pattern is supplied (or a
      'Pattern: ...' line is present in output_summary), it also compiles/reinforces a Proven Pattern.
    - Failed verification sessions (verification.passed is False) compile into a temporary
      local hazard (stance='dead_end') so retries do not repeat the failed approach.
    - Unverified sessions (verification.passed is None) compile into Agent Hypotheses.
    - Residual risks compile as Agent Hypotheses with stance='risk'.
    """
    results: list[CompileResult] = []
    scrubbed_goal, _ = scrub_text(item.goal or "", extra_secrets=extra_secrets)
    scrubbed_summary, _ = scrub_text(item.output_summary or "", extra_secrets=extra_secrets)

    # Skip if goal or summary contains instruction injection
    if contains_instruction_injection(scrubbed_goal) or contains_instruction_injection(scrubbed_summary):
        return results

    statement = _summarize_capture_outcome(scrubbed_goal, scrubbed_summary)
    if not statement:
        return results

    topic = infer_topic(scrubbed_goal or scrubbed_summary, fallback=item.project or "session")
    provider_tag = f"agent:{(agent_provider or 'session').strip()}"
    target_scope = "project" if item.project else "client"

    if item.verification.passed is True:
        res = compile_claim(
            corpus,
            statement=statement,
            pillar="fact",
            entity="system",
            stance="verified",
            topic=topic,
            scope=target_scope,
            project=item.project or None,
            project_id=item.project_id or None,
            provenance=[item.session_id],
            extra_secrets=extra_secrets,
        )
        results.append(res)

        # Extract or compile any explicit proven pattern ("The Line Ahead")
        extracted_pattern = proven_pattern
        if not extracted_pattern and scrubbed_summary:
            m = _PATTERN_LINE_RE.search(scrubbed_summary)
            if m:
                extracted_pattern = m.group(1).strip()
        if extracted_pattern:
            clean_pat, _ = scrub_text(str(extracted_pattern).strip(), extra_secrets=extra_secrets)
            if clean_pat and not contains_instruction_injection(clean_pat):
                pat_topic = infer_topic(clean_pat, fallback=topic)
                pat_res = compile_claim(
                    corpus,
                    statement=clean_pat[:240],
                    pillar="pattern",
                    entity="system",
                    stance="proven",
                    topic=pat_topic,
                    scope=target_scope,
                    project=item.project or None,
                    project_id=item.project_id or None,
                    provenance=[item.session_id],
                    commons_corpus=commons_corpus,
                    redact_terms=redact_terms,
                    extra_secrets=extra_secrets,
                )
                results.append(pat_res)
    elif item.verification.passed is False:
        dead_stmt = f"Failed verification (exit_code={item.verification.exit_code}): {statement}"
        res = compile_claim(
            corpus,
            statement=dead_stmt[:260],
            pillar="agent_hypothesis",
            entity=provider_tag,
            stance="dead_end",
            topic=topic,
            scope=target_scope,
            project=item.project or None,
            project_id=item.project_id or None,
            provenance=[item.session_id],
            extra_secrets=extra_secrets,
        )
        results.append(res)
    else:
        res = compile_claim(
            corpus,
            statement=statement,
            pillar="agent_hypothesis",
            entity=provider_tag,
            stance="hypothesis",
            topic=topic,
            scope=target_scope,
            project=item.project or None,
            project_id=item.project_id or None,
            provenance=[item.session_id],
            extra_secrets=extra_secrets,
        )
        results.append(res)

    for risk in (residual_risks or []):
        clean_risk, _ = scrub_text(str(risk).strip(), extra_secrets=extra_secrets)
        if not clean_risk or contains_instruction_injection(clean_risk):
            continue
        risk_topic = infer_topic(clean_risk, fallback=f"{topic}-risk")
        risk_res = compile_claim(
            corpus,
            statement=clean_risk[:240],
            pillar="agent_hypothesis",
            entity=provider_tag,
            stance="risk",
            topic=risk_topic,
            scope=target_scope,
            project=item.project or None,
            project_id=item.project_id or None,
            provenance=[item.session_id],
            extra_secrets=extra_secrets,
        )
        results.append(risk_res)

    return results


def digest_compiled_state(
    corpus: Corpus,
    *,
    project: str | None = None,
    commons_corpus: Corpus | None = None,
) -> KnowledgeDigest:
    """Build a structured digest highlighting Proven Patterns, Clashes, Dead Ends, and Open Hypotheses."""
    claims, _, _ = get_compiled_view(corpus, project=project, commons_corpus=commons_corpus)

    patterns = sorted(
        [c for c in claims if c.pillar == "pattern"],
        key=lambda c: (-c.evidence_count, c.topic),
    )
    dead_ends = [c for c in claims if c.pillar == "agent_hypothesis" and c.stance == "dead_end"]
    open_hypotheses = [c for c in claims if c.pillar == "agent_hypothesis" and c.stance != "dead_end"]

    # Detect perspective clashes: topics where multiple entities hold views or where a view is opposed/concern
    view_claims = [c for c in claims if c.pillar in ("operator_view", "stakeholder_view")]
    groups: list[list[CompiledClaim]] = []
    for vc in view_claims:
        placed = False
        for grp in groups:
            if _topics_match(grp[0].topic, vc.topic, grp[0].statement, vc.statement):
                grp.append(vc)
                placed = True
                break
        if not placed:
            groups.append([vc])

    clashes: list[TopicClash] = []
    for grp in groups:
        entities = {c.entity.lower() for c in grp}
        has_opposition = any(c.stance in ("opposed", "concern", "constraint") for c in grp)
        if len(entities) > 1 or (len(grp) > 1 and has_opposition):
            topic_name = grp[0].topic
            parts = [f"{c.entity} ({c.stance}): {c.statement}" for c in grp]
            clashes.append(
                TopicClash(
                    topic=topic_name,
                    claims=grp,
                    summary=" vs. ".join(parts),
                )
            )

    md_lines: list[str] = [f"# Knowledge Digest: {project or corpus.id}"]
    if patterns:
        md_lines.append("\n### Proven Patterns (The Line Ahead)")
        for p in patterns:
            ev = f" · {p.evidence_count}x" if p.evidence_count > 1 else ""
            md_lines.append(f"- **{p.topic}** [{p.stance}{ev}]: {p.statement}")
    if clashes:
        md_lines.append("\n### Perspective Clashes & Trade-offs")
        for cl in clashes:
            md_lines.append(f"- **{cl.topic}**: {cl.summary}")
    if dead_ends:
        md_lines.append("\n### Active Local Dead Ends (Needs Positive Pattern)")
        for d in dead_ends:
            md_lines.append(f"- **{d.topic}** [{d.entity}]: {d.statement}")
    if open_hypotheses:
        md_lines.append("\n### Open Hypotheses & Risks")
        for h in open_hypotheses:
            md_lines.append(f"- **{h.topic}** [{h.entity} | {h.stance}]: {h.statement}")

    if len(md_lines) == 1:
        md_lines.append("\n_No patterns, clashes, or open hazards recorded._")

    return KnowledgeDigest(
        patterns=patterns,
        clashes=clashes,
        dead_ends=dead_ends,
        open_hypotheses=open_hypotheses,
        markdown="\n".join(md_lines) + "\n",
    )


def get_compiled_view(
    corpus: Corpus,
    *,
    project: str | None = None,
    commons_corpus: Corpus | None = None,
) -> tuple[list[CompiledClaim], list[str], str]:
    """Assemble the merged compiled claims, contributing page relpaths, and rendered section text."""
    collected: list[CompiledClaim] = []
    pages: list[str] = []

    if project:
        proj_rel = _state_relpath_for(project, "project")
        proj_claims = read_compiled_claims(corpus, proj_rel)
        if proj_claims:
            collected.extend(proj_claims)
            pages.append(proj_rel)

    coll_rel = _state_relpath_for(None, "client")
    coll_claims = read_compiled_claims(corpus, coll_rel)
    if coll_claims:
        collected.extend(coll_claims)
        pages.append(coll_rel)

    if corpus.id == "personal":
        comm_rel = _state_relpath_for(None, "general", is_commons=True)
        comm_claims = read_compiled_claims(corpus, comm_rel)
        if comm_claims:
            collected.extend(comm_claims)
            pages.append(comm_rel)
    elif commons_corpus is not None and commons_corpus.id != corpus.id:
        comm_rel = _state_relpath_for(None, "general", is_commons=True)
        comm_claims = read_compiled_claims(commons_corpus, comm_rel)
        if comm_claims:
            # Tag commons claims that don't duplicate existing collection topics
            existing_topics = {(c.pillar, c.topic) for c in collected}
            for cc in comm_claims:
                if (cc.pillar, cc.topic) not in existing_topics:
                    collected.append(cc)
            pages.append(f"{commons_corpus.id}/{comm_rel}")

    if not collected:
        return [], [], ""

    rendered = render_compiled_markdown(collected, title="Compiled Knowledge (Latest View)")
    # Convert top-level '# ' header to '## ' for pack section embedding
    if rendered.startswith("# "):
        rendered = "#" + rendered
    return collected, pages, rendered.strip()
