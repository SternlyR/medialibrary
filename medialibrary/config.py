"""Application configuration loaded from environment variables."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tmdb_api_key: str = ""
    omdb_api_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./medialibrary.db"

    # TMDB base URLs
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base: str = "https://image.tmdb.org/t/p/w500"

    # Bluray.com
    bluray_base_url: str = "https://www.blu-ray.com"

    # UPCitemdb
    upcitemdb_base_url: str = "https://api.upcitemdb.com/prod/trial/lookup"

    # Best Buy Open API (second-tier UPC fallback)
    bestbuy_api_key: str = ""
    bestbuy_base_url: str = "https://api.bestbuy.com/v1"

    # Letterboxd (no API key required — public profile scraping)
    letterboxd_username: str = ""


settings = Settings()
