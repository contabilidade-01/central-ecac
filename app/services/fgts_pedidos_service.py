"""Pedidos ao robô do FGTS Digital — criação, travas, etapas e serialização (desvio 22).

⚠️ DESVIO INTENCIONAL — NÃO existe no exe. Pedido do Jean (23/10/2026). Ver
`integracao_models.FgtsPedido` e `fgts_digital_robo.py`.

Regras:
* um pedido ABERTO por empresa por vez (o robô troca de perfil a cada empresa; dois
  pedidos da mesma empresa não fazem sentido e só gastariam tempo do cliente);
* limite de pedidos por link por dia (`FGTS_MAX_PEDIDOS_DIA`, padrão 6);
* a competência do "emitir" tem de estar na última consulta do mesmo CNPJ feita hoje —
  o cliente nunca digita competência solta, escolhe da lista que o próprio FGTS Digital
  devolveu;
* o PDF só é servido para o CNPJ do pedido, e só se o texto do PDF trouxer a raiz desse
  CNPJ (camada contra guia de outra empresa).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.integracao_models import FgtsPedido

logger = logging.getLogger(__name__)

ACOES = ('consultar', 'emitir')
ABERTOS = ('fila', 'executando', 'aguardando_captcha', 'aguardando_escolha')

# Etapas na ordem em que o cliente as vê na página (chave → rótulo).
ETAPAS = [
    ('fila', 'Na fila'),
    ('login', 'Entrando no gov.br com o certificado do escritório'),
    ('captcha', 'Verificação de segurança do gov.br'),
    ('perfil', 'Abrindo a sua empresa no FGTS Digital'),
    ('consultando', 'Consultando débitos de FGTS'),
    ('emitindo', 'Emitindo a guia com valor atualizado'),
    ('concluido', 'Pronto'),
]
CHAVES_ETAPAS = [e[0] for e in ETAPAS]


class PedidoRecusado(Exception):
    def __init__(self, mensagem: str, status: int = 400):
        super().__init__(mensagem)
        self.status = status


def habilitado() -> bool:
    return os.getenv('FGTS_DIGITAL_HABILITADO', '0').strip().lower() in ('1', 'true', 'sim')


def liberado_para(cnpj: str) -> bool:
    """Piloto: com `FGTS_DIGITAL_CNPJS` (lista separada por vírgula) só esses CNPJs veem
    o FGTS no link; vazio = todos. Serve para validar com uma empresa antes de abrir."""
    if not habilitado():
        return False
    lista = {''.join(ch for ch in c if ch.isdigit()) for c in os.getenv('FGTS_DIGITAL_CNPJS', '').split(',')}
    lista.discard('')
    return not lista or cnpj in lista


def max_pedidos_dia() -> int:
    try:
        return max(1, int(os.getenv('FGTS_MAX_PEDIDOS_DIA', '6')))
    except ValueError:
        return 6


def pasta_pdfs(cnpj: str) -> Path:
    from app.config import DATA_DIR
    pasta = Path(DATA_DIR) / 'fgts' / cnpj
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def resultado(pedido: FgtsPedido) -> Dict[str, Any]:
    if not pedido.resultado_json:
        return {}
    try:
        return json.loads(pedido.resultado_json)
    except ValueError:
        return {}


def gravar_resultado(pedido: FgtsPedido, dados: Dict[str, Any]) -> None:
    pedido.resultado_json = json.dumps(dados, ensure_ascii=False)


def normalizar_competencia(valor: Any) -> str:
    """'02/2026' ou '202602' → '202602'."""
    texto = str(valor or '').strip()
    if '/' in texto:
        mes, _, ano = texto.partition('/')
        texto = f'{ano.strip()}{mes.strip().zfill(2)}'
    return ''.join(ch for ch in texto if ch.isdigit())


def ultima_consulta_hoje(cnpj: str) -> Optional[FgtsPedido]:
    inicio = datetime.utcnow() - timedelta(hours=12)
    return (FgtsPedido.query
            .filter_by(cnpj=cnpj, acao='consultar', status='concluido')
            .filter(FgtsPedido.concluido_em >= inicio)
            .order_by(FgtsPedido.id.desc()).first())


def ultimo_pedido_recente(cnpj: str) -> Optional[FgtsPedido]:
    inicio = datetime.utcnow() - timedelta(hours=12)
    return (FgtsPedido.query.filter_by(cnpj=cnpj)
            .filter(FgtsPedido.criado_em >= inicio)
            .order_by(FgtsPedido.id.desc()).first())


def pedido_aberto(cnpj: str) -> Optional[FgtsPedido]:
    return (FgtsPedido.query.filter_by(cnpj=cnpj)
            .filter(FgtsPedido.status.in_(ABERTOS))
            .order_by(FgtsPedido.id.desc()).first())


def pedidos_hoje_do_link(link_id: int) -> int:
    inicio = datetime.utcnow() - timedelta(hours=24)
    return (FgtsPedido.query.filter_by(link_id=link_id)
            .filter(FgtsPedido.criado_em >= inicio).count())


def criar(*, company_id: int, cnpj: str, acao: str, competencia: Any = None,
          link_id: Optional[int] = None, origem: str = 'link',
          solicitado_por: Optional[str] = None) -> FgtsPedido:
    if not liberado_para(cnpj):
        raise PedidoRecusado('O FGTS por aqui ainda não está ligado. Fale com o escritório.', 503)
    if acao not in ACOES:
        raise PedidoRecusado('Ação inválida.')
    aberto = pedido_aberto(cnpj)
    if aberto:
        # Não é erro: a página retoma o acompanhamento do pedido que já está andando.
        return aberto
    if link_id is not None and pedidos_hoje_do_link(link_id) >= max_pedidos_dia():
        raise PedidoRecusado(f'Limite de {max_pedidos_dia()} pedidos de FGTS por dia. '
                             'Tente amanhã ou fale com o escritório.', 429)
    escolha: List[str] = []
    if acao == 'emitir':
        consulta = ultima_consulta_hoje(cnpj)
        validas = [c['competencia'] for c in resultado(consulta).get('competencias', [])] if consulta else []
        pedidas = competencia if isinstance(competencia, list) else [competencia]
        for c in pedidas:
            comp = normalizar_competencia(c)
            if comp not in validas:
                raise PedidoRecusado('Consulte o FGTS de novo e escolha os meses da lista.', 409)
            if comp not in escolha:
                escolha.append(comp)
        if not escolha:
            raise PedidoRecusado('Escolha ao menos um mês.')
        competencia = escolha[0]
    else:
        competencia = None
    pedido = FgtsPedido(company_id=company_id, cnpj=cnpj, link_id=link_id, acao=acao,
                        competencia=competencia, status='fila', etapa='fila',
                        origem=origem[:20], solicitado_por=(solicitado_por or '')[:120] or None)
    if escolha:
        gravar_resultado(pedido, {'escolha': escolha})
    db.session.add(pedido)
    db.session.commit()
    from app.services import fgts_digital_robo
    fgts_digital_robo.robo().enfileirar(pedido.id)
    return pedido


def atualizar(pedido: FgtsPedido, *, status: Optional[str] = None, etapa: Optional[str] = None,
              mensagem: Optional[str] = None) -> None:
    if status:
        pedido.status = status
        if status == 'executando' and not pedido.iniciado_em:
            pedido.iniciado_em = datetime.utcnow()
        if status in ('concluido', 'erro'):
            pedido.concluido_em = datetime.utcnow()
    if etapa:
        pedido.etapa = etapa
    if mensagem is not None:
        pedido.mensagem = mensagem[:500]
    pedido.atualizado_em = datetime.utcnow()
    db.session.commit()


def posicao_na_fila(pedido: FgtsPedido) -> int:
    if pedido.status != 'fila':
        return 0
    return FgtsPedido.query.filter(FgtsPedido.status == 'fila', FgtsPedido.id < pedido.id).count() + 1


def serializar(pedido: FgtsPedido, *, base_pdf: Optional[str] = None) -> Dict[str, Any]:
    dados = resultado(pedido)
    etapa = pedido.etapa or 'fila'
    indice = CHAVES_ETAPAS.index(etapa) if etapa in CHAVES_ETAPAS else 0
    guias = []
    for n, g in enumerate(dados.get('guias', [])):
        item = {k: g.get(k) for k in ('grupo', 'vencimento', 'identificador', 'valor', 'competencia')}
        item['pdf_url'] = f'{base_pdf}/{n}' if base_pdf else None
        guias.append(item)
    etapas = []
    for i, (chave, rotulo) in enumerate(ETAPAS):
        if chave == 'captcha' and not dados.get('teve_captcha') and etapa != 'captcha':
            continue
        if chave == 'emitindo' and pedido.acao != 'emitir' and not (
                dados.get('emitiu') or etapa == 'emitindo'):
            continue
        if chave == 'consultando' and pedido.acao == 'emitir':
            continue
        if pedido.status == 'concluido':
            situacao = 'feito'
        elif i < indice:
            situacao = 'feito'
        elif i == indice:
            situacao = 'erro' if pedido.status == 'erro' else 'agora'
        else:
            situacao = 'depois'
        if chave == 'consultando' and pedido.status == 'aguardando_escolha':
            etapas.append({'chave': chave, 'rotulo': rotulo, 'situacao': 'feito'})
            etapas.append({'chave': 'escolha', 'rotulo': 'Esperando você marcar os meses acima',
                           'situacao': 'agora'})
            continue
        etapas.append({'chave': chave, 'rotulo': rotulo, 'situacao': situacao})
    return {
        'id': pedido.id,
        'acao': pedido.acao,
        'competencia': pedido.competencia,
        'status': pedido.status,
        'etapa': etapa,
        'etapas': etapas,
        'mensagem': pedido.mensagem,
        'fila': posicao_na_fila(pedido),
        'aguardando_captcha': pedido.status == 'aguardando_captcha',
        'aguardando_escolha': pedido.status == 'aguardando_escolha',
        'competencias': dados.get('competencias', []),
        'total_em_aberto': round(sum(c.get('total') or 0 for c in dados.get('competencias', [])), 2),
        'guias': guias,
        'criado_em': pedido.criado_em.isoformat() if pedido.criado_em else None,
        'concluido_em': pedido.concluido_em.isoformat() if pedido.concluido_em else None,
    }


def validar_escolha(pedido: FgtsPedido, competencias: Any) -> List[str]:
    """Só competências que o próprio FGTS Digital listou neste pedido."""
    if pedido.status != 'aguardando_escolha':
        raise PedidoRecusado('Este pedido não está esperando escolha.', 409)
    validas = [c['competencia'] for c in resultado(pedido).get('competencias', [])]
    if not isinstance(competencias, list):
        competencias = [competencias]
    escolha = []
    for c in competencias:
        comp = normalizar_competencia(c)
        if comp not in validas:
            raise PedidoRecusado('Competência fora da lista do FGTS Digital.')
        if comp not in escolha:
            escolha.append(comp)
    if not escolha:
        raise PedidoRecusado('Escolha ao menos uma competência.')
    return escolha


def caminho_pdf(pedido: FgtsPedido, indice: int) -> Optional[Path]:
    guias: List[Dict[str, Any]] = resultado(pedido).get('guias', [])
    if indice < 0 or indice >= len(guias):
        return None
    arquivo = guias[indice].get('arquivo')
    if not arquivo:
        return None
    caminho = pasta_pdfs(pedido.cnpj) / Path(arquivo).name   # nunca sai da pasta do CNPJ
    return caminho if caminho.exists() else None


def pdf_e_da_empresa(conteudo: bytes, cnpj: str) -> bool:
    """O texto da GFD traz o CNPJ do empregador (ex.: '36.298.150'). Sem ele, não serve."""
    raiz = cnpj[:8]
    raiz_fmt = f'{raiz[:2]}.{raiz[2:5]}.{raiz[5:8]}'
    try:
        import io
        from PyPDF2 import PdfReader
        texto = ''.join((p.extract_text() or '') for p in PdfReader(io.BytesIO(conteudo)).pages[:2])
    except Exception:  # PDF ilegível não sai
        logger.warning('PDF do FGTS ilegível para conferir CNPJ %s', cnpj)
        return False
    so_digitos = ''.join(ch for ch in texto if ch.isdigit())
    return raiz_fmt in texto or raiz in so_digitos
