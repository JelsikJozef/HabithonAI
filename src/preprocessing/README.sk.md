# Habithon Preprocessing — Konverzia ➜ Markdown a anglická varianta (offline)

Tento modul prevádza rôzne zdrojové dokumenty na čistý Markdown a môže vytvoriť deterministickú, offline anglickú verziu, ktorá zachováva štruktúru Markdown. Je navrhnutý pre dávkové spracovanie, reprodukovateľnosť a bez sieťových volaní za behu.

- Fáza 1: convert — Parsovanie podporovaných formátov a zápis Markdown (.md) plus príslušné súbory (assets).
- Fáza 2: translate — Vytvorenie anglickej varianty z Markdown pomocou offline MT enginu. Zachováva sa rozloženie, kódové bloky, odkazy, ciele obrázkov a iné nerevízne prvky.

Obe fázy riadi jediný CLI nástroj **mdify** a je možné ich spustiť ako:
- iba convert (convert-only)
- convert + translate
- iba translate (translate-only)


## Podporovaný pracovný tok

- convert: priečinok so zdrojovými súbormi ➜ priečinok s Markdown, zachovávajúci adresárovú štruktúru
  - Formáty (offline): DOCX, XLSX, PDF (pdfminer.six), MSG
  - Assets (obrázky, atď.) sa ukladajú vedľa `.md` v podsložke `assets/`
- translate: Markdown ➜ Markdown v angličtine, štruktúru zachovávajúc
  - Detekcia jazyka: fastText `lid.176.bin`
  - Enginy: `ct2_nllb` (CTranslate2 + NLLB) a `marian_opus` (Transformers MarianMT)
  - Voliteľná integrácia glosára a cachovanie prekladov

Príklady použitia:
- iba convert: normalizované Markdown pre ďalšie spracovanie
- convert + translate: originálne Markdown + anglická varianta v jednom kroku
- iba translate: existujúci strom Markdown ➜ pridaná anglická varianta


## Anglická varianta (translate)

**Prečo**: Unifikácia do angličtiny pred anonymizáciou a ďalším obohatením zjednodušuje workflow.
**Ako**:
- Detekcia jazyka: fastText `lid.176.bin` (lokálny súbor).
- Enginy (offline):
  - `ct2_nllb`: CTranslate2 s NLLB modelom; SentencePiece tokenizér.
  - `marian_opus`: Transformers MarianMT modely (CPU). Modely musia byť lokálne v cache alebo na ceste.
- Markdown segmenter: Extrahuje len textové segmenty, zachováva kódové bloky, inline kód, odkazy, tabulky.
- Glosár (voliteľný): pred/po/both substitúcie plain-text segmentov.
- Cache (voliteľná): cachovanie per-segment s fingerprintom enginu.

Výstup:
- Anglická varianta sa ukladá do podsložky `en/` v rámci výstupného adresára.


## CLI — mdify

Jediný príkaz riadi obe fázy.

**Convert iba:**
```bash
mdify --src ./in --out ./out \
  --include-ext .pdf .docx .xlsx .msg \
  --skip-existing --workers 4 --report ./out/run.json
```

**Convert + translate:**
```bash
mdify --src ./in --out ./out \
  --make-english \
  --translator ct2_nllb \
  --workers 4 --report ./out/run.json
```

**Translate-only:**
```bash
mdify --src ./out --out ./out \
  --translate-only --make-english \
  --translator marian_opus --workers 2
```

**Lepšie smerovanie pri zmiešaných jazykoch (nové nastavenia LangID):**
```bash
# Obmedzte kandidátov a zväčšite analyzované okno pre detekciu jazyka
mdify --src ./in --out ./out \
  --make-english --translator ct2_nllb \
  --lang-candidates sk,de,cs,en --lang-max-chars 20000 --lang-min-chars 80
```

**Hlavné príznaky:**
- **Scanning & selection (convert)**: `--recurse`, `--include-ext`, `--exclude-glob`, `--max-files`
- **Output & writing**: `--overwrite`/`--skip-existing`, `--assets-subdir`, `--write-meta` (none|sidecar|inline)
- **Performance**: `--workers`, `--on-error` (skip|fail), `--dry-run`
- **Translation**: `--make-english`, `--translator` (ct2_nllb|marian_opus), `--translate-only`, `--tgt-lang`,
  `--lang-candidates`, `--lang-max-chars` (nové), `--lang-min-chars` (nové),
  `--segment-max-chars`, `--translate-link-label`, `--translate-alt-text`, `--translate-table-cells`, `--collapse-softbreaks`,
  `--glossary-id`, `--glossary-mode`, `--mt-cache`, `--cache-disabled`, `--translate-on-error`
- **Diagnostics**: `--log-level`, `--progress`, `--report`, `--log-file`
- **Kompatibilita**: `--normalize-eol`, `--strict`

