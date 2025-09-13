from fastapi import FastAPI
from pydantic import BaseModel

from ..adapters.container import build_default
from ..app.denomize import deanonymize
from ..app.detect import detect_all
from ..app.pseudonymize import pseudonymize

app = FastAPI(title="Anonymization API", version="0.1.0")

_detectors, _vault = build_default()


class PiiEntityModel(BaseModel):
    type: str
    start: int
    end: int
    value: str
    score: float | None = None
    detector: str | None = None


class TokenMappingModel(BaseModel):
    token: str
    value: str
    type: str


class DetectRequest(BaseModel):
    text: str
    language: str | None = None


class DetectResponse(BaseModel):
    text: str
    entities: list[PiiEntityModel]


class PseudonymizeRequest(BaseModel):
    text: str
    context_id: str
    language: str | None = None


class PseudonymizeResponse(BaseModel):
    original_text: str
    pseudonymized_text: str
    mappings: list[TokenMappingModel]


class DeAnonymizeRequest(BaseModel):
    text: str
    context_id: str


class DeAnonymizeResponse(BaseModel):
    anonymized_text: str
    restored_text: str
    mappings_used: list[TokenMappingModel]


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/detect", response_model=DetectResponse)
async def detect(req: DetectRequest):
    result = detect_all(req.text, _detectors, language=req.language)
    return DetectResponse(
        text=result.text,
        entities=[PiiEntityModel(**e.__dict__) for e in result.entities],
    )


@app.post("/pseudonymize", response_model=PseudonymizeResponse)
async def do_pseudonymize(req: PseudonymizeRequest):
    result = pseudonymize(req.text, _detectors, _vault, req.context_id, language=req.language)
    return PseudonymizeResponse(
        original_text=result.original_text,
        pseudonymized_text=result.pseudonymized_text,
        mappings=[TokenMappingModel(**m.__dict__) for m in result.mappings],
    )


@app.post("/deanonymize", response_model=DeAnonymizeResponse)
async def do_deanonymize(req: DeAnonymizeRequest):
    result = deanonymize(req.text, _vault, req.context_id)
    return DeAnonymizeResponse(
        anonymized_text=result.anonymized_text,
        restored_text=result.restored_text,
        mappings_used=[TokenMappingModel(**m.__dict__) for m in result.mappings_used],
    )
