# ===========================================================================
# gunicorn.conf.py — Configuração de produção do servidor Gunicorn
#
# Estratégia: 1 worker UvicornWorker (não mais)
# Motivo: a aplicação é 100% async/await. Múltiplos workers Gunicorn
# criam processos Python independentes com event loops distintos, o que
# não traz ganho de throughput para I/O-bound async e adiciona risco de
# condição de corrida na escrita dos CSVs em disco compartilhado.
# O paralelismo real vem do asyncio.Semaphore + asyncio.gather internos.
# ===========================================================================
import os

# --- Binding ---
# Render.com injeta a variável PORT automaticamente.
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# --- Workers ---
# 1 worker async é suficiente para cargas I/O-bound; escale horizontalmente
# adicionando instâncias no Render se precisar de mais throughput.
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"

# --- Timeout ---
# O batch de 1000 NFs com gpt-4o-mini + semáforo 5 pode levar ~30 min.
# Timeout estendido evita que o Gunicorn mate o worker prematuramente.
# Em produção, prefira processar em background job; este valor cobre MVP.
timeout = 1800  # 30 minutos

# --- Keep-alive ---
keepalive = 5  # segundos — mantém conexões abertas entre requests

# --- Logging ---
accesslog = "-"   # stdout → coletado pelo log aggregator do Render
errorlog  = "-"   # stderr
loglevel  = "info"

# --- Graceful shutdown ---
graceful_timeout = 30
