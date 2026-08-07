"""Tier-2 small-LLM category fallback + identity guard + starter chips.

Uses a fake async LLM (no network) to prove:
  * deterministic hit → LLM is NOT called (happy path stays sub-ms)
  * deterministic miss + valid LLM code → category resolved via fallback
  * hallucinated / off-catalog code → ignored (fail-safe)
  * LLM raising / timing out → graceful clarify (never blocks)
  * identity questions → fixed Taksitlio message, never "AI/model"
  * starter chips exposed on opening/clarify screens
"""

from __future__ import annotations

import uuid

import pytest

from taksitlio.guest.campaign_only_pipeline import CampaignOnlyGuestPipeline, STARTER_CHIPS
from taksitlio.guest.llm_fallback import (
    GuestCategoryResolverLLM,
    IDENTITY_TEXT,
    SEARCHING_TEXT,
)


# --- in-memory CAS state + repo ----------------------------------------------
class _State:
    def __init__(self, sid, rev=1):
        self.session_id = sid
        self.revision = rev
        self.resolved_context = {}


class _CAS:
    def __init__(self, rev):
        self.revision = rev


class _MemState:
    def __init__(self):
        self.store = {}

    async def create_session(self, *, locale, actor, session_id=None, idempotency_key=None):
        sid = session_id or uuid.uuid4()
        self.store[str(sid)] = _State(sid, 1)
        return self.store[str(sid)]

    async def get_session(self, sid):
        if str(sid) not in self.store:
            raise KeyError(sid)
        return self.store[str(sid)]

    async def apply_model_update(self, sid, *, expected_revision, patch, **kw):
        self.store[str(sid)].resolved_context["guest"] = patch["value"]
        self.store[str(sid)].revision = expected_revision + 1
        return _CAS(expected_revision + 1)


_CAMPS = [
    {"id": 1, "title": "Albaraka", "brand": "Albaraka Türk", "category_code": "1",
     "status": "ACTIVE", "min_budget": 1000, "max_budget": 150000,
     "installment_count": 6, "attributes": {"rate_text": "%1,99", "scope": "GENERAL"}},
]


class _Repo:
    async def list_by_category_codes(self, codes, *, limit=50):
        return list(_CAMPS)  # GENERAL → any category

    async def list_active(self):
        return list(_CAMPS)


class _FakeLLM:
    """Duck-typed async resolver."""

    def __init__(self, code=None, raises=False):
        from taksitlio.guest.need_extraction import CATALOG_BY_CODE, CategoryHit

        self._code = code
        self._raises = raises
        self.calls = 0
        self.last_latency_ms = 12.3
        self._CATALOG = CATALOG_BY_CODE
        self._Hit = CategoryHit

    async def resolve(self, utterance):
        self.calls += 1
        if self._raises:
            raise RuntimeError("model down")
        if self._code and self._code in self._CATALOG:
            name, fam = self._CATALOG[self._code]
            return self._Hit(category_code=self._code, display_name=name, family=fam, confidence=0.7)
        return None


async def _run(pipe, utterance):
    opened = await pipe.start()
    return await pipe.handle(
        session_id=opened["session_id"],
        utterance=utterance,
        expected_revision=int(opened["revision"]),
    )


@pytest.mark.asyncio
async def test_deterministic_hit_does_not_call_llm():
    llm = _FakeLLM(code="1")
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm)
    res = await _run(pipe, "cep telefonu 40 bin")
    assert res["diagnostics"]["ctx_category"] == "1"
    assert llm.calls == 0  # deterministic resolved it; no model call


@pytest.mark.asyncio
async def test_llm_fallback_resolves_on_deterministic_miss():
    # "akordeon" is not in the lexicon → deterministic miss → LLM maps to, say, 19/None.
    llm = _FakeLLM(code="15")  # pretend model says "Mobilya"
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm)
    res = await _run(pipe, "zamazingo alıcam 30 bin bütçem var")
    assert llm.calls == 1
    assert res["diagnostics"]["ctx_category"] == "15"
    assert res["diagnostics"]["category_source"] == "llm_fallback"
    assert res["diagnostics"]["llm_latency_ms"] == 12.3


