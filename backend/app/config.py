"""Application settings, loaded from environment / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # psycopg3 sync driver. Local dev points at the Compose Postgres on 5433.
    database_url: str = "postgresql+psycopg://fleet:fleet@localhost:5433/fleet"
    # Comma-separated list of allowed CORS origins (the Vite dev server).
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
