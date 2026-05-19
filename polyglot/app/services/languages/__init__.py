"""Per-language NLP providers and registry.

Each language module exposes a `provider` instance implementing `NLPProvider`
(see base.py). The registry is built lazily so missing optional dependencies
(GR-NLP-TOOLKIT, spaCy + OdyCy model, LatinCy model) don't crash startup —
they raise `ProviderUnavailable` only when actually called.
"""
from app.services.languages.base import (
    NLPProvider,
    ProviderUnavailable,
    Token,
    LemmaCandidate,
    Morphology,
    get_provider,
    available_providers,
)

__all__ = [
    "NLPProvider",
    "ProviderUnavailable",
    "Token",
    "LemmaCandidate",
    "Morphology",
    "get_provider",
    "available_providers",
]
