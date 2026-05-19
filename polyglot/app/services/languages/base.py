"""NLP provider interface.

Every language plugs in here. Adding Icelandic later = implementing one
subclass and adding it to the registry below. The interface deliberately
mirrors the shape Alif's morphology.py + sentence_validator.py settled on after
100 days, minus the Arabic-specific clitic and root concerns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class ProviderUnavailable(RuntimeError):
    """Raised when a language's NLP toolkit isn't installed. Lets callers
    decide whether to degrade gracefully (skip lemmatization, show raw tokens)
    or surface the missing dependency to the user."""


@dataclass
class Token:
    """One whitespace/punctuation-segmented word in source order."""
    surface: str
    position: int
    is_punctuation: bool = False


@dataclass
class LemmaCandidate:
    """The provider's best guess for a surface form's lemma. `alternatives`
    holds runner-up readings — used for LLM disambiguation later, mirroring
    Alif's `out_alternatives` pattern in lemma lookup."""
    lemma: str                 # canonical form, with accents/diacritics
    lemma_bare: str            # normalized for lookup
    pos: str | None = None
    confidence: float = 1.0
    alternatives: list[str] = field(default_factory=list)


@dataclass
class Morphology:
    """Token-level morphological features. Shape is per-language; consumers
    inspect what they need."""
    surface: str
    lemma: str
    pos: str | None = None
    features: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class NLPProvider(Protocol):
    """Interface every language provider implements.

    Implementations may be lazy: don't load heavy models in __init__; load on
    first use so importing the module is cheap.
    """
    code: str            # 'el' / 'grc' / 'la'
    display_name: str    # 'Modern Greek'

    def tokenize(self, text: str) -> list[Token]: ...
    def lemmatize(self, surface: str, context: str | None = None) -> LemmaCandidate: ...
    def analyze(self, surface: str, context: str | None = None) -> Morphology: ...
    def normalize_bare(self, form: str) -> str: ...


# ─── Registry ──────────────────────────────────────────────────────────────
# Lazy-loaded to keep import-time work small. `get_provider` instantiates on
# first call and caches; missing dependencies surface as ProviderUnavailable
# only when the provider is actually requested.

_PROVIDER_FACTORIES: dict[str, callable] = {}
_PROVIDER_CACHE: dict[str, NLPProvider] = {}


def register_provider(code: str, factory):
    """Register a lazy provider factory. `factory()` should return an
    NLPProvider instance (loading models as needed)."""
    _PROVIDER_FACTORIES[code] = factory


def get_provider(code: str) -> NLPProvider:
    if code in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[code]
    factory = _PROVIDER_FACTORIES.get(code)
    if not factory:
        raise ProviderUnavailable(f"No NLP provider registered for language '{code}'")
    provider = factory()
    _PROVIDER_CACHE[code] = provider
    return provider


def available_providers() -> list[str]:
    """Return list of registered language codes (whether their toolkits are
    installed or not — call `get_provider` to find out)."""
    return list(_PROVIDER_FACTORIES.keys())


# ─── Wire up languages ─────────────────────────────────────────────────────
# Imports are inside functions to keep heavy deps out of base.py.

def _make_el():
    from app.services.languages.el import ModernGreekProvider
    return ModernGreekProvider()


def _make_grc():
    from app.services.languages.grc import AncientGreekProvider
    return AncientGreekProvider()


def _make_la():
    from app.services.languages.la import LatinProvider
    return LatinProvider()


register_provider("el", _make_el)
register_provider("grc", _make_grc)
register_provider("la", _make_la)
