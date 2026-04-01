# Phase 7 — Multi-Channel & Advanced Features

> **Status**: NOT STARTED
> **Goal**: Expand beyond WhatsApp. Slack, Web Chat, Email — with cross-channel session continuity. Plus: tenant onboarding, report scheduling, and webhook outbound.
> **Prerequisite**: Phase 6 complete (enterprise-hardened)
> **Estimated Prompts**: 8-10

---

## Tasks

### 7.1 — Channel Adapter Interface

**Files to create**:

- `app/channels/__init__.py`
- `app/channels/base.py` — abstract ChannelAdapter interface
- `app/channels/whatsapp.py` — refactored from existing WhatsApp code

**What**:

```python
class ChannelAdapter(ABC):
    @abstractmethod
    async def parse_incoming(self, raw_payload: dict) -> IncomingMessage: ...
    @abstractmethod
    async def send_text(self, user_id: str, text: str): ...
    @abstractmethod
    async def send_document(self, user_id: str, file_path: str, filename: str): ...
    @abstractmethod
    async def verify_webhook(self, request: Request) -> bool: ...
```

- Refactor existing `whatsapp_service.py` to implement `ChannelAdapter`
- Unified `IncomingMessage` model (channel-agnostic)
- Channel registry: `{"whatsapp": WhatsAppAdapter, "slack": SlackAdapter, ...}`

**Acceptance**: WhatsApp adapter works identically to current V1; interface ready for new channels.

---

### 7.2 — Slack Channel Adapter

**Files to create**: `app/channels/slack.py`

**What**:

- Slack Events API integration (message events)
- Slack interactive messages (buttons for confirmations)
- Bot token auth (OAuth 2.0)
- Thread-aware: replies in same thread as user message
- Slash commands: `/agentflow list automations`, `/agentflow help`
- User mapping: Slack user ID → platform user (via `channel_identifiers`)

**Acceptance**: Send message in Slack → bot replies; confirmation buttons work.

---

### 7.3 — Web Chat Widget (WebSocket)

**Files to create**:

- `app/channels/web.py` — WebSocket endpoint
- `dashboard/components/ChatWidget.tsx` — embeddable widget

**What**:

- WebSocket endpoint: `wss://api.agentflow.com/ws/{tenant_slug}`
- JWT auth via query param or first message
- Real-time bidirectional: messages, typing indicators, status updates
- React widget component: embeddable in any website via `<script>` tag
- Widget config: colors, position, welcome message (per-tenant)
- Chat history: load previous messages on connect (from DB)

**Acceptance**: Widget renders on webpage; messages flow bidirectionally in real-time.

---

### 7.4 — Email Inbound Adapter

**Files to create**: `app/channels/email.py`

**What**:

- Receive emails via webhook (Zoho Mail webhook, SendGrid, etc.)
- Parse: extract sender, subject, body (strip HTML), attachments
- Map sender email → platform user
- Thread tracking: email thread → session continuity
- Reply: send email back to user via configured email MCP

**Acceptance**: Send email to designated address → bot processes and replies via email.

---

### 7.5 — Cross-Channel Session Continuity

**Files to modify**: `app/core/session_manager.py`

**What**:

- Session keyed by `user_id` (not channel-specific identifier)
- Start conversation on WhatsApp → continue on Slack → check history on web
- Session context (org selection, active workflow) shared across channels
- Channel preference: user's last active channel gets notifications

**Acceptance**: Send message on WhatsApp; switch to Slack; see same conversation history.

---

### 7.6 — Tenant Onboarding Wizard (Backend)

**Files to create**: `app/services/onboarding_service.py`, `app/routes/admin/onboarding.py`

**What**:

- Multi-step onboarding flow:
  1. Create tenant (name, slug)
  2. Create owner user (email, password)
  3. Add first MCP connection (guided setup)
  4. Test connection → discover tools
  5. Seed default agents, intents, prompts for the MCP provider
  6. Add first WhatsApp user (phone number)
  7. Test message round-trip
- Provider-specific seeds: Zoho Books seed, Tally seed, Salesforce seed
- API: `POST /admin/onboard` (takes all steps in one payload)
- Dashboard: step-by-step wizard UI (created in 5.x)

**Acceptance**: Complete onboarding → tenant fully functional → WhatsApp message works end-to-end.

---

### 7.7 — Report Scheduling

**What**: Build on Phase 4 Event Engine

- New event rule templates: "Send weekly sales report every Monday"
- Schedule report generation as automation rule
- PDF generated → sent via WhatsApp document / email attachment
- Custom report builder (via admin UI): select data sources, metrics, charts

**Acceptance**: "Generate weekly report every Monday at 9 AM" → automation runs → PDF delivered.

---

### 7.8 — Webhook Outbound (External Notifications)

**Files to create**: `app/services/webhook_outbound.py`

**What**:

- When events occur, POST to configured external webhook URLs
- Tenant configures: event type → webhook URL + secret
- HMAC signature on outgoing webhooks
- Retry: 3 attempts with exponential backoff
- Use case: notify external CRM when invoice created, sync data to other systems

**Acceptance**: Configure outbound webhook → event occurs → POST sent to URL with HMAC signature.

---

### 7.9 — API Documentation (OpenAPI)

**What**:

- FastAPI auto-generates OpenAPI spec
- Add descriptions, examples, response models to all endpoints
- Separate API groups: Admin API, Webhook API, Public API
- Swagger UI at `/docs` (protected by JWT for admin)
- Redoc at `/redoc` (same)

**Acceptance**: `/docs` shows complete API with working try-it-out for all endpoints.

---

### 7.10 — Deployment Package

**Files to create**: `deploy/`, Kubernetes manifests or Docker Compose production

**What**:

- Production Docker Compose: all services with production configs
- Optional Kubernetes manifests: Deployment, Service, Ingress, ConfigMap, Secrets
- Terraform (optional): AWS infrastructure (RDS, ElastiCache, ECS/EKS)
- Environment guide: exact steps to deploy on AWS/GCP/Azure
- Backup strategy: PostgreSQL pg_dump schedule, Redis RDB

**Acceptance**: `docker compose -f docker-compose.prod.yml up` → production-ready system running.

---

## Completion Criteria

- [ ] Slack adapter working
- [ ] Web chat widget working
- [ ] Email inbound adapter working
- [ ] Cross-channel session continuity
- [ ] Tenant onboarding wizard (API + UI)
- [ ] Report scheduling via automations
- [ ] Outbound webhooks
- [ ] API documentation complete
- [ ] Production deployment package ready