Kódy návratu:
- `0`: úspech (alebo iba skip bez chýb)
- `1`: dokončené s niektorými chybami
- `2`: chyba v argumentoch/konfigurácii
- `3`: fatálna chyba pri inicializácii


## Nastavenia (settings_translation.py)

Jednotlivé sekcie konfigurácie:
- `engine`: ct2_nllb alebo marian_opus
- `ct2_nllb`: `model_dir`, `compute_type`, `device`, `num_threads`, `src_lang_map`, `tgt_lang_code`
- `marian`: `models` (mapovanie src_lang → model id/cesta), `device`, `dtype`, `local_files_only`, `hf_cache_dir`
- `decoding`: beam size, length_penalty, batch sizes pre oba enginy
- `segmenter`: `segment_max_chars`, `translate_alt_text`, `translate_link_label`, `translate_table_cells`, `preserve_whitespace`, `collapse_softbreaks`, `language_hint`
- `langid`: impl fasttext, `model_path`, `max_chars`, `min_chars`, `candidates`
- `glossary`: `enabled`, `mode`, `glossary_id`, `db_path`, `regex_enabled`
- `cache`: `enabled`, `backend`, `root_path`, limity, `namespace`
- `io`: `overwrite`, `dry_run`, `workers`, `on_error`
- `policy`: `network_access=false`, `allow_online_model_download=false`
- `logging/telemetry/schema`: diagnostika a fingerprinty

Funkcie: `validate_translation_settings(cfg)` → (ok, issues), `capabilities_summary(cfg)` → stručné info.

Env. premenné na prepísanie (príklady):
- `HABITHON_TRANSLATION_ENGINE`, `HABITHON_CT2_MODEL_DIR`, `HABITHON_LANGID_MODEL_PATH`
- `HABITHON_LANGID_MAX_CHARS=20000` (nové), `HABITHON_LANGID_MIN_CHARS=80` (nové)
- `HABITHON_MT_CACHE_PATH`, `HABITHON_IO_WORKERS`, `HABITHON_IO_OVERWRITE`


## Výstupná štruktúra a anglická varianta

- Markdown z convert fázy zachováva štruktúru `--src` pod `--out`.
- Anglická varianta sa vytvorí v `--out/en/<relatívna_cesta>.md`.
- Assets pre anglický súbor sú v sibling podsložke `assets/`.
- Pri `--write-meta=sidecar` vzniká súbor `file.md.meta.json`.


## Offline záruka

- Žiadne sieťové volania za behu; modely musia byť lokálne.
- `ct2_nllb`: CTranslate2 model + SentencePiece musia byť na disku.
- `marian_opus`: MarianMT modely v HF cache alebo lokálne; `local_files_only=true`.
- `fastText`: `lid.176.bin` na disku.
- Cache: SQLite súbor alebo FS adresár.


## Príklady

**Basic conversion:**
```bash
mdify --src ./resources/samples --out ./outputs/md --workers 2 --skip-existing
```

**Convert + translate (ct2_nllb):**
```bash
mdify --src ./resources/samples --out ./outputs/md \
  --make-english --translator ct2_nllb \
  --workers 4 --report ./outputs/cli_report.json
```

**Translate-only (marian_opus):**
```bash
mdify --src ./outputs/md --out ./outputs/md \
  --translate-only --make-english \
  --translator marian_opus --workers 2 --overwrite
```


## Inštalácia

```bash
pip install ./src/preprocessing             # base (convert-only)
pip install './src/preprocessing[parsers]' # add document parsers
pip install './src/preprocessing[ct2,langid]'  # CTranslate2 + SentencePiece + fastText
pip install './src/preprocessing[marian,langid]' # Transformers + torch + fastText
pip install './src/preprocessing[all]'       # všetko
```


## Architektúra (stručne)

- **presentation**: CLI/API, argumenty, reporting
- **app**: orchestrácia convert a translate flows
- **adapters**: parsy, langid, translate, cache, segmenter
- **domain**: dátové modely, porty (rozhrania), typy chýb


## Závislosti podľa schopností

- Convert: `pdfminer.six`, `python-docx`, `extract_msg`
- LangID: `fasttext`
- Translate (ct2_nllb): `ctranslate2`, `sentencepiece`
- Translate (marian_opus): `transformers`, `torch`


## Riešenie problémov

- "No parser for extension …": nainštalujte extras `[parsers]`.
- Chyby nastavení prekladu: spustite s `--make-english` a skontrolujte logy, overte existenciu modelov a `lid.176.bin`.
- Marian offline: naplňte HF cache alebo nastavte `HABITHON_MARIAN_CACHE_DIR`.
- EOL: predvolené LF, zmeňte `--normalize-eol keep`.


## Licence

MIT