@pytest.mark.asyncio
async def test_llm_hallucinated_code_is_ignored():
    llm = _FakeLLM(code="999")  # off-catalog → fake returns None
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm)
    res = await _run(pipe, "zamazingo alıcam 20 bin bütçe")
    assert llm.calls == 1
    assert res["phase"] == "CLARIFY"  # no category → clarify, not a wrong answer


@pytest.mark.asyncio
async def test_llm_failure_is_graceful():
    llm = _FakeLLM(raises=True)
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm)
    res = await _run(pipe, "zamazingo alıcam 20 bin bütçe")
    assert res["phase"] == "CLARIFY"  # fail-open


@pytest.mark.asyncio
async def test_identity_never_reveals_ai():
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo())
    for q in ("sen kimsin", "yapay zeka mısın", "hangi modelsin", "chatgpt misin"):
        res = await _run(pipe, q)
        assert res["reply"] == IDENTITY_TEXT
        assert "yapay zeka" not in res["reply"].lower()
        assert "model" not in res["reply"].lower()


@pytest.mark.asyncio
async def test_starter_chips_and_progress_hint_present():
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo())
    opened = await pipe.start()
    assert opened["chips"] == STARTER_CHIPS
    assert opened["progress_hint"] == SEARCHING_TEXT
    # tapping a chip sends its message → real recommendation
    res = await pipe.handle(
        session_id=opened["session_id"],
        utterance=STARTER_CHIPS[0]["message"],
        expected_revision=int(opened["revision"]),
    )
    assert res["campaigns"]


@pytest.mark.asyncio
async def test_llm_primary_overrides_deterministic():
    # Deterministic would resolve "laptop" → 3; in primary mode the LLM decides.
    llm = _FakeLLM(code="3")
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm, llm_primary=True)
    res = await _run(pipe, "laptop 35 bin")
    assert llm.calls == 1  # called on EVERY need turn, not only on miss
    assert res["diagnostics"]["category_source"] == "llm_primary"
    assert res["diagnostics"]["ctx_category"] == "3"


@pytest.mark.asyncio
async def test_llm_primary_hard_guard_blocks_bad_override():
    # Utterance clearly says buzdolabı; a hallucinating LLM claims phone (1).
    # The hard lexical guard must keep it white-goods, not phone.
    llm = _FakeLLM(code="1")
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm, llm_primary=True)
    res = await _run(pipe, "buzdolabı 30 bin")
    assert res["diagnostics"]["ctx_category"] not in ("1", "MOBILE_PHONE")


@pytest.mark.asyncio
async def test_llm_primary_falls_back_to_deterministic_on_failure():
    llm = _FakeLLM(raises=True)
    pipe = CampaignOnlyGuestPipeline(_MemState(), _Repo(), category_llm=llm, llm_primary=True)
    res = await _run(pipe, "cep telefonu 40 bin")  # deterministic → 1
    assert res["diagnostics"]["ctx_category"] == "1"  # safety net kept it
    assert res["diagnostics"]["category_source"] == "llm_primary_fallback_deterministic"


def test_from_env_absent_returns_none():
    assert GuestCategoryResolverLLM.from_env({}) is None


def test_from_env_builds_when_configured():
    llm = GuestCategoryResolverLLM.from_env(
        {"GUEST_FAST_BASE_URL": "http://x:8000", "GUEST_FAST_MODEL": "small-fast"}
    )
    assert llm is not None
    body = llm.request_body("cep telefonu 40 bin")
    assert body["model"] == "small-fast"
    assert body["messages"][1]["content"] == "cep telefonu 40 bin"
    assert "category_code" in body["messages"][0]["content"]  # classifier prompt


def test_parse_code_extracts_and_validates():
    ok = {"choices": [{"message": {"content": '{"category_code":"7"}'}}]}
    assert GuestCategoryResolverLLM._parse_code(ok) == "7"
    null = {"choices": [{"message": {"content": '{"category_code":null}'}}]}
    assert GuestCategoryResolverLLM._parse_code(null) is None
