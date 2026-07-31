"""Progress event types and data-origin-aware display messages (ADR-011)."""

from __future__ import annotations

from enum import Enum
from typing import Optional


class SearchProgressEventType(str, Enum):
    SEARCH_ACCEPTED = "SEARCH_ACCEPTED"
    FAST_PARSE_STARTED = "FAST_PARSE_STARTED"
    FAST_PARSE_COMPLETED = "FAST_PARSE_COMPLETED"
    ENTITY_RESOLUTION_STARTED = "ENTITY_RESOLUTION_STARTED"
    ENTITY_RESOLUTION_COMPLETED = "ENTITY_RESOLUTION_COMPLETED"
    GAP_ANALYSIS_COMPLETED = "GAP_ANALYSIS_COMPLETED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CLARIFICATION_ANSWERED = "CLARIFICATION_ANSWERED"
    LLM_JOB_QUEUED = "LLM_JOB_QUEUED"
    LLM_JOB_STARTED = "LLM_JOB_STARTED"
    PRODUCT_POOL_SEARCH_STARTED = "PRODUCT_POOL_SEARCH_STARTED"
    PRODUCT_POOL_PARTIAL_READY = "PRODUCT_POOL_PARTIAL_READY"
    MERCHANT_CANDIDATES_RESOLVED = "MERCHANT_CANDIDATES_RESOLVED"
    BRAND_CANDIDATES_RESOLVED = "BRAND_CANDIDATES_RESOLVED"
    FINANCE_SEARCH_STARTED = "FINANCE_SEARCH_STARTED"
    FINANCIAL_INSTITUTION_CANDIDATES_FOUND = "FINANCIAL_INSTITUTION_CANDIDATES_FOUND"
    PAYMENT_PLAN_CALCULATION_STARTED = "PAYMENT_PLAN_CALCULATION_STARTED"
    PARTIAL_RESULTS_READY = "PARTIAL_RESULTS_READY"
    LLM_JOB_COMPLETED = "LLM_JOB_COMPLETED"
    LLM_JOB_TIMED_OUT = "LLM_JOB_TIMED_OUT"
    RANKING_STARTED = "RANKING_STARTED"
    FINAL_RESULTS_READY = "FINAL_RESULTS_READY"
    SEARCH_COMPLETED = "SEARCH_COMPLETED"
    SEARCH_COMPLETED_DEGRADED = "SEARCH_COMPLETED_DEGRADED"
    SEARCH_FAILED = "SEARCH_FAILED"
    SEARCH_CANCELLED = "SEARCH_CANCELLED"


class DataOrigin(str, Enum):
    LOCAL_VERIFIED_SNAPSHOT = "LOCAL_VERIFIED_SNAPSHOT"
    MERCHANT_FEED = "MERCHANT_FEED"
    MERCHANT_API = "MERCHANT_API"
    FINANCIAL_INSTITUTION_API = "FINANCIAL_INSTITUTION_API"
    CACHED_VERIFIED_RESULT = "CACHED_VERIFIED_RESULT"


