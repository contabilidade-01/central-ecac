"""Área "Escritório" — telas modeladas no visual do Integra Contador.

⚠️ DESVIO INTENCIONAL (17o) — NÃO existe no exe. Pedido do Jean em 15/09/2026: trazer
para o Central e-CAC as telas de escritório que ele usa no Integra Contador (painel de
cards, Lançamentos, Transmitir PGDAS-D, Caminhos XML, leitores de NFS-e e NF-e,
Upload PGDAS-D e Declaração de Faturamento/Envio do DAS).

Identidade do escritório / certificado A1 / chaves SERPRO: **não** há tela própria —
reusa `AppSetting` (menu Configurações do sistema). Card "Configuração • Dados do
Escritório" está oculto.

Primeira etapa = modelagem visual (dados de exemplo, sem ações, nada chama a SERPRO).
As funções entram tela a tela, na ordem de prioridade que o Jean definir.

Já funcional: **Ler XML NFe** (`/escritorio/xml/nfe`) + tabela NCM × CST (`/escritorio/ncm`)
+ **Lançamentos** (`/escritorio/simples/lancamentos`) lendo/gravando a memória.
**Transmitir PGDAS-D**: Calcular → Enviar/Retificar → Consultar → Gerar DAS pela SERPRO em
`/escritorio/api/pgdasd/*` (regras de custo em `services/escritorio_pgdasd.py`).

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


def _config_sistema() -> dict:
    """Credenciais/identidade do escritório = Configurações gerais (`AppSetting`).

    A aba Escritório NÃO tem cadastro próprio de certificado/CNPJ/chave SERPRO.
    Tudo que for emitir DAS / autenticar na Integra Contador reusa o mesmo registro
    que DAS Lote, Caixa Postal, etc.
    """
    from pathlib import Path

    from app.models import AppSetting
    from app.services import certificado as cert_svc

    s = AppSetting.query.first()
    if not s:
        return {
            'office_name': '',
            'contador_cnpj': '',
            'contador_cnpj_fmt': '',
            'tem_certificado': False,
            'tem_serpro': False,
            'tem_contador': False,
            'pronto_serpro': False,
            'procurador_pf': False,
            'url_configuracoes': '/?aba=configuracoes',
        }

    cnpj = re.sub(r'\D', '', s.contador_cnpj or '')
    path_gravado = (s.certificado_path or '').strip()
    tem_cert = False
    if path_gravado:
        try:
            tem_cert = Path(cert_svc.resolver(path_gravado)).is_file()
        except Exception:
            tem_cert = Path(path_gravado).is_file()
    tem_serpro = bool((s.serpro_consumer_key or '').strip()
                      and (s.serpro_consumer_secret or '').strip())
    tem_contador = len(cnpj) in (11, 14)
    # senha precisa existir para autenticar (não exibimos o valor)
    tem_senha = bool((s.certificado_password or '').strip())
    return {
        'office_name': (s.office_name or '').strip(),
        'contador_cnpj': cnpj,
        'contador_cnpj_fmt': _fmt_cnpj(cnpj) if len(cnpj) == 14 else cnpj,
        'tem_certificado': tem_cert and tem_senha,
        'tem_serpro': tem_serpro,
        'tem_contador': tem_contador,
        'pronto_serpro': tem_cert and tem_senha and tem_serpro and tem_contador,
        'procurador_pf': bool(s.procurador_pf_habilitado),
        'url_configuracoes': '/?aba=configuracoes',
    }


# Cards do painel, na mesma ordem e cor do original. `rota` = tela já modelada;
# sem rota, o card aparece igual mas avisa que a tela ainda não foi modelada.
CARDS = [
    {'titulo': 'Empresas', 'sub': 'Cadastro e gestão', 'cor': ('#5b21b6', '#7c3aed')},
    # Oculto: Configuração própria do Escritório — usar Configurações do sistema
    # ({'titulo': 'Configuração', 'sub': 'Dados do Escritório', 'cor': ('#581c87', '#7e22ce'),
    #  'rota': 'configuracao', 'cert': True}),
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
    from app.services import escritorio_pgdas as pgdas

    competencia = _competencia()
    empresa_ids = _ids_empresas_usuario()
    dados_mem = svc.listar_competencia(competencia, empresa_ids=empresa_ids)
    por_cnpj = {e['cnpj']: e for e in dados_mem['empresas']}
    rbt12_map = pgdas.rbt12_em_lote(list(por_cnpj.keys()), competencia)

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
            'rbt12': rbt12_map.get(c.cnpj, 0.0),
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
            'rbt12': rbt12_map.get(cnpj, 0.0),
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
        'cfg': _config_sistema(),
    }
    base.update(extra)
    return base


# -------------------------------------------------------------------- telas
@escritorio_bp.get('')
@escritorio_bp.get('/')
def painel():
    return render_template('escritorio/painel.html', CSS=CSS, FIM=FIM,
                           LATERAL=lateral('escritorio'), cards=CARDS,
                           cfg=_config_sistema())


# Oculto — identidade/certificado/chave SERPRO ficam em Configurações do sistema.
# Template configuracao.html removido (15/09/2026); card em CARDS também comentado.


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
        cfg=_config_sistema(),
    )


@escritorio_bp.get('/simples/transmitir')
def transmitir():
    """Lista quem tem lançamento na competência + situação da declaração/DAS no Central.

    Calcular / Enviar / Retificar / Consultar / Gerar DAS usam `/escritorio/api/pgdasd/*`
    (pré-voo grátis, confirmação de custo, auditoria). Ver `services/escritorio_pgdasd.py`.
    """
    from pathlib import Path

    from app.escritorio_models import EscritorioDeclaracao
    from app.services import escritorio_lancamentos as svc
    from app.services import escritorio_pgdas as pgdas
    from app.services.serpro_pgdasd_client import CUSTO_ESTIMADO
    competencia = _competencia()
    periodo = competencia.replace('-', '')  # AAAAMM para a SERPRO
    dados = svc.listar_competencia(competencia, empresa_ids=_ids_empresas_usuario())
    cnpjs = [e['cnpj'] for e in dados['empresas']]
    rbt12_map = pgdas.rbt12_em_lote(cnpjs, competencia)
    declaracoes = {d.cnpj: d for d in EscritorioDeclaracao.query.filter(
        EscritorioDeclaracao.pa == periodo, EscritorioDeclaracao.cnpj.in_(cnpjs)).all()} if cnpjs else {}
    empresas = []
    for e in dados['empresas']:
        receita = sum(p['valores'].get('total_receita', 0) for p in e['perfis'])
        tr = int(e.get('transmitido') or 0)
        status = 'manual' if tr == 2 else ('enviado' if tr == 1 else 'pendente')
        d = declaracoes.get(e['cnpj'])
        if d is not None and d.situacao == 'incerta':
            status = 'incerto'
        elif d is not None and d.situacao != 'transmitida' and d.ultimo_erro and tr == 0:
            status = 'erro'
        company_id = e.get('company_id')
        try:
            company_id = int(company_id) if company_id not in (None, '') else None
        except (TypeError, ValueError):
            company_id = None
        conf = pgdas.tem_historico_suficiente(e['cnpj'], competencia)
        das_valor = (d.das_valor_total or d.total_devido or 0) if d is not None else 0
        empresas.append({
            'id': company_id or e['cnpj'],
            'company_id': company_id,
            'razao': e['razao'],
            'cnpj': e.get('cnpj_fmt') or _fmt_cnpj(e['cnpj']),
            'cnpj_raw': e['cnpj'],
            'receita': receita,
            'rbt12': rbt12_map.get(e['cnpj'], 0),
            'rbt12_ok': conf['ok'],
            'das': das_valor,
            'aliq': round(das_valor / receita * 100, 2) if receita and das_valor else 0,
            'ok': e['ok'],
            'status': status,
            'situacao': d.situacao if d is not None else 'rascunho',
            'tem_das': bool(d is not None and d.das_arquivo and Path(d.das_arquivo).is_file()),
            'tem_declaracao': bool(d is not None and d.arquivo_declaracao and Path(d.arquivo_declaracao).is_file()),
            'tem_recibo': bool(d is not None and d.arquivo_recibo and Path(d.arquivo_recibo).is_file()),
            'ultimo_erro': (d.ultimo_erro or '') if d is not None else '',
        })
    return render_template(
        'escritorio/transmitir.html',
        competencia=competencia,
        periodo_apuracao=periodo,
        empresas=empresas,
        m=dados['metricas'],
        moeda=_moeda,
        dados_reais=True,
        cfg=_config_sistema(),
        custos={k: float(v) for k, v in CUSTO_ESTIMADO.items()},
        api_pgdasd=url_for('escritorio.api_pgdasd_estado').rsplit('/', 1)[0],
    )


@escritorio_bp.get('/simples/rbt12')
def rbt12():
    return render_template(
        'escritorio/rbt12.html',
        **_contexto(),
        url_importar=url_for('escritorio.api_pgdas_importar'),
    )


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


@escritorio_bp.post('/api/pgdas/importar')
def api_pgdas_importar():
    """Importa histórico do PDF PGDAS-D (mesmo JSON do Integra Contador).

    Grava receitas/folha por mês + meta RBT12 do PA. Empresa precisa estar cadastrada.
    """
    from app.models import Company
    from app.services import escritorio_pgdas as pgdas
    from app.services import permissoes

    dados = request.get_json(silent=True) or {}
    cnpj = re.sub(r'\D', '', str(dados.get('cnpj') or ''))
    if len(cnpj) != 14:
        return jsonify({'ok': False, 'msg': 'CNPJ inválido ou ausente no PDF/importação.'}), 400
    company = Company.query.filter_by(cnpj=cnpj).first()
    if not company:
        return jsonify({
            'ok': False,
            'msg': f'Empresa {cnpj} não cadastrada. Cadastre o CNPJ no sistema antes de importar.',
        }), 400
    u = _usuario()
    if u and not permissoes.pode_empresa(u, company.id):
        return jsonify({'ok': False, 'msg': 'Empresa não liberada para o seu usuário.'}), 403

    try:
        resultado = pgdas.importar_payload(dados, usuario=_nome_usuario())
    except ValueError as exc:
        return jsonify({'ok': False, 'msg': str(exc)}), 400
    except Exception as exc:
        return jsonify({'ok': False, 'msg': f'Falha ao importar: {exc}'}), 500
    return jsonify(resultado)


@escritorio_bp.get('/api/pgdas/rbt12')
def api_pgdas_rbt12():
    """Consulta RBT12 de um CNPJ na competência (PA)."""
    from app.services import escritorio_pgdas as pgdas
    cnpj = re.sub(r'\D', '', request.args.get('cnpj', ''))
    pa = request.args.get('pa') or request.args.get('competencia') or _competencia()
    if len(cnpj) != 14:
        return jsonify({'ok': False, 'msg': 'CNPJ inválido.'}), 400
    conf = pgdas.tem_historico_suficiente(cnpj, pa)
    return jsonify({
        'ok': True, **conf, 'cnpj': cnpj,
        'pa': pgdas.normalizar_competencia(pa),
    })


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


# =====================================================================================
#  PGDAS-D pela SERPRO: Calcular → Transmitir → Gerar DAS (PDF guardado)
#  Regras de custo em app/services/escritorio_pgdasd.py e serpro_pgdasd_client.py.
# =====================================================================================
_OPERACOES_PAGAS = ('calcular', 'transmitir', 'consultar', 'gerar_das')


def _pgdasd_erro(exc):
    return jsonify({'ok': False, 'bloqueios': exc.bloqueios, 'avisos': exc.avisos,
                    'mensagem': ' '.join(exc.bloqueios), 'custo_estimado': 0.0}), exc.status


def _pgdasd_parametros():
    corpo = request.get_json(silent=True) if request.method == 'POST' else None
    corpo = corpo if isinstance(corpo, dict) else {}
    origem = corpo or request.args
    return corpo, re.sub(r'\D', '', str(origem.get('cnpj', ''))), str(origem.get('competencia', ''))


def _pgdasd_contexto():
    """(ctx, corpo, None) ou (None, None, resposta_de_erro). Checa empresa do usuário."""
    from app.services import escritorio_pgdasd as svc
    from app.services import permissoes
    corpo, cnpj, competencia = _pgdasd_parametros()
    if len(cnpj) != 14:
        return None, None, (jsonify({'ok': False, 'mensagem': 'CNPJ inválido.'}), 400)
    try:
        ctx = svc.carregar(cnpj, competencia, _nome_usuario())
    except svc.BloqueioPgdasd as exc:
        return None, None, _pgdasd_erro(exc)
    u = _usuario()
    if u and not permissoes.e_admin(u):
        if ctx.company is None or not permissoes.pode_empresa(u, ctx.company.id):
            return None, None, (jsonify({'ok': False,
                                         'mensagem': 'Empresa não liberada para o seu usuário.'}), 403)
    return ctx, corpo, None


def _pgdasd_executar(operacao):
    """Executa uma operação paga. Exige `confirmar_custo: true` no corpo — a tela só
    envia depois que o usuário viu empresa, competência, valores e custo estimado."""
    from app.services import escritorio_pgdasd as svc
    ctx, corpo, erro = _pgdasd_contexto()
    if erro:
        return erro
    if corpo.get('confirmar_custo') is not True:
        return jsonify({'ok': False, 'mensagem': 'Confirmação de custo ausente. Nada foi enviado.'}), 428
    try:
        if operacao == 'calcular':
            r = svc.calcular(ctx)
        elif operacao == 'transmitir':
            r = svc.transmitir(ctx, retificar=bool(corpo.get('retificar')),
                               hash_confirmado=corpo.get('hash_confirmado') or '')
        elif operacao == 'consultar':
            r = svc.consultar(ctx)
        else:
            r = svc.gerar_das(ctx, data_consolidacao=corpo.get('data_consolidacao') or None,
                              forcar=bool(corpo.get('forcar')),
                              confirmar_externa=bool(corpo.get('confirmar_externa')))
    except svc.BloqueioPgdasd as exc:
        return _pgdasd_erro(exc)
    except Exception:
        from flask import current_app
        db.session.rollback()
        current_app.logger.exception('PGDAS-D %s falhou (%s %s)', operacao, ctx.cnpj, ctx.pa)
        return jsonify({'ok': False, 'mensagem': 'Erro interno. Veja o log do servidor antes de repetir.'}), 500
    return jsonify(r), 200


@escritorio_bp.get('/api/pgdasd/estado')
def api_pgdasd_estado():
    """Grátis: situação, valores calculados, arquivos guardados e o que pode ser feito."""
    from app.services import escritorio_pgdasd as svc
    ctx, _corpo, erro = _pgdasd_contexto()
    if erro:
        return erro
    pf = svc.preflight(ctx, 'estado')
    return jsonify({'ok': True, 'estado': svc.estado(ctx), 'configuracao': {
        'bloqueios': pf['bloqueios'], 'avisos': pf['avisos'], 'info': pf['info']}})


@escritorio_bp.post('/api/pgdasd/pre-visualizar')
def api_pgdasd_pre_visualizar():
    """Grátis: JSON exato que seria enviado + problemas encontrados."""
    from app.services import escritorio_pgdasd as svc
    ctx, _corpo, erro = _pgdasd_contexto()
    if erro:
        return erro
    return jsonify({'ok': True, **svc.pre_visualizar(ctx), 'estado': svc.estado(ctx)})


@escritorio_bp.post('/api/pgdasd/calcular')
def api_pgdasd_calcular():
    return _pgdasd_executar('calcular')


@escritorio_bp.post('/api/pgdasd/transmitir')
def api_pgdasd_transmitir():
    return _pgdasd_executar('transmitir')


@escritorio_bp.post('/api/pgdasd/consultar')
def api_pgdasd_consultar():
    return _pgdasd_executar('consultar')


@escritorio_bp.post('/api/pgdasd/gerar-das')
def api_pgdasd_gerar_das():
    return _pgdasd_executar('gerar_das')


@escritorio_bp.get('/api/pgdasd/arquivo/<tipo>')
def api_pgdasd_arquivo(tipo):
    """Grátis: devolve o PDF guardado (DAS, declaração, recibo, MAED). Nunca chama a SERPRO."""
    from flask import send_file
    from app.services import escritorio_pgdasd as svc
    ctx, _corpo, erro = _pgdasd_contexto()
    if erro:
        return erro
    caminho = svc.caminho_arquivo(ctx, tipo)
    if not caminho:
        return jsonify({'ok': False, 'mensagem': 'Arquivo não encontrado no Central.'}), 404
    nome = f'{tipo}_{ctx.cnpj}_{ctx.pa}.pdf'
    return send_file(caminho, mimetype='application/pdf', as_attachment=request.args.get('download') == '1',
                     download_name=nome, max_age=0)


@escritorio_bp.get('/api/pgdasd/das-zip')
def api_pgdasd_das_zip():
    """Grátis: ZIP com os PDFs de DAS JÁ GUARDADOS da competência (sem emitir nada)."""
    import io
    import zipfile
    from pathlib import Path
    from flask import send_file
    from app.escritorio_models import EscritorioDeclaracao
    from app.services import escritorio_pgdasd as svc
    from app.services import permissoes
    try:
        pa = svc.pa_de(request.args.get('competencia', ''))
    except svc.BloqueioPgdasd as exc:
        return _pgdasd_erro(exc)
    cnpjs = {re.sub(r'\D', '', c) for c in request.args.get('cnpjs', '').split(',') if c.strip()}
    consulta = EscritorioDeclaracao.query.filter(EscritorioDeclaracao.pa == pa,
                                                 EscritorioDeclaracao.das_arquivo.isnot(None))
    if cnpjs:
        consulta = consulta.filter(EscritorioDeclaracao.cnpj.in_(cnpjs))
    u = _usuario()
    buffer, total = io.BytesIO(), 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for d in consulta.all():
            if u and not permissoes.e_admin(u) and not (d.company_id and permissoes.pode_empresa(u, d.company_id)):
                continue
            ctx_path = Path(d.das_arquivo)
            if ctx_path.is_file():
                zf.write(ctx_path, f'DAS_{d.cnpj}_{pa}_{d.das_numero or ""}.pdf')
                total += 1
    if not total:
        return jsonify({'ok': False, 'mensagem': 'Nenhum DAS guardado para a seleção. Gere as guias primeiro.'}), 404
    buffer.seek(0)
    return send_file(buffer, mimetype='application/zip', as_attachment=True,
                     download_name=f'DAS_{pa}_{total}.zip', max_age=0)


@escritorio_bp.post('/api/pgdasd/lote/iniciar')
def api_pgdasd_lote_iniciar():
    from flask import current_app
    from app.models import Company
    from app.services import escritorio_pgdasd as svc
    from app.services import permissoes
    corpo = request.get_json(silent=True) or {}
    if corpo.get('confirmar_custo') is not True:
        return jsonify({'ok': False, 'mensagem': 'Confirmação de custo ausente. Nada foi enviado.'}), 428
    acao = str(corpo.get('acao') or '')
    competencia = str(corpo.get('competencia') or '')
    itens, vistos = [], set()
    u = _usuario()
    for item in corpo.get('itens') or []:
        cnpj = re.sub(r'\D', '', str((item or {}).get('cnpj', '')))
        if len(cnpj) != 14 or cnpj in vistos:
            continue
        vistos.add(cnpj)
        if u and not permissoes.e_admin(u):
            emp = Company.query.filter_by(cnpj=cnpj).first()
            if not emp or not permissoes.pode_empresa(u, emp.id):
                return jsonify({'ok': False, 'mensagem': f'Empresa {cnpj} não liberada para o seu usuário.'}), 403
        itens.append({'cnpj': cnpj, 'competencia': competencia, 'hash': item.get('hash_confirmado')})
    if not itens:
        return jsonify({'ok': False, 'mensagem': 'Selecione ao menos uma empresa.'}), 400
    if len(itens) > 300:
        return jsonify({'ok': False, 'mensagem': 'Lote limitado a 300 empresas por vez.'}), 400
    try:
        lote = svc.iniciar_lote(current_app._get_current_object(), acao, itens, _nome_usuario())
    except svc.BloqueioPgdasd as exc:
        return _pgdasd_erro(exc)
    return jsonify({'ok': True, 'lote': lote})


@escritorio_bp.get('/api/pgdasd/lote/<lote_id>')
def api_pgdasd_lote_status(lote_id):
    from app.services import escritorio_pgdasd as svc
    lote = svc.status_lote(lote_id)
    if not lote:
        return jsonify({'ok': False, 'mensagem': 'Lote não encontrado (o servidor pode ter reiniciado).'}), 404
    return jsonify({'ok': True, 'lote': lote})
