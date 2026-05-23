from app.services.llm_cli import resolve_model, strict_response_schema


def test_strict_response_schema_requires_every_property_and_preserves_optional_as_null():
    schema = {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "reason": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "note": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["id"],
    }

    out = strict_response_schema(schema)

    assert out["additionalProperties"] is False
    assert out["required"] == ["id", "reason", "items"]
    assert out["properties"]["reason"]["type"] == ["string", "null"]
    assert out["properties"]["items"]["type"] == ["array", "null"]
    nested = out["properties"]["items"]["items"]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["name", "note"]
    assert nested["properties"]["note"]["type"] == ["string", "null"]


def test_resolve_model_maps_claude_aliases_to_codex_when_configured(monkeypatch):
    monkeypatch.setenv("POLYGLOT_LLM_PROVIDER", "codex")
    monkeypatch.setenv("POLYGLOT_CODEX_MODEL", "gpt-5.5")
    monkeypatch.setenv("POLYGLOT_CODEX_FAST_MODEL", "gpt-5.5-mini")

    assert resolve_model("sonnet", {"sonnet": "claude-sonnet"}) == "gpt-5.5"
    assert resolve_model("claude-sonnet-4-5-20250929") == "gpt-5.5"
    assert resolve_model("haiku", {"haiku": "claude-haiku"}) == "gpt-5.5-mini"
    assert resolve_model("gpt-5.4") == "gpt-5.4"
