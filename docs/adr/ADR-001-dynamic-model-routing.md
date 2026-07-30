# ADR-001: Dinamik Model Routing (FAST / FALLBACK)

## Durum

Kabul edildi — güncellendi (MVP hardening)

## Bağlam

Taksitlio chatbotunda her kullanıcı mesajı Türkçe serbest metin olarak gelir. Ağır local modeli her mesajda çalıştırmak gecikmeyi yükseltir. Model adlarının kod veya `.env` içinde sabitlenmesi, POC sonrası model değişimini uygulama yeniden yayınlamaya bağlar.

Modelin kendi `confidence` skoru tek başına routing kararı olamaz. Eksik kullanıcı bilgisi ile model anlama başarısızlığı aynı şey değildir.

## Karar

1. İhtiyaç anlama görevi iki katmanlıdır: her mesajda **FAST**, yalnızca politikaya göre **FALLBACK**.
2. Model kimlikleri `ai_model_profiles` tablosundadır; **çalışan runtime** `ai_model_deployments` + `ai_provider_connections` üzerindedir (bkz. ADR-002).
3. Görev yönlendirmesi versiyonlu `ai_route_versions` ile yapılır (condition, traffic_weight, A/B).
4. `ModelRouter` **system_confidence** + reason code ile karar verir:
   * `CONTINUE` | `CLARIFY` | `FALLBACK` | `SAFE_FAILURE`
5. `MISSING_INFORMATION` → CLARIFY (FALLBACK değil).
6. `INVALID_SCHEMA` / `COMPREHENSION_FAILURE` / düşük sistem güveni → FALLBACK.
7. Timeout **absolute deadline** (`total_budget_ms`) ile yönetilir; kalan süre yetmezse FALLBACK çağrılmaz.
8. `ModelGateway` deployment üzerinden OpenAI-compatible çağrı yapar; uygulama kodu model adı/IP/port bilmez.

## Sonuçlar

**Olumlu**

* Yönetim panelinden model/deployment/route değişimi; mobil API dokunulmaz.
* Clarification ile fallback ayrımı yanlış büyük-model çağrılarını keser.
* A/B ve challenger trafik ağırlığı mümkün olur.

**Olumsuz / risk**

* FAST Türkçe doğruluğu golden set ile kanıtlanmalıdır.
* System confidence ağırlıkları kalibre edilmelidir.
* Çoklu route version operasyonel karmaşıklık ekler.

## Alternatifler (reddedildi)

* Tek büyük model her mesajda.
* Model adının env’de sabitlenmesi.
* Model self-confidence ile doğrudan routing.
* Eksik bilgi için otomatik FALLBACK.
