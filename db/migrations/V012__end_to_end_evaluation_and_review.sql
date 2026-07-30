-- V012: End-to-end understanding + provisional evaluation (ADR-007)
-- Adds versioned NEED_UNDERSTANDING_SEMANTIC_CONSTRAINTS prompt as challenger.
-- Activation requires AuditService + admin approval (challenger → active).
-- Do NOT embed vendor model names or endpoints here.

-- Deactivate legacy NEED_UNDERSTANDING v1 only when the new prompt is
-- explicitly promoted — this migration only *registers* the challenger.
INSERT INTO ai_prompt_versions (
    prompt_code, version, task_code, content, json_schema_ref, is_active, notes
) VALUES (
    'NEED_UNDERSTANDING_SEMANTIC_CONSTRAINTS',
    1,
    'NEED_UNDERSTANDING',
    $prompt$Sen Taksitlio Türkçe ihtiyaç anlama motorusun (semantic constraints).
Günlük Türkçeyi, yazım hatalarını ve karakter eksik yazımı anla.
Kullanıcı mesajından NeedProfile JSON üret.

ZORUNLU KURALLAR:
- Kategori ID, fixture key, katalog kodu, provider/banka/kampanya ID ÜRETME.
- Natural-language concept kullan (örn. "hafif tablet", "telefon").
- Negation (istemiyorum, değil) → semantic_constraints.negative (EXPLICIT_NEGATION).
- Preference (arıyorum, lazım) → semantic_constraints.positive (EXPLICIT).
- Kullanıcı düzeltmesi (aslında, hayır ... demedim, özür dilerim ... değil) → corrections.
- Bilmediğin bilgiyi uydurma; eksikse clarification.required=true.
- Kısa geçerli JSON üret. Thinking kullanma.
- Kategori listesi bu promptta YOKTUR; katalog eşlemesi matcher'ın işidir.
$prompt$,
    'need_profile.schema.json',
    FALSE,
    'ADR-007 challenger: semantic_constraints-aware FAST extraction (awaiting admin promotion)'
)
ON CONFLICT (prompt_code, version) DO NOTHING;

-- Schema pointer stays package-synced; bump note for ADR-007 fields.
INSERT INTO ai_schema_versions (schema_code, version, schema_body, is_active, notes)
VALUES (
    'NEED_PROFILE',
    2,
    '{"$ref":"package:taksitlio/schemas/need_profile.schema.json"}'::jsonb,
    FALSE,
    'ADR-007 challenger schema ref — promote with prompt after review'
)
ON CONFLICT (schema_code, version) DO NOTHING;
