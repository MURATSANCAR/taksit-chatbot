# ADR-001: Dinamik Model Routing (FAST / FALLBACK)

## Durum

Kabul edildi — MVP

## Bağlam

Taksitlio chatbotunda her kullanıcı mesajı Türkçe serbest metin olarak gelir. Ağır local modeli her mesajda çalıştırmak gecikmeyi yükseltir. Model adlarının kod veya `.env` içinde sabitlenmesi, POC sonrası model değişimini uygulama yeniden yayınlamaya bağlar.

## Karar

1. İhtiyaç anlama görevi iki katmanlıdır: her mesajda **FAST**, yalnızca politikaya göre **FALLBACK**.
2. Model kimlikleri `ai_model_profiles` tablosunda tutulur; görev yönlendirmesi `ai_task_routes` ile yapılır.
3. `ModelRouter` confidence / schema / çelişki politikasına göre FAST → clarification veya FALLBACK seçer.
4. `ModelGateway` provider endpoint’lerine (ör. llama.cpp) soyut erişim sağlar; uygulama kodu model adını bilmez.

## Sonuçlar

**Olumlu**

* Yönetim panelinden model değişimi; mobil API ve chat akışı dokunulmaz.
* Büyük model yükü düşer; P50/P95 hedefleri gerçekçi hale gelir.
* A/B ve challenger karşılaştırması mümkün olur.

**Olumsuz / risk**

* FAST model Türkçe doğruluğu golden set ile kanıtlanmalıdır.
* İki inference runtime’ının operasyonel maliyeti artar.
* Routing politikası yanlış ayarlanırsa fallback oranı veya clarification frekansı bozulabilir.

## Alternatifler (reddedildi)

* Tek büyük model her mesajda — hız hedefiyle uyumsuz.
* Model adının env’de sabitlenmesi — operasyonel esneklik yok.
* Kural / keyword tabanlı Türkçe anlama — dolaylı ve hatalı günlük konuşmayı çözemez.
