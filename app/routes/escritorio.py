"""Área "Escritório" — telas modeladas no visual do Integra Contador.

⚠️ DESVIO INTENCIONAL (17o) — NÃO existe no exe. Pedido do Jean em 15/09/2026: trazer
para o Central e-CAC as telas de escritório que ele usa no Integra Contador (painel de
cards, Configuração, Lançamentos, Transmitir PGDAS-D, Caminhos XML, leitores de NFS-e e
NF-e, Upload PGDAS-D e Declaração de Faturamento/Envio do DAS).

Primeira etapa = modelagem visual (dados de exemplo, sem ações, nada chama a SERPRO).
As funções entram tela a tela, na ordem de prioridade que o Jean definir.

Já funcional: **Ler XML NFe** (`/escritorio/xml/nfe`) + tabela NCM × CST (`/escritorio/ncm`)
+ **Lançamentos** (`/escritorio/simples/lancamentos`) lendo/gravando a memória.
Transmitir PGDAS-D lista os dados reais; cálculo/envio SERPRO fica para a etapa 2.

Organização
-----------
* `/escritorio` — painel de cards, dentro da barra lateral do sistema;
* `/escritorio/<modulo>` — cada tela é uma página inteira (sem barra lateral), aberta
  numa janela sobre o painel, como no original. Também abre direto pela URL.
* Visual em `app/templates/escritorio/` (`_tema.css` = tokens e base;
  `_modulo.css` = componentes das telas de lista).
"""

import json
import re
from datetime import date

from flask import Blueprint, jsonify, render_template, request, url_for

from app.extensions import db
from app.ui import CSS, FIM, lateral

escritorio_bp = Blueprint('escritorio', __name__, url_prefix='/escritorio')


@escritorio_bp.record_once
def _preparar_tabelas(estado):
    """Cria as tabelas próprias da área (fora de app/models.py) e a carga NCM×CST."""
    app = estado.app
    with app.app_context():
        from app import escritorio_models  # noqa: F401  (registra as tabelas)
        from app.migrations import add_column_if_not_exists
        from app.services import escritorio_ncm
        db.create_all()
        # colunas novas da conferência (Lançamentos funcional)
        for col, sql in (
            ('rec_sem_st_isencao',
             "ALTER TABLE escritorio_lancamentos ADD COLUMN rec_sem_st_isencao FLOAT DEFAULT 0"),
            ('saldo_sefaz',
             "ALTER TABLE escritorio_lancamentos ADD COLUMN saldo_sefaz FLOAT DEFAULT 0"),
            ('ok',
             "ALTER TABLE escritorio_lancamentos ADD COLUMN ok BOOLEAN DEFAULT 0"),
            ('transmitido',
             "ALTER TABLE escritorio_lancamentos ADD COLUMN transmitido INTEGER DEFAULT 0"),
        ):
            try:
                add_column_if_not_exists('escritorio_lancamentos', col, sql)
            except Exception:
                app.logger.exception('Falha ao migrar coluna %s de escritorio_lancamentos', col)
        try:
            escritorio_ncm.garantir_carga_inicial()
        except Exception:
            app.logger.exception('Falha na carga inicial da tabela NCM x CST')


def _usuario():
    try:
        from app.security import usuario_atual
        return usuario_atual() or {}
    except Exception:
        return {}


def _nome_usuario() -> str:
    u = _usuario()
    return (u.get('usuario') or u.get('nome') or '')[:120]


def _e_admin() -> bool:
    from app.services import permissoes
    u = _usuario()
    # sem autenticação configurada (ex.: teste local) o sistema age como o exe: dono da máquina
    return (not u) or permissoes.e_admin(u)


def _num(valor) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _fmt_cnpj(cnpj: str) -> str:
    d = re.sub(r'\D', '', cnpj or '')
    if len(d) != 14:
        return cnpj or ''
    return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'


