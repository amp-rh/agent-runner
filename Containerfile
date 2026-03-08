FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS base

RUN microdnf install -y which tar gzip \
      --nodocs --setopt=install_weak_deps=0 && \
    microdnf clean all

FROM base AS install-gcloud-sdk
RUN curl -fsSL https://dl.google.com/dl/cloudsdk/channels/rapid/google-cloud-sdk.tar.gz \
    | tar -xz -C /opt && \
    rm -f /opt/google-cloud-sdk/RELEASE_NOTES \
          /opt/google-cloud-sdk/install.bat \
          /opt/google-cloud-sdk/install.sh \
          /opt/google-cloud-sdk/path.*.inc \
          /opt/google-cloud-sdk/completion.*.inc && \
    rm -rf /opt/google-cloud-sdk/deb \
           /opt/google-cloud-sdk/rpm && \
    for d in /opt/google-cloud-sdk/lib/surface/*/; do \
      case "$(basename "$d")" in \
        auth|bq|components|config|firestore|iam|meta|projects|run|secrets|services|storage|topic) ;; \
        *) rm -rf "$d" ;; \
      esac; \
    done && \
    for d in $(find /opt/google-cloud-sdk -type d -name __pycache__ 2>/dev/null); do rm -rf "$d"; done
ENV PATH="/opt/google-cloud-sdk/bin:/usr/local/bin:$PATH"

FROM install-gcloud-sdk AS install-uv
ENV UV_PYTHON_INSTALL_DIR=/usr/local/lib/uv/python
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh && \
    uv python install 3.11 && \
    chmod -R 755 /usr/local/lib/uv && \
    for f in /usr/local/lib/uv/python/cpython-3.11*/bin/python3.11; do \
      ln -sf "$f" /usr/local/bin/python3.11; break; \
    done
ENV CLOUDSDK_PYTHON=/usr/local/bin/python3.11

FROM install-uv AS install-claude-cli
RUN curl -fsSL https://claude.ai/install.sh | bash && \
    cp /root/.local/bin/claude /usr/local/bin/claude && \
    rm -rf /root/.local

FROM install-claude-cli AS install-packages
RUN UV_PYTHON_INSTALL_MIRROR="" uv pip install --system --python python3.11 --break-system-packages \
      mcp "PyJWT[crypto]" uvicorn google-cloud-firestore google-cloud-pubsub httpx && \
    find /usr/local/lib/uv/python -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
    rm -rf /usr/share/doc /usr/share/man /usr/share/locale /var/cache/dnf /var/lib/rpm && \
    true

RUN mkdir -p /usr/local/lib/mcp-server && \
    mkdir -p /home/user/.claude/agents && chown -R 1001:1001 /home/user
COPY server.py /usr/local/lib/mcp-server/server.py
COPY oauth.py /usr/local/lib/mcp-server/oauth.py
COPY agent_loader.py /usr/local/lib/mcp-server/agent_loader.py
COPY agent_registry.py /usr/local/lib/mcp-server/agent_registry.py
COPY --chown=1001:1001 .claude/agents/gcloud-operator.md /home/user/.claude/agents/gcloud-operator.md
COPY --chown=1001:1001 .claude/agents/orchestrator.md /home/user/.claude/agents/orchestrator.md
COPY --chown=1001:1001 .claude/agents/firestore-agent.md /home/user/.claude/agents/firestore-agent.md
COPY --chown=1001:1001 .claude/agents/pubsub-agent.md /home/user/.claude/agents/pubsub-agent.md
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV HOME=/home/user
USER 1001:1001

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD []
