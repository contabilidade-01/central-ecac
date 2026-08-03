"""Ponto de entrada WSGI (Gunicorn no servidor).

    gunicorn --workers 1 --threads 8 --timeout 300 --bind 0.0.0.0:5847 wsgi:app

⚠️ **1 WORKER, SEMPRE.** Dois pontos do sistema quebram com múltiplos processos:

1. `MONITOR_STATUS` (caixa postal) e `PARCELAMENTO_PROGRESS` vivem em memória. Com 2+
   workers, a barra de progresso responderia ora de um processo ora de outro e ficaria
   pulando/zerando.
2. O banco é SQLite. Um único processo escrevendo evita `database is locked`.

Paralelismo vem das THREADS (`--threads`), que compartilham a mesma memória — é assim
que os jobs de parcelamento (ThreadPoolExecutor) e o monitoramento já funcionam.

`--timeout 300` porque o botão "Monitorar" é síncrono: com 72 empresas a requisição
pode levar 2–3 minutos.

Localmente (Windows) continue usando `python main_production.py`, que sobe o Waitress.
"""

from app import create_app

app = create_app()
