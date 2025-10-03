# Návrh: Jednotný metadátový súbor pre každý originálny dokument (RAG-ready)

Cieľ: mať jednu pravdivú (authoritative) metadátovú stopu na dokument, ktorá robustne spája originál, jeho anonymizované a preložené verzie, LLM sumarizácie/keywords, a pripravuje dáta na RAG ingest do Qdrant.

—

1) Ciele a obmedzenia
- Jedna pravda: pre každý originálny súbor existuje jedno „meta“ (sidecar) JSON.
- Stabilné identifikátory: dokument_uid a obsahové hash-e pre verziovanie.
- Prepojenie všetkých derivátov: originál ↔ anonymizované ↔ preklady ↔ LLM artefakty (summary/keywords) ↔ vektorové chunky.
- RAG‑ready: meta obsahuje všetko potrebné na bezstratovú ingestiu do Qdrant (point IDs, chunking parametre, embedding info, väzby na súbory).
- Kompatibilita s existujúcim projektom: rešpektovať outputs/artifacts/{document_uid}/stepX a GUI/CLI nástroje.

—

2) Kde budú metadáta a ako sa to volá
- Umiestnenie autoritatívneho meta súboru: outputs/artifacts/{document_uid}/meta.json
- Jeden meta.json na jeden originálny dokument.
- Prehľadná nadstavba: vedľa už existujúcich step1/step2/step3 adresárov; meta.json agreguje odkazy a fakty.

Adresárová štruktúra (zjednodušené)
- inputs/...                      (ľubovoľný zdroj)
- outputs/
  - artifacts/
    - {document_uid}/
      - step1/...                 (konverzia/normalizácia)
      - step2/...                 (segmentácia)
      - step3/
        - result.json             (existujúce summary/keywords z pipeline)
      - files/
        - original/{rel_path}     (voliteľné: kópia alebo symlink na originál)
        - anonymized/{rel_path}   (anonymizované MD)
        - translations/{lang}/{rel_path}
      - meta.json                 (tento návrh: autoritatívny súhrn)
- outputs/logs/...                (diagnostika)

Pozn.: Ak nechcete kopírovať dáta, priečinok files/ môže držať relatívne cesty (symlink alebo referenčné path) do pôvodného stromu.

—

3) Stabilné identifikátory a hashovanie
- document_uid: stabilný identifikátor dokumentu odvodený z cesty a obsahu (viď src/shared/hashing.py, src/shared/ids.py). Použijeme už existujúce utily:
  - content_hash: SHA256 textu (alebo normalizovaného MD) – verzovanie.
  - point_id pre chunky: použime shared.ids (napr. stable Qdrant point IDs) tak, aby boli deterministické na základe {document_uid, chunk_index, content_hash}.
- Všetky deriváty (anonymizované, preklady, LLM výstupy) referencujú parent.document_uid a majú vlastné content_hash.

—

4) Jednotný meta.json – návrh schémy
Pozn.: Schéma je „forgiving“ (polia voliteľné), no odporúčame dodržiavať kľúče nižšie.

