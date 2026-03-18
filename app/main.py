import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import httpx

from app.routes.webhook import router as webhook_router
from app.services.mcp_service import MCPService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Shared async HTTP client for outbound WhatsApp API calls
    # 120s timeout for long operations like media uploads during report sending
    app.state.http_client = httpx.AsyncClient(timeout=120.0)

    # Initialize MCP service and LangGraph agent
    mcp_service = MCPService()
    await mcp_service.initialize()
    app.state.mcp_service = mcp_service
    app.state.mcp_agent = mcp_service.get_agent()

    yield

    await mcp_service.close()
    await app.state.http_client.aclose()


app = FastAPI(
    title="WhatsApp Messaging Service",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)
