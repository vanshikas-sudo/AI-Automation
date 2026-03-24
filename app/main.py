import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx

from app.config import get_settings
from app.core.session_manager import SessionManager
from app.mcp.manager import MCPManager
from app.providers.llm_factory import create_chat_model
from app.routes.webhook import router as webhook_router

# Set up logging to both console AND file so we never lose logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("app.log", mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Configure session manager from settings
    SessionManager.configure(
        max_history=settings.max_history_messages,
        session_ttl_minutes=settings.session_ttl_minutes,
    )

    # Shared async HTTP client (120s timeout for media uploads)
    app.state.http_client = httpx.AsyncClient(timeout=120.0)

    # Initialize LLM model (multi-provider factory)
    app.state.llm_model = create_chat_model(settings)

    # Initialize MCP manager (client + tool registry + org ID)
    mcp_manager = MCPManager()
    app.state.mcp_manager = mcp_manager

    try:
        await mcp_manager.initialize()
        logger.info("MCP initialized successfully with %d tools", len(mcp_manager.registry.get_all()))
    except Exception as e:
        logger.error("MCP initialization failed: %s — app will start without tools", e)

    yield

    await mcp_manager.close()
    await app.state.http_client.aclose()


app = FastAPI(
    title="WhatsApp Zoho MCP Bot",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)
