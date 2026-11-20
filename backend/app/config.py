from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "ReleaseLens AI"
    database_url: str = "sqlite:///./releaselens.db"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 60 * 24
    encryption_key: str = ""
    cors_origins: str = "http://localhost:5173"

    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_max_retries: int = 3

    github_token: str = ""
    max_commits: int = 100
    max_diff_chars_per_change: int = 3000
    max_breaking_files: int = 40

    chroma_path: str = "./chroma_data"
    embedding_model: str = "all-MiniLM-L6-v2"
    risk_model_path: str = ""

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
