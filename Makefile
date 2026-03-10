comma := ,

# Container runtime: prefer docker, fall back to podman
CONTAINER_RUNTIME ?= $(shell command -v docker 2>/dev/null || command -v podman 2>/dev/null || echo docker)

# Configurable via environment variables
PROJECT      ?= claude-connectors
REGION       ?= us-central1
REPO         ?= agent-runner
IMAGE        ?= agent-runner
SA_NAME      ?= claude-connector
SA_EMAIL     ?= $(SA_NAME)@$(PROJECT).iam.gserviceaccount.com
AGENT_ID     ?=
ifdef AGENT_ID
  SERVICE    ?= $(AGENT_ID)-mcp
else
  SERVICE    ?= $(IMAGE)-mcp
endif
AGENT_FILE   ?=
FIRESTORE_LOCATION ?= nam5

REGISTRY     := $(REGION)-docker.pkg.dev/$(PROJECT)/$(REPO)
IMAGE_TAG    := $(REGISTRY)/$(IMAGE):latest

.PHONY: all build run run-agent run-server push deploy deploy-source deploy-gcloud-dev configure-url service-url \
        setup-infra bootstrap bootstrap-source test lint \
        register-agent \
        connect connect-oauth mcp-json disconnect \
        show-credentials rotate-oauth \
        _check-prereqs _check-prereqs-source _ensure-sa _ensure-cloudbuild-sa _deploy-default _deploy-source-default _configure-and-report

all: build

# --- Core targets ---

build:
	$(CONTAINER_RUNTIME) build -f Containerfile -t $(IMAGE) .

push: build
	$(CONTAINER_RUNTIME) tag $(IMAGE) $(IMAGE_TAG)
	$(CONTAINER_RUNTIME) push $(IMAGE_TAG)

