"""
Configuration management for Video Analyzer Web App.
Supports both environment variables and runtime configuration via API.
"""
import os
from typing import Optional
from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env file if exists
load_dotenv()


class Settings(BaseModel):
    """Application settings with defaults from environment variables."""

    # Analyzer settings
    analyzer_type: str = "gemini"  # 'gemini' or 'openrouter'

    # Google Cloud / Gemini settings
    google_cloud_project_id: Optional[str] = None
    vertex_ai_location: str = "global"
    gemini_model_name: str = "gemini-2.0-flash"
    gemini_api_key: Optional[str] = None  # For direct Gemini API (not Vertex AI)

    # OpenRouter settings
    openrouter_api_key: Optional[str] = None
    openrouter_model_name: str = "google/gemini-2.0-flash-exp:free"

    # Processing settings
    chunk_duration_minutes: int = 10
    max_upload_size_mb: int = 500

    # Paths
    upload_directory: str = "uploads"
    output_directory: str = "outputs"
    temp_directory: str = "temporary"
    prompts_directory: str = "prompts"


class ConfigManager:
    """
    Manages runtime configuration.
    Allows updating settings via web interface without restart.
    """

    def __init__(self):
        self._settings = self._load_from_env()
        self._runtime_api_keys: dict = {}

    def _load_from_env(self) -> Settings:
        """Load settings from environment variables."""
        return Settings(
            analyzer_type=os.getenv("ANALYZER_TYPE", "gemini"),
            google_cloud_project_id=os.getenv("GOOGLE_CLOUD_PROJECT_ID"),
            vertex_ai_location=os.getenv("VERTEX_AI_LOCATION", "global"),
            gemini_model_name=os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash"),
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
            openrouter_model_name=os.getenv("OPENROUTER_MODEL_NAME", "google/gemini-2.0-flash-exp:free"),
            chunk_duration_minutes=int(os.getenv("CHUNK_DURATION_MINUTES", "10")),
            max_upload_size_mb=int(os.getenv("MAX_UPLOAD_SIZE_MB", "500")),
            upload_directory=os.getenv("UPLOAD_DIRECTORY", "uploads"),
            output_directory=os.getenv("OUTPUT_DIRECTORY", "outputs"),
            temp_directory=os.getenv("TEMP_DIRECTORY", "temporary"),
            prompts_directory=os.getenv("PROMPTS_DIRECTORY", "prompts"),
        )

    @property
    def settings(self) -> Settings:
        return self._settings

    def get_api_key(self, provider: str) -> Optional[str]:
        """
        Get API key for provider.
        Priority: runtime key > environment variable
        """
        if provider == "gemini":
            return self._runtime_api_keys.get("gemini") or self._settings.gemini_api_key
        elif provider == "openrouter":
            return self._runtime_api_keys.get("openrouter") or self._settings.openrouter_api_key
        return None

    def set_runtime_api_key(self, provider: str, api_key: str):
        """Set API key at runtime (from web interface)."""
        self._runtime_api_keys[provider] = api_key

    def has_valid_api_key(self, provider: str) -> bool:
        """Check if valid API key exists for provider."""
        key = self.get_api_key(provider)
        return key is not None and len(key) > 10

    def update_settings(self, **kwargs):
        """Update settings at runtime."""
        current_dict = self._settings.model_dump()
        current_dict.update(kwargs)
        self._settings = Settings(**current_dict)

    def get_google_project_id(self) -> Optional[str]:
        """Get Google Cloud project ID."""
        return self._settings.google_cloud_project_id


# Global config manager instance
config_manager = ConfigManager()