def _ids_empresas_usuario():
    """None = todas; lista = filtro do usuário restrito."""
    from app.services import permissoes
    u = _usuario()
    if not u or permissoes.e_admin(u):
        return None
    liberadas = permissoes.empresas_do_usuario(u)
    if liberadas == permissoes.TODAS:
        return None
    return [int(e) for e in (liberadas or [])]


# Cards do painel, na mesma ordem e cor do original. `rota` = tela já modelada;
# sem rota, o card aparece igual mas avisa que a tela ainda não foi modelada.
CARDS = [
    {'titulo': 'Empresas', 'sub': 'Cadastro e gestão', 'cor': ('#5b21b6', '#7c3aed')},
    {'titulo': 'Configuração', 'sub': 'Dados do Escritório', 'cor': ('#581c87', '#7e22ce'),
     'rota': 'configuracao', 'cert': True},
    {'titulo': 'Simples Nacional', 'sub': 'Lançamentos', 'cor': ('#064e3b', '#065f46'),
     'rota': 'simples/lancamentos'},
    {'titulo': 'Simples Nacional', 'sub': 'Enviar PGDAS-D', 'cor': ('#064e3b', '#065f46'),
     'rota': 'simples/transmitir'},
    {'titulo': 'Caminhos XMLs', 'sub': 'Automação', 'cor': ('#14532d', '#166534'),
     'rota': 'xml/caminhos'},
    {'titulo': 'Ler XML NFSe', 'sub': 'Automação', 'cor': ('#166534', '#15803d'),
     'rota': 'xml/nfse'},
    {'titulo': 'Ler XML NFe', 'sub': 'Automação', 'cor': ('#166534', '#15803d'),
     'rota': 'xml/nfe'},
    {'titulo': 'NCM × CST', 'sub': 'PIS/COFINS monofásicos', 'cor': ('#1e3a8a', '#2563eb'),
     'rota': 'ncm'},
    {'titulo': 'Upload PGDAS-D', 'sub': 'RBT12', 'cor': ('#166534', '#15803d'),
     'rota': 'simples/rbt12'},
    {'titulo': 'Saldo Portal NFSe', 'sub': 'Facilitador', 'cor': ('#22c55e', '#4ade80')},
    {'titulo': 'Saldo Sefaz', 'sub': 'Facilitador', 'cor': ('#22c55e', '#4ade80')},
    {'titulo': 'Valor Folha', 'sub': 'Cálculo do Fator R', 'cor': ('#0f766e', '#14b8a6')},
    {'titulo': 'Opção Regime', 'sub': 'Simples Nacional', 'cor': ('#1f7a4f', '#2fbf71')},
    {'titulo': 'ÚTEIS', 'sub': 'Declaração de Faturamento e enviar DAS',
     'cor': ('#ff1744', '#d500f9'), 'rota': 'uteis/faturamento'},
    {'titulo': 'DEFIS', 'sub': 'Simples Nacional', 'bloqueado': True},
    {'titulo': 'MIT', 'sub': 'Apuração e DARF', 'bloqueado': True},
    {'titulo': 'Situação Fiscal', 'sub': 'Integrações extras', 'bloqueado': True},
    {'titulo': 'LUCROS', 'sub': 'Envio do R-4010', 'bloqueado': True},
    {'titulo': 'VÍDEOS DE AJUDA', 'sub': 'Aprenda a usar o sistema',
     'cor': ('#dc2626', '#7f1d1d'), 'destaque': True},
    {'titulo': 'DOWNLOAD ROBÔ', 'sub': 'Baixar robô de automação', 'cor': ('#1d4ed8', '#06b6d4')},
]


def _competencia() -> str:
    """AAAA-MM da query; sem ela, o mês anterior (competência em apuração)."""
    valor = (request.args.get('competencia') or '').strip()
    if len(valor) == 7 and valor[4] == '-' and valor[:4].isdigit() and valor[5:].isdigit():
        return valor
    hoje = date.today()
    ano, mes = (hoje.year, hoje.month - 1) if hoje.month > 1 else (hoje.year - 1, 12)
    return f'{ano}-{mes:02d}'