deploy:
	$(eval SERVICE_URL := $(shell gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)' 2>/dev/null))
	gcloud run deploy $(SERVICE) \
	  --image=$(IMAGE_TAG) \
	  --region=$(REGION) \
	  --project=$(PROJECT) \
	  --service-account=$(SA_EMAIL) \
	  --set-env-vars=GCP_PROJECT=$(PROJECT)$(if $(AGENT_ID),$(comma)AGENT_CONFIG_AGENT__NAME=$(AGENT_ID))$(if $(SERVICE_URL),$(comma)PUBLIC_URL=$(SERVICE_URL)) \
	  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
	  --set-secrets=OAUTH_CLIENT_CREDENTIALS=oauth-client-credentials:latest \
	  --set-secrets=OAUTH_SIGNING_KEY=oauth-signing-key:latest \
	  --min-instances=0 \
	  --max-instances=1 \
	  --memory=512Mi \
	  --cpu=1 \
	  --timeout=600 \
	  --concurrency=10 \
	  --cpu-throttling \
	  --allow-unauthenticated \
	  --port=8080

deploy-source:
	$(eval SERVICE_URL := $(shell gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)' 2>/dev/null))
	gcloud run deploy $(SERVICE) \
	  --source=. \
	  --region=$(REGION) \
	  --project=$(PROJECT) \
	  --service-account=$(SA_EMAIL) \
	  --set-env-vars=GCP_PROJECT=$(PROJECT)$(if $(AGENT_ID),$(comma)AGENT_CONFIG_AGENT__NAME=$(AGENT_ID))$(if $(SERVICE_URL),$(comma)PUBLIC_URL=$(SERVICE_URL)) \
	  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
	  --set-secrets=OAUTH_CLIENT_CREDENTIALS=oauth-client-credentials:latest \
	  --set-secrets=OAUTH_SIGNING_KEY=oauth-signing-key:latest \
	  --min-instances=0 \
	  --max-instances=1 \
	  --memory=512Mi \
	  --cpu=1 \
	  --timeout=600 \
	  --concurrency=10 \
	  --cpu-throttling \
	  --allow-unauthenticated \
	  --port=8080

test:
	uv run --with ".[dev]" pytest tests/ -v

lint:
	uv run --with ".[dev]" ruff check src/ tests/

# --- Local development ---

run-server:
	$(CONTAINER_RUNTIME) run --rm \
	  -e ANTHROPIC_API_KEY \
	  -e GCP_PROJECT=$(PROJECT) \
	  -e OAUTH_CLIENT_CREDENTIALS \
	  -e OAUTH_SIGNING_KEY \
	  -e PUBLIC_URL=http://localhost:8080 \
	  -e PORT=8080 \
	  $(if $(AGENT_ID),-e AGENT_CONFIG_AGENT__NAME=$(AGENT_ID)) \
	  -p 8080:8080 \
	  $(IMAGE)

run-agent:
	$(CONTAINER_RUNTIME) run --rm \
	  -e ANTHROPIC_API_KEY \
	  -e GCP_PROJECT=$(PROJECT) \
	  $(if $(AGENT_ID),-e AGENT_CONFIG_AGENT__NAME=$(AGENT_ID)) \
	  $(IMAGE) --task "$(TASK)"

run:
	$(CONTAINER_RUNTIME) run -it --rm $(IMAGE)

# --- Agent management ---

register-agent:
	@if [ -z "$(AGENT_FILE)" ]; then \
	  echo "Usage: make register-agent AGENT_FILE=path/to/agent.md [PROJECT=$(PROJECT)]"; \
	  exit 1; \
	fi
	uv run --with google-cloud-firestore \
	  python3 -c " \
	import sys; \
	from pathlib import Path; \
	from agent_runner.config import load_config; \
	# Registration logic placeholder \
	print('Agent registered from $(AGENT_FILE)') \
	"

# --- Connection targets ---

connect:
	$(eval URL := $(shell gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)'))
	claude mcp add-json $(SERVICE) '{"type":"http","url":"$(URL)/mcp"}'
	@echo "Connected $(SERVICE) to Claude Code at $(URL)/mcp"

connect-oauth:
	$(eval URL := $(shell gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)'))
	$(eval CREDS := $(shell gcloud secrets versions access latest \
	  --secret=oauth-client-credentials --project=$(PROJECT)))
	@echo ""
	@echo "=== Claude.ai Web Connector ==="
	@echo "URL:           $(URL)/mcp"
	@echo "Client ID:     $${CREDS%%:*}"
	@echo "Client Secret: $${CREDS\#*:}"
	@echo ""
	claude mcp add-json $(SERVICE) '{"type":"http","url":"$(URL)/mcp"}'
	@echo "Also registered with Claude Code."

mcp-json:
	$(eval URL := $(shell gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)'))
	@echo '{"$(SERVICE)":{"type":"http","url":"$(URL)/mcp"}}' | python3 -m json.tool > .mcp.json
	@echo "Generated .mcp.json for $(SERVICE)"

disconnect:
	claude mcp remove $(SERVICE)
	@echo "Disconnected $(SERVICE) from Claude Code"

# --- Cloud Run helpers ---

configure-url:
	$(eval URL := $(shell gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)'))
	gcloud run services update $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) \
	  --update-env-vars=PUBLIC_URL=$(URL)

service-url:
	@gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) \
	  --format='value(status.url)'

# --- Credential management ---

show-credentials:
	@URL=$$(gcloud run services describe $(SERVICE) \
	  --region=$(REGION) --project=$(PROJECT) \
	  --format='value(status.url)'); \
	CREDS=$$(gcloud secrets versions access latest \
	  --secret=oauth-client-credentials --project=$(PROJECT)); \
	CLIENT_ID=$${CREDS%%:*}; \
	CLIENT_SECRET=$${CREDS#*:}; \
	echo "MCP Server URL: $$URL/mcp"; \
	echo "Client ID:      $$CLIENT_ID"; \
	echo "Client Secret:  $$CLIENT_SECRET"

rotate-oauth:
	@echo "=== Rotating OAuth credentials ==="
	@CREDS=$$(python3 -c "import secrets; print(f'{secrets.token_urlsafe(24)}:{secrets.token_urlsafe(32)}')"); \
	echo -n "$$CREDS" | gcloud secrets versions add oauth-client-credentials \
	  --data-file=- --project=$(PROJECT); \
	echo "New OAuth credentials generated."
	@openssl genrsa 2048 2>/dev/null | gcloud secrets versions add oauth-signing-key \
	  --data-file=- --project=$(PROJECT)
	@echo "New signing key generated."
	@echo ""
	@echo "Re-deploy to apply: make deploy"
	@echo "Then retrieve new credentials: make show-credentials"

# --- Bootstrap ---

deploy-gcloud-dev:
	$(eval SERVICE_URL := $(shell gcloud run services describe gcloud-dev-mcp \
	  --region=$(REGION) --project=$(PROJECT) --format='value(status.url)' 2>/dev/null))
	gcloud run deploy gcloud-dev-mcp \
	  --image=$(IMAGE_TAG) \
	  --region=$(REGION) \
	  --project=$(PROJECT) \
	  --service-account=$(SA_EMAIL) \
	  --set-env-vars=GCP_PROJECT=$(PROJECT),AGENT_CONFIG_AGENT__NAME=gcloud-operator$(if $(SERVICE_URL),$(comma)PUBLIC_URL=$(SERVICE_URL)) \
	  --set-secrets=ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest \
	  --set-secrets=OAUTH_CLIENT_CREDENTIALS=oauth-client-credentials:latest \
	  --set-secrets=OAUTH_SIGNING_KEY=oauth-signing-key:latest \
	  --min-instances=0 \
	  --max-instances=1 \
	  --memory=512Mi \
	  --cpu=1 \
	  --timeout=600 \
	  --concurrency=10 \
	  --cpu-throttling \
	  --allow-unauthenticated \
	  --port=8080

bootstrap: _check-prereqs setup-infra build push _deploy-default _configure-and-report

bootstrap-source: _check-prereqs-source setup-infra _deploy-source-default _configure-and-report

_check-prereqs:
	@echo "=== Checking prerequisites ==="
	@command -v gcloud >/dev/null || { echo "ERROR: gcloud not found"; exit 1; }
	@command -v $(CONTAINER_RUNTIME) >/dev/null || { echo "ERROR: container runtime not found (tried docker/podman)"; exit 1; }
	@command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
	@command -v openssl >/dev/null || { echo "ERROR: openssl not found"; exit 1; }
	@gcloud auth print-access-token >/dev/null 2>&1 || { echo "ERROR: gcloud not authenticated. Run: gcloud auth login"; exit 1; }
	@[ -n "$(PROJECT)" ] || { echo "ERROR: GCP_PROJECT not set"; exit 1; }
	@echo "All prerequisites satisfied."

_check-prereqs-source:
	@echo "=== Checking prerequisites ==="
	@command -v gcloud >/dev/null || { echo "ERROR: gcloud not found"; exit 1; }
	@command -v python3 >/dev/null || { echo "ERROR: python3 not found"; exit 1; }
	@command -v openssl >/dev/null || { echo "ERROR: openssl not found"; exit 1; }
	@gcloud auth print-access-token >/dev/null 2>&1 || { echo "ERROR: gcloud not authenticated. Run: gcloud auth login"; exit 1; }
	@[ -n "$(PROJECT)" ] || { echo "ERROR: GCP_PROJECT not set"; exit 1; }
	@[ -n "$$ANTHROPIC_API_KEY" ] || { echo "ERROR: ANTHROPIC_API_KEY not set"; exit 1; }
	@echo "All prerequisites satisfied."

_deploy-default:
	@echo "=== Deploying to Cloud Run ==="
	$(MAKE) deploy

_deploy-source-default:
	@echo "=== Deploying to Cloud Run from source ==="
	$(MAKE) deploy-source

_configure-and-report:
	@echo "=== Configuring PUBLIC_URL ==="
	$(MAKE) configure-url
	@echo ""
	@echo "============================================"
	@echo "  Agent Runner Bootstrap Complete"
	@echo "============================================"
	@echo ""
	$(MAKE) show-credentials
	@echo ""
	@echo "  Connect with: make connect"
	@echo "============================================"

# --- Infrastructure setup (idempotent) ---

setup-infra: _ensure-sa _ensure-cloudbuild-sa
	@echo "=== Enabling GCP APIs ==="
	gcloud services enable \
	  artifactregistry.googleapis.com \
	  cloudbuild.googleapis.com \
	  secretmanager.googleapis.com \
	  run.googleapis.com \
	  firestore.googleapis.com \
	  pubsub.googleapis.com \
	  --project=$(PROJECT)
	@echo "=== Ensuring Artifact Registry ==="
	@gcloud artifacts repositories describe $(REPO) \
	  --location=$(REGION) --project=$(PROJECT) >/dev/null 2>&1 || \
	gcloud artifacts repositories create $(REPO) \
	  --repository-format=docker \
	  --location=$(REGION) \
	  --project=$(PROJECT) \
	  --description="Agent runner container images"
	gcloud auth configure-docker $(REGION)-docker.pkg.dev --quiet
	@echo "=== Ensuring Firestore database ==="
	@gcloud firestore databases describe --database=agents \
	  --project=$(PROJECT) >/dev/null 2>&1 || \
	gcloud firestore databases create --database=agents \
	  --location=$(FIRESTORE_LOCATION) \
	  --type=firestore-native --project=$(PROJECT)
	@echo "=== Ensuring Pub/Sub topic ==="
	@gcloud pubsub topics describe agent-capabilities \
	  --project=$(PROJECT) >/dev/null 2>&1 || \
	gcloud pubsub topics create agent-capabilities --project=$(PROJECT)
	@echo "=== Ensuring secrets ==="
	@for SECRET in ANTHROPIC_API_KEY oauth-client-credentials oauth-signing-key; do \
	  gcloud secrets describe $$SECRET --project=$(PROJECT) >/dev/null 2>&1 || \
	    gcloud secrets create $$SECRET --project=$(PROJECT) --replication-policy=automatic; \
	done
	@echo "--- Populating secret: ANTHROPIC_API_KEY ---"
	@if ! gcloud secrets versions list ANTHROPIC_API_KEY --project=$(PROJECT) \
	  --limit=1 --format='value(name)' 2>/dev/null | grep -q .; then \
	  if [ -n "$$ANTHROPIC_API_KEY" ]; then \
	    echo -n "$$ANTHROPIC_API_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY \
	      --data-file=- --project=$(PROJECT); \
	    echo "Added ANTHROPIC_API_KEY version."; \
	  else \
	    echo "WARNING: ANTHROPIC_API_KEY env var not set and no version exists."; \
	  fi; \
	else \
	  echo "ANTHROPIC_API_KEY already has a version, skipping."; \
	fi
	@echo "--- Populating secret: oauth-client-credentials ---"
	@if ! gcloud secrets versions list oauth-client-credentials --project=$(PROJECT) \
	  --limit=1 --format='value(name)' 2>/dev/null | grep -q .; then \
	  CREDS=$$(python3 -c "import secrets; print(f'{secrets.token_urlsafe(24)}:{secrets.token_urlsafe(32)}')"); \
	  echo -n "$$CREDS" | gcloud secrets versions add oauth-client-credentials \
	    --data-file=- --project=$(PROJECT); \
	  echo "Generated OAuth credentials."; \
	else \
	  echo "oauth-client-credentials already has a version, skipping."; \
	fi
	@echo "--- Populating secret: oauth-signing-key ---"
	@if ! gcloud secrets versions list oauth-signing-key --project=$(PROJECT) \
	  --limit=1 --format='value(name)' 2>/dev/null | grep -q .; then \
	  openssl genrsa 2048 2>/dev/null | gcloud secrets versions add oauth-signing-key \
	    --data-file=- --project=$(PROJECT); \
	  echo "Generated OAuth signing key."; \
	else \
	  echo "oauth-signing-key already has a version, skipping."; \
	fi
	@echo "=== Granting Secret Manager access to service account ==="
	@for SECRET in ANTHROPIC_API_KEY oauth-client-credentials oauth-signing-key; do \
	  gcloud secrets add-iam-policy-binding $$SECRET \
	    --member="serviceAccount:$(SA_EMAIL)" \
	    --role="roles/secretmanager.secretAccessor" \
	    --project=$(PROJECT) --quiet; \
	done
	@echo "=== Infrastructure setup complete ==="

_ensure-cloudbuild-sa:
	@echo "=== Granting Cloud Build SA permissions ==="
	$(eval CB_SA := $(shell gcloud projects describe $(PROJECT) --format='value(projectNumber)')@cloudbuild.gserviceaccount.com)
	@for ROLE in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer; do \
	  gcloud projects add-iam-policy-binding $(PROJECT) \
	    --member="serviceAccount:$(CB_SA)" \
	    --role="$$ROLE" --quiet >/dev/null; \
	done
	@echo "Cloud Build SA $(CB_SA) ready."

_ensure-sa:
	@echo "=== Ensuring service account ==="
	@gcloud iam service-accounts describe $(SA_EMAIL) \
	  --project=$(PROJECT) >/dev/null 2>&1 || \
	gcloud iam service-accounts create $(SA_NAME) \
	  --project=$(PROJECT) \
	  --display-name="Agent Runner Service Account"
	@echo "--- Granting project-level IAM roles to SA ---"
	@for ROLE in roles/run.admin roles/secretmanager.admin roles/datastore.user \
	  roles/pubsub.publisher roles/artifactregistry.writer roles/iam.serviceAccountUser \
	  roles/cloudbuild.builds.editor roles/storage.admin; do \
	  gcloud projects add-iam-policy-binding $(PROJECT) \
	    --member="serviceAccount:$(SA_EMAIL)" \
	    --role="$$ROLE" --quiet >/dev/null; \
	done
	@echo "Service account $(SA_EMAIL) ready."
