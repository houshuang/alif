import os
from pathlib import Path
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str = f"sqlite:///{BASE_DIR / 'alif.db'}"
    gemini_key: str = ""
    openai_key: str = ""
    anthropic_api_key: str = ""
    anthropic_key: str = ""
    elevenlabs_api_key: str = ""
    log_dir: Path = BASE_DIR / "data" / "logs"

    model_config = {"env_file": [BASE_DIR / ".env", BASE_DIR.parent / ".env"], "extra": "ignore"}


settings = Settings()