def _moeda(valor) -> str:
    texto = f'{float(valor or 0):,.2f}'
    return texto.replace(',', 'X').replace('.', ',').replace('X', '.')


def _pct(parte, total) -> str:
    return (f'{(parte / total * 100):.1f}' if total else '0.0').replace('.', ',')


def _contexto(**extra):
    """Contexto das telas do Escritório — só empresas reais do cadastro + memória."""
    from app.models import Company
    from app.services import escritorio_lancamentos as svc

    competencia = _competencia()
    empresa_ids = _ids_empresas_usuario()
    dados_mem = svc.listar_competencia(competencia, empresa_ids=empresa_ids)
    por_cnpj = {e['cnpj']: e for e in dados_mem['empresas']}

    q = Company.query.filter_by(ativo=True).order_by(Company.razao_social.asc())
    if empresa_ids is not None:
        q = q.filter(Company.id.in_(empresa_ids or [-1]))
    cadastradas = q.all()

    empresas = []
    vistos = set()
    for c in cadastradas:
        mem = por_cnpj.get(c.cnpj)
        receita = 0.0
        ok = False
        status = 'pendente'
        if mem:
            receita = sum(p['valores'].get('total_receita', 0) for p in mem['perfis'])
            ok = bool(mem.get('ok'))
            tr = int(mem.get('transmitido') or 0)
            status = 'manual' if tr == 2 else ('enviado' if tr == 1 else 'pendente')
            vistos.add(c.cnpj)
        empresas.append({
            'id': c.id,
            'razao': c.razao_social,
            'cnpj': c.cnpj,
            'cnpj_fmt': _fmt_cnpj(c.cnpj),
            'perfil': (mem['perfis'][0]['perfil'] if mem and mem['perfis'] else 'comercio'),
            'perfil_nome': (mem['perfis'][0]['perfil_nome'] if mem and mem['perfis'] else 'Comércio'),
            'receita': receita,
            'rbt12': 0.0,
            'das': 0.0,
            'aliq': 0.0,
            'ok': ok,
            'status': status,
            'caminho_nfse': '',
            'caminho_nfe': '',
            'tem_lancamento': mem is not None,
        })
    # lançamentos sem company_id (CNPJ ainda não cadastrado)
    for cnpj, mem in por_cnpj.items():
        if cnpj in vistos:
            continue
        receita = sum(p['valores'].get('total_receita', 0) for p in mem['perfis'])
        tr = int(mem.get('transmitido') or 0)
        empresas.append({
            'id': mem.get('company_id') or cnpj,
            'razao': mem['razao'],
            'cnpj': cnpj,
            'cnpj_fmt': mem.get('cnpj_fmt') or _fmt_cnpj(cnpj),
            'perfil': mem['perfis'][0]['perfil'] if mem['perfis'] else 'comercio',
            'perfil_nome': mem['perfis'][0]['perfil_nome'] if mem['perfis'] else 'Comércio',
            'receita': receita,
            'rbt12': 0.0,
            'das': 0.0,
            'aliq': 0.0,
            'ok': bool(mem.get('ok')),
            'status': 'manual' if tr == 2 else ('enviado' if tr == 1 else 'pendente'),
            'caminho_nfse': '',
            'caminho_nfe': '',
            'tem_lancamento': True,
        })

    total = len(empresas)
    ok = sum(1 for e in empresas if e['ok'])
    enviadas = sum(1 for e in empresas if e['status'] in ('enviado', 'manual'))
    erros = sum(1 for e in empresas if e['status'] == 'erro')
    pendentes = sum(1 for e in empresas if e['status'] == 'pendente' and not e['ok'])
    base = {
        'competencia': competencia,
        'empresas': empresas,
        'm': {'total': total, 'ok': ok, 'enviadas': enviadas, 'erros': erros,
              'pendentes': pendentes,
              'p_ok': _pct(ok, total), 'p_env': _pct(enviadas, total),
              'p_err': _pct(erros, total), 'p_pend': _pct(pendentes, total)},
        'moeda': _moeda,
        'dados_reais': True,
    }
    base.update(extra)
    return base


