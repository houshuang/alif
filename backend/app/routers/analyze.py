from fastapi import APIRouter

from app.schemas import (
    AnalyzeWordIn, AnalyzeWordOut,
    AnalyzeSentenceIn, AnalyzeSentenceOut,
)
from app.services.morphology import analyze_word, analyze_sentence

router = APIRouter(prefix="/api/analyze", tags=["analyze"])


@router.post("/word", response_model=AnalyzeWordOut)
def analyze_word_endpoint(body: AnalyzeWordIn):
    return analyze_word(body.word)


@router.post("/sentence", response_model=AnalyzeSentenceOut)
def analyze_sentence_endpoint(body: AnalyzeSentenceIn):
    return analyze_sentence(body.sentence)
