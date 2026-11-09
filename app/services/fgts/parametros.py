"""Parâmetros do FGTS Digital, editados pelo administrador na Central.

Ordem de leitura de cada valor: o que o administrador gravou na tela → a variável de
ambiente antiga, quando existe → o padrão abaixo. Assim nada muda em produção até alguém
salvar, e a tela passa a mandar a partir daí. Toda gravação fica no histórico.

Datas de vencimento, dias liberados, valores e encargos NÃO são parâmetros: vêm sempre do
portal, para a Central nunca contrariar o cálculo oficial.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.extensions import db

BOOL, INT, TEXTO, HORA, LISTA_CNPJ, ESCOLHA = 'bool', 'int', 'texto', 'hora', 'lista_cnpj', 'escolha'


@dataclass(frozen=True)
class Parametro:
    chave: str
    grupo: str
    rotulo: str
    tipo: str
    padrao: Any
    ambiente: Optional[str] = None
    minimo: Optional[int] = None
    maximo: Optional[int] = None
    opcoes: tuple = ()
    ajuda: str = ''
    publico: bool = False          # entra em /api/interno/fgts/config


CATALOGO: List[Parametro] = [
    # Alcance
    Parametro('habilitado', 'Alcance', 'Módulo FGTS ligado', BOOL, False, 'FGTS_DIGITAL_HABILITADO', publico=True),
    Parametro('cnpjs_piloto', 'Alcance', 'CNPJs do piloto no link do cliente (vazio = todos)', LISTA_CNPJ, [], 'FGTS_DIGITAL_CNPJS'),
    Parametro('executor', 'Alcance', 'Quem executa no portal', ESCOLHA, 'agente', 'FGTS_EXECUTOR',
              opcoes=('agente', 'vps'), ajuda='agente = PC do escritório; vps = robô no servidor', publico=True),
    Parametro('link_cliente', 'Alcance', 'FGTS no link sem senha do cliente', BOOL, False, 'FGTS_LINK_CLIENTE',
              ajuda='Desligado por decisão de 09/11/2026: só o escritório pede FGTS.'),
    Parametro('modo_avulso', 'Alcance', 'Pedido avulso (uma empresa)', BOOL, True, publico=True),
    Parametro('modo_lote', 'Alcance', 'Pedido em lote', BOOL, True, publico=True),
    # Débitos
    Parametro('incluir_mensal', 'Débitos', 'Incluir mensal', BOOL, True, publico=True),
    Parametro('incluir_rescisorio', 'Débitos', 'Incluir rescisório', BOOL, True, publico=True),
    Parametro('incluir_processo_trabalhista', 'Débitos', 'Incluir processo trabalhista', BOOL, True, publico=True),
    Parametro('incluir_consignado', 'Débitos', 'Incluir consignado', BOOL, True, publico=True),
    Parametro('max_competencias', 'Débitos', 'Máximo de competências por consulta (0 = todas)', INT, 0,
              'FGTS_MAX_COMPETENCIAS', minimo=0, maximo=240),
    # Guia
    Parametro('perfil', 'Guia', 'Perfil de acesso', ESCOLHA, 'procurador', opcoes=('procurador',)),
    Parametro('reaproveitar_guia', 'Guia', 'Reaproveitar guia válida já emitida', BOOL, True, publico=True),
    Parametro('max_emissoes_dia', 'Guia', 'Emissões por empresa, competência e dia', INT, 3, minimo=1, maximo=20),
    Parametro('validade_consulta_h', 'Guia', 'Validade da consulta para permitir emissão (horas)', INT, 12,
              minimo=1, maximo=48),
    Parametro('corte_pagar_hoje', 'Guia', 'Horário de corte para pagar hoje', HORA, '21:30', publico=True,
              ajuda='O Pix da GFD vale até 21h59 do dia; o corte deixa folga.'),
    Parametro('tolerancia_centavos', 'Guia', 'Diferença aceita entre tela e PDF (centavos)', INT, 0,
              minimo=0, maximo=100),
    # Agente
    Parametro('agente_sinal_s', 'Agente', 'Intervalo do sinal (s)', INT, 30, minimo=10, maximo=300),
    Parametro('agente_offline_s', 'Agente', 'Sem sinal por (s) = offline', INT, 120, minimo=30, maximo=1800),
    Parametro('ritmo_s', 'Agente', 'Pausa entre empresas (s)', INT, 5, minimo=1, maximo=120),
    Parametro('reserva_min', 'Agente', 'Tempo máximo por pedido (min)', INT, 10, minimo=2, maximo=60),
    Parametro('max_empresas_lote', 'Agente', 'Máximo de empresas por lote', INT, 50, minimo=1, maximo=500),
    Parametro('janela_inicio', 'Agente', 'Janela de operação: início', HORA, '08:00'),
    Parametro('janela_fim', 'Agente', 'Janela de operação: fim', HORA, '18:00'),
    Parametro('so_dias_uteis', 'Agente', 'Só em dias úteis', BOOL, True),
    Parametro('versao_minima', 'Agente', 'Versão mínima do agente', TEXTO, ''),
    Parametro('captcha_timeout_s', 'Agente', 'Espera pela verificação do gov.br (s)', INT, 300,
              'FGTS_CAPTCHA_TIMEOUT_S', minimo=60, maximo=1800),
    Parametro('login_timeout_s', 'Agente', 'Tempo máximo do login, fora a verificação (s)', INT, 60,
              'FGTS_LOGIN_TIMEOUT_S', minimo=20, maximo=600),
    Parametro('certificado_nome', 'Agente', 'Certificado escolhido no login', TEXTO,
              'NESCON SERVICOS EMPRESARIAIS LTDA:35736034000123',
              ajuda='Nome exato como aparece na lista de certificados do Chrome.'),
    # Consumidores
    Parametro('atendechat_habilitado', 'Consumidores', 'Atendechat pode pedir FGTS', BOOL, True, publico=True),
    Parametro('portal_habilitado', 'Consumidores', 'Portal (admin) pode pedir FGTS', BOOL, True, publico=True),
    Parametro('pix_na_mensagem', 'Consumidores', 'Mandar o Pix copia e cola na mensagem', BOOL, True, publico=True),
    Parametro('reconferir_dias_uteis', 'Consumidores', 'Reconferir pagamento após envio (dias úteis)', INT, 5,
              minimo=1, maximo=30, publico=True),
    # Alertas
    Parametro('aviso_whatsapp', 'Alertas', 'WhatsApp do escritório para avisos (login, agente fora)', TEXTO, ''),
    # Dados
    Parametro('retencao_pdf_dias', 'Dados', 'Guardar PDFs por (dias)', INT, 1825, minimo=30, maximo=3650),
    Parametro('retencao_json_dias', 'Dados', 'Guardar respostas do portal por (dias)', INT, 90, minimo=7, maximo=730),
    Parametro('retencao_prints_dias', 'Dados', 'Guardar prints de erro por (dias)', INT, 30, minimo=1, maximo=365),
    # Saúde fiscal
    Parametro('peso_saude', 'Saúde fiscal', 'Peso do FGTS na nota (0 a 100)', INT, 15, minimo=0, maximo=100),
]
POR_CHAVE: Dict[str, Parametro] = {p.chave: p for p in CATALOGO}
_RE_HORA = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


class ParametroInvalido(ValueError):
    pass


def _so_digitos(valor: Any) -> str:
    return ''.join(ch for ch in str(valor or '') if ch.isdigit())


def validar(p: Parametro, valor: Any) -> Any:
    """Converte e confere o valor; levanta ParametroInvalido com mensagem para a tela."""
    if p.tipo == BOOL:
        if isinstance(valor, bool):
            return valor
        texto = str(valor).strip().lower()
        if texto in ('1', 'true', 'sim', 'on', 'yes'):
            return True
        if texto in ('0', 'false', 'nao', 'não', 'off', 'no', ''):
            return False
        raise ParametroInvalido(f'{p.rotulo}: use sim ou não.')
    if p.tipo == INT:
        try:
            numero = int(str(valor).strip())
        except (TypeError, ValueError):
            raise ParametroInvalido(f'{p.rotulo}: informe um número inteiro.')
        if p.minimo is not None and numero < p.minimo or p.maximo is not None and numero > p.maximo:
            raise ParametroInvalido(f'{p.rotulo}: use um valor entre {p.minimo} e {p.maximo}.')
        return numero
    if p.tipo == HORA:
        texto = str(valor or '').strip()
        if not _RE_HORA.match(texto):
            raise ParametroInvalido(f'{p.rotulo}: use o formato HH:MM.')
        return texto
    if p.tipo == ESCOLHA:
        texto = str(valor or '').strip().lower()
        if texto not in p.opcoes:
            raise ParametroInvalido(f'{p.rotulo}: escolha entre {", ".join(p.opcoes)}.')
        return texto
    if p.tipo == LISTA_CNPJ:
        itens = valor if isinstance(valor, list) else str(valor or '').replace('\n', ',').split(',')
        cnpjs = [_so_digitos(i) for i in itens if _so_digitos(i)]
        ruins = [c for c in cnpjs if len(c) != 14]
        if ruins:
            raise ParametroInvalido(f'{p.rotulo}: CNPJ inválido ({ruins[0]}).')
        return sorted(set(cnpjs))
    texto = str(valor or '').strip()
    if len(texto) > 200:
        raise ParametroInvalido(f'{p.rotulo}: texto longo demais.')
    return texto


def _do_ambiente(p: Parametro) -> Any:
    if not p.ambiente:
        return None
    bruto = os.getenv(p.ambiente)
    if bruto is None or bruto == '':
        return None
    try:
        return validar(p, bruto)
    except ParametroInvalido:
        return None


def _gravados() -> Dict[str, Any]:
    from app.fgts_models import FgtsParametro
    saida = {}
    for linha in FgtsParametro.query.all():
        try:
            saida[linha.chave] = json.loads(linha.valor_json)
        except ValueError:
            continue
    return saida


def valor(chave: str) -> Any:
    p = POR_CHAVE[chave]
    gravado = _gravados().get(chave)
    if gravado is not None:
        try:
            return validar(p, gravado)
        except ParametroInvalido:
            pass
    do_ambiente = _do_ambiente(p)
    return do_ambiente if do_ambiente is not None else p.padrao


def todos() -> List[Dict[str, Any]]:
    gravados = _gravados()
    saida = []
    for p in CATALOGO:
        origem = 'tela' if p.chave in gravados else ('ambiente' if _do_ambiente(p) is not None else 'padrão')
        saida.append({'chave': p.chave, 'grupo': p.grupo, 'rotulo': p.rotulo, 'tipo': p.tipo,
                      'valor': valor(p.chave), 'padrao': p.padrao, 'origem': origem,
                      'opcoes': list(p.opcoes), 'minimo': p.minimo, 'maximo': p.maximo, 'ajuda': p.ajuda})
    return saida


def publicos() -> Dict[str, Any]:
    return {p.chave: valor(p.chave) for p in CATALOGO if p.publico}


def salvar(alteracoes: Dict[str, Any], por: Optional[str]) -> List[str]:
    """Valida tudo antes de gravar qualquer coisa. Devolve as chaves que mudaram."""
    from app.fgts_models import FgtsParametro, FgtsParametroHistorico
    desconhecidas = [c for c in alteracoes if c not in POR_CHAVE]
    if desconhecidas:
        raise ParametroInvalido(f'Parâmetro desconhecido: {desconhecidas[0]}.')
    novos = {c: validar(POR_CHAVE[c], v) for c, v in alteracoes.items()}
    mudaram = []
    for chave, novo in novos.items():
        atual = valor(chave)
        if novo == atual:
            continue
        linha = db.session.get(FgtsParametro, chave)
        anterior = linha.valor_json if linha else json.dumps(atual, ensure_ascii=False)
        if not linha:
            linha = FgtsParametro(chave=chave)
            db.session.add(linha)
        linha.valor_json = json.dumps(novo, ensure_ascii=False)
        linha.atualizado_por = (por or '')[:120] or None
        linha.atualizado_em = datetime.utcnow()
        db.session.add(FgtsParametroHistorico(chave=chave, anterior_json=anterior,
                                              novo_json=linha.valor_json, por=linha.atualizado_por))
        mudaram.append(chave)
    db.session.commit()
    return mudaram


def historico(limite: int = 100) -> List[Dict[str, Any]]:
    from app.fgts_models import FgtsParametroHistorico
    linhas = FgtsParametroHistorico.query.order_by(FgtsParametroHistorico.id.desc()).limit(limite).all()
    return [{'chave': h.chave, 'rotulo': POR_CHAVE[h.chave].rotulo if h.chave in POR_CHAVE else h.chave,
             'anterior': json.loads(h.anterior_json) if h.anterior_json else None,
             'novo': json.loads(h.novo_json), 'por': h.por, 'em': h.em.isoformat()} for h in linhas]


def filtros_portal() -> Dict[str, str]:
    """Os quatro filtros que o portal recebe na consulta de débitos."""
    def b(chave):
        return 'true' if valor(chave) else 'false'
    return {'mensal': b('incluir_mensal'), 'rescisorio': b('incluir_rescisorio'),
            'processoTrabalhista': b('incluir_processo_trabalhista'), 'consignado': b('incluir_consignado')}
