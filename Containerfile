FROM registry.access.redhat.com/ubi9/ubi-minimal:latest AS base

RUN microdnf install -y which tar gzip xz findutils git \
      --nodocs --setopt=install_weak_deps=0 && \
    microdnf clean all

FROM base AS install-node
RUN curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.xz \
    | tar -xJ --strip-components=1 -C /usr/local && \
    rm -rf /usr/local/share/doc /usr/local/share/man /usr/local/include

FROM install-node AS install-gcloud
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
    find /opt/google-cloud-sdk -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
ENV PATH="/opt/google-cloud-sdk/bin:$PATH"

FROM install-gcloud AS install-python
ENV UV_PYTHON_INSTALL_DIR=/usr/local/lib/uv/python
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh && \
    uv python install 3.11 && \
    chmod -R 755 /usr/local/lib/uv && \
    for f in /usr/local/lib/uv/python/cpython-3.11*/bin/python3.11; do \
      ln -sf "$f" /usr/local/bin/python3.11; break; \
    done
ENV CLOUDSDK_PYTHON=/usr/local/bin/python3.11

FROM install-python AS install-app
COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
RUN cd /app && UV_PYTHON_INSTALL_MIRROR="" uv pip install --system --python python3.11 --break-system-packages . && \
    rm -rf /app && \
    find /usr/local/lib/uv/python -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
    rm -rf /usr/share/doc /usr/share/man /var/cache/dnf /var/lib/rpm; true

COPY agent-config.example.yaml /etc/agent-runner/config.yaml
RUN mkdir -p /home/user/.claude/agents && chown -R 1001:1001 /home/user
COPY --chown=1001:1001 .claude/agents/gcloud-operator.md /home/user/.claude/agents/gcloud-operator.md

ENV HOME=/home/user
USER 1001:1001

ENTRYPOINT ["python3.11", "-m", "agent_runner"]
CMD []
