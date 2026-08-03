"""Agendamento automático das rotinas (situação fiscal, caixa postal, parcelamentos).

⚠️ DESVIO INTENCIONAL (8o) — NÃO existe no exe, que dependia de alguém clicar em cada
botão. Pedido do Jean (02/08/2026).

Regra de negócio combinada
--------------------------
* **Situação fiscal** (é o que atualiza as PENDÊNCIAS e faz o débito pago sumir do
  painel): ligado por padrão, **mensal no dia 25**.
* **Demais módulos**: o usuário escolhe — todos começam DESLIGADOS, para nada gastar
  API sem decisão explícita.
* Frequências por módulo: `semanal` (dia da semana), `quinzenal` (a cada 15 dias) ou
  `mensal` (dia do mês).

Onde fica a configuração
------------------------
`<DATA_DIR>/instance/agendamento.json`, ao lado do banco — mesmo padrão do mapa de
procurações. Não altera `models.py` (regra 3 do projeto).

Travas de segurança (todas obrigatórias antes de gastar)
--------------------------------------------------------
1. `ProcuracaoService.pode_gastar()` — empresa recusada pela SERPRO não é consultada.
2. `LimiteGastoService.pode_gastar()` — teto mensal de gasto configurável.
3. `apenas_uma_execucao_por_vez` — um lock em memória impede duas rodadas simultâneas.

O disparo é feito pelo `scheduler.py` (thread do próprio processo).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import DB_PATH

logger = logging.getLogger(__name__)

ARQUIVO = 'agendamento.json'
VERSAO = 1

FREQ_SEMANAL = 'semanal'
FREQ_QUINZENAL = 'quinzenal'
FREQ_MENSAL = 'mensal'
FREQUENCIAS = (FREQ_SEMANAL, FREQ_QUINZENAL, FREQ_MENSAL)

DIAS_SEMANA = ('segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado', 'domingo')

#: módulos agendáveis. `custo` é só informativo, para a tela avisar o usuário.
MODULOS: Dict[str, Dict[str, Any]] = {
    'situacao_fiscal': {
        'titulo': 'Situação fiscal (pendências e débitos)',
        'descricao': 'Processa o relatório de cada empresa. É o que faz um débito pago '
                     'sumir do painel.',
        'custo': 'pago (1 emissão + 1 consulta por empresa)',
        'padrao': {'ativo': True, 'frequencia': FREQ_MENSAL, 'dia_mes': 25, 'hora': '03:00'},
    },
    'caixa_postal': {
        'titulo': 'Caixa postal do e-CAC',
        'descricao': 'Verifica o indicador de mensagens novas e baixa as que houver.',
        'custo': 'indicador GRATUITO; lista e detalhe são pagos',
        'padrao': {'ativo': False, 'frequencia': FREQ_SEMANAL, 'dia_semana': 0, 'hora': '04:00'},
    },
    'parcelamentos': {
        'titulo': 'Parcelamentos',
        'descricao': 'Busca pedidos e parcelas dos parcelamentos ativos.',
        'custo': 'pago (1 consulta por tipo habilitado, por empresa)',
        'padrao': {'ativo': False, 'frequencia': FREQ_MENSAL, 'dia_mes': 5, 'hora': '05:00'},
    },
}

_LOCK = threading.Lock()
#: impede duas execuções simultâneas do mesmo módulo
_EM_EXECUCAO: Dict[str, bool] = {}


def _agora() -> datetime:
    return datetime.now()


# --------------------------------------------------------------------- arquivo

def caminho() -> Path:
    return Path(DB_PATH).parent / ARQUIVO


def carregar() -> Dict[str, Any]:
    arquivo = caminho()
    dados: Dict[str, Any] = {}
    if arquivo.exists():
        try:
            dados = json.loads(arquivo.read_text(encoding='utf-8'))
        except Exception:
            logger.exception('agendamento.json ilegível — recriando com os padrões')
            dados = {}

    dados.setdefault('versao', VERSAO)
    dados.setdefault('modulos', {})
    for nome, meta in MODULOS.items():
        atual = dados['modulos'].setdefault(nome, dict(meta['padrao']))
        for chave, valor in meta['padrao'].items():
            atual.setdefault(chave, valor)
    return dados


def salvar(dados: Dict[str, Any]) -> None:
    dados['versao'] = VERSAO
    dados['atualizado_em'] = _agora().isoformat()
    arquivo = caminho()
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding='utf-8')


# ------------------------------------------------------------------ validação

def validar(config: Dict[str, Any]) -> Optional[str]:
    """Devolve a mensagem de erro, ou None se estiver tudo certo."""
    frequencia = config.get('frequencia')
    if frequencia not in FREQUENCIAS:
        return f"frequência deve ser uma de {', '.join(FREQUENCIAS)}"

    if frequencia == FREQ_MENSAL:
        dia = config.get('dia_mes')
        if not isinstance(dia, int) or not 1 <= dia <= 28:
            return ('dia_mes deve ser um número de 1 a 28 '
                    '(acima de 28 não existe em todo mês)')

    if frequencia == FREQ_SEMANAL:
        dia = config.get('dia_semana')
        if not isinstance(dia, int) or not 0 <= dia <= 6:
            return 'dia_semana deve ser de 0 (segunda) a 6 (domingo)'

    hora = str(config.get('hora') or '')
    try:
        h, m = hora.split(':')
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError
    except Exception:
        return "hora deve estar no formato HH:MM (ex.: 03:00)"

    return None


# --------------------------------------------------------- cálculo de vencimento

def _horario_do_dia(dia: date, hora: str) -> datetime:
    h, m = (int(p) for p in hora.split(':'))
    return datetime(dia.year, dia.month, dia.day, h, m)

def proxima_execucao(config: Dict[str, Any], referencia: Optional[datetime] = None) -> datetime:
    """Quando esta rotina deve rodar pela próxima vez, a partir de `referencia`."""
    agora = referencia or _agora()
    hora = config.get('hora', '03:00')
    frequencia = config.get('frequencia')

    if frequencia == FREQ_MENSAL:
        dia_mes = int(config.get('dia_mes', 25))
        candidato = _horario_do_dia(date(agora.year, agora.month, dia_mes), hora)
        if candidato <= agora:
            ano = agora.year + (1 if agora.month == 12 else 0)
            mes = 1 if agora.month == 12 else agora.month + 1
            candidato = _horario_do_dia(date(ano, mes, dia_mes), hora)
        return candidato

    if frequencia == FREQ_SEMANAL:
        alvo = int(config.get('dia_semana', 0))
        dias = (alvo - agora.weekday()) % 7
        candidato = _horario_do_dia(agora.date() + timedelta(days=dias), hora)
        if candidato <= agora:
            candidato += timedelta(days=7)
        return candidato

    # quinzenal: 15 dias após a última execução; se nunca rodou, hoje no horário
    ultima = config.get('ultima_execucao')
    if ultima:
        try:
            base = datetime.fromisoformat(ultima)
        except ValueError:
            base = agora
        candidato = _horario_do_dia((base + timedelta(days=15)).date(), hora)
        if candidato <= agora:
            return agora
        return candidato

    candidato = _horario_do_dia(agora.date(), hora)
    return candidato if candidato > agora else agora


def esta_vencida(config: Dict[str, Any], referencia: Optional[datetime] = None) -> bool:
    """True quando a rotina deveria ter rodado e ainda não rodou."""
    if not config.get('ativo'):
        return False

    agora = referencia or _agora()
    ultima = config.get('ultima_execucao')
    if not ultima:
        # nunca rodou: só dispara quando o horário do dia já passou
        return proxima_execucao(config, agora) <= agora

    try:
        quando = datetime.fromisoformat(ultima)
    except ValueError:
        return True

    return proxima_execucao(config, quando) <= agora


# --------------------------------------------------------------------- estado

def registrar_execucao(modulo: str, resultado: Dict[str, Any]) -> None:
    with _LOCK:
        dados = carregar()
        config = dados['modulos'].setdefault(modulo, dict(MODULOS[modulo]['padrao']))
        config['ultima_execucao'] = _agora().isoformat()
        config['ultimo_resultado'] = resultado
        salvar(dados)


def atualizar_modulo(modulo: str, novos: Dict[str, Any]) -> Dict[str, Any]:
    """Grava a configuração de um módulo (usado pela tela)."""
    if modulo not in MODULOS:
        raise ValueError(f'módulo desconhecido: {modulo}')

    with _LOCK:
        dados = carregar()
        config = dados['modulos'][modulo]
        for chave in ('ativo', 'frequencia', 'dia_mes', 'dia_semana', 'hora'):
            if chave in novos:
                config[chave] = novos[chave]
        config['ativo'] = bool(config.get('ativo'))

        erro = validar(config)
        if erro:
            raise ValueError(erro)

        salvar(dados)
        return config


def resumo() -> Dict[str, Any]:
    dados = carregar()
    modulos = []
    for nome, meta in MODULOS.items():
        config = dados['modulos'][nome]
        modulos.append({
            'modulo': nome,
            'titulo': meta['titulo'],
            'descricao': meta['descricao'],
            'custo': meta['custo'],
            'ativo': bool(config.get('ativo')),
            'frequencia': config.get('frequencia'),
            'dia_mes': config.get('dia_mes'),
            'dia_semana': config.get('dia_semana'),
            'hora': config.get('hora'),
            'ultima_execucao': config.get('ultima_execucao'),
            'ultimo_resultado': config.get('ultimo_resultado'),
            'proxima_execucao': (proxima_execucao(config).isoformat()
                                 if config.get('ativo') else None),
            'em_execucao': bool(_EM_EXECUCAO.get(nome)),
            'checkpoint': dados.get('checkpoints', {}).get(nome),
        })
    return {
        'arquivo': str(caminho()),
        'atualizado_em': dados.get('atualizado_em'),
        'dias_semana': list(DIAS_SEMANA),
        'frequencias': list(FREQUENCIAS),
        'modulos': modulos,
    }


# ------------------------------------------------------------------- execução

def _empresas_liberadas() -> List[Any]:
    """Empresas ativas que NÃO estão travadas pelo mapa de procurações."""
    from app.models import Company
    from app.services.procuracao_service import ProcuracaoService

    liberadas = []
    for company in Company.query.filter_by(ativo=True).order_by(Company.id.asc()).all():
        pode, motivo = ProcuracaoService.pode_gastar(company)
        if pode:
            liberadas.append(company)
        else:
            logger.info('[AGENDA] empresa_id=%s pulada: %s', company.id, motivo)
    return liberadas


def checkpoint(modulo: str) -> Dict[str, Any]:
    """Estado da última execução interrompida deste módulo (vazio se não houver)."""
    return carregar().get('checkpoints', {}).get(modulo, {})


def _gravar_checkpoint(modulo: str, dados_ckpt: Optional[Dict[str, Any]]) -> None:
    with _LOCK:
        dados = carregar()
        checkpoints = dados.setdefault('checkpoints', {})
        if dados_ckpt is None:
            checkpoints.pop(modulo, None)
        else:
            checkpoints[modulo] = dados_ckpt
        salvar(dados)


def executar_modulo(modulo: str) -> Dict[str, Any]:
    """Roda a rotina de um módulo respeitando as travas. Retorna o resumo.

    **Retomada:** quando o teto de gasto é atingido no meio de um lote, as empresas que
    faltam ficam gravadas num *checkpoint*. Na execução seguinte o lote **continua de
    onde parou** — as já processadas não são consultadas de novo (não se paga duas vezes
    pela mesma empresa).
    """
    from app.services.limite_gasto_service import LimiteGastoService

    if modulo not in MODULOS:
        raise ValueError(f'módulo desconhecido: {modulo}')

    if _EM_EXECUCAO.get(modulo):
        return {'success': False, 'message': 'Já existe uma execução em andamento.'}

    # Teto já estourado antes de começar: nem inicia (o indicador da caixa postal é
    # gratuito, então esse módulo não é barrado aqui — a trava dele é por empresa).
    pode, motivo = LimiteGastoService.pode_gastar()
    if not pode and modulo != 'caixa_postal':
        logger.warning('[AGENDA] %s não iniciado: %s', modulo, motivo)
        pendentes = checkpoint(modulo).get('pendentes') or []
        return {
            'success': False,
            'interrompido_por': 'teto_de_gasto',
            'message': motivo,
            'pendentes': len(pendentes),
            'aviso': ('Nada foi consultado. O lote retoma de onde parou assim que o teto '
                      'for aumentado ou o mês virar.') if pendentes else motivo,
        }

    _EM_EXECUCAO[modulo] = True
    inicio = _agora()
    try:
        if modulo == 'situacao_fiscal':
            resultado = _executar_situacao_fiscal()
        elif modulo == 'caixa_postal':
            resultado = _executar_caixa_postal()
        else:
            resultado = _executar_parcelamentos()
    except Exception as exc:
        logger.exception('[AGENDA] falha ao executar %s', modulo)
        resultado = {'success': False, 'message': str(exc)}
    finally:
        _EM_EXECUCAO[modulo] = False

    resultado['duracao_s'] = round((_agora() - inicio).total_seconds(), 1)
    registrar_execucao(modulo, resultado)
    logger.info('[AGENDA] %s concluído: %s', modulo, resultado)
    return resultado


def _lote_com_retomada(modulo: str, processar) -> Dict[str, Any]:
    """Percorre as empresas checando o teto ANTES de cada uma.

    `processar(company)` deve devolver True (sucesso) ou False (falha).

    Se o teto estourar no meio, grava as pendentes no checkpoint e devolve o aviso.
    Se terminar, o checkpoint é apagado.
    """
    from app.services.limite_gasto_service import LimiteGastoService

    anterior = checkpoint(modulo)
    pendentes_ids = anterior.get('pendentes') or []
    retomada = bool(pendentes_ids)

    empresas = _empresas_liberadas()
    if retomada:
        # só as que faltavam, preservando a ordem original
        empresas = [c for c in empresas if c.id in set(pendentes_ids)]
        logger.info('[AGENDA] %s retomando de onde parou: %s empresas pendentes',
                    modulo, len(empresas))

    total = len(empresas)
    concluidas = list(anterior.get('concluidas') or [])
    ok = falhas = 0
    restantes: List[int] = []

    for indice, company in enumerate(empresas):
        pode, motivo = LimiteGastoService.pode_gastar()
        if not pode:
            restantes = [c.id for c in empresas[indice:]]
            _gravar_checkpoint(modulo, {
                'interrompido_por': 'teto_de_gasto',
                'em': _agora().isoformat(),
                'motivo': motivo,
                'concluidas': concluidas,
                'pendentes': restantes,
            })
            logger.warning('[AGENDA] %s INTERROMPIDO pelo teto — %s empresas pendentes: %s',
                           modulo, len(restantes), motivo)
            return {
                'success': False,
                'interrompido_por': 'teto_de_gasto',
                'message': motivo,
                'aviso': (f'Lote interrompido: {len(restantes)} empresa(s) não foram '
                          f'consultadas. Na próxima execução o sistema continua de onde '
                          f'parou — as {len(concluidas)} já feitas não serão repetidas.'),
                'processadas': ok,
                'falhas': falhas,
                'pendentes': len(restantes),
                'retomada': retomada,
            }

        try:
            sucesso = processar(company)
        except Exception:
            logger.exception('[AGENDA] %s falhou na empresa %s', modulo, company.id)
            sucesso = False

        if sucesso:
            ok += 1
        else:
            falhas += 1
        concluidas.append(company.id)

    _gravar_checkpoint(modulo, None)
    return {
        'success': True,
        'empresas': total,
        'processadas': ok,
        'falhas': falhas,
        'pendentes': 0,
        'retomada': retomada,
    }


def _executar_situacao_fiscal() -> Dict[str, Any]:
    from app.services.report_service import ReportService

    service = ReportService()

    def processar(company):
        resultado = service.process_company(company.id)
        return bool(getattr(resultado, 'success', False))

    return _lote_com_retomada('situacao_fiscal', processar)


def _executar_caixa_postal() -> Dict[str, Any]:
    from app.services.caixa_postal_service import CaixaPostalService

    resultado = CaixaPostalService().monitorar_todas_empresas(
        only_if_due=True, baixar_mensagens_quando_houver=True)
    resultado['success'] = True
    return resultado


def _executar_parcelamentos() -> Dict[str, Any]:
    from app.services.parcelamentos_serpro_service import ParcelamentosSerproService

    service = ParcelamentosSerproService()

    def processar(company):
        resultado = service.buscar_pedidos_empresa(company.id)
        return 'erro' not in (resultado or {})

    return _lote_com_retomada('parcelamentos', processar)
