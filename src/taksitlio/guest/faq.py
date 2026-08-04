"""Grounded FAQ answers for guest (loginsiz) — no LLM, no invented offers."""

from __future__ import annotations

from typing import Any, Optional


FAQ_ANSWERS: dict[str, dict[str, Any]] = {
    "membership_how": {
        "reply": (
            "Üyelik ücretsiz ve yaklaşık 1 dakika sürer.\n\n"
            "Uygulamada “Üye ol” butonuna dokun → telefon doğrulama → "
            "kısa kimlik bilgileri. Üye olduktan sonra kampanyalara başvuru, "
            "ödeme planı ve limit süreçlerini birlikte yönetebiliriz."
        ),
        "cta": True,
    },
    "membership_required": {
        "reply": (
            "Kampanyaları görmek için üyelik zorunlu değil; loginsiz alanda "
            "ihtiyaç ve bütçeni yazarak 1-2 uygun kampanyayı gösterebiliyorum.\n\n"
            "Başvuru yapmak, detaylı filtre ve ödeme planı için üyelik gerekir."
        ),
        "cta": True,
    },
    "installment_how": {
        "reply": (
            "Taksitlio’da gördüğün oranlar banka/finansman kurumunun kampanya "
            "koşullarına göredir (kar oranı / faiz, vade, tahsis ücreti, BSMV).\n\n"
            "Örnek: %1,99 kar × 6 ay gibi. Kesin ödeme planı başvuru sonrası "
            "kurum onayına bağlıdır; loginsiz alanda sadece güncel kampanya özetini paylaşırım."
        ),
        "cta": True,
    },
    "campaign_conditions": {
        "reply": (
            "Koşullar kampanyaya göre değişir. Sık görülenler:\n"
            "• Yeni müşteri / ilk defa o bankadan finansman\n"
            "• Tutar ve vade üst sınırları\n"
            "• Belirli ürün kategorileri (telefon, beyaz eşya…)\n\n"
            "İhtiyaç ve bütçeni yaz, sana uyan aktif kampanyaların kısa özetini "
            "ve “Üye ol, kampanyadan yararlan” adımını getireyim."
        ),
        "cta": True,
    },
    "bank_diff": {
        "reply": (
            "Bankalar arasında fark genelde kar/faiz oranı, max vade, üst limit "
            "ve “yeni müşteri” şartında olur.\n\n"
            "Loginsiz alanda bütçene göre en uygun 1-2 kampanyayı yan yana "
            "gösterebilirim. Detaylı karşılaştırma ve başvuru için üyelik gerekir."
        ),
        "cta": True,
    },
    "what_is_taksitlio": {
        "reply": (
            "Taksitlio; elektronik ve diğer kategorilerde taksitli / finansmanlı "
            "alışveriş kampanyalarını tek yerden görmeni sağlar.\n\n"
            "İhtiyaç ve bütçeni yaz, uygun kampanyaları listeleyeyim; "
            "başvuru için üye olman yeterli."
        ),
        "cta": True,
    },
    "fees": {
        "reply": (
            "Kampanya metinlerinde geçen tahsis ücreti, BSMV ve aylık/yıllık "
            "maliyet oranları kuruma aittir. Loginsiz özetlerde bunları olduğu gibi "
            "gösteririm; kesin tutar başvuru ve onay sonrası netleşir.\n\n"
            "Gizli ek ücret uydurmam — sadece kampanyada yazanları iletirim."
        ),
        "cta": True,
    },
}

CTA = {
    "label": "Üye ol, kampanyadan yararlan",
    "action": "NAVIGATE_REGISTER",
    "require_membership": True,
    "style": "primary",
}


def answer_faq(faq_key: str) -> dict[str, Any]:
    item = FAQ_ANSWERS.get(faq_key) or {
        "reply": (
            "Bu konuda loginsiz alanda net bir özetim yok. "
            "İhtiyaç ve bütçeni yazarsan uygun kampanyaları gösterebilirim; "
            "detay için üye olman yeterli."
        ),
        "cta": True,
    }
    out: dict[str, Any] = {
        "reply": item["reply"],
        "faq_key": faq_key,
    }
    if item.get("cta"):
        out["membership_cta"] = CTA
    return out


def answer_smalltalk(utterance: str) -> dict[str, Any]:
    t = (utterance or "").strip().lower()
    if any(w in t for w in ("teşekkür", "sağol", "saol")):
        reply = "Rica ederim! İhtiyacın olursa ihtiyaç ve bütçeni yazman yeterli."
    elif any(w in t for w in ("tamam", "ok", "peki", "anladım")):
        reply = "Tamam. Ne almak istediğini ve bütçeni yazabilirsin."
    else:
        reply = (
            "Merhaba! Senin için ihtiyaç analizi yapayım mı? "
            "Ne almak istediğini ve bütçeni kısaca yazman yeterli "
            "(örnek: \"cep telefonu, 40 bin TL\")."
        )
    return {"reply": reply, "membership_cta": None}


def answer_oos() -> dict[str, Any]:
    return {
        "reply": (
            "Bu soruyu loginsiz alanda net cevaplayamıyorum "
            "(stok, başvuru durumu, belge, detaylı model karşılaştırması vb.).\n\n"
            "Üye olursan kampanyaları detaylı filtreleyebilir, ödeme planı çıkarabilir "
            "ve başvuru sürecini birlikte yönetebiliriz.\n\n"
            "Şimdilik ihtiyaç + bütçe yazarsan en uygun 1-2 kampanyayı gösterebilirim."
        ),
        "membership_cta": CTA,
    }


def answer_unknown() -> dict[str, Any]:
    return {
        "reply": (
            "Tam olarak ne istediğini anlayamadım.\n\n"
            "Şunları deneyebilirsin:\n"
            "• İhtiyaç + bütçe: \"tablet, 15 bin TL\"\n"
            "• Üyelik: \"nasıl üye olurum?\"\n"
            "• Taksit: \"taksit nasıl işler?\"\n"
            "• Öneri sonrası: \"daha ucuz olsun\" / \"daha uzun vade\""
        ),
        "membership_cta": CTA,
    }
