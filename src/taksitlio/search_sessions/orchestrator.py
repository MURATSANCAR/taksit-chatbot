"""Search session orchestrator — clarification-first routing (ADR-011)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from taksitlio.entity_resolution import EntityCandidate
from taksitlio.llm_routing import (
    LlmJobStatus,
    apply_if_fresh,
    build_llm_input,
    create_job,
    should_route_to_llm,
)
from taksitlio.progressive_results import build_partial_snapshot
from taksitlio.query_clarification import (
    apply_clarification_answer,
    build_clarification,
    select_best_uncertainty,
    should_ask_clarification,
)
from taksitlio.query_fallback import degrade_with_deterministic, evaluate_deadlines
from taksitlio.query_state import (
    QueryNeedState,
    chips_from_state,
    hydrate_parse_from_state,
    merge_parse_into_state,
)
from taksitlio.query_understanding import CatalogHints, detect_gaps, fast_parse
from taksitlio.search_progress import (
    DataOrigin,
    SearchProgressEventType,
    assert_truthful_message,
    display_message_for,
)
from taksitlio.search_sessions.metrics import GLOBAL_SEARCH_METRICS
from taksitlio.search_sessions.repository import (
    InMemorySearchSessionRepository,
    SearchSession,
)
from taksitlio.search_sessions.status import (
    InvalidTransitionError,
    SearchSessionStatus,
    can_transition,
    is_hard_terminal,
)
from taksitlio.semantic_matching.query_intent import (
    OUT_OF_SCOPE_ASSIST_MESSAGE,
    is_off_domain_for_assist,
)

@dataclass
class LogoCandidate:
    entity_id: str
    display_name: str
    logo_cdn_url: Optional[str]
    kind: str  # merchant | brand | institution


@dataclass
class SearchOrchestrator:
    repo: InMemorySearchSessionRepository
    catalog: CatalogHints = field(default_factory=CatalogHints)
    product_pool: list[dict[str, Any]] = field(default_factory=list)
    category_clarify_options: list[dict[str, str]] = field(default_factory=list)
    category_token_map: dict[str, tuple[str, ...]] = field(default_factory=dict)
    states: dict[str, QueryNeedState] = field(default_factory=dict)
    parses: dict[str, Any] = field(default_factory=dict)
    utterances: dict[str, str] = field(default_factory=dict)
    clarifications: dict[str, dict[str, Any]] = field(default_factory=dict)
    llm_jobs: dict[str, Any] = field(default_factory=dict)
    logo_rails: dict[str, dict[str, list[LogoCandidate]]] = field(default_factory=dict)
    started_mono: dict[str, float] = field(default_factory=dict)
    circuit_open: bool = False
    logo_resolver: Any = None

    def _constraints_with_category_tokens(
        self, parse: Any, *, utterance: str = ""
    ) -> dict[str, Any]:
        constraints = parse.to_dict() if hasattr(parse, "to_dict") else dict(parse or {})
        cats = list(constraints.get("positive_categories") or [])
        u_raw = (utterance or "").casefold()
        try:
            from taksitlio.semantic_matching.turkish_normalize import normalize_turkish

            u_norm = normalize_turkish(utterance or "").value or ""
        except Exception:  # noqa: BLE001
            u_norm = u_raw
        enriched: list[dict[str, Any]] = []
        for cat in cats:
            row = dict(cat) if isinstance(cat, dict) else {"display_name": str(cat)}
            rid = str(row.get("resolved_id") or "")
            tokens = list(self.category_token_map.get(rid) or ())
            if tokens and (u_raw or u_norm):
                display_n = str(row.get("display_name") or "").casefold()
                specific: list[str] = []
                for t in tokens:
                    t_raw = str(t or "").strip()
                    if not t_raw:
                        continue
                    t_cf = t_raw.casefold()
                    try:
                        t_n = normalize_turkish(t_raw).value or t_cf
                    except Exception:  # noqa: BLE001
                        t_n = t_cf
                    if t_cf in u_raw or (t_n and t_n in u_norm):
                        # Prefer concrete synonym (buzdolabı) over broad category label.
                        if t_cf != display_n and t_n != normalize_turkish(display_n).value:
                            specific.append(t_raw)
                if specific:
                    tokens = list(dict.fromkeys(specific))
            if tokens:
                row["include_tokens"] = tokens
            else:
                # FREE_TEXT_PRODUCT / unknown catalog id: filter by display name.
                dn = str(row.get("display_name") or "").strip()
                if dn:
                    row["include_tokens"] = [dn]
            enriched.append(row)
        if enriched:
            constraints["positive_categories"] = enriched
        return constraints

    def _logo_url(self, kind: str, entity_id: Optional[str]) -> Optional[str]:
        resolver = self.logo_resolver
        if resolver is None or entity_id is None:
            return None
        if kind == "merchant":
            return resolver.merchant(entity_id)
        if kind == "brand":
            return resolver.brand(entity_id)
        if kind == "institution":
            return resolver.institution(entity_id)
        return None

    def _elapsed_ms(self, session_id: str) -> float:
        started = self.started_mono.get(session_id)
        if started is None:
            return 0.0
        return (time.monotonic() - started) * 1000.0

    def _record_latency(self, session_id: str, name: str, value_ms: float) -> None:
        self.repo.record_metric(session_id, name, float(value_ms))
        GLOBAL_SEARCH_METRICS.observe(name, float(value_ms), session_id=session_id)

    def _emit(
        self,
        session: SearchSession,
        event_type: SearchProgressEventType,
        *,
        data_origin: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        msg = display_message_for(event_type, data_origin=data_origin)
        assert_truthful_message(msg, data_origin=data_origin)
        event = self.repo.append_event(
            session.id,
            query_version=session.active_query_version,
            event_type=event_type.value,
            display_message=msg,
            data_origin=data_origin,
            payload=payload,
        )
        return {
            "event_id": event.id,
            "search_session_id": session.id,
            "query_version": session.active_query_version,
            "type": event.event_type,
            "timestamp": event.created_at.isoformat(),
            "display": {"message": event.display_message, "severity": event.severity},
            "data": event.payload,
            "data_origin": event.data_origin,
        }

    def start(
        self,
        *,
        conversation_id: str,
        message: str,
        client_query_id: Optional[str] = None,
        user_id: Optional[str] = None,
        organization_id: Optional[str] = None,
    ) -> dict[str, Any]:
        session, version = self.repo.create_session(
            conversation_id=conversation_id,
            message=message,
            client_query_id=client_query_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        self.started_mono[session.id] = time.monotonic()
        self.states[session.id] = QueryNeedState()
        self.logo_rails[session.id] = {"merchant": [], "brand": [], "institution": []}
        self._emit(session, SearchProgressEventType.SEARCH_ACCEPTED)
        return self._run_pipeline(session, message)

    def _run_pipeline(self, session: SearchSession, message: str) -> dict[str, Any]:
        self.repo.set_status(session.id, SearchSessionStatus.FAST_PARSING)
        self._emit(session, SearchProgressEventType.FAST_PARSE_STARTED)

        # Deterministic refuse: no general chat / no inventing off-system facts.
        if is_off_domain_for_assist(message):
            return self._refuse_off_domain(session)

        parse = fast_parse(message, catalog=self.catalog)
        version = self.repo.get_version(session.id, session.active_query_version)
        if version:
            version.state_snapshot = parse.to_dict()
            version.confidence = parse.confidence
            version.requires_llm = parse.requires_llm
        self.parses[session.id] = parse
        self.utterances[session.id] = message
        self._emit(
            session,
            SearchProgressEventType.FAST_PARSE_COMPLETED,
            payload={"confidence": parse.confidence, "intent": parse.intent},
        )

        self.repo.set_status(session.id, SearchSessionStatus.ENTITY_RESOLVING)
        self._emit(session, SearchProgressEventType.ENTITY_RESOLUTION_STARTED)
        merchants: list[LogoCandidate] = []
        brands: list[LogoCandidate] = []
        if parse.merchant and parse.merchant.resolved_id:
            merchants.append(
                LogoCandidate(
                    entity_id=parse.merchant.resolved_id,
                    display_name=parse.merchant.display_name,
                    logo_cdn_url=self._logo_url("merchant", parse.merchant.resolved_id),
                    kind="merchant",
                )
            )
        for b in parse.brands:
            if b.resolved_id:
                brands.append(
                    LogoCandidate(
                        entity_id=b.resolved_id,
                        display_name=b.display_name,
                        logo_cdn_url=self._logo_url("brand", b.resolved_id),
                        kind="brand",
                    )
                )
        self.logo_rails[session.id]["merchant"] = merchants
        self.logo_rails[session.id]["brand"] = brands
        if merchants:
            self._emit(
                session,
                SearchProgressEventType.MERCHANT_CANDIDATES_RESOLVED,
                payload={
                    "merchant_ids": [m.entity_id for m in merchants],
                    "merchant_count": len(merchants),
                },
            )
        if brands:
            self._emit(
                session,
                SearchProgressEventType.BRAND_CANDIDATES_RESOLVED,
                payload={"brand_ids": [b.entity_id for b in brands], "brand_count": len(brands)},
            )
        self._emit(session, SearchProgressEventType.ENTITY_RESOLUTION_COMPLETED)

        state = merge_parse_into_state(self.states[session.id], parse.to_dict())
        self.states[session.id] = state
        parse = hydrate_parse_from_state(parse, state)
        self.parses[session.id] = parse
        if version:
            version.state_snapshot = parse.to_dict()
            version.confidence = parse.confidence
            version.requires_llm = parse.requires_llm

        self.repo.set_status(session.id, SearchSessionStatus.GAP_ANALYSIS)
        gaps = detect_gaps(parse, category_candidates=self.category_clarify_options)
        self._emit(
            session,
            SearchProgressEventType.GAP_ANALYSIS_COMPLETED,
            payload=gaps.to_dict(),
        )

        chips = chips_from_state(state)
        policy = self.repo.policy

        if gaps.confidence_band == "HIGH":
            return self._fast_retrieve(session, parse)

        if should_ask_clarification(
            gaps=gaps,
            clarification_count=session.clarification_count,
            max_per_session=policy.max_clarifications_per_session,
            parse=parse,
        ):
            best = select_best_uncertainty(gaps)
            assert best is not None
            # Prefer usage question when use-case unresolved and no category options needed
            catalog_opts = self.category_clarify_options if best.field in {"category", "product_type"} else None
            if best.reason_code == "UNRESOLVED_USE_CASE" and not parse.positive_categories:
                # Ask product type first (highest pool reduction)
                q = build_clarification(
                    best,
                    catalog_options=catalog_opts
                    or [
                        {"id": "laptop", "label": "Laptop"},
                        {"id": "tablet", "label": "Tablet"},
                        {"id": "phone", "label": "Telefon"},
                    ],
                )
            else:
                q = build_clarification(best, catalog_options=catalog_opts)
            self.clarifications[q.clarification_id] = {
                "session_id": session.id,
                "question": q.to_dict(),
                "field": q.field,
                "query_version": session.active_query_version,
            }
            self.repo.clarifications.setdefault(session.id, []).append(q.to_dict())
            self.repo.set_status(session.id, SearchSessionStatus.CLARIFICATION_REQUIRED)
            self._emit(
                session,
                SearchProgressEventType.CLARIFICATION_REQUIRED,
                payload=q.to_dict(),
            )
            self.repo.set_status(session.id, SearchSessionStatus.WAITING_USER_ANSWER)
            return {
                "search_session_id": session.id,
                "query_version": session.active_query_version,
                "status": session.status.value,
                "events_url": f"/api/v1/search-sessions/{session.id}/events",
                "clarification": q.to_dict(),
                "chips": chips,
                "understanding": parse.to_dict(),
                "route": "CLARIFICATION",
            }

        if should_route_to_llm(
            parse,
            gaps,
            clarification_count=session.clarification_count,
            max_clarifications=policy.max_clarifications_per_session,
            circuit_open=self.circuit_open,
        ):
            return self._start_llm_route(session, parse, message, chips)

        # Fallback: wide deterministic
        return self._fast_retrieve(session, parse, degraded=False)

    def _fast_retrieve(
        self,
        session: SearchSession,
        parse: Any,
        *,
        degraded: bool = False,
    ) -> dict[str, Any]:
        # LLM route may already be PARTIAL_RESULTS_READY / LLM_RUNNING — do not
        # force FAST_RETRIEVAL (illegal transition). Jump ahead when allowed.
        if can_transition(session.status, SearchSessionStatus.FAST_RETRIEVAL):
            self.repo.set_status(session.id, SearchSessionStatus.FAST_RETRIEVAL)
        self._emit(session, SearchProgressEventType.PRODUCT_POOL_SEARCH_STARTED)
        constraints = self._constraints_with_category_tokens(
            parse, utterance=self.utterances.get(session.id, "")
        )
        state = self.states.get(session.id)
        ranking_mode = getattr(parse, "ranking_mode", None) or (
            (state.payment_preferences or {}).get("ranking_mode") if state else None
        )
        if ranking_mode:
            constraints["ranking_mode"] = str(ranking_mode)
        partial = build_partial_snapshot(
            query_version=session.active_query_version,
            products=self.product_pool,
            constraints=constraints,
        )
        self.repo.partial_snapshots.setdefault(session.id, []).append(partial.to_dict())
        if partial.products:
            if can_transition(session.status, SearchSessionStatus.PARTIAL_RESULTS_READY):
                self.repo.set_status(session.id, SearchSessionStatus.PARTIAL_RESULTS_READY)
            self._emit(
                session,
                SearchProgressEventType.PARTIAL_RESULTS_READY,
                payload={"count": len(partial.products), "label": partial.label},
            )
            self._record_latency(
                session.id, "partial_result_latency_ms", self._elapsed_ms(session.id)
            )

        # Finance from local snapshot by default (truthful)
        origin = DataOrigin.LOCAL_VERIFIED_SNAPSHOT.value
        self._emit(
            session,
            SearchProgressEventType.FINANCE_SEARCH_STARTED,
            data_origin=origin,
        )
        institution_ids = [
            i.get("institution_id")
            for i in (parse.preferred_institutions or [])
            if i.get("institution_id")
        ]
        if institution_ids:
            self.logo_rails[session.id]["institution"] = [
                LogoCandidate(
                    entity_id=str(i),
                    display_name=str(
                        next(
                            (
                                x.get("display_name")
                                for x in (parse.preferred_institutions or [])
                                if x.get("institution_id") == i
                            ),
                            i,
                        )
                    ),
                    logo_cdn_url=self._logo_url("institution", str(i)),
                    kind="institution",
                )
                for i in institution_ids
            ]
            self._emit(
                session,
                SearchProgressEventType.FINANCIAL_INSTITUTION_CANDIDATES_FOUND,
                data_origin=origin,
                payload={
                    "institution_count": len(institution_ids),
                    "institution_ids": institution_ids,
                },
            )

        if can_transition(session.status, SearchSessionStatus.RANKING):
            self.repo.set_status(session.id, SearchSessionStatus.RANKING)
        self._emit(session, SearchProgressEventType.RANKING_STARTED)
        final_status = (
            SearchSessionStatus.COMPLETED_DEGRADED if degraded else SearchSessionStatus.COMPLETED
        )
        if can_transition(session.status, final_status):
            self.repo.set_status(session.id, final_status)
        evt = (
            SearchProgressEventType.SEARCH_COMPLETED_DEGRADED
            if degraded
            else SearchProgressEventType.SEARCH_COMPLETED
        )
        self._emit(session, SearchProgressEventType.FINAL_RESULTS_READY, payload={"count": len(partial.products)})
        self._emit(session, evt)
        self.repo.record_metric(session.id, "fast_path_completion", 1.0 if not degraded else 0.0)
        self._record_latency(session.id, "search_complete_ms", self._elapsed_ms(session.id))
        if not degraded:
            self._record_latency(session.id, "fast_path_completion_ms", self._elapsed_ms(session.id))
        chips = chips_from_state(self.states[session.id])
        payload: dict[str, Any] = {
            "search_session_id": session.id,
            "query_version": session.active_query_version,
            "status": session.status.value,
            "events_url": f"/api/v1/search-sessions/{session.id}/events",
            "route": "FAST" if not degraded else "DEGRADED",
            "chips": chips,
            "understanding": parse.to_dict(),
            "partial_results": partial.to_dict(),
            "results": partial.to_dict(),
            "logos": self._logos_public(session.id),
        }
        if not partial.products:
            payload["reply"] = (
                "Bu kriterlere uygun ürün bulamadım. Ürün türünü veya bütçeni tekrar yazabilirsin."
            )
        elif ranking_mode:
            payload["reply"] = f"Sonuçları «{partial.label}» olarak sıraladım."
        return payload

    def _start_llm_route(
        self,
        session: SearchSession,
        parse: Any,
        message: str,
        chips: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.repo.set_status(session.id, SearchSessionStatus.LLM_QUEUED)
        job = create_job(
            search_session_id=session.id,
            query_version=session.active_query_version,
            conversation_state_version=self.states[session.id].state_version,
            input_payload=build_llm_input(
                user_message=message,
                parse=parse,
                conversation_state=self.states[session.id].to_dict(),
            ),
        )
        self.llm_jobs[job.id] = job
        self.repo.llm_jobs[job.id] = {
            "id": job.id,
            "search_session_id": session.id,
            "query_version": job.query_version,
            "status": job.status.value,
            "platform_role": "UNDERSTANDING_SERVICE",
        }
        self._emit(session, SearchProgressEventType.LLM_JOB_QUEUED, payload={"job_id": job.id})
        queue_wait = self._elapsed_ms(session.id)
        self._record_latency(session.id, "queue_wait_ms", queue_wait)
        self.repo.set_status(session.id, SearchSessionStatus.LLM_RUNNING)
        job.status = LlmJobStatus.RUNNING
        self._emit(session, SearchProgressEventType.LLM_JOB_STARTED, payload={"job_id": job.id})

        # Progressive retrieval in parallel (do not wait for LLM)
        constraints = self._constraints_with_category_tokens(
            parse, utterance=message or self.utterances.get(session.id, "")
        )
        self._emit(session, SearchProgressEventType.PRODUCT_POOL_SEARCH_STARTED)
        partial = build_partial_snapshot(
            query_version=session.active_query_version,
            products=self.product_pool,
            constraints=constraints,
        )
        self.repo.partial_snapshots.setdefault(session.id, []).append(partial.to_dict())
        if partial.products:
            self.repo.set_status(session.id, SearchSessionStatus.PARTIAL_RESULTS_READY)
            self._emit(
                session,
                SearchProgressEventType.PARTIAL_RESULTS_READY,
                payload={"count": len(partial.products), "label": partial.label},
            )
            self._record_latency(
                session.id, "partial_result_latency_ms", self._elapsed_ms(session.id)
            )
        self.repo.record_metric(session.id, "llm_route", 1.0)
        GLOBAL_SEARCH_METRICS.incr("llm_route")
        return {
            "search_session_id": session.id,
            "query_version": session.active_query_version,
            "status": session.status.value,
            "events_url": f"/api/v1/search-sessions/{session.id}/events",
            "route": "LLM",
            "llm_job_id": job.id,
            "platform_role": "UNDERSTANDING_SERVICE",
            "chips": chips,
            "understanding": parse.to_dict(),
            "partial_results": partial.to_dict(),
            "logos": self._logos_public(session.id),
            "controls": {
                "show_current_results": True,
                "edit_filters": True,
                "cancel": True,
            },
        }

    def answer_clarification(
        self,
        session_id: str,
        *,
        clarification_id: str,
        selected_option_ids: Sequence[str],
        free_text: Optional[str] = None,
        expected_query_version: int,
    ) -> dict[str, Any]:
        session = self.repo.get(session_id)
        if session is None:
            raise KeyError("search_session_not_found")
        if session.active_query_version != expected_query_version:
            raise ValueError("query_version_mismatch")
        meta = self.clarifications.get(clarification_id)
        if meta is None or meta["session_id"] != session_id:
            raise KeyError("clarification_not_found")

        parse = self.parses[session_id]
        option_labels = {
            o["option_id"]: o["label"] for o in meta["question"].get("options") or []
        }
        parse = apply_clarification_answer(
            parse,
            field=meta["field"],
            selected_option_ids=selected_option_ids,
            free_text=free_text,
            option_labels=option_labels,
        )
        self.parses[session_id] = parse
        session.clarification_count += 1
        answer_text = free_text or ",".join(selected_option_ids)
        self.repo.append_query_version(session_id, raw_user_text=answer_text)
        version = self.repo.get_version(session_id, session.active_query_version)
        if version:
            version.state_snapshot = parse.to_dict()
            version.confidence = parse.confidence
        self._emit(
            session,
            SearchProgressEventType.CLARIFICATION_ANSWERED,
            payload={
                "clarification_id": clarification_id,
                "selected_option_ids": list(selected_option_ids),
            },
        )
        self.repo.set_status(session_id, SearchSessionStatus.FAST_PARSING)
        # Re-run gap analysis on updated parse without re-parsing raw text
        gaps = detect_gaps(parse, category_candidates=self.category_clarify_options)
        merge_parse_into_state(self.states[session_id], parse.to_dict())
        chips = chips_from_state(self.states[session_id])
        policy = self.repo.policy

        if gaps.confidence_band == "HIGH" or parse.confidence >= 0.90:
            self.repo.record_metric(session_id, "llm_avoided_by_clarification", 1.0)
            return self._fast_retrieve(session, parse)

        if should_ask_clarification(
            gaps=gaps,
            clarification_count=session.clarification_count,
            max_per_session=policy.max_clarifications_per_session,
        ):
            best = select_best_uncertainty(gaps, already_asked=[meta["field"]])
            if best is not None:
                q = build_clarification(best, catalog_options=self.category_clarify_options)
                self.clarifications[q.clarification_id] = {
                    "session_id": session.id,
                    "question": q.to_dict(),
                    "field": q.field,
                    "query_version": session.active_query_version,
                }
                self.repo.set_status(session.id, SearchSessionStatus.CLARIFICATION_REQUIRED)
                self._emit(session, SearchProgressEventType.CLARIFICATION_REQUIRED, payload=q.to_dict())
                self.repo.set_status(session.id, SearchSessionStatus.WAITING_USER_ANSWER)
                return {
                    "search_session_id": session.id,
                    "query_version": session.active_query_version,
                    "status": session.status.value,
                    "clarification": q.to_dict(),
                    "chips": chips,
                    "route": "CLARIFICATION",
                }

        if should_route_to_llm(
            parse,
            gaps,
            clarification_count=session.clarification_count,
            max_clarifications=policy.max_clarifications_per_session,
            circuit_open=self.circuit_open,
        ):
            return self._start_llm_route(session, parse, answer_text, chips)

        return self._fast_retrieve(session, parse)

    def supersede_with_message(self, session_id: str, message: str) -> dict[str, Any]:
        """User correction / follow-up refinement on the same search session."""

        session = self.repo.get(session_id)
        if session is None:
            raise KeyError("search_session_not_found")
        if is_hard_terminal(session.status):
            raise InvalidTransitionError(
                f"Cannot supersede hard-terminal session status={session.status.value}"
            )
        # Cancel old LLM jobs
        for job in self.llm_jobs.values():
            if job.search_session_id == session_id and job.status in {
                LlmJobStatus.QUEUED,
                LlmJobStatus.RUNNING,
            }:
                job.status = LlmJobStatus.CANCEL_REQUESTED
        self.repo.append_query_version(session_id, raw_user_text=message)
        self.repo.append_message(session_id, session.active_query_version, role="USER", content=message)
        self.repo.set_status(session_id, SearchSessionStatus.FAST_PARSING)
        return self._run_pipeline(session, message)

    def complete_llm_job(
        self,
        job_id: str,
        patch: dict[str, Any],
        *,
        active_state_version: Optional[int] = None,
    ) -> dict[str, Any]:
        job = self.llm_jobs.get(job_id)
        if job is None:
            raise KeyError("job_not_found")
        session = self.repo.get(job.search_session_id)
        if session is None:
            raise KeyError("search_session_not_found")
        state_ver = (
            active_state_version
            if active_state_version is not None
            else self.states[session.id].state_version
        )
        status, validated = apply_if_fresh(
            job,
            active_query_version=session.active_query_version,
            active_state_version=state_ver,
            patch=patch,
        )
        if status == LlmJobStatus.STALE_RESULT:
            self.repo.record_metric(session.id, "stale_llm_result", 1.0)
            return {"status": "STALE_RESULT", "applied": False}
        if status == LlmJobStatus.CANCELLED:
            return {"status": "CANCELLED", "applied": False}
        self._emit(session, SearchProgressEventType.LLM_JOB_COMPLETED, payload={"job_id": job_id})
        # Merge inferred preferences carefully (not as required)
        parse = self.parses[session.id]
        validated_patch = validated or {}
        intent = str(validated_patch.get("intent") or "").upper()
        safe_to_retrieve = bool(validated_patch.get("safe_to_retrieve", True))
        if (not safe_to_retrieve) or intent in {
            "OUT_OF_SCOPE",
            "GENERAL_CHAT",
            "OTHER",
        }:
            return self._refuse_off_domain(session)

        # ADR-012 NEGATIVE_CONSTRAINT_GATE: LLM cannot reintroduce locked negatives
        from taksitlio.recommendation_safety import (
            ConstraintSource,
            NegativeConstraintLock,
        )

        lock = NegativeConstraintLock()
        for neg in parse.negative_categories:
            lock.lock(neg.display_name, source=ConstraintSource.USER_EXPLICIT)
        state = self.states.get(session.id)
        if state is not None:
            for excluded in getattr(state, "excluded_categories", ()) or ():
                name = (
                    excluded.display_name
                    if hasattr(excluded, "display_name")
                    else str(excluded)
                )
                lock.lock(str(name), source=ConstraintSource.USER_EXPLICIT)

        proposed = [
            str(pref.get("concept") or "")
            for pref in validated_patch.get("inferred_preferences") or []
            if pref.get("concept")
        ]
        blocked = set(
            lock.reject_llm_reintroduction(
                proposed_positive=proposed,
                proposed_source=ConstraintSource.LLM_INFERENCE,
            )
        )
        for pref in validated_patch.get("inferred_preferences") or []:
            concept = pref.get("concept")
            if not concept:
                continue
            if str(concept).casefold() in blocked:
                continue
            if concept not in parse.preferences:
                parse.preferences.append(str(concept))
        return self._fast_retrieve(session, parse)

    def _refuse_off_domain(self, session: SearchSession) -> dict[str, Any]:
        """Stop retrieval and return a fixed refuse payload (no invented facts)."""

        if can_transition(session.status, SearchSessionStatus.FAILED):
            self.repo.set_status(session.id, SearchSessionStatus.FAILED)
        elif can_transition(session.status, SearchSessionStatus.COMPLETED):
            self.repo.set_status(session.id, SearchSessionStatus.COMPLETED)
        self._emit(
            session,
            SearchProgressEventType.SEARCH_FAILED,
            payload={"reason": "OUT_OF_SCOPE"},
        )
        empty = {"products": [], "label": ""}
        return {
            "search_session_id": session.id,
            "query_version": session.active_query_version,
            "status": session.status.value,
            "route": "OUT_OF_SCOPE",
            "reply": OUT_OF_SCOPE_ASSIST_MESSAGE,
            "chips": [],
            "results": empty,
            "partial_results": empty,
            "events_url": f"/v1/search-sessions/{session.id}/events",
        }

    def complete_with_current_results(self, session_id: str) -> dict[str, Any]:
        session = self.repo.get(session_id)
        if session is None:
            raise KeyError("search_session_not_found")
        for job in self.llm_jobs.values():
            if job.search_session_id == session_id and job.status in {
                LlmJobStatus.QUEUED,
                LlmJobStatus.RUNNING,
            }:
                job.status = LlmJobStatus.CANCEL_REQUESTED
        parse = self.parses[session_id]
        return self._fast_retrieve(session, parse, degraded=True)

    def cancel(self, session_id: str) -> dict[str, Any]:
        session = self.repo.get(session_id)
        if session is None:
            raise KeyError("search_session_not_found")
        for job in self.llm_jobs.values():
            if job.search_session_id == session_id:
                job.status = LlmJobStatus.CANCEL_REQUESTED
        self.repo.set_status(session_id, SearchSessionStatus.CANCELLED)
        self._emit(session, SearchProgressEventType.SEARCH_CANCELLED)
        return {"search_session_id": session_id, "status": session.status.value}

    def timeout_if_needed(self, session_id: str) -> Optional[dict[str, Any]]:
        session = self.repo.get(session_id)
        if session is None:
            return None
        started = self.started_mono.get(session_id, time.monotonic())
        elapsed = (time.monotonic() - started) * 1000.0
        partials = self.repo.partial_snapshots.get(session_id) or []
        llm_running = any(
            j.search_session_id == session_id and j.status == LlmJobStatus.RUNNING
            for j in self.llm_jobs.values()
        )
        decision = evaluate_deadlines(
            elapsed_ms=elapsed,
            policy=self.repo.policy,
            has_partial_results=bool(partials),
            llm_still_running=llm_running,
        )
        if decision.action == "HARD_TIMEOUT":
            self.repo.set_status(session_id, SearchSessionStatus.TIMED_OUT)
            self._emit(session, SearchProgressEventType.LLM_JOB_TIMED_OUT)
            parse = self.parses[session_id]
            result = self._fast_retrieve(session, parse, degraded=True)
            self.repo.record_metric(session_id, "llm_timeout", 1.0)
            self.repo.record_metric(session_id, "degraded_completion", 1.0)
            return result
        return {"decision": decision.action, "message": decision.message}

    def update_constraint(
        self,
        session_id: str,
        *,
        action: str,
        constraint_id: str,
        value: Any,
        expected_query_version: int,
    ) -> dict[str, Any]:
        session = self.repo.get(session_id)
        if session is None:
            raise KeyError("search_session_not_found")
        if session.active_query_version != expected_query_version:
            raise ValueError("query_version_mismatch")
        self.repo.append_query_version(session_id, raw_user_text=f"constraint:{action}:{constraint_id}")
        state = self.states[session_id]
        if action == "UPDATE" and constraint_id.startswith("budget"):
            state.budget = {"maximum": value, "currency": "TRY", "type": "RANGE"}
            state.bump()
        elif action == "DELETE":
            state.cancelled_constraints.append({"id": constraint_id, "value": value})
            state.bump()
        parse = self.parses[session_id]
        if state.budget:
            parse.budget = dict(state.budget)
        return self._fast_retrieve(session, parse)

    def list_event_payloads(self, session_id: str, *, after_id: Optional[str] = None) -> list[dict[str, Any]]:
        events = self.repo.list_events(session_id, after_id=after_id)
        return [
            {
                "event_id": e.id,
                "search_session_id": e.search_session_id,
                "query_version": e.query_version,
                "type": e.event_type,
                "timestamp": e.created_at.isoformat(),
                "display": {"message": e.display_message, "severity": e.severity},
                "data": e.payload,
                "data_origin": e.data_origin,
            }
            for e in events
        ]

    def _logos_public(self, session_id: str) -> dict[str, list[dict[str, Any]]]:
        rail = self.logo_rails.get(session_id) or {}
        out: dict[str, list[dict[str, Any]]] = {}
        for kind, items in rail.items():
            out[kind] = [
                {
                    "entity_id": i.entity_id,
                    "display_name": i.display_name,
                    "logo_cdn_url": i.logo_cdn_url,
                    "kind": i.kind,
                }
                for i in items
            ]
        return out


def build_empty_orchestrator() -> SearchOrchestrator:
    """Production-safe orchestrator with no synthetic catalog or products."""

    return SearchOrchestrator(repo=InMemorySearchSessionRepository())


def build_demo_orchestrator() -> SearchOrchestrator:
    """Test-only synthetic catalog (do not use in production containers).

    Entity aliases (including typo forms) load from evaluation fixture JSON —
    not hardcoded query→entity maps in this module (ADR-010 §32 / ADR-013).
    """

    from taksitlio.evaluation.query_golden.catalog import build_query_golden_test_catalog
    from taksitlio.progressive_results.category_match import CATEGORY_FAMILIES

    catalog = build_query_golden_test_catalog()
    products = [
        {
            "product_id": "p-laptop-1",
            "display_name": "16GB Laptop A",
            "merchant_display_name": "Teknosa",
            "merchant_id": "merchant-teknosa",
            "price": 38999,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.9,
        },
        {
            "product_id": "p-laptop-2",
            "display_name": "Lightweight Laptop B",
            "merchant_display_name": "Teknosa",
            "merchant_id": "merchant-teknosa",
            "price": 34999,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.85,
        },
        {
            "product_id": "p-tablet-1",
            "display_name": "Tablet C",
            "merchant_display_name": "Teknosa",
            "price": 18000,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.7,
        },
        {
            "product_id": "p-fridge-1",
            "display_name": "No-Frost Buzdolabı",
            "merchant_display_name": "Teknosa",
            "merchant_id": "merchant-teknosa",
            "price": 27999,
            "stock_status": "AVAILABLE",
            "price_freshness": "FRESH",
            "has_primary_image": True,
            "query_relevance": 0.8,
        },
    ]
    token_map: dict[str, tuple[str, ...]] = {}
    for cand in catalog.categories:
        legacy = CATEGORY_FAMILIES.get(cand.entity_id, {}).get("include") or ()
        token_map[cand.entity_id] = tuple(
            dict.fromkeys([*(a.casefold() for a in cand.aliases), cand.display_name.casefold(), *legacy])
        )
    return SearchOrchestrator(
        repo=InMemorySearchSessionRepository(),
        catalog=catalog,
        product_pool=products,
        category_token_map=token_map,
        category_clarify_options=[
            {"id": "category-phone", "label": "Telefon"},
            {"id": "category-tablet", "label": "Tablet"},
            {"id": "category-laptop", "label": "Laptop"},
            {"id": "HOME_APPLIANCE", "label": "Beyaz Eşya"},
            {"id": "FOOTWEAR", "label": "Ayakkabı"},
        ],
    )