# -------------------------------------------------------------------- telas
@escritorio_bp.get('')
@escritorio_bp.get('/')
def painel():
    return render_template('escritorio/painel.html', CSS=CSS, FIM=FIM,
                           LATERAL=lateral('escritorio'), cards=CARDS)


@escritorio_bp.get('/configuracao')
def configuracao():
    return render_template('escritorio/configuracao.html', **_contexto())


@escritorio_bp.get('/simples/lancamentos')
def lancamentos():
    from app.services import escritorio_lancamentos as svc
    competencia = _competencia()
    dados = svc.listar_competencia(competencia, empresa_ids=_ids_empresas_usuario())
    return render_template(
        'escritorio/lancamentos.html',
        competencia=competencia,
        empresas=dados['empresas'],
        m=dados['metricas'],
        moeda=_moeda,
        url_nfe=url_for('escritorio.ler_nfe'),
        url_salvar=url_for('escritorio.api_salvar_linha_lancamento'),
    )


@escritorio_bp.get('/simples/transmitir')
def transmitir():
    """Lista só quem tem lançamento na competência (memória real). SERPRO = etapa 2."""
    from app.services import escritorio_lancamentos as svc
    competencia = _competencia()
    dados = svc.listar_competencia(competencia, empresa_ids=_ids_empresas_usuario())
    empresas = []
    for e in dados['empresas']:
        receita = sum(p['valores'].get('total_receita', 0) for p in e['perfis'])
        tr = int(e.get('transmitido') or 0)
        status = 'manual' if tr == 2 else ('enviado' if tr == 1 else 'pendente')
        empresas.append({
            'id': e.get('company_id') or e['cnpj'],
            'razao': e['razao'],
            'cnpj': e.get('cnpj_fmt') or _fmt_cnpj(e['cnpj']),
            'cnpj_raw': e['cnpj'],
            'receita': receita,
            'rbt12': 0,
            'das': 0,
            'aliq': 0,
            'ok': e['ok'],
            'status': status,
        })
    return render_template(
        'escritorio/transmitir.html',
        competencia=competencia,
        empresas=empresas,
        m=dados['metricas'],
        moeda=_moeda,
        dados_reais=True,
    )


@escritorio_bp.get('/simples/rbt12')
def rbt12():
    return render_template('escritorio/rbt12.html', **_contexto())


@escritorio_bp.get('/xml/caminhos')
def caminhos_xml():
    return render_template('escritorio/caminhos.html', **_contexto())


@escritorio_bp.get('/xml/nfse')
def ler_nfse():
    return render_template('escritorio/ler_nfse.html', **_contexto())


@escritorio_bp.get('/xml/nfe')
def ler_nfe():
    # Tela FUNCIONAL: a leitura dos XMLs acontece no navegador (como no modelo); o
    # servidor só responde o CST de cada NCM e grava o lançamento.
    competencia = _competencia()
    mes = request.args.get('mes') or competencia[5:]
    ano = request.args.get('ano') or competencia[:4]
    cnpj = re.sub(r'\D', '', request.args.get('cnpj', ''))[:8]
    return render_template('escritorio/ler_nfe.html', competencia=competencia,
                           mes_inicial=mes, ano_inicial=ano, cnpj_base_inicial=cnpj,
                           pode_editar_ncm=_e_admin())


@escritorio_bp.get('/ncm')
def tabela_ncm():
    return render_template('escritorio/ncm.html', pode_editar=_e_admin())


# ------------------------------------------------------------------- API
@escritorio_bp.get('/api/ncm-cst')
def api_ncm_cst():
    """CST de PIS/COFINS por NCM, em lote: ?ncms=30049099,22021000,...

    GET de propósito: é leitura, e o guarda de segurança recusa POST sem empresa
    para usuário restrito.
    """
    from app.services import escritorio_ncm
    ncms = [n for n in re.split(r'[,;\s]+', request.args.get('ncms', '')) if n][:1000]
    return jsonify({'ok': True, 'cst': escritorio_ncm.cst_em_lote(ncms)})


