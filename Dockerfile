# syntax=docker/dockerfile:1
# -----------------------------------------------------------------------
# Stage 1 — Python dependency build
# Using Red Hat UBI 9 minimal with Python 3.11 as required by IBM policy
# -----------------------------------------------------------------------
FROM registry.redhat.io/ubi9/python-311-minimal:latest AS python-builder

WORKDIR /build

# Install build tools required by some native Python packages
USER 0
RUN microdnf install -y gcc python3-devel && microdnf clean all

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt --target /install

# -----------------------------------------------------------------------
# Stage 2 — Node.js dependency build (for TypeScript frontend client)
# -----------------------------------------------------------------------
FROM registry.redhat.io/ubi9/nodejs-20-minimal:latest AS node-builder

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY frontend-client/ ./frontend-client/
RUN npx tsc --project tsconfig.json

# -----------------------------------------------------------------------
# Stage 3 — Runtime image
# -----------------------------------------------------------------------
FROM registry.redhat.io/ubi9/python-311-minimal:latest AS runtime

# Create a dedicated non-root user
USER 0
RUN useradd -m -u 1001 -s /sbin/nologin appuser \
 && microdnf install -y shadow-utils \
 && microdnf clean all

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=python-builder /install /usr/local/lib/python3.11/site-packages/

# Copy application source
COPY --chown=appuser:appuser auth_service/ ./auth_service/
COPY --from=node-builder --chown=appuser:appuser /build/dist/ ./frontend-client/dist/

# Drop all capabilities and run as non-root
USER 1001

# Expose only on localhost — never bind to 0.0.0.0 in production
EXPOSE 8000

# Healthcheck — supports liveness probes in Kubernetes / OpenShift
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["python", "-m", "uvicorn", "auth_service.main:app", \
     "--host", "127.0.0.1", "--port", "8000", \
     "--workers", "2", "--log-level", "info"]
