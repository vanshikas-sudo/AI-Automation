"""
LLM Provider Factory
Creates the appropriate LangChain chat model based on the configured provider.
Supports: Anthropic (Claude), OpenAI (GPT), Azure OpenAI, Google (Gemini), Groq.
"""

import logging

from langchain_core.language_models.chat_models import BaseChatModel

from app.config import LLMProvider, Settings

logger = logging.getLogger(__name__)


def create_chat_model(settings: Settings) -> BaseChatModel:
    """
    Create a LangChain chat model instance based on the configured LLM provider.

    Raises:
        ValueError: If the provider is not supported or API key is missing.
        ImportError: If the required provider package is not installed.
    """
    provider = settings.llm_provider
    api_key = settings.llm_api_key
    model = settings.resolved_model

    if not api_key:
        raise ValueError(
            f"LLM_API_KEY is required for provider '{provider.value}'. "
            f"Set it in your .env file."
        )

    logger.info("Creating LLM: provider=%s, model=%s", provider.value, model)

    if provider == LLMProvider.ANTHROPIC:
        return _create_anthropic(api_key, model)
    elif provider == LLMProvider.OPENAI:
        return _create_openai(api_key, model)
    elif provider == LLMProvider.AZURE:
        return _create_azure(api_key, model, settings)
    elif provider == LLMProvider.GOOGLE:
        return _create_google(api_key, model)
    elif provider == LLMProvider.GROQ:
        return _create_groq(api_key, model)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider.value}")


def _create_anthropic(api_key: str, model: str) -> BaseChatModel:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:
        raise ImportError(
            "langchain-anthropic is required for the Anthropic provider. "
            "Install it with: pip install langchain-anthropic"
        )
    return ChatAnthropic(model=model, api_key=api_key)


def _create_openai(api_key: str, model: str) -> BaseChatModel:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise ImportError(
            "langchain-openai is required for the OpenAI provider. "
            "Install it with: pip install langchain-openai"
        )
    return ChatOpenAI(model=model, api_key=api_key)


def _create_azure(api_key: str, model: str, settings: Settings) -> BaseChatModel:
    try:
        from langchain_openai import AzureChatOpenAI
    except ImportError:
        raise ImportError(
            "langchain-openai is required for the Azure OpenAI provider. "
            "Install it with: pip install langchain-openai"
        )
    if not settings.azure_endpoint:
        raise ValueError(
            "AZURE_ENDPOINT is required for the Azure provider. "
            "Set it in your .env file."
        )
    return AzureChatOpenAI(
        model=model,
        api_key=api_key,
        azure_endpoint=settings.azure_endpoint,
        api_version=settings.azure_api_version,
        azure_deployment=settings.azure_deployment or model,
    )


def _create_google(api_key: str, model: str) -> BaseChatModel:
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        raise ImportError(
            "langchain-google-genai is required for the Google provider. "
            "Install it with: pip install langchain-google-genai"
        )
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key)


def _create_groq(api_key: str, model: str) -> BaseChatModel:
    try:
        from langchain_groq import ChatGroq
    except ImportError:
        raise ImportError(
            "langchain-groq is required for the Groq provider. "
            "Install it with: pip install langchain-groq"
        )
    return ChatGroq(model=model, groq_api_key=api_key)
