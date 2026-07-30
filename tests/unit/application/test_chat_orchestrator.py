"""ChatOrchestrator unit tests (ADR-007 §D).

Uses the real ConversationStateManager (in-memory repo) and the real
SemanticCategoryMatcher wired to the v2 fixture catalog. The FAST
extractor is either DeterministicFastExtractor (rule-based) or
StubRemoteFastExtractor (raises FastDeploymentUnavailable).
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from taksitlio.application import (
    ChatOrchestrator,
    OrchestratorInputs,
    OrchestratorRuntimeError,
)
from taksitlio.conversation_state import (
    ConversationStateManager,
    InMemoryConversationStateRepository,
)
from taksitlio.evaluation.fixture_catalog import (
    build_fixture_catalog,
    dispose_fixture_catalog,
)
from taksitlio.semantic_matching import (
    InMemoryCategoryMatchCache,
    LexicalFallbackGateway,
    SemanticCategoryMatcher,
    SemanticMatchPolicy,
    StaticSemanticMatchPolicyProvider,
)
from taksitlio.understanding.fast import (
    DeterministicFastExtractor,
    StubRemoteFastExtractor,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_V2 = (
    REPO_ROOT / "evaluation" / "fixtures" / "catalogs" / "category-fixture.v2.json"
)


async def _build_orchestrator(fast=None):
    handle = await build_fixture_catalog(fixture_path=FIXTURE_V2)
    manager = ConversationStateManager(InMemoryConversationStateRepository())
    matcher = SemanticCategoryMatcher(
        snapshot_provider=handle.service,
        embedding_repository=handle.embedding_repository,
        query_gateway=LexicalFallbackGateway(dim=64),
        policy_provider=StaticSemanticMatchPolicyProvider(SemanticMatchPolicy()),
        cache=InMemoryCategoryMatchCache(),
    )
    orchestrator = ChatOrchestrator(
        conversation_state=manager,
        fast_understanding=fast or DeterministicFastExtractor(),
        matcher=matcher,
        catalog_id=handle.catalog_id,
        embedding_profile_id=handle.embedding_profile_id,
        catalog_revision=handle.revision,
    )
    return handle, manager, orchestrator


async def _make_session(manager: ConversationStateManager):
    state = await manager.create_session()
    return state.session_id


async def test_happy_path_writes_two_cas_revisions() -> None:
    handle, manager, orchestrator = await _build_orchestrator()
    try:
        session_id = await _make_session(manager)
        outcome = await orchestrator.handle_turn(
            OrchestratorInputs(
                session_id=session_id,
                utterance="telefon istemiyorum tablet bakıyorum",
                client_message_id="msg-1",
                idempotency_key="idem-1",
            )
        )
        assert outcome.fast is not None
        assert outcome.match_result is not None
        assert outcome.matcher_skipped is False
        # Two CAS writes (need + category) push revision to 2.
        state = await manager.get_session(session_id)
        assert state.revision >= 2
        assert outcome.reevaluation_count == 0
    finally:
        await dispose_fixture_catalog(handle)


async def test_fast_deployment_unavailable_skips_matcher() -> None:
    handle, manager, orchestrator = await _build_orchestrator(
        fast=StubRemoteFastExtractor()
    )
    try:
        session_id = await _make_session(manager)
        outcome = await orchestrator.handle_turn(
            OrchestratorInputs(
                session_id=session_id,
                utterance="telefon istemiyorum tablet bakıyorum",
                client_message_id="msg-1",
                idempotency_key="idem-1",
            )
        )
        assert outcome.fast is None
        assert outcome.match_result is None
        assert outcome.matcher_skipped is True
        assert outcome.fast_failure_reason == "FAST_DEPLOYMENT_UNAVAILABLE"
        state = await manager.get_session(session_id)
        assert state.revision == 0, (
            "no CAS must be attempted when FAST failed — state must stay unchanged"
        )
    finally:
        await dispose_fixture_catalog(handle)


async def test_version_conflict_reevaluated_once(monkeypatch) -> None:
    handle, manager, orchestrator = await _build_orchestrator()
    try:
        session_id = await _make_session(manager)

        original_apply = manager.apply_model_update
        conflict_count = {"n": 0}
        from taksitlio.conversation_state.errors import ConversationVersionConflict

        async def flaky_apply(*args, **kwargs):
            if conflict_count["n"] == 0:
                conflict_count["n"] += 1
                raise ConversationVersionConflict(str(session_id))
            return await original_apply(*args, **kwargs)

        monkeypatch.setattr(manager, "apply_model_update", flaky_apply)
        outcome = await orchestrator.handle_turn(
            OrchestratorInputs(
                session_id=session_id,
                utterance="tablet bakıyorum",
                client_message_id="msg-2",
                idempotency_key="idem-2",
            )
        )
        assert outcome.reevaluation_count == 1
        assert outcome.match_result is not None
    finally:
        await dispose_fixture_catalog(handle)


async def test_two_conflicts_raise_unrecoverable(monkeypatch) -> None:
    handle, manager, orchestrator = await _build_orchestrator()
    try:
        session_id = await _make_session(manager)
        from taksitlio.conversation_state.errors import ConversationVersionConflict

        async def always_conflict(*args, **kwargs):
            raise ConversationVersionConflict(str(session_id))

        monkeypatch.setattr(manager, "apply_model_update", always_conflict)
        with pytest.raises(OrchestratorRuntimeError) as excinfo:
            await orchestrator.handle_turn(
                OrchestratorInputs(
                    session_id=session_id,
                    utterance="tablet bakıyorum",
                    client_message_id="msg-3",
                    idempotency_key="idem-3",
                )
            )
        assert excinfo.value.reason_code == "VERSION_CONFLICT_UNRECOVERABLE"
    finally:
        await dispose_fixture_catalog(handle)
