# Anonymizácia (SK)

Systém na anonymizované spracovanie dokumentov a ich bezpečné použitie s LLM.

Ciele
- Deterministická detekcia PII (osobných údajov) cez viacero detektorov (regex, Microsoft Presidio).
- Pseudonymizácia: nahradenie PII robustnými tokenmi a uloženie máp token -> pôvodná hodnota do „vault“.
- De‑anonymizácia: obnova pôvodného textu pre autorizovaných používateľov z vault databázy.
- Deterministická anonymizácia: nahradenie PII HMAC hash tokenmi (nehotovateľná spätná obnova), vhodné pre audit a zdieľanie bez rizika úniku PII.

Architektúra
- domain: čistá logika (entity, porty, deterministická anonymizácia)
- app: use‑cases (detect_all, pseudonymize, deanonymize) a konfigurácia PII
- adapters: implementácie detektorov (regex, Presidio) a „token vaultu“ (súborový/Postgres), kryptografia
- presentation: CLI a jednoduché API
- gui: integrácia do desktopovej GUI (tab Anonymization)

Detektory
- RegexDetector (offline):
  - EMAIL: jednoduchý RFC‑like regex
  - PHONE: medzinárodné/miestne vzory (best‑effort)
- PresidioDetector (voliteľné):
  - využíva spaCy modely; jazyky a modely nastavíte cez ANON_PRESIDIO_LANGS
  - fallback na regex keď Presidio/modely nie sú dostupné

Vault (úložisko máp token->hodnota)
- FileTokenVault: per‑"context_id" JSON súbory v adresári ANON_VAULT_DIR (predvolené src/anonymization/.anonymization_vault)
- PostgresTokenVault: tabuľka pii_tokens s primárnym kľúčom (tenant_id, token_id)

Kryptografia
- Crypto.hash(value, tenant_id?): HMAC‑SHA256 deterministický token: h:<kid>:<hex>
- Crypto.tokenize(value, tenant_id?): HMAC‑SHA256 základ pre stabilný identifikátor: t:<kid>:<id>
- Kľúče a rotácia: nastavte ANON_KEYSET (JSON)

Prostredie (ENV)
- ANON_DETECTORS=regex|presidio (predvolene „presidio“ s fallbackom na regex)
- ANON_PRESIDIO_LANGS="en:en_core_web_sm,de:de_core_news_sm,sk:xx_ent_wiki_sm"
- ANON_PRESIDIO_FALLBACK_MODEL=xx_ent_wiki_sm
- ANON_VAULT_DIR=src/anonymization/.anonymization_vault
- ANON_POSTGRES_DSN=postgresql://user:pass@host/db (ak chcete Postgres vault)
- ANON_KEYSET='{"active_kid":"kidA","keys":{"kidA":"<base64-32B>"}}'

API použitie (Python)
- Pseudonymizácia (reverzibilná):
  - from anonymization.adapters.container import build_default
  - from anonymization.app.pseudonymize import pseudonymize
  - detectors, vault = build_default()
  - res = pseudonymize("Kontakt: alice@example.com", detectors, vault, context_id="doc-1", language="en")
  - print(res.pseudonymized_text); vault.save_mappings("doc-1", res.mappings)
- De‑anonymizácia:
  - from anonymization.app.denomize import deanonymize
  - orig = deanonymize(res.pseudonymized_text, vault, context_id="doc-1").restored_text
- Deterministická anonymizácia (nevratná):
  - from anonymization.domain.anonymizer import anonymize
  - from anonymization.adapters.crypto.crypto import Crypto
  - detectors, vault = build_default(); crypto = Crypto()
  - out = anonymize("Volajte +421 123 456 789", detectors, crypto, vault, context_id="doc-1", tenant_id="acme", language="sk")

GUI integrácia
- Tab „Anonymization“ ponúka:
  - Detect: zobrazí zistené entity a ich rozpätia
  - Pseudonymize: nahradí PII tokenmi {{PII:TYPE:i:rand}} a uloží mapy
  - De‑anonymize: obnoví pôvodné hodnoty podľa "Context ID"
  - Deterministic anonymize: nahradí PII HMAC tokenmi (h:<kid>:<hex>); voliteľné „Tenant ID“ pre oddelenie domén

Odporúčania
- Detekciu PII robte nad anglickou verziou dokumentu (pozri preprocessing modul pre „make_english“) – presnosť Presidia je najlepšia v podporovaných jazykoch.
- Pre LLM posielajte iba pseudonymizovaný text; po návrate odpovede deterministicky de‑anonymizujte lokálne cez vault.
- Context ID voľte stabilne per dokument (napr. hash cesty/obsahu) pre deduplikáciu a audit.

Testy
- Unit: deterministická anonymizácia (rovnaké hodnoty -> rovnaký token), merge prekryvov, pseudonymize/deanonymize.
- Integration: PostgresTokenVault (schema, upsert, lookup).
- E2E: CLI anonymizácie v rámci pre‑process pipeline (anonymize‑en).