{
  "schema_version": "1.0",
  "document_uid": "<stable-id>",
  "source": {
    "original_path": "<abs|rel path to original>",
    "media_type": "text/markdown|application/pdf|...",
    "detected_lang": "sk",
    "content_hash": "sha256:...",
    "size_bytes": 123456,
    "mtime": "2025-09-27T12:34:56+02:00"
  },
  "derivatives": {
    "anonymized": {
      "path": "<path k anonymizovanému MD>",
      "content_hash": "sha256:...",
      "created_at": "...",
      "pii": {
        "detector": "presidio|regex|...",
        "language": "sk",
        "entity_counts": {"EMAIL": 2, "PHONE": 1},
        "context_id": "<vault context>"
      }
    },
    "translations": [
      {
        "lang": "en",
        "path": "<path k prekladu>",
        "source": "anonymized|original",
        "translator": "marian-nmt|nllb|...",
        "content_hash": "sha256:...",
        "created_at": "..."
      }
      // ...ďalšie jazyky
    ]
  },
  "llm": {
    "summaries": [
      {
        "scope": "document|segment",
        "source_variant": "anonymized|translation:en",
        "model": "gpt-4o-mini",
        "policy_version": "step3-v1",
        "summary": "one-sentence...",
        "keywords": ["a","b","c","d","e"],
        "raw_path_summary": "<path>.summary.txt",      // ak existuje sidecar
        "raw_path_keywords": "<path>.keywords.json",   // ak existuje sidecar
        "created_at": "...",
        "cost": {"prompt_tokens": 0, "completion_tokens": 0, "currency": "USD", "value": 0.0001}
      }
    ]
  },
  "vectorization": {
    "chunking": {
      "policy": "markdown-headings|fixed-tokens",
      "params": {"max_tokens": 512, "overlap": 64, "model": "tiktoken:gpt-4o"}
    },
    "embedding": {
      "model": "text-embedding-3-large",
      "dim": 3072,
      "provider": "openai"
    },
    "qdrant": {
      "collection": "habithon_docs_v1",
      "point_id_prefix": "<derived from document_uid>",
      "payload_template_version": "1.0"
    },
    "chunks": [
      {
        "index": 0,
        "point_id": "<stable point id>",
        "source_variant": "anonymized|translation:en",
        "text_path": "<path k textu z ktorého sme embedovali>",
        "text_sha256": "sha256:...",
        "start_char": 0,
        "end_char": 1023,
        "token_count": 450,
        "embedding_sha256": "sha256:<digest of vector bytes>",
        "norm": 1.0
      }
      // ...
    ]
  },
  "relations": {
    "parents": [],
    "children": [
      {"type": "translation", "lang": "en", "path": "..."},
      {"type": "llm_summary", "path": "..."}
    ]
  },
  "audit": {
    "created_at": "...",
    "created_by": "gui|cli|pipeline",
    "updated_at": "...",
    "notes": "..."
  }
}

Kľúčové zásady:
- source.original_path drží referenciu na originál (absolútne alebo repo-relatívne). Ak ho migrujete, meta.json ostáva pravdou; ďalšie worker-y môžu cesty zreparovať.
- derivatives.anonymized/translations sú „variácie“ textu; každá má vlastný content_hash a metadata o pôvode a nástroji.
- llm.summaries je zoznam – môžeme mať viac súhrnov (iné modely, iný scope). Udržujeme aj odkazy na existujúce sidecary, aby sme boli spätne kompatibilní (viď GUI summarize_folder_run).
- vectorization.chunks je voliteľné; ak neleží embedding lokálne, stačí mať metadáta (point_id, text_sha256). Samotný vektor môže žiť len v Qdrante.

—

5) Väzba na aktuálny kód a výstupy
- GUI summarize_folder_run (src/gui/services/facade.py) už dnes ukladá:
  - <file>.summary.txt
  - <file>.keywords.json
  vedľa anonymizovaných .md. Tieto cesty vložíme do llm.summaries[...].raw_path_*.
- Step 3 pipeline už dnes zapisuje outputs/artifacts/{uid}/step3/result.json. Z toho načítame summary/keywords do llm.summaries a zároveň ponecháme referenciu na step3/result.json pre audit.
- outputs/logs/ obsahuje audity (pre GUI aj CLI). Do audit.notes môžeme vložiť odkaz na posledný log súbor.
- shared/ids.py a shared/hashing.py: využijeme na výpočet document_uid, content_hash, a deterministických point_id pre Qdrant.

—

6) Ingest do Qdrant – kontrakt
- Vstup: meta.json (1 dokument).
- Výstup: upsert do Qdrant.collection podľa vectorization.qdrant.collection.
- Pre každý chunks[i]:
  - point_id: použite predpis zo shared.ids (napr. hash(document_uid + variant + index) -> int/str).
  - payload: uložte minimálne:
    - document_uid, source_variant, lang, rel_path, content_hash, chunk_index, token_count,
    - summary_short (ak k dispozícii), keywords (limitované), routing/tenant/id podľa potreby.
  - vector: embedding vyrobený z textu chunku podľa embedding.model. Ak embedding nie je v meta.json, vygenerujte on-the-fly a uložte do Qdrant.

