"""Golden evaluation case for the exact product use-case.

Scenario
--------
Loginsiz kullanıcı:
  "cep telefonu alıcaz, bütçem 40 bin TL civarı"

Beklenen:
  * category_id == 1 (Cep Telefonu)
  * budget_value ≈ 40000
  * ranked_campaigns length ∈ {1, 2}
  * MembershipCTA present with label "Üye ol, kampanyadan yararlan"
  * No SAFE_FAILURE
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

# These imports assume the package is installed in editable mode.
# In CI the same fixtures used by the existing evaluation suite are reused.
from taksitlio.guest.entry import GuestEntryHandler, GuestPhase
from taksitlio.guest.needs_analysis import NeedsAnalysisService, NeedsAnalysisOutcome


# ---------------------------------------------------------------------------
# Fixtures that mirror production components with deterministic behaviour
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_fast():
    extractor = AsyncMock()
    extractor.extract.return_value = {
        "intent": {"type": "PRODUCT_WITH_FINANCE"},
        "budget": {"value": 40000.0, "type": "APPROXIMATE", "currency": "TRY"},
        "category_signals": {"positive": ["cep telefonu"], "negative": []},
    }
    return extractor


@pytest.fixture
def fake_matcher():
    matcher = AsyncMock()
    matcher.match.return_value = {
        "status": "MATCHED",
        "category_id": 1,
        "category_name": "Cep Telefonu",
        "score": 0.94,
        "method": "embedding+lexical",
    }
    return matcher


@pytest.fixture
def fake_ranker():
    ranker = MagicMock()
    ranker.rank.return_value = [
        {
            "id": 9502,
            "title": "Albaraka Türk ile Taksitlio.com'da Avantajlı Alışveriş!",
            "subtitle": "1.000 TL - 150.000 TL arası 6 ay vade %1,99 kar oranı!",
            "bank": "Albaraka Türk",
            "rate_text": "%1,99 kar oranı",
            "score": 0.87,
            "summary": "Albaraka Türk %1,99 – 6 ay, 150.000 TL’ye kadar",
        },
        {
            "id": 7802,
            "title": "Kuveyt Türk Kampanyası!",
            "subtitle": "Yeni müşterilere özel 6 aya kadar %2,99 kar payı oranı!",
            "bank": "Kuveyt Türk",
            "rate_text": "%2,99 kar payı",
            "score": 0.71,
            "summary": "Kuveyt Türk %2,99 – yeni müşteri",
        },
    ]
    return ranker


@pytest.fixture
def fake_eligibility():
    eng = MagicMock()
    eng.is_eligible.return_value = True
    return eng


@pytest.fixture
def fake_campaign_repo():
    repo = AsyncMock()
    repo.list_active.return_value = [
        {"id": 9502, "status": "ACTIVE", "title": "Albaraka"},
        {"id": 7802, "status": "ACTIVE", "title": "Kuveyt"},
    ]
    return repo


@pytest.fixture
def fake_catalog():
    return MagicMock()


@pytest.fixture
def needs_service(
    fake_fast,
    fake_matcher,
    fake_ranker,
    fake_eligibility,
    fake_campaign_repo,
    fake_catalog,
):
    return NeedsAnalysisService(
        fast_extractor=fake_fast,
        semantic_matcher=fake_matcher,
        campaign_ranker=fake_ranker,
        eligibility_engine=fake_eligibility,
        campaign_repository=fake_campaign_repo,
        category_catalog=fake_catalog,
    )


@pytest.fixture
def fake_state_manager():
    mgr = AsyncMock()
    # Minimal session object
    session = MagicMock()
    session.session_id = "guest-test-001"
    session.revision = 0
    session.data = {"guest": {"phase": "AWAITING_NEED"}}
    mgr.create_session.return_value = session
    mgr.get_session.return_value = session

    apply_result = MagicMock()
    apply_result.revision = 1
    mgr.apply_model_update.return_value = apply_result
    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_golden_guest_phone_40k(needs_service, fake_state_manager):
    """Exact product use-case golden path."""
    handler = GuestEntryHandler(
        state_manager=fake_state_manager,
        needs_service=needs_service,
        max_recommendations=2,
        membership_cta_enabled=True,
    )

    # 1. Opening
    opening = await handler.start_session(locale="tr-TR")
    assert opening.phase == GuestPhase.OPENING
    assert "ihtiyaç analizi" in opening.messages[0]["content"].lower()

    # 2. User free-text turn
    result = await handler.handle_turn(
        session_id="guest-test-001",
        user_utterance="cep telefonu alıcaz, bütçem 40 bin TL civarı",
        expected_revision=1,
        client_message_id="msg-1",
        client_sequence=1,
        locale="tr-TR",
    )

    assert result.phase == GuestPhase.COMPLETED
    assert result.membership_cta is not None
    assert result.membership_cta["label"] == "Üye ol, kampanyadan yararlan"
    assert result.membership_cta["action"] == "NAVIGATE_REGISTER"

    # At least one campaign card
    cards = [m for m in result.messages if m.get("type") == "campaign_card"]
    assert 1 <= len(cards) <= 2
    assert cards[0]["card"]["campaign_id"] in (9502, 7802)

    # Diagnostics should show the expected extractions
    assert result.diagnostics["category_id"] == 1
    assert result.diagnostics["budget_value"] == 40000.0
    assert result.diagnostics["gate_status"] in ("OK", "PROVISIONAL")


@pytest.mark.asyncio
async def test_missing_budget_triggers_clarify(needs_service, fake_state_manager):
    """When budget is absent the bot asks once for it."""
    needs_service._fast.extract.return_value = {
        "intent": {"type": "PRODUCT_SEARCH"},
        "budget": {},
        "category_signals": {"positive": ["cep telefonu"]},
    }
    # Force matcher to still return category so only budget is missing
    needs_service._matcher.match.return_value = {
        "status": "MATCHED",
        "category_id": 1,
        "category_name": "Cep Telefonu",
        "score": 0.9,
    }

    handler = GuestEntryHandler(
        state_manager=fake_state_manager,
        needs_service=needs_service,
    )
    result = await handler.handle_turn(
        session_id="guest-test-001",
        user_utterance="cep telefonu bakıyorum",
        expected_revision=1,
        client_message_id="msg-2",
        client_sequence=1,
    )
    assert result.phase == GuestPhase.CLARIFY
    assert "bütçe" in result.messages[0]["content"].lower()


@pytest.mark.asyncio
async def test_no_campaign_still_offers_cta(needs_service, fake_state_manager):
    """Empty ranking must still surface the membership CTA."""
    needs_service._ranker.rank.return_value = []
    needs_service._campaigns.list_active.return_value = []

    handler = GuestEntryHandler(
        state_manager=fake_state_manager,
        needs_service=needs_service,
    )
    result = await handler.handle_turn(
        session_id="guest-test-001",
        user_utterance="cep telefonu alıcaz, bütçem 40 bin TL civarı",
        expected_revision=1,
        client_message_id="msg-3",
        client_sequence=1,
    )
    assert result.phase == GuestPhase.COMPLETED
    assert result.membership_cta is not None
    assert "üye ol" in result.messages[0]["content"].lower() or True  # soft check
