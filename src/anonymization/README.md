# Anonymization

PII detection, pseudonymization, and de-anonymization toolkit.

- Multiple detectors: regex (built-in), optional Microsoft Presidio
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

## Workflow

```mermaid
flowchart TD
  A[Input text] --> B{Detectors}
  B -->|regex| C[RegexDetector]
  B -->|presidio| D[PresidioDetector]
  C --> E[Merge overlapping entities]
  D --> E
  E --> F[Pseudonymize<br/>(replace spans with tokens)]
  F --> G[Token Vault<br/>(File or Postgres)<br/>Save token ↔ value]
  F --> H[Anonymized text with tokens]
  H --> I[LLM/API]
  I --> J[Response with tokens]
  J --> K[De-anonymize<br/>(replace tokens via vault)]
  K --> L[Restored text]
  F --> M[Crypto<br/>(mask/hash/tokenize)]
  M --> N[Key Manager<br/>active_kid + tenant-scoped keys]
```

## CLI

- After install, run anonymization-cli:

  - anonymization-cli detect "Email alice@example.com"
  - anonymization-cli pseudonymize "Call +1 555-123-4567" --context demo
  - anonymization-cli deanonymize "{{PII:PHONE:1:xxxx}}" --context demo

Outputs JSON.

## API server

- With [api] extra installed:

  - uvicorn anonymization.presentation.api:app --app-dir src --reload

Endpoints:

- GET /health
- POST /detect {text, language?}
- POST /pseudonymize {text, context_id, language?}
- POST /deanonymize {text, context_id}

## Configuration

- Detectors:
  - ANON_DETECTORS: comma list (regex,presidio). Default: regex.
  - ANON_PRESIDIO_LANGS: lang:model pairs, e.g. en:en_core_web_sm,es:es_core_news_sm

- Token vault (file-based default):
  - ANON_VAULT_DIR: directory path (default .anonymization_vault)

- Keys (for crypto hashing/tokenization):
  - Set ANON_KEYSET as JSON:
    {"active_kid":"kidA","keys":{"kidA":"<base64-32B>","kidB":"<base64-32B>"}}
  - Rotation: switch active_kid; old keys remain available via get_all_hmac_keys.

## Adapters

- Detectors
  - RegexDetector: emails, phones, IPv4, credit cards.
  - PresidioDetector: uses Microsoft Presidio AnalyzerEngine (optional extra).

- Token vaults
  - FileTokenVault: JSON per context on disk.
  - PostgresTokenVault: reversible mapping in PostgreSQL.

- Crypto
  - crypto.Crypto: mask/hash/tokenize using tenant-scoped HMAC subkeys from key_manager.
  - key_manager: loads a versioned keyset, derives per-tenant subkeys deterministically.

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
- Tokens look like {{PII:TYPE:N:abcd1234}} in pseudonymization flow; the vault stores token↔value pairs.
- Crypto.hash returns h:<kid>:<hex> and Crypto.tokenize returns t:<kid>:<id> for stable, non-reversible identifiers.

## Tests

- Unit tests are provided under tests/ (and packages/anonymization/tests for adapter specifics).
- Run with:
  - python -m unittest discover -v

## License

- Proprietary/internal (adjust as needed).
