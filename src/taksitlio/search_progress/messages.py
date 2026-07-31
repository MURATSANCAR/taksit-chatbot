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


# Generic catalog — no bank/merchant names.
_EVENT_MESSAGES: dict[SearchProgressEventType, str] = {
    SearchProgressEventType.SEARCH_ACCEPTED: "İsteğini çözümlüyorum...",
    SearchProgressEventType.FAST_PARSE_STARTED: "İsteğini çözümlüyorum...",
    SearchProgressEventType.FAST_PARSE_COMPLETED: "İhtiyaçlar çözümlendi",
    SearchProgressEventType.ENTITY_RESOLUTION_STARTED: "Uygun mağaza ve ürün kategorilerini eşleştiriyorum...",
    SearchProgressEventType.ENTITY_RESOLUTION_COMPLETED: "Uygun mağazalar eşleştirildi",
    SearchProgressEventType.GAP_ANALYSIS_COMPLETED: "İhtiyaçlar değerlendirildi",
    SearchProgressEventType.CLARIFICATION_REQUIRED: "Bir tercihinizi netleştirmem yeterli olacak",
    SearchProgressEventType.CLARIFICATION_ANSWERED: "Tercihiniz alındı",
    SearchProgressEventType.LLM_JOB_QUEUED: "Tercihlerinizi ürün özellikleriyle eşleştiriyorum...",
    SearchProgressEventType.LLM_JOB_STARTED: "Tercihlerinizi ürün özellikleriyle eşleştiriyorum...",
    SearchProgressEventType.PRODUCT_POOL_SEARCH_STARTED: "Kriterlerinize uygun ürünler hazırlanıyor...",
    SearchProgressEventType.PRODUCT_POOL_PARTIAL_READY: "İlk uygun ürünleri buldum. Sonuçları tercihlerinize göre daraltıyorum.",
    SearchProgressEventType.MERCHANT_CANDIDATES_RESOLVED: "Uygun mağazalar bulundu",
    SearchProgressEventType.BRAND_CANDIDATES_RESOLVED: "Uygun markalar eşleştirildi",
    SearchProgressEventType.FINANCE_SEARCH_STARTED: "Finansman seçenekleri hazırlanıyor...",
    SearchProgressEventType.FINANCIAL_INSTITUTION_CANDIDATES_FOUND: "Uygun finansman seçenekleri karşılaştırılıyor",
    SearchProgressEventType.PAYMENT_PLAN_CALCULATION_STARTED: "Vade ve aylık ödeme seçenekleri hesaplanıyor...",
    SearchProgressEventType.PARTIAL_RESULTS_READY: "İlk uygun ürünleri buldum. Sonuçları tercihlerinize göre daraltıyorum.",
    SearchProgressEventType.LLM_JOB_COMPLETED: "Tercihler ürün özellikleriyle eşleştirildi",
    SearchProgressEventType.LLM_JOB_TIMED_OUT: "Elimdeki kesin kriterlere göre en yakın sonuçları hazırladım.",
    SearchProgressEventType.RANKING_STARTED: "Sonuçlar sıralanıyor...",
    SearchProgressEventType.FINAL_RESULTS_READY: "Sonuçlar hazır",
    SearchProgressEventType.SEARCH_COMPLETED: "Arama tamamlandı",
    SearchProgressEventType.SEARCH_COMPLETED_DEGRADED: (
        "Elimdeki kesin kriterlere göre ürünleri sıraladım. "
        "Dilerseniz bir tercih daha ekleyerek sonuçları daraltabilirsiniz."
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
