"""Emissão de guia (DAS SN, DAS MEI, DARF DCTFWeb) com PDF guardado e evento registrado.

⚠️ DESVIO INTENCIONAL (18o) — NÃO existe no exe. As rotas `/api/das/*` do exe
devolvem o PDF e esquecem; cada clique é uma chamada paga. Aqui, antes de pagar:

1. **Guia guardada e ainda válida** (vencimento ≥ hoje, mesma data de consolidação
   pedida) é servida do disco — custo zero. É o padrão que a área Escritório já usa.
2. **Trava de procuração** e **teto mensal** (`_bloqueio_custo`, como em das_routes).
3. **Limite diário por empresa/competência** (`INTEGRACAO_MAX_EMISSOES_DIA`, padrão 3):
   o botão do portal fica na mão do cliente, e clique repetido não pode virar conta.

Depois de emitir: PDF em `REPORTS_DIR/das_emitidas/<cnpj>/<pa>/`, custo em
`api_usage_logs` e uma linha em `das_emissoes` — o evento que o portal usa para saber
que o cliente recalculou a guia (e então regerar o relatório dias depois).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import current_app

from app.extensions import db
from app.integracao_models import DasEmissao
from app.models import Company
from app.services.api_usage_service import ApiUsageService
from app.services.pendencias_service import hoje_brasil

logger = logging.getLogger(__name__)

from app.services.parcelamentos_serpro_service import PARCELAMENTO_TYPES

#: DESVIO 19: parcela de parcelamento é emitida pelo mesmo caminho (mesmas travas);
#: `periodo_apuracao` = parcela (AAAAMM).
TIPOS_PARCELA = tuple(PARCELAMENTO_TYPES.keys())
TIPOS = ('SN', 'MEI', 'DCTFWEB') + TIPOS_PARCELA
ENDPOINT_POR_TIPO = {
    'SN': 'PGDASD/GERARDAS12',
    'MEI': 'PGMEI/GERARDASPDF21',
    'DCTFWEB': 'DCTFWEB/GERARGUIA31',
    **{t: f"{c['pdf']['idSistema']}/{c['pdf']['idServico']}" for t, c in PARCELAMENTO_TYPES.items()},
}


class EmissaoBloqueada(Exception):
    """Nada foi enviado à SERPRO; a mensagem explica por quê."""

    def __init__(self, mensagem: str, status: int = 409):
        super().__init__(mensagem)
        self.status = status


def _digits(valor: Any) -> str:
    return ''.join(ch for ch in str(valor or '') if ch.isdigit())


def limite_diario() -> int:
    try:
        return max(1, int(os.getenv('INTEGRACAO_MAX_EMISSOES_DIA', '3')))
    except ValueError:
        return 3


def _pasta(cnpj: str, pa: str) -> Path:
    base = Path(current_app.config.get('REPORTS_DIR') or 'reports')
    destino = base / 'das_emitidas' / cnpj / pa
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _salvar_pdf(cnpj: str, pa: str, nome: str, conteudo: bytes) -> str:
    if not conteudo or not conteudo.startswith(b'%PDF'):
        raise ValueError('A SERPRO devolveu um conteúdo que não é PDF.')
    destino = _pasta(cnpj, pa) / re.sub(r'[^\w.\-]', '_', nome)
    temporario = destino.with_suffix(destino.suffix + '.tmp')
    temporario.write_bytes(conteudo)
    os.replace(temporario, destino)
    return str(destino)


def _aaaammdd(valor: Any) -> Optional[str]:
    d = _digits(valor)
    return d if len(d) == 8 else None


_RE_PAGAR_ATE = re.compile(r'pagar\s+(?:este\s+documento\s+)?at[ée]\D{0,80}?(\d{2})/(\d{2})/(\d{4})',
                           re.IGNORECASE | re.DOTALL)
_RE_VENCIMENTO = re.compile(r'vencimento\D{0,80}?(\d{2})/(\d{2})/(\d{4})', re.IGNORECASE | re.DOTALL)
_RE_VALOR_TOTAL = re.compile(r'valor\s+total(?:\s+do\s+documento)?\D{0,80}?(\d{1,3}(?:\.\d{3})*,\d{2})',
                             re.IGNORECASE | re.DOTALL)


def extrair_vencimento_valor(texto: str) -> Dict[str, Any]:
    """'Pagar este documento até dd/mm/aaaa' e 'Valor Total do Documento 1.234,56' do texto
    de um DARF/DAS. Devolve no formato do `detalhamento` do GERARDAS12 (o que achar)."""
    saida: Dict[str, Any] = {}
    texto = texto or ''
    m = _RE_PAGAR_ATE.search(texto) or _RE_VENCIMENTO.search(texto)
    if m:
        saida['dataVencimento'] = f'{m.group(3)}{m.group(2)}{m.group(1)}'
    m = _RE_VALOR_TOTAL.search(texto)
    if m:
        saida['valores'] = {'total': float(m.group(1).replace('.', '').replace(',', '.'))}
    return saida


def detalhe_do_pdf(pdf: bytes) -> Dict[str, Any]:
    """Melhor esforço: nunca lança. PDF ilegível → {} (a guia sai sem valor/vencimento)."""
    try:
        import io
        from PyPDF2 import PdfReader
        leitor = PdfReader(io.BytesIO(pdf))
        texto = '\n'.join((pagina.extract_text() or '') for pagina in leitor.pages[:2])
        return extrair_vencimento_valor(texto)
    except Exception:
        logger.warning('[DAS] não foi possível ler vencimento/valor do PDF', exc_info=True)
        return {}


def _bloqueio_custo(company: Optional[Company]) -> Optional[str]:
    from app.services.limite_gasto_service import LimiteGastoService
    from app.services.procuracao_service import ProcuracaoService
    if company is not None:
        pode, motivo = ProcuracaoService.pode_gastar(company)
        if not pode:
            return f'Chamadas pagas travadas para {company.razao_social}: {motivo}.'
    custo = float(ApiUsageService._get_emitir_cost(1))
    pode, motivo = LimiteGastoService.pode_gastar(custo)
    if not pode:
        return f'Teto de gasto: {motivo}.'
    return None


def _emissoes_hoje(cnpj: str, tipo: str, pa: str) -> int:
    inicio = datetime.combine(hoje_brasil(), datetime.min.time())
    # emitido_em é UTC; a folga de 3h cobre o fuso sem exigir conversão exata.
    inicio_utc = inicio + timedelta(hours=3)
    # As guias que o CLIENTE gera pela página do link sem senha (desvio 21, origem
    # 'link') ficam fora desta conta: têm limite próprio por token e nunca podem
    # bloquear o portal do cliente nem o escritório nesta competência.
    return (DasEmissao.query
            .filter(DasEmissao.cnpj == cnpj, DasEmissao.tipo == tipo,
                    DasEmissao.periodo_apuracao == pa,
                    DasEmissao.reuso_de_id.is_(None),
                    DasEmissao.origem != 'link',
                    DasEmissao.emitido_em >= inicio_utc)
            .count())


def guia_valida_guardada(cnpj: str, tipo: str, pa: str, categoria: Optional[str],
                         data_consolidacao: Optional[str],
                         hoje: Optional[date] = None) -> Optional[DasEmissao]:
    """Última guia guardada que ainda serve — ou None."""
    hoje = hoje or hoje_brasil()
    consulta = (DasEmissao.query
                .filter(DasEmissao.cnpj == cnpj, DasEmissao.tipo == tipo,
                        DasEmissao.periodo_apuracao == pa,
                        DasEmissao.reuso_de_id.is_(None),
                        DasEmissao.pdf_local_path.isnot(None)))
    if categoria:
        consulta = consulta.filter(DasEmissao.categoria == categoria)
    for emissao in consulta.order_by(DasEmissao.id.desc()).limit(10).all():
        if not emissao.pdf_local_path or not Path(emissao.pdf_local_path).is_file():
            continue
        if data_consolidacao and emissao.data_consolidacao != data_consolidacao:
            continue
        vence = emissao.vencimento or emissao.data_consolidacao
        if vence and vence < hoje.strftime('%Y%m%d'):
            continue
        return emissao
    return None


def serializar(emissao: DasEmissao, reuso: bool = False) -> Dict[str, Any]:
    return {
        'id': emissao.id,
        'company_id': emissao.company_id,
        'cnpj': emissao.cnpj,
        'tipo': emissao.tipo,
        'periodo_apuracao': emissao.periodo_apuracao,
        'categoria': emissao.categoria,
        'data_consolidacao': emissao.data_consolidacao,
        'numero_documento': emissao.numero_documento,
        'vencimento': emissao.vencimento,
        'valor_total': emissao.valor_total,
        'origem': emissao.origem,
        'solicitado_por': emissao.solicitado_por,
        'reuso': reuso or emissao.reuso_de_id is not None,
        'reuso_de_id': emissao.reuso_de_id,
        'custo_estimado': emissao.custo_estimado,
        'emitido_em': emissao.emitido_em.isoformat() if emissao.emitido_em else None,
        'pdf_disponivel': bool(emissao.pdf_local_path and Path(emissao.pdf_local_path).is_file()),
    }


def _chamar_serpro(tipo: str, cnpj: str, pa: str, categoria: Optional[str],
                   data_consolidacao: Optional[str]) -> Tuple[bytes, Dict[str, Any]]:
    from app.services.serpro_das_service import SerproDasService
    service = SerproDasService()
    if tipo == 'DCTFWEB':
        pdf = service.emitir_pdf_dctfweb(
            contribuinte_numero=cnpj,
            categoria=categoria or 'GERAL_MENSAL',
            competencia=pa,
            ano_pa=pa[:4],
            data_acolhimento=data_consolidacao,
        )
        return pdf or b'', {}
    if tipo in TIPOS_PARCELA:
        from app.services.serpro_das_service import emitir_pdf_parcela
        cfg = PARCELAMENTO_TYPES[tipo]['pdf']
        return emitir_pdf_parcela(cnpj, cfg['idSistema'], cfg['idServico'], pa), {}
    return service.emitir_pdf_detalhado(
        contribuinte_numero=cnpj,
        periodo_apuracao=pa,
        tipo_das='mei' if tipo == 'MEI' else 'simples',
        data_consolidacao=data_consolidacao,
    )


def emitir(cnpj: str, tipo: str, periodo_apuracao: str, data_consolidacao: Optional[str] = None,
           categoria: Optional[str] = None, origem: str = 'interno',
           solicitado_por: Optional[str] = None, forcar: bool = False,
           chamar=None, apenas_reuso: bool = False) -> Tuple[DasEmissao, bool]:
    """Devolve (emissão, reuso). Lança `EmissaoBloqueada` quando nada é enviado.

    `apenas_reuso`: só serve guia já guardada e válida; se não houver, 402 sem chamar a
    SERPRO (quem pede não tem permissão para gerar custo — ex.: atendente do AtendeChat).
    """
    cnpj = _digits(cnpj)
    tipo = str(tipo or '').upper()
    pa = _digits(periodo_apuracao)
    consolidacao = _aaaammdd(data_consolidacao)

    if len(cnpj) != 14:
        raise EmissaoBloqueada('CNPJ inválido.', 400)
    if tipo not in TIPOS:
        raise EmissaoBloqueada(f'Tipo de guia inválido: {tipo}.', 400)
    if len(pa) != 6:
        raise EmissaoBloqueada('Informe a competência no formato AAAAMM.', 400)
    hoje_txt = hoje_brasil().strftime('%Y%m%d')
    if tipo == 'DCTFWEB':
        # Competência 13 (relatório traz '13/AAAA') é a DCTFWeb do 13º salário.
        categoria = categoria or ('GERAL_13o_SALARIO' if pa[4:] == '13' else 'GERAL_MENSAL')
        if categoria not in ('GERAL_MENSAL', 'GERAL_13o_SALARIO'):
            raise EmissaoBloqueada('Categoria inválida para DCTFWeb.', 400)
        # DESVIO 19: data de pagamento vai como dataAcolhimentoProposta (multa/juros até ela).
        if consolidacao and consolidacao < hoje_txt:
            raise EmissaoBloqueada('A data de pagamento não pode ser anterior a hoje.', 400)
    elif tipo in TIPOS_PARCELA:
        categoria = None
        if consolidacao:
            raise EmissaoBloqueada('Parcela de parcelamento não aceita data de pagamento: a Receita '
                                   'calcula a parcela para o mês da emissão.', 400)
    else:
        categoria = None

    company = Company.query.filter_by(cnpj=cnpj).first()
    if not company:
        raise EmissaoBloqueada('Empresa não cadastrada no Central e-CAC.', 404)
    if not company.ativo:
        raise EmissaoBloqueada('Empresa inativa no Central e-CAC.', 409)
    if tipo in TIPOS_PARCELA:
        from app.services import parcelas_service
        if not parcelas_service.parcela_consta(cnpj, tipo, pa):
            raise EmissaoBloqueada('Esta parcela não consta na última consulta de parcelas. '
                                   'Consulte as parcelas antes de emitir.', 409)

    # 1) guia guardada e válida → sem custo
    if apenas_reuso:
        forcar = False
    if not forcar:
        guardada = guia_valida_guardada(cnpj, tipo, pa, categoria, consolidacao)
        if guardada:
            registro = DasEmissao(
                company_id=company.id, cnpj=cnpj, tipo=tipo, periodo_apuracao=pa,
                categoria=categoria, data_consolidacao=guardada.data_consolidacao,
                numero_documento=guardada.numero_documento, vencimento=guardada.vencimento,
                valor_total=guardada.valor_total, pdf_local_path=guardada.pdf_local_path,
                origem=origem, solicitado_por=solicitado_por, reuso_de_id=guardada.id,
                custo_estimado=0.0, detalhe_json=guardada.detalhe_json,
            )
            db.session.add(registro)
            db.session.commit()
            return registro, True
    if apenas_reuso:
        raise EmissaoBloqueada('Não há guia já gerada e válida para esta competência. Peça a '
                               'quem tem permissão para gerar (recalcular) a guia.', 402)

    # 2) travas de custo
    bloqueio = _bloqueio_custo(company)
    if bloqueio:
        raise EmissaoBloqueada(bloqueio, 409)
    if _emissoes_hoje(cnpj, tipo, pa) >= limite_diario():
        raise EmissaoBloqueada(
            f'Limite de {limite_diario()} emissão(ões) por dia para esta competência já '
            'atingido. A última guia emitida continua disponível para download.', 429)

    # 3) chamada paga
    pdf, detalhe = (chamar or _chamar_serpro)(tipo, cnpj, pa, categoria, consolidacao)
    if not pdf:
        raise EmissaoBloqueada('PDF não retornado pela SERPRO.', 502)
    if tipo == 'DCTFWEB' or tipo in TIPOS_PARCELA:
        # A resposta não traz vencimento/valor; lê do próprio PDF (melhor esforço).
        detalhe = {**detalhe_do_pdf(pdf), **(detalhe or {})}

    numero = str(detalhe.get('numeroDocumento') or '') or datetime.now().strftime('%Y%m%d%H%M%S')
    caminho = _salvar_pdf(cnpj, pa, f'{tipo}_{numero}.pdf', pdf)

    custo = float(ApiUsageService._get_emitir_cost(1))
    try:
        ApiUsageService.register_usage(route_type='emitir', endpoint=ENDPOINT_POR_TIPO[tipo],
                                       company_id=company.id)
    except Exception:
        db.session.rollback()
        logger.exception('Falha ao registrar custo da emissão %s', ENDPOINT_POR_TIPO[tipo])

    valores = detalhe.get('valores') if isinstance(detalhe.get('valores'), dict) else {}
    registro = DasEmissao(
        company_id=company.id, cnpj=cnpj, tipo=tipo, periodo_apuracao=pa, categoria=categoria,
        data_consolidacao=consolidacao,
        numero_documento=numero if detalhe.get('numeroDocumento') else None,
        # Parcela sem vencimento na resposta: vale só hoje para reaproveitar (conservador).
        vencimento=(_aaaammdd(detalhe.get('dataVencimento')) or consolidacao
                    or (hoje_txt if tipo in TIPOS_PARCELA else None)),
        valor_total=(float(valores['total']) if valores.get('total') is not None else None),
        pdf_local_path=caminho, origem=origem, solicitado_por=solicitado_por,
        custo_estimado=custo, detalhe_json=json.dumps(detalhe, ensure_ascii=False)[:20000] or None,
    )
    db.session.add(registro)
    db.session.commit()
    logger.info('[DAS] emitida %s %s PA=%s origem=%s custo=%.2f', tipo, cnpj, pa, origem, custo)
    return registro, False


def ler_pdf(emissao: DasEmissao) -> Optional[bytes]:
    if not emissao.pdf_local_path:
        return None
    caminho = Path(emissao.pdf_local_path)
    if not caminho.is_file():
        return None
    return caminho.read_bytes()


def registrar_emissao_avulsa(company: Optional[Company], cnpj: str, tipo: str, pa: str,
                             pdf: bytes, detalhe: Dict[str, Any], data_consolidacao: Optional[str],
                             origem: str, categoria: Optional[str] = None) -> Optional[DasEmissao]:
    """Guarda uma guia que outra rota já emitiu (rotas do exe). Nunca lança."""
    try:
        cnpj = _digits(cnpj)
        pa = _digits(pa)
        numero = str(detalhe.get('numeroDocumento') or '') or datetime.now().strftime('%Y%m%d%H%M%S')
        caminho = _salvar_pdf(cnpj, pa, f'{tipo}_{numero}.pdf', pdf)
        valores = detalhe.get('valores') if isinstance(detalhe.get('valores'), dict) else {}
        registro = DasEmissao(
            company_id=getattr(company, 'id', None), cnpj=cnpj, tipo=tipo, periodo_apuracao=pa,
            categoria=categoria, data_consolidacao=_aaaammdd(data_consolidacao),
            numero_documento=numero if detalhe.get('numeroDocumento') else None,
            vencimento=_aaaammdd(detalhe.get('dataVencimento')) or _aaaammdd(data_consolidacao),
            valor_total=(float(valores['total']) if valores.get('total') is not None else None),
            pdf_local_path=caminho, origem=origem,
            custo_estimado=float(ApiUsageService._get_emitir_cost(1)),
            detalhe_json=json.dumps(detalhe, ensure_ascii=False)[:20000] or None,
        )
        db.session.add(registro)
        db.session.commit()
        return registro
    except Exception:
        db.session.rollback()
        logger.exception('[DAS] não foi possível guardar a guia emitida (%s %s)', tipo, cnpj)
        return None
