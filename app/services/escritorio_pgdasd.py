"""PGDAS-D do Escritório: Calcular → Transmitir → Gerar DAS (com PDF guardado).

⚠️ DESVIO INTENCIONAL (17o) — não existe no exe. Pedido do Jean em 15/09/2026.

Princípio: **toda verificação que dá para fazer de graça é feita ANTES de gastar.**
Uma chamada paga só sai quando o pré-voo (`preflight`) não encontrou nenhum bloqueio.

Fluxo e custo (estimado por chamada — ver `serpro_pgdasd_client.CUSTO_ESTIMADO`)
-------------------------------------------------------------------------------
1. **Pré-visualizar** (grátis) — monta o JSON da declaração a partir da memória
   (`escritorio_lancamentos`) e mostra os problemas.
2. **Calcular** (1× Declarar) — TRANSDECLARACAO11 com `indicadorTransmissao=false`:
   a Receita devolve os valores devidos SEM transmitir. Guardamos valores + hash.
3. **Transmitir** (1× Declarar, + 1× Consultar se não houver consulta recente) —
   só com o MESMO hash do cálculo e com `indicadorComparacao=true` +
   `valoresParaComparacao`: se a Receita calcular 1 centavo diferente, ela NÃO
   transmite (MSG_ISN_035). Antes, consulta se já existe declaração do PA (feita no
   PGDAS-D web ou por outro sistema) para não mandar "original" em cima de outra.
4. **Gerar DAS** (1× Emitir) — só depois de declaração transmitida/confirmada.
   O PDF fica em disco; baixar de novo NÃO chama a SERPRO. Nova emissão só se o
   usuário pedir (ex.: pagar após o vencimento, com data de consolidação).
5. **Consultar** (1× Consultar) — resolve envio INCERTO (timeout) e confirma
   declarações entregues fora do Central.

Mapeamento da memória (perfil comércio) → atividades do PGDAS-D
---------------------------------------------------------------
* `rec_sem_st`        → atividade 1 (revenda sem ST/monofásica)
* `rec_com_st_mono`   → atividade 2, parcela com COFINS/PIS monofásicos (id 9) e ICMS ST (id 8)
* `rec_monofasica`    → atividade 2, parcela com COFINS/PIS monofásicos (id 9)
* `rec_com_st`        → atividade 2, parcela com ICMS ST (id 8)
* `rec_sem_st_isencao`→ **bloqueia** (isenção/redução de ICMS exige percentual por UF)

As qualificações vão em `qualificacoesTributarias` ({codigoTributo, id}), com os ids do
domínio SERPRO (8 = Substituição Tributária, 9 = Tributação Monofásica).
⚠️ 15/09/2026: a 1ª versão mandava em `isencoes` e a SERPRO recusou no Calcular
("SN-Entregar: Campo 'isencao/identificacao' inválido") — `isencoes` é só para
isenção/redução de verdade (valor + identificador próprio).
**Valide o primeiro caso real com "Calcular" e confira com o PGDAS-D web.**
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import or_, update

from app.extensions import db
from app.services.serpro_pgdasd_client import (
    CUSTO_ESTIMADO, SerproPgdasdClient, custo_de, hash_dados)

logger = logging.getLogger(__name__)

TZ = ZoneInfo('America/Sao_Paulo')
VALIDADE_CALCULO = timedelta(hours=int(os.getenv('PGDASD_VALIDADE_CALCULO_H', '72')))
VALIDADE_CONSULTA = timedelta(hours=int(os.getenv('PGDASD_VALIDADE_CONSULTA_H', '12')))
TRAVA_EXPIRA = timedelta(minutes=15)

TRIBUTOS = {1001: 'IRPJ', 1002: 'CSLL', 1004: 'COFINS', 1005: 'PIS/PASEP',
            1006: 'CPP', 1007: 'ICMS', 1008: 'IPI', 1010: 'ISS'}
ID_ST, ID_MONOFASICA = 8, 9
COFINS, PIS, ICMS = 1004, 1005, 1007


class BloqueioPgdasd(Exception):
    """Pré-voo reprovou: NADA foi enviado à SERPRO (custo zero)."""

    def __init__(self, bloqueios: List[str], avisos: Optional[List[str]] = None,
                 status: int = 422):
        super().__init__('; '.join(bloqueios))
        self.bloqueios = bloqueios
        self.avisos = avisos or []
        self.status = status


# ------------------------------------------------------------------ utilidades
def so_digitos(valor: Any) -> str:
    return re.sub(r'\D', '', str(valor or ''))


def cnpj_valido(cnpj: str) -> bool:
    c = so_digitos(cnpj)
    if len(c) != 14 or c == c[0] * 14:
        return False

    def dv(base: str, pesos: List[int]) -> str:
        resto = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return '0' if resto < 2 else str(11 - resto)

    p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    return c[12] == dv(c[:12], p1) and c[13] == dv(c[:13], [6] + p1)


def pa_de(competencia: str) -> str:
    d = so_digitos(competencia)
    if len(d) != 6:
        raise BloqueioPgdasd(['Competência inválida. Use AAAA-MM.'], status=400)
    return d


def competencia_de(pa: str) -> str:
    return f'{pa[:4]}-{pa[4:]}'


def hoje() -> date:
    return datetime.now(TZ).date()


def _r2(valor: Any) -> float:
    try:
        return round(float(valor or 0) + 0.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _json(texto: Optional[str], padrao: Any) -> Any:
    try:
        return json.loads(texto) if texto else padrao
    except Exception:
        return padrao


# -------------------------------------------------------------- configuração
_CACHE_CERT: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def _checar_certificado(caminho: str, senha: str) -> Dict[str, Any]:
    """Abre o .pfx localmente (grátis) — senha errada ou certificado vencido
    derrubariam a autenticação de qualquer forma."""
    from cryptography.hazmat.primitives.serialization import pkcs12
    mtime = os.path.getmtime(caminho)
    chave = f'{caminho}|{hash_dados(senha)}'
    em_cache = _CACHE_CERT.get(chave)
    if em_cache and em_cache[0] == mtime:
        return em_cache[1]
    info: Dict[str, Any] = {'ok': False}
    try:
        _k, cert, _extra = pkcs12.load_key_and_certificates(
            Path(caminho).read_bytes(), senha.encode('utf-8'))
        if cert is None:
            info['erro'] = 'o arquivo .pfx não contém certificado.'
        else:
            try:
                fim = cert.not_valid_after_utc
            except AttributeError:            # cryptography antigo
                fim = cert.not_valid_after
            info.update(ok=True, valido_ate=fim.date().isoformat())
    except ValueError:
        info['erro'] = 'a senha do certificado não abre o arquivo .pfx.'
    except Exception as exc:
        info['erro'] = f'não foi possível ler o certificado ({exc.__class__.__name__}).'
    _CACHE_CERT[chave] = (mtime, info)
    return info


def checar_configuracao() -> Tuple[List[str], List[str], Dict[str, Any]]:
    from app.models import AppSetting
    from app.services import certificado

    bloqueios, avisos, info = [], [], {}
    s = AppSetting.query.first()
    if not s:
        return ['Configurações do sistema não cadastradas.'], avisos, info
    if s.procurador_pf_habilitado:
        bloqueios.append('Procurador PF está ligado em Configurações, mas o envio por procurador '
                         'ainda não está implementado no Central (SerproProcuradorService não tem '
                         'auth_headers/build_payload). Desligue o procurador PF para usar o '
                         'certificado do escritório.')
    contador = so_digitos(s.contador_cnpj)
    if len(contador) not in (11, 14):
        bloqueios.append('CNPJ/CPF do contador (contratante) não configurado.')
    if not ((s.serpro_consumer_key or '').strip() and (s.serpro_consumer_secret or '').strip()):
        bloqueios.append('Consumer key/secret da SERPRO não configurados.')
    senha = (s.certificado_password or '').strip()
    if not senha:
        bloqueios.append('Senha do certificado A1 não configurada.')
    try:
        caminho = certificado.resolver(s.certificado_path)
    except ValueError as exc:
        bloqueios.append(str(exc))
        caminho = None
    if caminho and senha:
        cert = _checar_certificado(caminho, senha)
        if not cert.get('ok'):
            bloqueios.append(f'Certificado A1: {cert.get("erro")}')
        else:
            info['certificado_valido_ate'] = cert['valido_ate']
            fim = date.fromisoformat(cert['valido_ate'])
            if fim < hoje():
                bloqueios.append(f'Certificado A1 vencido em {fim:%d/%m/%Y}.')
            elif fim - hoje() <= timedelta(days=15):
                avisos.append(f'Certificado A1 vence em {fim:%d/%m/%Y}.')
    return bloqueios, avisos, info


# ------------------------------------------------------------------ contexto
class Contexto:
    def __init__(self, cnpj: str, pa: str, usuario: str = ''):
        from app.escritorio_models import EscritorioDeclaracao, EscritorioLancamento
        from app.models import Company

        self.cnpj = so_digitos(cnpj)
        self.pa = pa
        self.competencia = competencia_de(pa)
        self.usuario = (usuario or '')[:120]
        self.company = Company.query.filter_by(cnpj=self.cnpj).first()
        self.rba: Dict[str, Any] = {}
        self.lancamentos = (EscritorioLancamento.query
                            .filter_by(cnpj=self.cnpj, competencia=self.competencia)
                            .order_by(EscritorioLancamento.perfil).all())
        decl = EscritorioDeclaracao.query.filter_by(cnpj=self.cnpj, pa=pa).first()
        if decl is None:
            decl = EscritorioDeclaracao(cnpj=self.cnpj, pa=pa, situacao='rascunho',
                                        company_id=getattr(self.company, 'id', None))
            db.session.add(decl)
            db.session.commit()
        self.decl = decl

    # consulta recente mostrando declaração entregue
    def declaracoes_na_receita(self) -> Optional[List[Dict[str, Any]]]:
        if not self.decl.consultado_em or datetime.utcnow() - self.decl.consultado_em > VALIDADE_CONSULTA:
            return None
        return _json(self.decl.consulta_json, {}).get('declaracoes') or []


def carregar(cnpj: str, competencia: str, usuario: str = '') -> Contexto:
    return Contexto(cnpj, pa_de(competencia), usuario)


# ------------------------------------------------------------- declaração
# ----------------------------------------------- 1ª declaração / ordem dos PAs
# Manual do PGDAS-D (item 6.3): as receitas brutas dos meses ANTERIORES À OPÇÃO são
# informadas no primeiro acesso; ficam dispensadas se a empresa já era optante nos 12
# PAs anteriores ou se o mês de início de atividade coincide com o PA. Meses já
# declarados não são editáveis (a API ignora o valor). E a Receita só aceita um PA
# depois de transmitidos os anteriores ("É necessário transmitir as seguintes
# declarações: 07/2026").
_RE_PENDENCIAS = re.compile(r'necess[aá]rio\s+transmitir\s+as\s+seguintes\s+declara[cç][oõ]es\s*:?\s*([0-9/ ,;e]+)',
                            re.I)


def pendencias_receita(texto: Optional[str]) -> List[str]:
    """PAs (AAAA-MM) que a Receita mandou transmitir antes, lidos da mensagem de recusa."""
    achado = _RE_PENDENCIAS.search(texto or '')
    if not achado:
        return []
    return sorted({f'{ano}-{mes}' for mes, ano in re.findall(r'(\d{2})/(\d{4})', achado.group(1))})


def _lista_meses(competencias: List[str]) -> str:
    return ', '.join(f'{c[5:]}/{c[:4]}' for c in sorted(competencias))


def pas_transmitidos(cnpj: str, competencias: List[str]) -> set:
    """Competências (AAAA-MM) que o Central sabe estarem transmitidas (pelo Central,
    confirmadas por Consultar ou marcadas como enviadas em Lançamentos)."""
    from app.escritorio_models import EscritorioDeclaracao as D
    from app.escritorio_models import EscritorioLancamento as L
    if not competencias:
        return set()
    feitos = {f'{d.pa[:4]}-{d.pa[4:]}' for d in D.query.filter(
        D.cnpj == cnpj, D.pa.in_([c.replace('-', '') for c in competencias]), D.situacao == 'transmitida')}
    feitos |= {l.competencia for l in L.query.filter(
        L.cnpj == cnpj, L.competencia.in_(competencias), L.transmitido > 0)}
    return feitos


def datas_inicio(ctx: 'Contexto') -> Dict[str, Optional[str]]:
    """Início de atividade e 1º PA no Simples (AAAA-MM). Manual (tela) > Situação Fiscal."""
    from app.escritorio_models import EscritorioEmpresa
    from app.models import RelatorioSitFiscal
    saida: Dict[str, Optional[str]] = {'atividade': None, 'simples': None, 'origem_simples': None}
    if ctx.company is None:
        return saida
    emp = EscritorioEmpresa.query.filter_by(company_id=ctx.company.id).first()
    if emp is not None:
        saida['atividade'] = emp.inicio_atividade or None
        if emp.inicio_simples:
            saida['simples'], saida['origem_simples'] = emp.inicio_simples, 'manual'
    if not saida['simples']:
        rel = (RelatorioSitFiscal.query
               .filter(RelatorioSitFiscal.company_id == ctx.company.id,
                       RelatorioSitFiscal.simples_nacional_inclusao.isnot(None))
               .order_by(RelatorioSitFiscal.data_hora.desc()).first())
        if rel is not None:
            saida['simples'] = rel.simples_nacional_inclusao.strftime('%Y-%m')
            saida['origem_simples'] = 'situacao_fiscal'
    return saida


def montar_declaracao(ctx: Contexto, tipo: int = 1) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """JSON de TRANSDECLARACAO11 a partir da memória. (dados, bloqueios, avisos)."""
    from app.escritorio_models import EscritorioPgdasHistorico
    from app.services.escritorio_pgdas import meses_rbt12

    bloqueios, avisos = [], []
    if not ctx.lancamentos:
        bloqueios.append(f'Sem lançamento em {ctx.competencia}. Gere pelo Ler XML e salve na memória.')
        return {}, bloqueios, avisos

    atividade1 = 0.0
    parcelas2: List[Dict[str, Any]] = []
    for lanc in ctx.lancamentos:
        if lanc.perfil != 'comercio':
            bloqueios.append(f'Perfil "{lanc.perfil}" ainda não tem mapeamento para as atividades '
                             'do PGDAS-D (hoje só comércio). Nada será enviado.')
            continue
        isencao = _r2(lanc.rec_sem_st_isencao)
        if isencao > 0:
            bloqueios.append(f'"Rec. s/ ST c/ isenção" (R$ {isencao:.2f}) ainda não é enviada pela API '
                             '(exige tributo/percentual por UF). Declare pelo PGDAS-D web ou ajuste.')
        atividade1 += _r2(lanc.rec_sem_st)
        for valor, qualif in (
            (_r2(lanc.rec_com_st_mono), [(COFINS, ID_MONOFASICA), (PIS, ID_MONOFASICA), (ICMS, ID_ST)]),
            (_r2(lanc.rec_monofasica), [(COFINS, ID_MONOFASICA), (PIS, ID_MONOFASICA)]),
            (_r2(lanc.rec_com_st), [(ICMS, ID_ST)]),
        ):
            if valor < 0:
                bloqueios.append('Há valor negativo nos lançamentos (devoluções maiores que as vendas). '
                                 'Revise em Lançamentos.')
            elif valor > 0:
                parcelas2.append({'valor': valor, 'qualificacoesTributarias': [
                    {'codigoTributo': cod, 'id': ident} for cod, ident in qualif]})
        soma_linhas = _r2(_r2(lanc.rec_sem_st) + _r2(lanc.rec_sem_st_isencao) + _r2(lanc.rec_com_st_mono)
                          + _r2(lanc.rec_monofasica) + _r2(lanc.rec_com_st))
        if abs(soma_linhas - _r2(lanc.total_receita)) > 0.01:
            bloqueios.append(f'Total da receita (R$ {_r2(lanc.total_receita):.2f}) não fecha com a soma das '
                             f'linhas (R$ {soma_linhas:.2f}). Salve o lançamento de novo.')
        if not lanc.ok:
            bloqueios.append('Lançamento não está marcado como OK (conferido) em Lançamentos.')

    atividade1 = _r2(atividade1)
    if atividade1 < 0:
        bloqueios.append('Receita sem ST negativa. Revise em Lançamentos.')
    atividades = []
    if atividade1 > 0:
        atividades.append({'idAtividade': 1, 'valorAtividade': atividade1,
                           'receitasAtividade': [{'valor': atividade1}]})
    if parcelas2:
        atividades.append({'idAtividade': 2, 'valorAtividade': _r2(sum(p['valor'] for p in parcelas2)),
                           'receitasAtividade': parcelas2})
    rpa = _r2(sum(a['valorAtividade'] for a in atividades))
    if rpa == 0:
        avisos.append('Receita zero: será uma declaração SEM MOVIMENTO.')

    estabelecimento: Dict[str, Any] = {'cnpjCompleto': ctx.cnpj}
    if atividades:
        estabelecimento['atividades'] = atividades
    if not ctx.cnpj.endswith('0001') and ctx.cnpj[8:12] != '0001':
        avisos.append('O CNPJ não é de matriz (0001). A declaração deve ser feita pela matriz.')
    avisos.append('Filiais: a declaração precisa listar TODOS os estabelecimentos ativos no período. '
                  'Hoje o Central envia só o CNPJ do lançamento.')

    declaracao: Dict[str, Any] = {
        'tipoDeclaracao': tipo,
        'receitaPaCompetenciaInterno': rpa,
        'receitaPaCompetenciaExterno': 0.0,
        'estabelecimentos': [estabelecimento],
    }
    # Receitas brutas anteriores (RBA) — ver regras em `datas_inicio`/manual 6.3.
    from app.escritorio_models import EscritorioLancamento
    meses = meses_rbt12(ctx.competencia)
    datas = datas_inicio(ctx)
    abertura, entrada = datas['atividade'], datas['simples']
    historico = {h.competencia: _r2(h.receita_bruta) for h in EscritorioPgdasHistorico.query.filter(
        EscritorioPgdasHistorico.cnpj == ctx.cnpj,
        EscritorioPgdasHistorico.competencia.in_(meses)).all()}
    lancados: Dict[str, float] = {}
    for lanc in EscritorioLancamento.query.filter(EscritorioLancamento.cnpj == ctx.cnpj,
                                                  EscritorioLancamento.competencia.in_(meses),
                                                  EscritorioLancamento.ok.is_(True)).all():
        lancados[lanc.competencia] = _r2(lancados.get(lanc.competencia, 0) + _r2(lanc.total_receita))
    transmitidos = pas_transmitidos(ctx.cnpj, meses)
    existentes = [m for m in meses if not abertura or m >= abertura]      # antes da abertura não há receita
    if abertura and abertura >= ctx.competencia:
        exigidos: List[str] = []                                           # início de atividade no próprio PA
    elif entrada:
        exigidos = [m for m in existentes if m < entrada]                  # só os anteriores à opção
    else:
        exigidos = list(existentes)                                        # sem data de opção: não dá para dispensar
    exigidos = [m for m in exigidos if m not in transmitidos]
    valores: Dict[str, float] = {}
    origem: Dict[str, str] = {}
    for m in existentes:
        if m in historico:
            valores[m], origem[m] = historico[m], 'extrato'
        elif m in lancados:
            valores[m], origem[m] = lancados[m], 'lancamento'
    if valores:
        declaracao['receitasBrutasAnteriores'] = [
            {'pa': int(m.replace('-', '')), 'valorInterno': valores[m], 'valorExterno': 0.0}
            for m in meses if m in valores]
    faltando = [m for m in exigidos if m not in valores]
    ctx.rba = {'abertura': abertura, 'entrada_simples': entrada, 'origem_entrada': datas['origem_simples'],
               'exigidos': exigidos, 'faltando': faltando, 'movimento': rpa > 0,
               'de_lancamento': [m for m in exigidos if origem.get(m) == 'lancamento']}
    if faltando:
        dica_datas = ('' if (abertura or entrada) else
                      ' Se a empresa já é optante há 12 meses ou mais, ou abriu há menos de 12 meses, '
                      'informe o início de atividade / entrada no Simples — aí esses meses deixam de ser exigidos.')
        if rpa > 0:
            bloqueios.append(f'Receitas brutas anteriores obrigatórias sem valor: {_lista_meses(faltando)}. '
                             'Empresa com movimento: importe o extrato/espelho do PGDAS-D (Upload PGDAS-D) '
                             'ou gere e marque OK o lançamento desses meses.' + dica_datas)
        else:
            avisos.append(f'Declaração sem movimento, mas faltam receitas brutas anteriores de '
                          f'{_lista_meses(faltando)}. Se for a 1ª declaração no Simples, a Receita pode recusar.'
                          + dica_datas)
    if ctx.rba['de_lancamento']:
        avisos.append(f'Receitas anteriores de {_lista_meses(ctx.rba["de_lancamento"])} vêm dos lançamentos '
                      '(XML), não do extrato. Confira: depois de declaradas não podem ser alteradas.')
    inicio = max([d for d in (abertura, entrada) if d] or [''])
    if inicio:
        sem_confirmacao = [m for m in meses if m >= inicio and m not in transmitidos]
        if sem_confirmacao:
            avisos.append(f'Ordem dos PAs: a Receita só aceita {ctx.competencia[5:]}/{ctx.competencia[:4]} '
                          f'depois de {_lista_meses(sem_confirmacao)}, que ainda não constam como transmitidas '
                          'no Central (se foram pelo PGDAS-D web, ignore).')

    dados = {'cnpjCompleto': ctx.cnpj, 'pa': int(ctx.pa), 'indicadorTransmissao': False,
             'indicadorComparacao': False, 'declaracao': declaracao}
    return dados, bloqueios, avisos


def hash_declaracao(dados: Dict[str, Any]) -> str:
    """Hash do CONTEÚDO (sem tipo/indicadores/valores de comparação)."""
    base = json.loads(json.dumps(dados))
    for chave in ('indicadorTransmissao', 'indicadorComparacao', 'valoresParaComparacao'):
        base.pop(chave, None)
    base.get('declaracao', {}).pop('tipoDeclaracao', None)
    return hash_dados(base)


def tipo_para(ctx: Contexto, retificar: bool) -> int:
    if retificar or ctx.decl.situacao == 'transmitida' or ctx.declaracoes_na_receita():
        return 2
    return 1


# ------------------------------------------------------------------- pré-voo
def preflight(ctx: Contexto, operacao: str, custo: float = 0.0, **opcoes) -> Dict[str, Any]:
    from app.services.limite_gasto_service import LimiteGastoService
    from app.services.procuracao_service import ProcuracaoService

    bloqueios, avisos, info = checar_configuracao()
    if not cnpj_valido(ctx.cnpj):
        bloqueios.append(f'CNPJ {ctx.cnpj} inválido (dígito verificador).')
    if ctx.company is None:
        bloqueios.append('Empresa não cadastrada no Central. Cadastre o CNPJ antes de usar a SERPRO.')
    elif not ctx.company.ativo:
        bloqueios.append('Empresa inativa no cadastro.')
    atual = hoje().strftime('%Y%m')
    if ctx.pa < '201801':
        bloqueios.append('PGDAS-D pela API só a partir de 01/2018.')
    elif ctx.pa > atual:
        bloqueios.append(f'Competência {ctx.competencia} ainda não começou.')
    elif ctx.pa == atual and operacao in ('calcular', 'transmitir'):
        avisos.append('A competência é o mês corrente — a receita ainda pode mudar.')

    if ctx.company is not None and custo > 0:
        pode, motivo = ProcuracaoService.pode_gastar(ctx.company)
        if not pode:
            bloqueios.append(f'Chamadas pagas travadas para esta empresa: {motivo}. Veja Procurações.')
    if custo > 0:
        pode, motivo = LimiteGastoService.pode_gastar(custo)
        if not pode:
            bloqueios.append(f'Teto de gasto: {motivo}.')

    decl = ctx.decl
    if decl.operacao and decl.operacao_desde and datetime.utcnow() - decl.operacao_desde < TRAVA_EXPIRA:
        bloqueios.append(f'Já existe "{decl.operacao}" em andamento para esta empresa/competência '
                         f'(por {decl.operacao_por or "outro usuário"}). Aguarde.')

    if operacao in ('calcular', 'transmitir', 'pre_visualizar'):
        _dados, b, a = montar_declaracao(ctx)
        bloqueios += b
        avisos += a
    if operacao in ('calcular', 'transmitir'):
        pendentes = [p for p in pendencias_receita(decl.ultimo_erro) if p < ctx.competencia]
        faltam = [p for p in pendentes if p not in pas_transmitidos(ctx.cnpj, pendentes)]
        if faltam and not opcoes.get('confirmar_pendencias'):
            bloqueios.append(f'A Receita exige transmitir antes: {_lista_meses(faltam)}. Transmita essa(s) '
                             'competência(s) primeiro. Se já transmitiu fora do Central, marque a confirmação — '
                             'assim não pagamos outro cálculo recusado pelo mesmo motivo.')
    if operacao in ('calcular', 'transmitir') and decl.situacao == 'incerta':
        bloqueios.append('O último envio ficou INCERTO (a SERPRO não respondeu). Clique em Consultar '
                         'para saber se a declaração foi recebida antes de tentar de novo.')
    if operacao == 'transmitir':
        retificar = bool(opcoes.get('retificar'))
        if not retificar and decl.situacao == 'transmitida':
            bloqueios.append('Declaração já transmitida para esta competência. Para alterar, use Retificar.')
        if retificar and decl.situacao != 'transmitida' and not ctx.declaracoes_na_receita():
            bloqueios.append('Não há declaração conhecida para retificar. Consulte a Receita primeiro.')
        dados, _b, _a = montar_declaracao(ctx)
        h = hash_declaracao(dados) if dados else None
        if not decl.hash_dados or not decl.calculado_em:
            bloqueios.append('Calcule antes de transmitir (o valor calculado é conferido na transmissão).')
        elif decl.hash_dados != h:
            bloqueios.append('O lançamento mudou depois do cálculo. Calcule de novo.')
        elif datetime.utcnow() - decl.calculado_em > VALIDADE_CALCULO:
            bloqueios.append('O cálculo tem mais de 72 h. Calcule de novo antes de transmitir.')
        confirmado = opcoes.get('hash_confirmado')
        if confirmado is not None and confirmado != decl.hash_dados:
            bloqueios.append('A tela está desatualizada em relação ao cálculo. Recarregue e confira.')
    if operacao == 'gerar_das':
        if decl.situacao == 'incerta':
            bloqueios.append('Envio INCERTO: consulte a Receita antes de gerar o DAS.')
        elif not (decl.situacao == 'transmitida' or ctx.declaracoes_na_receita()
                  or opcoes.get('confirmar_externa')):
            bloqueios.append('Não há declaração transmitida conhecida para este período. Transmita pelo '
                             'Central ou clique em Consultar. Sem declaração a SERPRO recusa o DAS '
                             '(MSG_ISN_005) — e a chamada é cobrada.')
    if operacao == 'recuperar':
        if decl.situacao == 'incerta':
            bloqueios.append('Envio INCERTO: clique em Consultar primeiro.')
        elif not (decl.situacao == 'transmitida' or ctx.declaracoes_na_receita()):
            bloqueios.append('Não há declaração conhecida para este período. Clique em Consultar primeiro.')
        elif not opcoes.get('forcar') and decl.arquivo_declaracao and decl.arquivo_recibo \
                and Path(decl.arquivo_declaracao).is_file() and Path(decl.arquivo_recibo).is_file():
            bloqueios.append('Declaração e recibo já estão guardados no Central (abrem sem custo).')
    return {'bloqueios': bloqueios, 'avisos': avisos, 'info': info}


def _exigir(pf: Dict[str, Any]) -> None:
    if pf['bloqueios']:
        raise BloqueioPgdasd(pf['bloqueios'], pf['avisos'])


# --------------------------------------------------------------------- trava
def _travar(ctx: Contexto, operacao: str) -> None:
    from app.escritorio_models import EscritorioDeclaracao as D
    agora = datetime.utcnow()
    res = db.session.execute(
        update(D).where(D.id == ctx.decl.id)
        .where(or_(D.operacao.is_(None), D.operacao_desde < agora - TRAVA_EXPIRA))
        .values(operacao=operacao, operacao_desde=agora, operacao_por=ctx.usuario or None))
    db.session.commit()
    if res.rowcount != 1:
        raise BloqueioPgdasd(['Outra operação começou agora mesmo nesta empresa/competência. Aguarde.'],
                             status=409)
    db.session.refresh(ctx.decl)
    ctx.trava_propria = True       # o estado devolvido a quem chamou não mostra a própria trava


def _destravar(ctx: Contexto) -> None:
    ctx.trava_propria = False
    ctx.decl.operacao = None
    ctx.decl.operacao_desde = None
    ctx.decl.operacao_por = None
    db.session.commit()


def _erro(ctx: Contexto, texto: str) -> None:
    ctx.decl.ultimo_erro = texto[:4000]
    ctx.decl.ultimo_erro_em = datetime.utcnow()


# ------------------------------------------------------------------ arquivos
def pasta(cnpj: str, pa: str) -> Path:
    from flask import current_app
    base = Path(current_app.config.get('REPORTS_DIR') or 'reports')
    destino = base / 'escritorio' / 'pgdasd' / cnpj / pa
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def salvar_pdf_b64(cnpj: str, pa: str, nome: str, conteudo_b64: Optional[str]) -> Optional[str]:
    if not conteudo_b64 or not isinstance(conteudo_b64, str):
        return None
    try:
        binario = base64.b64decode(conteudo_b64, validate=False)
    except (binascii.Error, ValueError):
        logger.warning('PDF base64 inválido (%s)', nome)
        return None
    if not binario.startswith(b'%PDF'):
        logger.warning('Conteúdo não é PDF (%s)', nome)
        return None
    destino = pasta(cnpj, pa) / re.sub(r'[^\w.\-]', '_', nome)
    temporario = destino.with_suffix(destino.suffix + '.tmp')
    temporario.write_bytes(binario)
    os.replace(temporario, destino)
    return str(destino)


def _novo_cliente(cliente: Optional[SerproPgdasdClient]) -> SerproPgdasdClient:
    return cliente or SerproPgdasdClient()


# ------------------------------------------------------------------ operações
def pre_visualizar(ctx: Contexto) -> Dict[str, Any]:
    dados, bloqueios, avisos = montar_declaracao(ctx, tipo_para(ctx, False))
    return {'dados': dados, 'hash': hash_declaracao(dados) if dados else None,
            'bloqueios': bloqueios, 'avisos': avisos}


def calcular(ctx: Contexto, cliente: Optional[SerproPgdasdClient] = None,
             confirmar_pendencias: bool = False) -> Dict[str, Any]:
    custo = float(custo_de('TRANSDECLARACAO11'))
    pf = preflight(ctx, 'calcular', custo, confirmar_pendencias=confirmar_pendencias)
    _exigir(pf)
    dados, _b, _a = montar_declaracao(ctx, tipo_para(ctx, False))
    h = hash_declaracao(dados)
    _travar(ctx, 'calcular')
    try:
        res = _novo_cliente(cliente).chamar('TRANSDECLARACAO11', ctx.cnpj, dados, operacao='calcular',
                                            company=ctx.company, pa=ctx.pa, usuario=ctx.usuario)
        decl = ctx.decl
        corpo = res.dados if isinstance(res.dados, dict) else {}
        if res.ok and 'valoresDevidos' not in corpo:
            # Sem a lista não há o que comparar na transmissão: não validamos o cálculo.
            _erro(ctx, res.texto or 'A SERPRO não devolveu os valores devidos.')
            db.session.commit()
            return {'ok': False, 'serpro': res.resumo(), 'avisos': pf['avisos'],
                    'mensagem': 'A SERPRO respondeu sem os valores devidos. O cálculo NÃO foi '
                                'validado; confira no PGDAS-D web antes de tentar de novo.',
                    'estado': estado(ctx)}
        if res.ok:
            valores = _valores_devidos(corpo)
            decl.hash_dados = h
            decl.valores_devidos_json = json.dumps(valores, ensure_ascii=False)
            decl.total_devido = _r2(sum(v['valor'] for v in valores))
            decl.calculado_em = datetime.utcnow()
            if decl.situacao in ('rascunho', 'erro'):
                decl.situacao = 'calculada'
            decl.ultimo_erro = None
        else:
            _erro(ctx, res.texto)
            if res.incerto:
                pf['avisos'].append('Cálculo sem resposta — nada é transmitido no cálculo, pode tentar de novo.')
        db.session.commit()
        return {'ok': res.ok, 'serpro': res.resumo(), 'avisos': pf['avisos'], 'estado': estado(ctx)}
    finally:
        _destravar(ctx)


def _data_aaaammdd(valor: Any) -> Optional[str]:
    """SERPRO devolve AAAAMMDD; aceita também DD/MM/AAAA e AAAA-MM-DD."""
    texto = str(valor or '').strip()
    d = so_digitos(texto)
    if len(d) != 8:
        return None
    if re.match(r'^\d{2}/\d{2}/\d{4}', texto) or not d.startswith(('19', '20')):
        d = d[4:] + d[2:4] + d[:2]
    return d


def _valores_devidos(corpo: Dict[str, Any]) -> List[Dict[str, Any]]:
    saida = []
    for item in corpo.get('valoresDevidos') or []:
        try:
            codigo = int(item.get('codigoTributo'))
        except (TypeError, ValueError, AttributeError):
            continue
        saida.append({'codigoTributo': codigo, 'valor': _r2(item.get('valor')),
                      'tributo': TRIBUTOS.get(codigo, str(codigo))})
    return saida


def consultar(ctx: Contexto, cliente: Optional[SerproPgdasdClient] = None,
              _ja_travado: bool = False) -> Dict[str, Any]:
    custo = float(custo_de('CONSDECLARACAO13'))
    if not _ja_travado:
        pf = preflight(ctx, 'consultar', custo)
        _exigir(pf)
        _travar(ctx, 'consultar')
    try:
        res = _novo_cliente(cliente).chamar('CONSDECLARACAO13', ctx.cnpj, {'periodoApuracao': ctx.pa},
                                            operacao='consultar', company=ctx.company, pa=ctx.pa,
                                            usuario=ctx.usuario)
        decl = ctx.decl
        if res.ok:
            declaracoes, das = _indice(res.dados, ctx.pa)
            decl.consulta_json = json.dumps({'declaracoes': declaracoes, 'das': das}, ensure_ascii=False)
            decl.consultado_em = datetime.utcnow()
            if declaracoes:
                ultima = declaracoes[-1]
                estava_incerta = decl.situacao == 'incerta'
                if decl.situacao != 'transmitida' or decl.id_declaracao != ultima['numero']:
                    decl.situacao = 'transmitida'
                    decl.id_declaracao = ultima['numero']
                    decl.data_hora_transmissao = ultima.get('data_hora')
                    decl.tipo_declaracao = 2 if len(declaracoes) > 1 else 1
                    decl.transmitido_em = decl.transmitido_em or datetime.utcnow()
                    if estava_incerta and decl.hash_dados:
                        decl.hash_transmitido = decl.hash_dados
                for lanc in ctx.lancamentos:
                    if int(lanc.transmitido or 0) == 0:
                        lanc.transmitido = 1 if estava_incerta else 2
            elif decl.situacao == 'incerta':
                decl.situacao = 'calculada' if decl.hash_dados else 'rascunho'
            decl.ultimo_erro = None
        else:
            _erro(ctx, res.texto)
        db.session.commit()
        return {'ok': res.ok, 'serpro': res.resumo(), 'estado': estado(ctx)}
    finally:
        if not _ja_travado:
            _destravar(ctx)


def _indice(dados: Any, pa: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    periodos = []
    if isinstance(dados, dict):
        if isinstance(dados.get('periodo'), dict):
            periodos = [dados['periodo']]
        elif isinstance(dados.get('periodos'), list):
            periodos = dados['periodos']
    declaracoes, das = [], []
    for periodo in periodos:
        if str(periodo.get('periodoApuracao')) != pa:
            continue
        for op in periodo.get('operacoes') or []:
            idec = op.get('indiceDeclaracao') or None
            idas = op.get('indiceDas') or None
            if idec:
                declaracoes.append({'tipo': op.get('tipoOperacao'), 'numero': str(idec.get('numeroDeclaracao') or ''),
                                    'data_hora': str(idec.get('dataHoraTransmissao') or ''),
                                    'malha': idec.get('malha') or None})
            if idas:
                das.append({'numero': str(idas.get('numeroDas') or ''),
                            'emitido': str(idas.get('dataHoraEmissaoDas') or idas.get('datahoraEmissaoDas') or ''),
                            'pago': bool(idas.get('dasPago'))})
    declaracoes.sort(key=lambda d: d['data_hora'])
    return declaracoes, das


def transmitir(ctx: Contexto, retificar: bool = False, hash_confirmado: Optional[str] = None,
               cliente: Optional[SerproPgdasdClient] = None, confirmar_pendencias: bool = False) -> Dict[str, Any]:
    cliente = _novo_cliente(cliente)
    decl = ctx.decl
    precisa_consultar = (not retificar and decl.situacao != 'transmitida'
                         and ctx.declaracoes_na_receita() is None)
    custo = float(custo_de('TRANSDECLARACAO11')) + (float(custo_de('CONSDECLARACAO13')) if precisa_consultar else 0)
    pf = preflight(ctx, 'transmitir', custo, retificar=retificar, hash_confirmado=hash_confirmado,
                   confirmar_pendencias=confirmar_pendencias)
    _exigir(pf)
    _travar(ctx, 'transmitir')
    try:
        if precisa_consultar:
            consulta = consultar(ctx, cliente, _ja_travado=True)
            if not consulta['ok']:
                return {'ok': False, 'etapa': 'consulta_previa', 'serpro': consulta['serpro'],
                        'mensagem': 'Não foi possível confirmar se já existe declaração do período. '
                                    'Nada foi transmitido.', 'estado': estado(ctx)}
            if ctx.declaracoes_na_receita():
                return {'ok': False, 'etapa': 'consulta_previa', 'serpro': consulta['serpro'],
                        'mensagem': 'A Receita já tem declaração deste período (entregue fora do Central '
                                    'ou antes). Nada foi transmitido. Para alterar, use Retificar.',
                        'estado': estado(ctx)}

        tipo = 2 if retificar else 1
        dados, _b, _a = montar_declaracao(ctx, tipo)
        valores = [{'codigoTributo': v['codigoTributo'], 'valor': v['valor']}
                   for v in _json(decl.valores_devidos_json, []) if v.get('valor', 0) > 0]
        dados['indicadorTransmissao'] = True
        dados['indicadorComparacao'] = bool(valores)
        if valores:
            dados['valoresParaComparacao'] = valores

        res = cliente.chamar('TRANSDECLARACAO11', ctx.cnpj, dados, operacao='retificar' if retificar else 'transmitir',
                             company=ctx.company, pa=ctx.pa, usuario=ctx.usuario)
        corpo = res.dados if isinstance(res.dados, dict) else {}
        if res.ok and corpo.get('idDeclaracao'):
            decl.situacao = 'transmitida'
            decl.tipo_declaracao = tipo
            decl.id_declaracao = str(corpo.get('idDeclaracao'))
            decl.data_hora_transmissao = str(corpo.get('dataHoraTransmissao') or '')
            decl.transmitido_em = datetime.utcnow()
            decl.hash_transmitido = decl.hash_dados
            devidos = _valores_devidos(corpo)
            if devidos:
                decl.valores_devidos_json = json.dumps(devidos, ensure_ascii=False)
                decl.total_devido = _r2(sum(v['valor'] for v in devidos))
            idd = decl.id_declaracao
            decl.arquivo_declaracao = salvar_pdf_b64(ctx.cnpj, ctx.pa, f'declaracao_{idd}.pdf', corpo.get('declaracao')) or decl.arquivo_declaracao
            decl.arquivo_recibo = salvar_pdf_b64(ctx.cnpj, ctx.pa, f'recibo_{idd}.pdf', corpo.get('recibo')) or decl.arquivo_recibo
            decl.arquivo_maed_notificacao = salvar_pdf_b64(ctx.cnpj, ctx.pa, f'maed_notificacao_{idd}.pdf', corpo.get('notificacaoMaed'))
            decl.arquivo_maed_darf = salvar_pdf_b64(ctx.cnpj, ctx.pa, f'maed_darf_{idd}.pdf', corpo.get('darf'))
            decl.consultado_em = None          # a consulta anterior ficou velha
            # DAS antigo pertence à declaração anterior (retificação muda valores)
            if retificar:
                decl.das_arquivo = None
                decl.das_numero = None
                decl.das_vencimento = None
                decl.das_valor_total = None
            decl.ultimo_erro = None
            for lanc in ctx.lancamentos:
                lanc.transmitido = 1
        elif res.incerto or (res.ok and not corpo.get('idDeclaracao')):
            # Sem resposta, ou "sucesso" sem número da declaração: pode ter sido gravada.
            decl.situacao = 'incerta'
            _erro(ctx, res.texto or 'A SERPRO respondeu sem o número da declaração. Consulte antes de repetir.')
        else:
            if res.tem_codigo('MSG_ISN_035'):
                decl.hash_dados = None          # a Receita calculou diferente: exige novo cálculo
                decl.calculado_em = None
                decl.situacao = 'rascunho' if decl.situacao != 'transmitida' else decl.situacao
            elif not res.preenchimento:
                # Falha genérica/instabilidade ("Houve um problema na transmissão..."): a doc diz
                # que erro não grava nada, mas por segurança a PRÓXIMA tentativa consulta antes
                # (evita transmitir em cima de uma declaração que tenha entrado).
                decl.consultado_em = None
                pf['avisos'].append('A Receita informou problema na transmissão. Aguarde alguns minutos; '
                                    'ao clicar Enviar de novo o Central consulta antes se a declaração '
                                    'entrou (+ custo de 1 consulta) e só transmite se não houver.')
            _erro(ctx, res.texto or 'A SERPRO não confirmou a transmissão (sem idDeclaracao).')
        db.session.commit()
        return {'ok': bool(res.ok and corpo.get('idDeclaracao')), 'etapa': 'transmissao',
                'serpro': res.resumo(), 'avisos': pf['avisos'], 'estado': estado(ctx)}
    finally:
        _destravar(ctx)


def gerar_das(ctx: Contexto, data_consolidacao: Optional[str] = None, forcar: bool = False,
              confirmar_externa: bool = False, cliente: Optional[SerproPgdasdClient] = None) -> Dict[str, Any]:
    from app.services.serpro_das_service import SerproDasService
    decl = ctx.decl

    # 1) DAS já guardado e ainda válido → zero custo
    if decl.das_arquivo and Path(decl.das_arquivo).is_file() and not forcar and not data_consolidacao:
        vence = decl.das_vencimento or ''
        if not vence or vence >= hoje().strftime('%Y%m%d'):
            return {'ok': True, 'reuso': True, 'mensagem': 'DAS já emitido — usando o PDF guardado (sem custo).',
                    'estado': estado(ctx)}
        raise BloqueioPgdasd([f'O DAS guardado venceu em {vence[6:]}/{vence[4:6]}/{vence[:4]}. Para pagar '
                              'agora, gere uma nova guia informando a data de pagamento (consolidação).'],
                             status=409)

    custo = float(custo_de('GERARDAS12'))
    pf = preflight(ctx, 'gerar_das', custo, confirmar_externa=confirmar_externa)
    _exigir(pf)
    dados: Dict[str, Any] = {'periodoApuracao': ctx.pa}
    consolidacao = SerproDasService._data_consolidacao_aceita(data_consolidacao) if data_consolidacao else ''
    if consolidacao:
        dados['dataConsolidacao'] = consolidacao
    _travar(ctx, 'gerar_das')
    try:
        res = _novo_cliente(cliente).chamar('GERARDAS12', ctx.cnpj, dados, operacao='gerar_das',
                                            company=ctx.company, pa=ctx.pa, usuario=ctx.usuario)
        item = res.dados[0] if isinstance(res.dados, list) and res.dados else (
            res.dados if isinstance(res.dados, dict) else {})
        pdf_b64 = item.get('pdf') if isinstance(item, dict) else None
        if res.ok and pdf_b64:
            det = item.get('detalhamento')
            det = det[0] if isinstance(det, list) and det else (det if isinstance(det, dict) else {})
            numero = str(det.get('numeroDocumento') or '') or datetime.now(TZ).strftime('%Y%m%d%H%M%S')
            caminho = salvar_pdf_b64(ctx.cnpj, ctx.pa, f'das_{numero}.pdf', pdf_b64)
            if not caminho:
                _erro(ctx, 'A SERPRO devolveu um PDF inválido.')
                db.session.commit()
                return {'ok': False, 'serpro': res.resumo(), 'mensagem': 'PDF inválido retornado.',
                        'estado': estado(ctx)}
            decl.das_arquivo = caminho
            decl.das_numero = numero
            decl.das_vencimento = _data_aaaammdd(det.get('dataVencimento'))
            decl.das_valor_total = _r2((det.get('valores') or {}).get('total')) if det.get('valores') else None
            decl.das_data_consolidacao = consolidacao or None
            decl.das_emitido_em = datetime.utcnow()
            decl.das_detalhe_json = json.dumps(det, ensure_ascii=False)[:20000]
            decl.ultimo_erro = None
            ok, mensagem = True, 'DAS emitido e guardado.'
        else:
            ok = False
            if res.tem_codigo('MSG_ISN_005'):
                mensagem = ('A Receita informa que NÃO há declaração transmitida para o período. '
                            'Transmita (ou retifique) antes de gerar o DAS.')
                if decl.situacao == 'transmitida' and not decl.transmitido_em:
                    decl.situacao = 'rascunho'
                decl.consultado_em = None
            elif res.incerto:
                mensagem = res.texto + ' Um DAS pode ter sido gerado; tente de novo em alguns minutos.'
            else:
                mensagem = res.texto or 'A SERPRO não devolveu o PDF do DAS.'
            _erro(ctx, mensagem)
        db.session.commit()
        return {'ok': ok, 'serpro': res.resumo(), 'mensagem': mensagem, 'avisos': pf['avisos'],
                'estado': estado(ctx)}
    finally:
        _destravar(ctx)


def _pdf_de(bloco: Any, *chaves: str) -> Optional[str]:
    """Primeiro base64 encontrado no bloco (dict) pelas chaves, ou o próprio texto."""
    if isinstance(bloco, str):
        return bloco
    if isinstance(bloco, dict):
        for chave in chaves:
            if isinstance(bloco.get(chave), str) and bloco.get(chave):
                return bloco[chave]
    return None


def recuperar_documentos(ctx: Contexto, forcar: bool = False,
                         cliente: Optional[SerproPgdasdClient] = None) -> Dict[str, Any]:
    """CONSULTIMADECREC14 (1× consultar): baixa declaração + recibo (+ MAED) da ÚLTIMA
    declaração do PA — útil quando ela foi entregue fora do Central. Não repete se os
    PDFs já estão guardados (a não ser com `forcar`)."""
    custo = float(custo_de('CONSULTIMADECREC14'))
    pf = preflight(ctx, 'recuperar', custo, forcar=forcar)
    _exigir(pf)
    _travar(ctx, 'recuperar')
    try:
        res = _novo_cliente(cliente).chamar('CONSULTIMADECREC14', ctx.cnpj, {'periodoApuracao': ctx.pa},
                                            operacao='recuperar', company=ctx.company, pa=ctx.pa,
                                            usuario=ctx.usuario)
        decl = ctx.decl
        corpo = res.dados if isinstance(res.dados, dict) else {}
        numero = str(corpo.get('numeroDeclaracao') or '')
        salvos = []
        if res.ok and numero:
            if decl.id_declaracao and decl.id_declaracao != numero:
                # Há declaração mais nova (retificada fora do Central): documentos antigos não valem.
                decl.arquivo_maed_notificacao = decl.arquivo_maed_darf = None
            decl.id_declaracao = numero
            if decl.situacao != 'transmitida':
                decl.situacao = 'transmitida'
                decl.transmitido_em = decl.transmitido_em or datetime.utcnow()
            maed = corpo.get('maed') or {}
            for campo, nome, conteudo in (
                ('arquivo_declaracao', f'declaracao_{numero}.pdf', _pdf_de(corpo.get('declaracao'), 'pdf')),
                ('arquivo_recibo', f'recibo_{numero}.pdf', _pdf_de(corpo.get('recibo'), 'pdf')),
                ('arquivo_maed_notificacao', f'maed_notificacao_{numero}.pdf',
                 _pdf_de(maed, 'pdfNotificacao', 'notificacao')),
                ('arquivo_maed_darf', f'maed_darf_{numero}.pdf', _pdf_de(maed, 'pdfDarf', 'darf')),
            ):
                caminho = salvar_pdf_b64(ctx.cnpj, ctx.pa, nome, conteudo)
                if caminho:
                    setattr(decl, campo, caminho)
                    salvos.append(campo.replace('arquivo_', ''))
            for lanc in ctx.lancamentos:
                if int(lanc.transmitido or 0) == 0:
                    lanc.transmitido = 2
            decl.ultimo_erro = None
            ok = bool(salvos)
            mensagem = (f'Documentos da declaração {numero} guardados: {", ".join(salvos)}.' if salvos
                        else 'A SERPRO respondeu, mas sem PDF válido.')
        else:
            ok = False
            mensagem = res.texto or 'A SERPRO não devolveu a declaração do período.'
        if not ok:
            _erro(ctx, mensagem)
        db.session.commit()
        return {'ok': ok, 'serpro': res.resumo(), 'mensagem': mensagem, 'avisos': pf['avisos'],
                'estado': estado(ctx)}
    finally:
        _destravar(ctx)


# --------------------------------------------------------------------- estado
def estado(ctx: Contexto) -> Dict[str, Any]:
    d = ctx.decl
    valores = _json(d.valores_devidos_json, [])
    dados, bloqueios_montagem, _avisos = montar_declaracao(ctx) if ctx.lancamentos else ({}, [], [])
    hash_atual = hash_declaracao(dados) if dados else None
    calculo_valido = bool(d.hash_dados and d.calculado_em and d.hash_dados == hash_atual
                          and datetime.utcnow() - d.calculado_em <= VALIDADE_CALCULO)
    consulta = _json(d.consulta_json, {}) if d.consultado_em else {}
    tem_pdf = lambda p: bool(p and Path(p).is_file())  # noqa: E731
    na_receita = ctx.declaracoes_na_receita()
    return {
        'cnpj': ctx.cnpj, 'pa': ctx.pa, 'competencia': ctx.competencia,
        'company_id': getattr(ctx.company, 'id', None),
        'situacao': d.situacao, 'tipo_declaracao': d.tipo_declaracao,
        'id_declaracao': d.id_declaracao, 'data_hora_transmissao': d.data_hora_transmissao,
        'valores_devidos': valores, 'total_devido': d.total_devido,
        'calculado_em': d.calculado_em.isoformat() if d.calculado_em else None,
        'hash_calculo': d.hash_dados, 'calculo_valido': calculo_valido,
        'consulta': consulta, 'consultado_em': d.consultado_em.isoformat() if d.consultado_em else None,
        'das': {'numero': d.das_numero, 'vencimento': d.das_vencimento, 'valor': d.das_valor_total,
                'tem_pdf': tem_pdf(d.das_arquivo), 'emitido_em': d.das_emitido_em.isoformat() if d.das_emitido_em else None},
        'arquivos': {'declaracao': tem_pdf(d.arquivo_declaracao), 'recibo': tem_pdf(d.arquivo_recibo),
                     'maed_notificacao': tem_pdf(d.arquivo_maed_notificacao), 'maed_darf': tem_pdf(d.arquivo_maed_darf)},
        'operacao': (d.operacao if d.operacao and d.operacao_desde and not getattr(ctx, 'trava_propria', False)
                     and datetime.utcnow() - d.operacao_desde < TRAVA_EXPIRA else None),
        'ultimo_erro': d.ultimo_erro,
        'pode': {
            'calcular': not bloqueios_montagem and d.situacao != 'incerta',
            'transmitir': calculo_valido and d.situacao in ('calculada', 'rascunho', 'erro') and not na_receita,
            'retificar': calculo_valido and (d.situacao == 'transmitida' or bool(na_receita)),
            'gerar_das': d.situacao == 'transmitida' or bool(na_receita),
            'recuperar': (d.situacao == 'transmitida' or bool(na_receita))
                         and not (tem_pdf(d.arquivo_declaracao) and tem_pdf(d.arquivo_recibo)),
        },
        'custos': {k: float(v) for k, v in CUSTO_ESTIMADO.items()},
        'lancamento_transmitido': max((int(l.transmitido or 0) for l in ctx.lancamentos), default=0),
        'rba': getattr(ctx, 'rba', None) or {},
        'pendencias_receita': [p for p in pendencias_receita(d.ultimo_erro) if p < ctx.competencia
                               and p not in pas_transmitidos(ctx.cnpj, [p])],
        'montagem': {'bloqueios': bloqueios_montagem, 'avisos': _avisos},
    }


def caminho_arquivo(ctx: Contexto, tipo: str) -> Optional[str]:
    mapa = {'das': ctx.decl.das_arquivo, 'declaracao': ctx.decl.arquivo_declaracao,
            'recibo': ctx.decl.arquivo_recibo, 'maed_notificacao': ctx.decl.arquivo_maed_notificacao,
            'maed_darf': ctx.decl.arquivo_maed_darf}
    caminho = mapa.get(tipo)
    if not caminho:
        return None
    p = Path(caminho).resolve()
    raiz = pasta(ctx.cnpj, ctx.pa).resolve()
    if raiz not in p.parents or not p.is_file():     # nunca servir fora da pasta da empresa
        return None
    return str(p)


# ---------------------------------------------------------------------- lotes
LOTES: Dict[str, Dict[str, Any]] = {}
LOTES_LOCK = threading.Lock()
MAX_FALHAS_SISTEMICAS = 2


def iniciar_lote(app, acao: str, itens: List[Dict[str, str]], usuario: str,
                 opcoes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Executa em sequência, numa thread. Para no 2º erro SISTÊMICO seguido (rede,
    gateway, SERPRO fora) — erro de uma empresa não para o lote, mas pane geral sim,
    para não pagar N chamadas que vão falhar pelo mesmo motivo."""
    from app.services.limite_gasto_service import LimiteGastoService
    if acao not in ('calcular', 'transmitir', 'gerar_das', 'consultar'):
        raise BloqueioPgdasd(['Ação de lote inválida.'], status=400)
    servico = {'calcular': 'TRANSDECLARACAO11', 'transmitir': 'TRANSDECLARACAO11',
               'gerar_das': 'GERARDAS12', 'consultar': 'CONSDECLARACAO13'}[acao]
    custo_max = float(custo_de(servico)) * len(itens)
    pode, motivo = LimiteGastoService.pode_gastar(custo_max)
    if not pode:
        raise BloqueioPgdasd([f'Teto de gasto: {motivo}.'])
    lote_id = uuid.uuid4().hex[:10]
    with LOTES_LOCK:
        LOTES[lote_id] = {'id': lote_id, 'acao': acao, 'status': 'rodando', 'total': len(itens),
                          'feitos': 0, 'ok': 0, 'erros': 0, 'pulados': 0, 'custo_estimado': 0.0,
                          'itens': [], 'mensagem': '', 'criado_em': datetime.utcnow().isoformat()}
    threading.Thread(target=_rodar_lote, args=(app, lote_id, acao, itens, usuario, opcoes or {}),
                     daemon=True).start()
    return dict(LOTES[lote_id])


