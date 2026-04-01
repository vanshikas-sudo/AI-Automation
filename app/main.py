import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx

from app.config import get_settings
from app.core.session_manager import SessionManager
from app.mcp.manager import MCPManager
from app.providers.llm_factory import create_chat_model
from app.routes.webhook import router as webhook_router
from app.routes.automation import router as automation_router
from app.automation.rule_store import RuleStore
from app.automation.dlq import DeadLetterQueue

# Set up logging to both console AND file so we never lose logs
# NOTE: Log file placed in logs/ dir (excluded from uvicorn --reload watcher)
import os
_log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(_log_dir, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(_log_dir, "app.log"), mode="a", encoding="utf-8"),
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

    # Initialize automation engine (try Redis, fall back to in-memory for dev)
    try:
        rule_store = RuleStore(settings.redis_url)
        dlq = DeadLetterQueue(settings.redis_url)
        # Test the connection with a quick operation
        await rule_store.get_active_rules()
        app.state.rule_store = rule_store
        app.state.dlq = dlq
        active_rules = await rule_store.get_active_rules()
        logger.info("Automation engine initialized (Redis) — %d active rule(s)", len(active_rules))
    except Exception as e:
        logger.warning("Redis unavailable (%s) — using in-memory store (dev mode)", e)
        from app.automation.memory_store import InMemoryRuleStore, InMemoryDeadLetterQueue
        app.state.rule_store = InMemoryRuleStore()
        app.state.dlq = InMemoryDeadLetterQueue()

    yield

    # Shutdown automation engine
    if getattr(app.state, "rule_store", None):
        await app.state.rule_store.close()
    if getattr(app.state, "dlq", None):
        await app.state.dlq.close()

    await mcp_manager.close()
    await app.state.http_client.aclose()


app = FastAPI(
    title="WhatsApp Zoho MCP Bot",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)
app.include_router(automation_router)