@escritorio_bp.get('/api/empresa')
def api_empresa():
    """Empresa do Central pelo CNPJ completo (14 dígitos) lido do XML."""
    from app.models import Company
    from app.services import permissoes
    cnpj = re.sub(r'\D', '', request.args.get('cnpj', ''))
    if len(cnpj) != 14:
        return jsonify({'ok': False, 'msg': 'CNPJ inválido.'}), 400
    empresa = Company.query.filter_by(cnpj=cnpj).first()
    if not empresa:
        return jsonify({'ok': True, 'encontrada': False})
    u = _usuario()
    if u and not permissoes.pode_empresa(u, empresa.id):
        return jsonify({'ok': False, 'msg': 'Esta empresa não está liberada para o seu usuário.'}), 403
    return jsonify({'ok': True, 'encontrada': True, 'company_id': empresa.id,
                    'razao_social': empresa.razao_social})


@escritorio_bp.post('/api/lancamentos/salvar')
def api_salvar_lancamento():
    """Equivalente ao "Salvar na Memória" do modelo (salvar_lancamento.php)."""
    from app.escritorio_models import EscritorioLancamento
    from app.models import Company

    dados = request.get_json(silent=True) or {}
    cnpj = re.sub(r'\D', '', str(dados.get('cnpj', '')))
    try:
        mes, ano = int(dados.get('mes')), int(dados.get('ano'))
    except (TypeError, ValueError):
        return jsonify({'status': 'erro', 'msg': 'Mês/ano inválidos.'}), 400
    if len(cnpj) != 14 or not (1 <= mes <= 12) or not (2000 <= ano <= 2100):
        return jsonify({'status': 'erro', 'msg': 'CNPJ, mês ou ano inválidos.'}), 400

    campos = ('total_receita', 'devolucoes', 'outras_receitas',
              'rec_sem_st', 'rec_com_st_mono', 'rec_monofasica', 'rec_com_st')
    valores = {c: _num(dados.get(c)) for c in campos}
    if all(valores[c] == 0 for c in ('total_receita', 'rec_sem_st', 'rec_com_st_mono',
                                     'rec_monofasica', 'rec_com_st')):
        return jsonify({'status': 'erro',
                        'msg': 'Nenhum valor foi apurado. Gere o relatório antes de salvar.'}), 400

    empresa = Company.query.filter_by(cnpj=cnpj).first()
    competencia = f'{ano:04d}-{mes:02d}'
    lanc = EscritorioLancamento.query.filter_by(cnpj=cnpj, competencia=competencia,
                                                perfil='comercio').first()
    novo = lanc is None
    if novo:
        lanc = EscritorioLancamento(cnpj=cnpj, competencia=competencia, perfil='comercio')
        db.session.add(lanc)
    for campo, valor in valores.items():
        setattr(lanc, campo, valor)
    if 'rec_sem_st_isencao' in dados:
        lanc.rec_sem_st_isencao = _num(dados.get('rec_sem_st_isencao'))
    lanc.company_id = empresa.id if empresa else None
    lanc.origem = 'xml_nfe'
    detalhe = dados.get('detalhe')
    lanc.detalhe_json = json.dumps(detalhe, ensure_ascii=False)[:200000] if detalhe else None
    lanc.atualizado_por = _nome_usuario()
    # nova apuração XML: reabre conferência (OK volta a falso; transmitido permanece)
    lanc.ok = False
    db.session.commit()

    aviso = '' if empresa else ('CNPJ não cadastrado no Central: o lançamento foi gravado, '
                                'mas não está vinculado a uma empresa.')
    return jsonify({'status': 'ok', 'id': lanc.id, 'novo': novo, 'competencia': competencia,
                    'empresa': empresa.razao_social if empresa else None, 'aviso': aviso,
                    'url_lancamentos': url_for('escritorio.lancamentos', competencia=competencia)})


