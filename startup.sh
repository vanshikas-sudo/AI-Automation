#!/bin/bash
# Azure App Service startup script
# Set this as the startup command in:
#   Azure Portal → App Service → Configuration → General settings → Startup Command
# or pass it as the value of STARTUP_COMMAND in your CI/CD pipeline.
#
# Azure injects PORT automatically. If it is not set we fall back to 8000.

PORT=${PORT:-8000}

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --workers 1 \
    --log-level info \
    --no-access-log
