# Anonymization

PII detection, pseudonymization, and de-anonymization toolkit.

- Multiple detectors: regex (built-in), optional Microsoft Presidio
- Deterministic anonymization via HMAC-SHA256 tokens (h:<kid>:<hex>)
- Pseudonymize to robust tokens and store reversible mappings
- De-anonymize text using stored mappings
- File- and Postgres-backed token vaults
- API (FastAPI) and CLI included

## Install

Base package (no extra deps):

- Editable install from repo root:

  - pip install -e src/anonymization

Optional extras:

- API server: pip install -e src/anonymization[api]
- Presidio detector: pip install -e src/anonymization[presidio]
  - Plus spaCy models you need, e.g.: python -m spacy download en_core_web_sm
- Postgres vault: pip install -e src/anonymization[postgres]

Python 3.10+ is required.

## Quick start (Python)

- Detect + pseudonymize + de-anonymize

  - from anonymization.adapters.container import build_default
  - from anonymization.app.pseudonymize import pseudonymize
  - from anonymization.app.denomize import deanonymize
  - detectors, vault = build_default()
  - ctx = "session-123"
  - res = pseudonymize("Email alice@example.com", detectors, vault, ctx)
  - dean = deanonymize(res.pseudonymized_text, vault, ctx)

- Deterministic anonymization (non-reversible)

  - from anonymization.adapters.container import build_default
  - from anonymization.adapters.crypto.crypto import Crypto
  - from anonymization.domain.anonymizer import anonymize
  - detectors, vault = build_default()
  - crypto = Crypto()
  - res = anonymize("Call +1 555 123 4567 or mail alice@example.com", detectors, crypto, vault, context_id="doc-1", tenant_id="acme", language="en")

## Workflow

```mermaid
flowchart TD
  A[Input text] --> B{Detectors}
  B -->|regex| C[RegexDetector]
  B -->|presidio| D[PresidioDetector]
  C --> E[Merge entities]
  D --> E
  E --> F1[Anonymize (HMAC hash)]
  E --> F2[Pseudonymize (reversible token)]
  F1 --> I[Anonymized text]
  F2 --> G[Token Vault]
  G --> H[Save token<->value]
  I --> J[LLM or API]
  J --> K[Response]
```

## CLI

- After install, run anonymization-cli:

  - anonymization-cli detect "Email alice@example.com"
  - anonymization-cli pseudonymize "Call +1 555-123-4567" --context demo
  - anonymization-cli deanonymize "{{PII:PHONE:1:xxxx}}" --context demo
  - anonymization-cli anonymize "Contact alice@example.com" --context doc1 --tenant acme --lang en

Outputs JSON.

## Configuration

- Detectors:
  - ANON_DETECTORS: regex|presidio (default: presidio, falls back to regex when unavailable)
  - ANON_PRESIDIO_LANGS: lang:model pairs, e.g. en:en_core_web_sm,es:es_core_news_sm

- Token vault:
  - FileTokenVault (default): ANON_VAULT_DIR directory path (default src/anonymization/.anonymization_vault)
  - PostgresTokenVault: set ANON_POSTGRES_DSN (postgresql://user:pass@host/db). Table default: pii_tokens. Created automatically.

- Keys (for crypto hashing/tokenization):
  - Set ANON_KEYSET as JSON:
    {"active_kid":"kidA","keys":{"kidA":"<base64-32B>","kidB":"<base64-32B>"}}
  - Rotation: switch active_kid; old keys remain available via get_all_hmac_keys.

## Postgres vault schema

- Expected table (default name: pii_tokens):
  CREATE TABLE IF NOT EXISTS pii_tokens (
      tenant_id   text NOT NULL,
      token_id    text NOT NULL,
      pii_type    text NOT NULL,
      value_enc   bytea NOT NULL,
      first_seen  timestamptz NOT NULL DEFAULT NOW(),
      PRIMARY KEY (tenant_id, token_id)
  );

- Usage (Python):
  - from anonymization.adapters.token_vault.postgres_store import PostgresTokenVault
  - vault = PostgresTokenVault(dsn="postgresql://user:pass@host/db")
  - vault.ensure_schema()
  - vault.upsert(token_id, tenant_id, "EMAIL", "alice@example.com")
  - value = vault.lookup(token_id, tenant_id)

## Notes

- Presidio is lazily imported; if not installed, regex detection still works.
- Crypto.hash returns h:<kid>:<hex> and Crypto.tokenize returns t:<kid>:<id> for stable, non-reversible identifiers.

## Tests

- Unit tests under tests/unit cover anonymizer determinism.
- Integration tests under tests/integration validate the Postgres adapter (DB-API via sqlite in CI). Provide ANON_POSTGRES_DSN to test against real Postgres locally.
- End-to-end tests under tests/e2e cover preprocessing CLI anonymization (--anonymize-en).
