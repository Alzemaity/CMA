from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.0-flash-001"
    database_url: str = "sqlite+aiosqlite:///cma.db"
    aqar_base_url: str = "https://sa.aqar.fm"
    scraper_headless: bool = True
    max_listings_per_search: int = 50
    proxy_server: str = ""
    proxy_username: str = ""
    proxy_password: str = ""
    scraper_max_retries: int = 3

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