—

7) Migračný plán (bez prerábky fungujúceho)
- Fáza A: Generátor meta.json (batch script)
  - Prejde outputs/artifacts/*/{stepX} a vedľajšie sidecary pri anonymizovaných súboroch.
  - Postupne zostaví meta.json podľa schémy vyššie.
- Fáza B: Vstrekovanie do GUI/CLI
  - Po anonymizácii/prechode step3 doplniť/aktualizovať meta.json (idempotentne podľa content_hash).
- Fáza C: Ingestor do Qdrant
  - Nový modul src/vector_store/ingest.py: načíta meta.json, chunkuje podľa policy, embeduje a upsertuje do Qdrant.
- Fáza D: Validácia a monitoring
  - Krátke unit/integration testy: deterministické point_id, konzistentná schéma, round‑trip s Qdrant.

—

8) Názvoslovie súborov (kompatibilné s dneškom)
- Originál: mydoc.md
- Anonymizované: mydoc.anonymized.md
- Preklad EN: mydoc_en.md (alebo en/mydoc.md v translations/)
- LLM sidecary: mydoc.anonymized.summary.txt, mydoc.anonymized.keywords.json (vedľa zdroja, ktorý sumarizujeme)
- Autoritatívne meta: outputs/artifacts/{document_uid}/meta.json

—

9) ASCII diagramy

Tok spracovania
[originál súbor] ──convert/normalize──▶ step1 ──segment──▶ step2 ──LLM summary──▶ step3
       │                                                     │
       ├──anonymize──────────────────────────────────────────┘
       │
       ├──translate (en, de, ...)
       │
       └──► meta.json (agreguje všetko vyššie + chunking/embedding info)

Ingest do Qdrant
meta.json ──► chunker ──► embedder ──► {point_id, vector, payload} ──► Qdrant.collection

Väzby v meta.json
source.original_path ↔ derivatives.anonymized.path ↔ derivatives.translations[*].path ↔ llm.summaries[*].raw_path_* ↔ vectorization.chunks[*]

—

10) Minimálne kontrakty (pre implementátorov)
- Inputs
  - Existujúce artifacts (step1/2/3) + anonymized/translation súbory + sidecary z GUI summarize.
- Outputs
  - outputs/artifacts/{uid}/meta.json
  - voliteľne: vektorové chunky priamo do Qdrant (žiadne veľké binárky v repo).
- Success criteria
  - Pre ľubovoľný súbor dokážem nájsť jeho uid, všetky deriváty a RAG chunky cez samotný meta.json.
  - Ingest do Qdrant je deterministický (rovnaké point_id pre rovnaký chunk a obsah).
- Error modes
  - Chýbajúce súbory: meta.json drží stale cesty, no ingest preskočí s diagnostikou.
  - Neznáma verzia schémy: ingest varuje, ale pre polia ktoré rozumie pokračuje.

—

11) Poznámky k implementácii
- Nepresúvať existujúce súbory; začať tvorbou meta.json a postupne ho obohacovať.
- Preferovať relatívne cesty voči repo root (alebo voči outputs/artifacts/{uid}), aby boli prenositeľné.
- Do payloadu v Qdrant neposielať priveľa textu; stačí chunk_text a kľúčové metadáta. Dlhé súhrny držať mimo (alebo skrátené summary_short v payload-e, plné v meta.json/sidecaroch).
- Rešpektovať existujúce utily v shared/ids.py a shared/hashing.py pre deterministiku.

—

12) Next steps (pragmaticky)
- Pridať skript scripts/build_meta.py: prejde artifacts a vytvorí/aktualizuje meta.json podľa schémy (Fáza A).
- Pridať src/vector_store/ingest.py s funkciami: make_chunks(text, policy), embed(texts, model), upsert_qdrant(chunks, config).
- Z GUI Summary Tab po dobehnutí summarize_folder_run zavolať „meta updater“, ktorý doplní llm.summaries do meta.json.
- Doplniť krátky README k ingest pipeline a ukážkový config (qdrant_url, collection).

Koniec návrhu.
