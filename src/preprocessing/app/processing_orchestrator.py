"""Application use case: orchestrate a document (both variants) through the pipeline.

For ONE :class:`MarkdownDoc` variant, :func:`process_document` runs the deterministic chain

    anonymization -> Step1 (normalize) -> Step2 (segment) [-> Step3 (LLM summary)]

threading the ``document_uid`` / ``content_hash`` produced by Step1 into Step2 and Step3 so
every step operates on the same document variant. Step3 (the EN-only LLM step) runs only
when ``run_step3`` is set.

:func:`process_document_pair` is the document-level entry point: it takes the ORIGINAL
variant (any language) and its ENGLISH variant (produced upstream by ``ensure_english``) and

- runs the original through anonymization -> Step1 -> Step2 (NO Step3), in its own language;
- runs the English variant through anonymization -> Step1 -> Step2 -> Step3;
- binds the single LLM-generated metadata payload to BOTH variants with cross-variant links
  so they stay connected as one logical document (návrh 2.3.6 / 2.3.7).

Vectorization and the vector DB are a separate retrieval layer and out of scope here.

Critical invariant (see CLAUDE.md, invariant 1): anonymization runs FIRST for every variant,
and Step1/Step2/Step3 only ever see the anonymized text. The original / sensitive text never
reaches the LLM -- only the anonymized ENGLISH variant does. This is guaranteed by
construction (Step1 persists anonymized text; Step2/Step3 read solely from Step1 artifacts)
and double-checked by the shared ``anonymization_sanity`` guard.

- ``context_id`` is a variant-scoped, content-derived Token Vault namespace. When the caller
  does not supply one it is derived via :func:`derive_context_id` from the pre-anonymization
  source text + variant, so the original never shares a vault context with its English copy.
- No wiring into the ``mdify`` CLI.

The cross-context anonymizer is injected via :class:`AnonymizerPort` so the domain stays
free of concrete adapters and the use case is trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from src.preprocessing.analysis.step3_summary import (
    Step3Config,
    Step3Inputs,
    bind_variant_metadata,
)
from src.preprocessing.analysis.step3_summary import run_step3 as run_step3_fn
from src.preprocessing.app.markdown_to_step1 import markdown_doc_to_step1_inputs
from src.preprocessing.app.normalize import run_step1
from src.preprocessing.domain.models_markdown import MarkdownDoc
from src.preprocessing.segmenter.step2_segment import Step2Config, Step2Inputs, run_step2
from src.shared.hashing import derive_context_id


@runtime_checkable
class AnonymizerPort(Protocol):
    """Minimal anonymization surface the orchestrator depends on.

    Implemented by ``anonymization.app.services.pii_service.PiiService.anonymize``. The
    return value must expose ``pseudonymized_text`` (anonymized markdown string) and an
    iterable ``mappings``.
    """

    def anonymize(
        self,
        text: str,
        *,
        context_id: str,
        language: str | None = None,
        tenant_id: str | None = None,
    ) -> Any:
        ...


@dataclass
class ProcessingResult:
    """Outcome of running the chain for a single document."""

    status: str  # "ok" | "failed"
    document_uid: str | None = None
    content_hash: str | None = None
    context_id: str | None = None
    variant: str | None = None
    anonymization_mappings: int = 0
    step1: dict[str, Any] | None = None
    step2: dict[str, Any] | None = None
    step3: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)


def _is_english(doc: MarkdownDoc) -> bool:
    if doc.variant == "english":
        return True
    return isinstance(doc.lang, str) and doc.lang.strip().lower() == "en"


def process_document(
    doc: MarkdownDoc,
    anonymizer: AnonymizerPort,
    *,
    run_step3: bool = True,
    context_id: str | None = None,
    tenant_id: str | None = None,
    context: dict[str, Any] | None = None,
    step2_config: Step2Config | None = None,
    step3_config: Step3Config | None = None,
) -> ProcessingResult:
    """Run anonymization -> Step1 -> Step2 [-> Step3] for ONE document variant.

    Args:
        doc: A :class:`MarkdownDoc` variant. Any language is accepted for the Step1/Step2
            chain (normalization and segmentation are language-agnostic); Step3 is EN-only.
        anonymizer: Injected :class:`AnonymizerPort` (e.g. ``PiiService``). The document's
            own language (``doc.lang``) is forwarded so non-English PII is detected too.
        run_step3: When ``True`` (default) the EN-only LLM step runs and the document must be
            English; for the original variant pass ``run_step3=False`` to stop after Step2.
        context_id: Token-vault namespace for this variant. When ``None`` it is derived via
            :func:`derive_context_id` from the pre-anonymization source text + variant.
        tenant_id: Optional tenant scope forwarded to the anonymizer.
        context: Optional telemetry context (e.g. ``{"run_id": ...}``) threaded into steps.
        step2_config: Optional segmentation config.
        step3_config: Optional summarization config.

    Returns:
        A :class:`ProcessingResult`. On failure the chain is short-circuited and the failing
        step's errors are surfaced.
    """
    variant_tag = "en" if _is_english(doc) else "orig"

    # Guard: Step3 is the EN-only LLM step; refuse to run it on a non-English variant.
    if run_step3 and not _is_english(doc):
        return ProcessingResult(
            status="failed",
            variant=variant_tag,
            errors=[{"code": "step3_requires_english", "variant": doc.variant, "lang": doc.lang}],
        )

    # Variant-scoped vault namespace, derived from the PRE-anonymization source text so it
    # is available now (doc_uid is only minted post-anonymization in Step1).
    if context_id is None:
        context_id = derive_context_id(doc.text_md, doc.variant or variant_tag)

    # Record the vault context in the per-step telemetry so Step1 persists it alongside the
    # doc_uid (links outputs/artifacts/{doc_uid} back to the Token Vault entry).
    ctx = {**(context or {}), "context_id": context_id}

    # Step 0 -- anonymize FIRST (invariant 1: no PII downstream / to the LLM). The variant's
    # own language drives detection (e.g. "sk"/"de" for the original).
    anon = anonymizer.anonymize(
        doc.text_md,
        context_id=context_id,
        language=(doc.lang or "en"),
        tenant_id=tenant_id,
    )
    anon_doc = doc.copy_with(text_md=anon.pseudonymized_text)
    mappings_count = len(list(anon.mappings))

    # Step 1 -- normalize the ANONYMIZED text and mint the stable identifiers.
    s1 = run_step1(markdown_doc_to_step1_inputs(anon_doc, context=ctx))
    if s1.get("status") != "ok":
        return ProcessingResult(
            status="failed",
            context_id=context_id,
            variant=variant_tag,
            anonymization_mappings=mappings_count,
            step1=s1,
            errors=list(s1.get("errors") or [{"code": "step1_failed"}]),
        )
    uid = s1["document_uid"]
    chash = s1["content_hash"]

    # Step 2 -- structure-aware segmentation over the same document.
    s2 = run_step2(
        Step2Inputs(
            document_uid=uid,
            content_hash=chash,
            context=ctx,
            config=step2_config,
        )
    )
    if s2.get("status") != "ok":
        return ProcessingResult(
            status="failed",
            document_uid=uid,
            content_hash=chash,
            context_id=context_id,
            variant=variant_tag,
            anonymization_mappings=mappings_count,
            step1=s1,
            step2=s2,
            errors=list(s2.get("errors") or [{"code": "step2_failed"}]),
        )

    # Step 3 -- LLM summary/keywords over the anonymized EN text (only when requested).
    s3: dict[str, Any] | None = None
    if run_step3:
        s3 = run_step3_fn(
            Step3Inputs(
                document_uid=uid,
                content_hash=chash,
                context=ctx,
                config=step3_config,
            )
        )
        if s3.get("status") != "ok":
            return ProcessingResult(
                status="failed",
                document_uid=uid,
                content_hash=chash,
                context_id=context_id,
                variant=variant_tag,
                anonymization_mappings=mappings_count,
                step1=s1,
                step2=s2,
                step3=s3,
                errors=list(s3.get("errors") or [{"code": "step3_failed"}]),
            )

    return ProcessingResult(
        status="ok",
        document_uid=uid,
        content_hash=chash,
        context_id=context_id,
        variant=variant_tag,
        anonymization_mappings=mappings_count,
        step1=s1,
        step2=s2,
        step3=s3,
    )


@dataclass
class PairResult:
    """Outcome of processing both variants of one logical document."""

    status: str  # "ok" | "failed"
    original: ProcessingResult | None = None
    english: ProcessingResult | None = None
    metadata_bound: bool = False
    links: dict[str, str] | None = None  # {"orig": <uid>, "en": <uid>}
    errors: list[dict[str, Any]] = field(default_factory=list)


def process_document_pair(
    original: MarkdownDoc,
    english: MarkdownDoc,
    anonymizer: AnonymizerPort,
    *,
    run_step3: bool = True,
    tenant_id: str | None = None,
    context: dict[str, Any] | None = None,
    step2_config: Step2Config | None = None,
    step3_config: Step3Config | None = None,
) -> PairResult:
    """Process BOTH variants of one document and bind one metadata payload to both.

    - The ``original`` variant (any language) runs anonymization -> Step1 -> Step2 in its own
      language, WITHOUT Step3.
    - The ``english`` variant runs anonymization -> Step1 -> Step2 -> Step3.
    - The single LLM-generated metadata payload (from the English Step3) is bound to BOTH
      variants' ``metadata_merged.json`` with cross-variant links, keeping them connected as
      one logical document (návrh 2.3.6 / 2.3.7).

    Degenerate case: when ``original`` is already English, only one chain runs (the English
    variant) and the payload is bound to that single ``doc_uid`` -- no redundant "orig" tree.

    ``run_step3``: when ``False`` the EN-only LLM step is skipped entirely (offline mode) and
    no ``metadata_merged.json`` binding is written; the chain stops after Step2 for both
    variants. Use it to run the whole pipeline without any OpenAI call.

    ``ensure_english`` is a separate upstream step that produces ``english``; it is not
    invoked here so the orchestrator stays free of translation adapters.
    """

    def _run(doc: MarkdownDoc, *, do_step3: bool) -> ProcessingResult:
        return process_document(
            doc,
            anonymizer,
            run_step3=do_step3,
            tenant_id=tenant_id,
            context=context,
            step2_config=step2_config,
            step3_config=step3_config,
        )

    # Already-English source: a single chain, single doc_uid, metadata bound to it alone.
    if _is_english(original):
        res_en = _run(english, do_step3=run_step3)
        if res_en.status != "ok":
            return PairResult(status="failed", english=res_en, errors=res_en.errors)
        if run_step3:
            bind_variant_metadata(
                english_uid=res_en.document_uid,  # type: ignore[arg-type]
                original_uid=None,
                english_meta=(res_en.step1 or {}).get("canonical_metadata", {}),
                original_meta=None,
                summary=(res_en.step3 or {})["summary_one_sentence"],
                keywords=(res_en.step3 or {})["keywords_top5"],
            )
        return PairResult(
            status="ok",
            english=res_en,
            metadata_bound=run_step3,
            links={"en": res_en.document_uid},  # type: ignore[dict-item]
        )

    # Original (non-English) variant: Step1 + Step2 only, in its own language.
    res_orig = _run(original, do_step3=False)
    if res_orig.status != "ok":
        return PairResult(status="failed", original=res_orig, errors=res_orig.errors)

    # English variant: full chain including Step3 (unless offline).
    res_en = _run(english, do_step3=run_step3)
    if res_en.status != "ok":
        return PairResult(status="failed", original=res_orig, english=res_en, errors=res_en.errors)

    # Offline mode: stop after Step2, no LLM payload to bind.
    if not run_step3:
        return PairResult(
            status="ok",
            original=res_orig,
            english=res_en,
            metadata_bound=False,
            links={"orig": res_orig.document_uid, "en": res_en.document_uid},  # type: ignore[dict-item]
        )

    # Bind the one LLM payload to BOTH variants with preserved cross-links.
    bind_variant_metadata(
        english_uid=res_en.document_uid,  # type: ignore[arg-type]
        original_uid=res_orig.document_uid,
        english_meta=(res_en.step1 or {}).get("canonical_metadata", {}),
        original_meta=(res_orig.step1 or {}).get("canonical_metadata", {}),
        summary=(res_en.step3 or {})["summary_one_sentence"],
        keywords=(res_en.step3 or {})["keywords_top5"],
    )
    return PairResult(
        status="ok",
        original=res_orig,
        english=res_en,
        metadata_bound=True,
        links={"orig": res_orig.document_uid, "en": res_en.document_uid},  # type: ignore[dict-item]
    )
