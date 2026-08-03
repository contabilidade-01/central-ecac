# Central Pendências e-CAC — imagem de produção
#
# Python 3.13: é a versão em que o sistema roda e foi testado contra a SERPRO real
# (venv da máquina do Jean, 3.13.12). O Python **3.12** do executável original vale
# apenas para as FERRAMENTAS de leitura do bytecode (marshal312/dis312), que não fazem
# parte da aplicação e não entram nesta imagem.
#
# A regra aqui é reproduzir o ambiente TESTADO: versões do requirements.txt = as que
# rodam hoje. Ficar preso a versões antigas seria o risco, não o contrário.
#
# Build em dois estágios só para não carregar compiladores na imagem final.

# ---------------------------------------------------------------- estágio de build
FROM python:3.13-slim AS builder

# pycurl precisa compilar contra libcurl+openssl; lxml precisa de libxml2/libxslt.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libcurl4-openssl-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

ENV PYCURL_SSL_LIBRARY=openssl

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------- imagem final
FROM python:3.13-slim

# Runtime: só as bibliotecas dinâmicas, sem compiladores.
# tzdata para o horário do Brasil aparecer certo nos logs e nas datas.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 \
        libxml2 \
        libxslt1.1 \
        tzdata \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Sao_Paulo \
    DATA_DIR=/data \
    PORT=5847 \
    # Agendador ligado por padrão NO SERVIDOR (fora do container fica desligado, para
    # não disparar chamada paga durante desenvolvimento). O que protege o gasto é o
    # teto mensal (tela /agendamento) + o mapa de procurações.
    SCHEDULER_ENABLED=1

WORKDIR /app
COPY . .

# /data é o VOLUME PERSISTENTE: banco, PDFs, certificados e logs.
# Sem volume montado aqui, um redeploy apaga tudo.
RUN mkdir -p /data/instance /data/reports /data/certificates /data/logs /data/licenses

# Usuário sem privilégios.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 5847

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/healthz || exit 1

# 1 worker (estado em memória + SQLite) e 8 threads. Ver wsgi.py.
# timeout 300: o monitoramento das 72 empresas é síncrono e leva minutos.
CMD ["sh", "-c", "gunicorn --workers 1 --threads 8 --timeout 300 --access-logfile - --error-logfile - --bind 0.0.0.0:${PORT} wsgi:app"]