@escritorio_bp.post('/api/lancamentos/linha')
def api_salvar_linha_lancamento():
    """Salva uma linha (perfil) da tela de Lançamentos — save_row do Integra."""
    from app.services import escritorio_lancamentos as svc
    from app.services import permissoes

    dados = request.get_json(silent=True) or {}
    try:
        lanc_id = int(dados.get('id') or dados.get('lancamento_id') or 0)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'msg': 'Lançamento inválido.'}), 400
    if not lanc_id:
        return jsonify({'ok': False, 'msg': 'Lançamento inválido.'}), 400

    from app.escritorio_models import EscritorioLancamento
    lanc = db.session.get(EscritorioLancamento, lanc_id)
    if not lanc:
        return jsonify({'ok': False, 'msg': 'Lançamento não encontrado.'}), 404
    u = _usuario()
    if u and lanc.company_id and not permissoes.pode_empresa(u, lanc.company_id):
        return jsonify({'ok': False, 'msg': 'Empresa não liberada para o seu usuário.'}), 403

    try:
        ok = bool(dados.get('ok'))
        transmitido = int(dados.get('transmitido') or 0)
        if transmitido not in (0, 1, 2):
            transmitido = 2 if dados.get('transmitido') else 0
        svc.salvar_perfil(lanc_id, dados.get('valores') or {}, ok, transmitido,
                          usuario=_nome_usuario())
    except ValueError as exc:
        return jsonify({'ok': False, 'msg': str(exc)}), 400
    return jsonify({'ok': True, 'id': lanc_id})


@escritorio_bp.get('/api/ncm')
def api_ncm_listar():
    from app.services import escritorio_ncm
    return jsonify({'ok': True, 'itens': escritorio_ncm.listar()})


@escritorio_bp.post('/api/ncm')
def api_ncm_salvar():
    from app.escritorio_models import EscritorioNcmCst
    if not _e_admin():
        return jsonify({'ok': False, 'msg': 'Só o administrador altera a tabela NCM × CST.'}), 403
    dados = request.get_json(silent=True) or {}
    ncm = re.sub(r'\D', '', str(dados.get('ncm', '')))
    try:
        cst = int(dados.get('cst'))
    except (TypeError, ValueError):
        cst = 0
    if not (2 <= len(ncm) <= 8):
        return jsonify({'ok': False, 'msg': 'Informe o NCM (ou prefixo) com 2 a 8 dígitos.'}), 400
    if cst not in (1, 4, 5, 6):
        return jsonify({'ok': False, 'msg': 'CST deve ser 1, 4, 5 ou 6.'}), 400

    item = None
    if dados.get('id'):
        item = db.session.get(EscritorioNcmCst, int(dados['id']))
    duplicado = EscritorioNcmCst.query.filter_by(ncm=ncm).first()
    if duplicado and (item is None or duplicado.id != item.id):
        return jsonify({'ok': False, 'msg': f'O NCM {ncm} já está na tabela.'}), 400
    if item is None:
        item = EscritorioNcmCst(ncm=ncm)
        db.session.add(item)
    item.ncm = ncm
    item.cst = cst
    item.descricao = str(dados.get('descricao') or '')[:255]
    item.base_legal = str(dados.get('base_legal') or '')[:255]
    item.ativo = bool(dados.get('ativo', True))
    item.atualizado_por = _nome_usuario()
    db.session.commit()
    return jsonify({'ok': True, 'id': item.id})


@escritorio_bp.post('/api/ncm/<int:item_id>/excluir')
def api_ncm_excluir(item_id):
    from app.escritorio_models import EscritorioNcmCst
    if not _e_admin():
        return jsonify({'ok': False, 'msg': 'Só o administrador altera a tabela NCM × CST.'}), 403
    item = db.session.get(EscritorioNcmCst, item_id)
    if item:
        db.session.delete(item)
        db.session.commit()
    return jsonify({'ok': True})


@escritorio_bp.get('/uteis/faturamento')
def faturamento():
    aba = 'faturamento' if request.args.get('aba') == 'faturamento' else 'whatsapp'
    return render_template('escritorio/faturamento.html', **_contexto(aba=aba))