def _rodar_lote(app, lote_id, acao, itens, usuario, opcoes):
    with app.app_context():
        falhas_seguidas = 0
        for item in itens:
            registro = {'cnpj': item.get('cnpj'), 'competencia': item.get('competencia')}
            try:
                ctx = carregar(item['cnpj'], item['competencia'], usuario)
                registro['razao'] = getattr(ctx.company, 'razao_social', None)
                if acao == 'calcular':
                    r = calcular(ctx)
                elif acao == 'transmitir':
                    r = transmitir(ctx, retificar=False, hash_confirmado=item.get('hash'))
                elif acao == 'gerar_das':
                    r = gerar_das(ctx)
                else:
                    r = consultar(ctx)
                serpro = r.get('serpro') or {}
                registro.update(ok=bool(r.get('ok')), mensagem=r.get('mensagem') or serpro.get('mensagem') or '',
                                reuso=bool(r.get('reuso')), custo=serpro.get('custo_estimado', 0.0))
                sistemico = bool(serpro.get('sistemico'))
                falhas_seguidas = falhas_seguidas + 1 if (not r.get('ok') and sistemico) else 0
            except BloqueioPgdasd as exc:
                registro.update(ok=False, pulado=True, mensagem='; '.join(exc.bloqueios), custo=0.0)
            except Exception as exc:          # nunca derrubar o lote inteiro
                logger.exception('Falha no lote %s', lote_id)
                db.session.rollback()
                registro.update(ok=False, mensagem=f'Erro interno: {exc}', custo=0.0)
            with LOTES_LOCK:
                lote = LOTES[lote_id]
                lote['itens'].append(registro)
                lote['feitos'] += 1
                lote['custo_estimado'] = round(lote['custo_estimado'] + float(registro.get('custo') or 0), 2)
                if registro.get('pulado'):
                    lote['pulados'] += 1
                elif registro.get('ok'):
                    lote['ok'] += 1
                else:
                    lote['erros'] += 1
            if falhas_seguidas >= MAX_FALHAS_SISTEMICAS:
                with LOTES_LOCK:
                    LOTES[lote_id].update(status='interrompido',
                                          mensagem='Interrompido: 2 falhas seguidas da SERPRO/rede. '
                                                   'As empresas restantes NÃO foram chamadas.')
                return
        with LOTES_LOCK:
            LOTES[lote_id].update(status='concluido', mensagem='Lote concluído.')


def status_lote(lote_id: str) -> Optional[Dict[str, Any]]:
    with LOTES_LOCK:
        lote = LOTES.get(lote_id)
        return json.loads(json.dumps(lote)) if lote else None