# Generic catalog — no bank/merchant names. Marketing-friendly TR copy only.
_EVENT_MESSAGES: dict[SearchProgressEventType, str] = {
    SearchProgressEventType.SEARCH_ACCEPTED: "İhtiyacını dinliyorum…",
    SearchProgressEventType.FAST_PARSE_STARTED: "İhtiyacını dinliyorum…",
    SearchProgressEventType.FAST_PARSE_COMPLETED: "İhtiyacını anladım",
    SearchProgressEventType.ENTITY_RESOLUTION_STARTED:
        "Senin için uygun mağaza ve kategorileri eşleştiriyorum…",
    SearchProgressEventType.ENTITY_RESOLUTION_COMPLETED: "Uygun mağazalar eşleşti",
    SearchProgressEventType.GAP_ANALYSIS_COMPLETED: "Tercihlerini netleştirdim",
    SearchProgressEventType.CLARIFICATION_REQUIRED:
        "Bir tercihin daha netleşirse daha isabetli önerebilirim",
    SearchProgressEventType.CLARIFICATION_ANSWERED: "Tercihin alındı",
    SearchProgressEventType.LLM_JOB_QUEUED:
        "Tercihlerini ürün özellikleriyle eşleştiriyorum…",
    SearchProgressEventType.LLM_JOB_STARTED:
        "Tercihlerini ürün özellikleriyle eşleştiriyorum…",
    SearchProgressEventType.PRODUCT_POOL_SEARCH_STARTED:
        "Katalogdan sana uygun ürünleri arıyorum…",
    SearchProgressEventType.PRODUCT_POOL_PARTIAL_READY:
        "Katalogdan ilk eşleşmeleri getirdim",
    SearchProgressEventType.MERCHANT_CANDIDATES_RESOLVED:
        "Senin için uygun mağazaları seçiyorum",
    SearchProgressEventType.BRAND_CANDIDATES_RESOLVED: "Uygun markaları eşleştirdim",
    SearchProgressEventType.FINANCE_SEARCH_STARTED:
        "Taksit ve finansman seçeneklerini hazırlıyorum…",
    SearchProgressEventType.FINANCIAL_INSTITUTION_CANDIDATES_FOUND:
        "Taksit ve finansman seçeneklerini karşılaştırıyorum",
    SearchProgressEventType.PAYMENT_PLAN_CALCULATION_STARTED:
        "Aylık ödeme ve vade seçeneklerini hesaplıyorum…",
    SearchProgressEventType.PARTIAL_RESULTS_READY:
        "Ön sonuçlar hazır — tercihlerinle daraltıyorum",
    SearchProgressEventType.LLM_JOB_COMPLETED: "Tercihlerin ürünlerle eşleşti",
    SearchProgressEventType.LLM_JOB_TIMED_OUT:
        "Elimdeki kesin kriterlere göre en yakın sonuçları hazırladım",
    SearchProgressEventType.RANKING_STARTED: "Sana en uygun sırayı belirliyorum…",
    SearchProgressEventType.FINAL_RESULTS_READY: "Sana özel öneriler hazır",
    SearchProgressEventType.SEARCH_COMPLETED: "Araman tamamlandı",
    SearchProgressEventType.SEARCH_COMPLETED_DEGRADED: (
        "Elimdeki kesin kriterlere göre ürünleri sıraladım. "
        "Dilersen bir tercih daha ekleyerek sonuçları daraltabilirsin."
    ),
    SearchProgressEventType.SEARCH_FAILED: "Arama tamamlanamadı",
    SearchProgressEventType.SEARCH_CANCELLED: "Arama iptal edildi",
}

# Forbidden unless real integration workflow exists (guard list for tests).
FORBIDDEN_PROGRESS_PHRASES = (
    "bankalara başvuru yapılıyor",
    "kredi limitiniz hesaplanıyor",
    "bankalardan kişisel teklif alınıyor",
    "kredi onayınız kontrol ediliyor",
    "bankanızla bağlantı kuruluyor",
    "kampanyanız onaylanıyor",
    "bankalardan teklifler alınıyor",
)


def finance_progress_message(data_origin: Optional[DataOrigin | str]) -> str:
    if data_origin is None:
        return "Finansman seçenekleri hazırlanıyor..."
    origin = DataOrigin(data_origin) if isinstance(data_origin, str) else data_origin
    if origin == DataOrigin.LOCAL_VERIFIED_SNAPSHOT:
        return "Güncel finansman kayıtları karşılaştırılıyor..."
    if origin in {DataOrigin.MERCHANT_FEED, DataOrigin.MERCHANT_API}:
        return "Mağazalardaki ürün ve finansman seçenekleri kontrol ediliyor..."
    if origin == DataOrigin.FINANCIAL_INSTITUTION_API:
        return "Finans kuruluşlarından güncel teklifler alınıyor..."
    if origin == DataOrigin.CACHED_VERIFIED_RESULT:
        return "Güncel finansman seçenekleri karşılaştırılıyor..."
    return "Finansman seçenekleri hazırlanıyor..."


def display_message_for(
    event_type: SearchProgressEventType | str,
    *,
    data_origin: Optional[DataOrigin | str] = None,
) -> str:
    et = (
        SearchProgressEventType(event_type)
        if isinstance(event_type, str)
        else event_type
    )
    if et in {
        SearchProgressEventType.FINANCE_SEARCH_STARTED,
        SearchProgressEventType.FINANCIAL_INSTITUTION_CANDIDATES_FOUND,
    }:
        return finance_progress_message(data_origin)
    return _EVENT_MESSAGES.get(et, "İşlem devam ediyor...")


def assert_truthful_message(message: str, *, data_origin: Optional[str] = None) -> None:
    """Raise if message claims live bank API without matching data_origin."""

    lower = message.casefold()
    for phrase in FORBIDDEN_PROGRESS_PHRASES:
        if phrase in lower:
            raise ValueError(f"Forbidden progress phrase without verified workflow: {phrase}")
    live_claim = "finans kuruluşlarından güncel teklifler" in lower
    if live_claim and data_origin != DataOrigin.FINANCIAL_INSTITUTION_API.value:
        raise ValueError("Live institution API message requires FINANCIAL_INSTITUTION_API origin")
