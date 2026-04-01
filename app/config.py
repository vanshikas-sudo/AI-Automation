from enum import Enum

from pydantic_settings import BaseSettings
from functools import lru_cache


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    AZURE = "azure"
    GOOGLE = "google"
    GROQ = "groq"


class Settings(BaseSettings):
    # WhatsApp Cloud API
    whatsapp_api_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str = "your_custom_verify_token"
    whatsapp_app_secret: str = ""

    # WhatsApp API base URL
    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"

    # LLM Provider Selection
    llm_provider: LLMProvider = LLMProvider.ANTHROPIC

    # LLM Configuration (shared)
    llm_api_key: str = ""
    llm_model: str = ""
    llm_system_prompt: str = "You are a helpful WhatsApp assistant. Keep replies concise and conversational."

    # Azure OpenAI specific
    azure_endpoint: str = ""
    azure_api_version: str = "2024-12-01-preview"
    azure_deployment: str = ""

    # Provider-specific defaults (used when llm_model is empty)
    anthropic_default_model: str = "claude-sonnet-4-20250514"
    openai_default_model: str = "gpt-4o"
    azure_default_model: str = "gpt-4o"
    google_default_model: str = "gemini-2.0-flash"
    groq_default_model: str = "llama-3.3-70b-versatile"

    # MCP Server Configuration
    mcp_zoho_url: str = ""
    zoho_org_id: str = ""

    # Timezone (used for scheduling and display)
    timezone: str = "Asia/Kolkata"

    # Redis (queue broker + rule storage + DLQ)
    redis_url: str = "redis://localhost:6379/0"

    # Internal API key — protects /messages/send from unauthenticated callers.
    # Set a strong random value (e.g. `openssl rand -hex 32`) in your environment.
    # If left empty the endpoint returns 501.
    internal_api_key: str = ""

    # Token optimization
    max_history_messages: int = 10
    session_ttl_minutes: int = 30

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @property
    def resolved_model(self) -> str:
        """Return the model name, falling back to provider-specific default."""
        if self.llm_model:
            return self.llm_model
        defaults = {
            LLMProvider.ANTHROPIC: self.anthropic_default_model,
            LLMProvider.OPENAI: self.openai_default_model,
            LLMProvider.AZURE: self.azure_default_model,
            LLMProvider.GOOGLE: self.google_default_model,
            LLMProvider.GROQ: self.groq_default_model,
        }
        return defaults[self.llm_provider]


@lru_cache
def get_settings() -> Settings:
    return Settings()
