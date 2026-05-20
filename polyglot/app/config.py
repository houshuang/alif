from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str = f"sqlite:///{BASE_DIR / 'polyglot.db'}"
    port: int = 3001
    anthropic_api_key: str = ""
    anthropic_key: str = ""
    openai_key: str = ""
    gemini_key: str = ""
    log_dir: Path = BASE_DIR / "data" / "logs"

    model_config = {
        # pydantic-settings precedence is "last file wins" — polyglot's local
        # .env must load LAST so its DATABASE_URL override beats the shared
        # alif .env that supplies API keys but points at alif.db. Reversing
        # this order silently contaminated alif.db on the first cron + CLI
        # runs (2026-05-20) when systemd-set env vars weren't present.
        "env_file": [BASE_DIR.parent / ".env", BASE_DIR / ".env"],
        "extra": "ignore",
    }


settings = Settings()
