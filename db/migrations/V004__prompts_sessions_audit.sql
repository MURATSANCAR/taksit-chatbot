-- V004: Prompt seed, conversation audit, response policies

CREATE TABLE IF NOT EXISTS conversation_events (
    id              BIGSERIAL PRIMARY KEY,
    session_id      VARCHAR(128) NOT NULL,
    user_id         VARCHAR(128),
    event_type      VARCHAR(64)  NOT NULL,
    payload         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    model_profile_code VARCHAR(64),
    latency_ms      INTEGER,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversation_events_session
    ON conversation_events (session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS response_policies (
    id              BIGSERIAL PRIMARY KEY,
    policy_code     VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(128) NOT NULL,
    max_campaigns_in_reply INTEGER NOT NULL DEFAULT 3,
    require_grounding BOOLEAN NOT NULL DEFAULT TRUE,
    allow_fabricated_prices BOOLEAN NOT NULL DEFAULT FALSE,
    membership_cta_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    out_of_scope_message TEXT NOT NULL DEFAULT
        'Bu konuda yardımcı olamıyorum. Taksitlio ürün ve kampanya ihtiyaçlarınız için buradayım.',
    clarification_template TEXT NOT NULL DEFAULT
        'Daha iyi önerebilmem için netleştirmem gerekiyor: {question}',
    configuration   JSONB NOT NULL DEFAULT '{}'::jsonb,
    status          VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE', 'INACTIVE', 'DRAFT')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO response_policies (policy_code, display_name)
VALUES ('DEFAULT', 'Varsayılan grounded cevap politikası')
ON CONFLICT (policy_code) DO NOTHING;

-- NEED_UNDERSTANDING aktif prompt
INSERT INTO ai_prompt_versions (
    prompt_code, version, task_code, content, json_schema_ref, is_active, notes
) VALUES (
    'NEED_UNDERSTANDING',
    1,
    'NEED_UNDERSTANDING',
    $prompt$Sen Taksitlio Türkçe ihtiyaç anlama motorusun.
Kullanıcı mesajından yapılandırılmış ihtiyaç profili çıkar.
Kategori kodu ÜRETME. Kampanya seçme. Finansal tavsiye verme.
Sadece geçerli JSON döndür. Thinking kullanma.
need_description kısa ve semantik eşleşmeye uygun olsun.
Bütçe: "40 bin", "40 civarı", "35''i geçmesin" gibi ifadeleri doğru parse et.
Aylık ödeme ile toplam bütçeyi karıştırma; karışıklık varsa signals.budget_payment_confusion=true.
Birden fazla farklı ürün ihtiyacı varsa signals.multiple_needs=true.
Önceki session özeti varsa çelişkiyi signals.conflicts_with_session ile işaretle.
$prompt$,
    'need_profile.schema.json',
    TRUE,
    'MVP v1 NEED_UNDERSTANDING system prompt'
)
ON CONFLICT (prompt_code, version) DO NOTHING;

INSERT INTO ai_prompt_versions (
    prompt_code, version, task_code, content, json_schema_ref, is_active, notes
) VALUES (
    'CONVERSATION_UPDATE',
    1,
    'NEED_UNDERSTANDING',
    $prompt$Sen Taksitlio konuşma güncelleme motorusun.
Kullanıcı yeni bir mesaj gönderdi. Mevcut session ihtiyacına uygulanacak ConversationUpdate JSON üret.
Yeni bağımsız ihtiyaç oluşturma; yalnızca değişen alanları UPDATE et.
operation: UPDATE | REPLACE | CLARIFY | RESET.
Sadece geçerli JSON döndür.
$prompt$,
    'conversation_update.schema.json',
    TRUE,
    'MVP v1 conversation update prompt'
)
ON CONFLICT (prompt_code, version) DO NOTHING;

INSERT INTO ai_prompt_versions (
    prompt_code, version, task_code, content, json_schema_ref, is_active, notes
) VALUES (
    'GROUNDED_RESPONSE',
    1,
    'RESPONSE_GENERATION',
    $prompt$Sen Taksitlio asistanısın. Yalnızca verilen kampanya kayıtlarına dayanarak Türkçe cevap yaz.
Kampanyada olmayan fiyat, taksit veya özellik UYDURMA.
Kampanya yoksa nazikçe belirt ve gerekirse netleştirici soru sor.
Üyelik CTA''sını doğal biçimde ekle (policy izin veriyorsa).
Kısa, samimi, bankacılık diline uygun ol.
$prompt$,
    NULL,
    TRUE,
    'MVP v1 grounded response prompt'
)
ON CONFLICT (prompt_code, version) DO NOTHING;

-- Schema versiyonları (aktif body uygulama tarafında package schema ile senkron tutulur)
INSERT INTO ai_schema_versions (schema_code, version, schema_body, is_active, notes)
VALUES (
    'NEED_PROFILE',
    1,
    '{"$ref":"package:taksitlio/schemas/need_profile.schema.json"}'::jsonb,
    TRUE,
    'Package schema reference'
)
ON CONFLICT (schema_code, version) DO NOTHING;

INSERT INTO ai_schema_versions (schema_code, version, schema_body, is_active, notes)
VALUES (
    'CONVERSATION_UPDATE',
    1,
    '{"$ref":"package:taksitlio/schemas/conversation_update.schema.json"}'::jsonb,
    TRUE,
    'Package schema reference'
)
ON CONFLICT (schema_code, version) DO NOTHING;
