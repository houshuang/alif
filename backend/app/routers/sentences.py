"""Sentence generation and validation API endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.sentence_generator import (
    GeneratedSentence,
    GenerationError,
    generate_validated_sentence,
)
from app.services.sentence_validator import (
    ValidationResult,
    strip_diacritics,
    validate_sentence,
)

router = APIRouter(prefix="/api/sentences", tags=["sentences"])


class GenerateRequest(BaseModel):
    target_arabic: str
    target_translation: str
    known_words: list[dict[str, str]]
    difficulty_hint: str = "beginner"


class ValidateRequest(BaseModel):
    arabic_text: str
    target_bare: str
    known_bare_forms: list[str]


class ValidateResponse(BaseModel):
    valid: bool
    target_found: bool
    unknown_words: list[str]
    known_words: list[str]
    function_words: list[str]
    issues: list[str]


@router.post("/generate", response_model=GeneratedSentence)
def generate_sentence_endpoint(req: GenerateRequest):
    """Generate a validated sentence for a target word."""
    try:
        return generate_validated_sentence(
            target_arabic=req.target_arabic,
            target_translation=req.target_translation,
            known_words=req.known_words,
            difficulty_hint=req.difficulty_hint,
        )
    except GenerationError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/validate", response_model=ValidateResponse)
def validate_sentence_endpoint(req: ValidateRequest):
    """Validate an Arabic sentence against known vocabulary."""
    result = validate_sentence(
        arabic_text=req.arabic_text,
        target_bare=req.target_bare,
        known_bare_forms=set(req.known_bare_forms),
    )
    return ValidateResponse(
        valid=result.valid,
        target_found=result.target_found,
        unknown_words=result.unknown_words,
        known_words=result.known_words,
        function_words=result.function_words,
        issues=result.issues,
    )
