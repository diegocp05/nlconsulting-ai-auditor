# ===========================================================================
# Dockerfile — NL Consulting AuditorIA Backend
#
# Multi-stage build: imagem final ~200 MB (sem venv, sem cache pip)
# Base: python:3.12-slim (Debian Bookworm) — menor superfície de ataque
# ===========================================================================

# ---------------------------------------------------------------------------
# Estágio 1: Builder — instala dependências em camada separada
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Atualiza o gerenciador de pacotes e instala apenas o necessário
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia somente o requirements para aproveitar o cache de layer do Docker
COPY requirements.txt .

# Instala dependências num virtualenv isolado (mais seguro que site-packages global)
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Estágio 2: Runtime — imagem mínima sem ferramentas de build
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# Metadados OCI
LABEL org.opencontainers.image.title="AuditorIA Backend"
LABEL org.opencontainers.image.description="NL Consulting — API de auditoria de notas fiscais"
LABEL org.opencontainers.image.vendor="NL Consulting"

# Segurança: roda como usuário não-root
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copia o venv do estágio builder (sem o gcc e libs de compilação)
COPY --from=builder /opt/venv /opt/venv

# Copia o código da aplicação
COPY . .

# Cria o diretório de exportação com permissão para o appuser
# Em produção no Render, monte um Persistent Disk em /app/exports
RUN mkdir -p /app/exports \
    && chown -R appuser:appgroup /app

# Usa o Python do venv por padrão
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Muda para o usuário não-root
USER appuser

# Expõe a porta padrão (Render sobrescreve com $PORT)
EXPOSE 8000

# Health check para o orquestrador (Docker Swarm / Render health probe)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/api/health').raise_for_status()"

# Comando de start: Gunicorn com worker Uvicorn, config via gunicorn.conf.py
CMD ["gunicorn", "main:app", "--config", "gunicorn.conf.py"]
