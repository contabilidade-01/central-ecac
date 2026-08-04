"""Tela e API da situação de procuração por empresa.

⚠️ DESVIO INTENCIONAL (5o) — NÃO existe no exe. Pedido do Jean (31/07/2026).

Por que uma tela separada em vez de mexer na existente: o React do exe é bundle
compilado e não temos o fonte (`app/static/app/assets/index-*.js`). A tabela
"Acompanhamento" nem exibe o campo `erro`. Esta página é servida direto pelo Flask e
não toca em nada do exe.

Rotas (prefixo `/api/procuracoes` + a página em `/procuracoes`) — não aparecem na
auditoria de rotas porque ela só compara os prefixos que existem no exe.
"""

import threading

from flask import Blueprint, current_app, jsonify, render_template_string, request

from app.models import Company
from app.ui import CSS, FIM, lateral
from app.services.procuracao_service import (
    SITUACAO_DESCONHECIDA,
    SITUACAO_OK,
    SITUACAO_SEM_PROCURACAO,
    ProcuracaoService,
)

procuracoes_bp = Blueprint('procuracoes', __name__)

_VARREDURA_LOCK = threading.Lock()


def _linhas():
    """Junta as empresas do banco com o que o mapa de procurações sabe delas."""
    dados = ProcuracaoService.carregar()
    registros = dados.get('empresas', {})
    linhas = []
    for company in Company.query.order_by(Company.razao_social.asc()).all():
        registro = registros.get(company.cnpj, {})
        ultimo = registro.get('ultimo_erro') or {}
        pode, motivo = ProcuracaoService.pode_gastar(company)
        linhas.append({
            'company_id': company.id,
            'razao_social': company.razao_social,
            'cnpj': company.cnpj,
            'ativo': bool(company.ativo),
            'situacao': registro.get('situacao', SITUACAO_DESCONHECIDA),
            'marcado_manualmente': bool(registro.get('marcado_manualmente')),
            'erros_seguidos': registro.get('erros_seguidos', 0),
            'ultimo_ok_em': registro.get('ultimo_ok_em'),
            'ultimo_erro_em': registro.get('ultimo_erro_em'),
            'erro_codigo': ultimo.get('codigo'),
            'erro_texto': ultimo.get('texto'),
            'erro_http': ultimo.get('http_status'),
            'erro_servico': ultimo.get('servico'),
            'erro_bruto': ultimo.get('bruto'),
            'observacao': registro.get('observacao'),
            'pode_gastar': pode,
            'motivo_bloqueio': motivo,
        })
    return linhas


@procuracoes_bp.get('/api/procuracoes')
def listar_procuracoes():
    resumo = ProcuracaoService.resumo()
    resumo['empresas'] = _linhas()
    return jsonify(resumo)


@procuracoes_bp.post('/api/procuracoes/marcar')
def marcar_procuracao():
    payload = request.get_json(silent=True) or {}
    cnpj = str(payload.get('cnpj') or '').strip()
    situacao = str(payload.get('situacao') or '').strip()
    if not cnpj:
        return jsonify({'success': False, 'message': 'Informe o CNPJ.'}), 400
    if situacao not in (SITUACAO_OK, SITUACAO_SEM_PROCURACAO):
        return jsonify({
            'success': False,
            'message': f"situacao deve ser '{SITUACAO_OK}' ou '{SITUACAO_SEM_PROCURACAO}'.",
        }), 400

    registro = ProcuracaoService.marcar(cnpj, situacao, payload.get('observacao', ''))
    return jsonify({'success': True, 'registro': registro})


# ------------------------------------------------------------ varredura grátis

@procuracoes_bp.post('/api/procuracoes/varredura')
def varredura_gratuita():
    """Sonda TODAS as empresas com o indicador da caixa postal — custo R$ 0,00.

    O indicador (`INNOVAMSG63`) **não é cobrado** — o próprio frontend do exe declara
    isso, e por isso `monitorar_empresa` não chama `register_usage`. Aqui ele é usado
    só como sonda: quem a SERPRO recusar fica marcado no mapa de procurações
    (`registrar_erro`), e quem responder é reabilitado (`registrar_sucesso`).

    `baixar_mensagens_quando_houver=False` é o que mantém o custo em zero: sem isso, a
    rota do botão "Monitorar" baixaria lista e detalhe (PAGOS) de quem tivesse mensagem
    nova. É a única diferença entre esta varredura e aquele botão.

    Roda em thread e devolve na hora, porque com 72 empresas leva minutos e um proxy
    no meio derrubaria a requisição por timeout. O acompanhamento sai no MONITOR_STATUS.
    """
    from app.services.caixa_postal_service import CaixaPostalService, get_monitor_status

    if get_monitor_status().get('running'):
        return jsonify({'success': False,
                        'message': 'Já existe uma varredura em andamento.'}), 409

    if not _VARREDURA_LOCK.acquire(blocking=False):
        return jsonify({'success': False,
                        'message': 'Já existe uma varredura em andamento.'}), 409

    payload = request.get_json(silent=True) or {}
    company_ids = payload.get('company_ids') or None
    app = current_app._get_current_object()

    def _rodar():
        try:
            with app.app_context():
                CaixaPostalService().monitorar_todas_empresas(
                    only_if_due=False,
                    baixar_mensagens_quando_houver=False,   # <- mantém o custo em zero
                    company_ids=company_ids,
                )
        except Exception:
            app.logger.exception('[PROCURACOES] varredura gratuita falhou')
        finally:
            _VARREDURA_LOCK.release()

    threading.Thread(target=_rodar, daemon=True).start()

    total = Company.query.filter_by(ativo=True).count() if not company_ids else len(company_ids)
    return jsonify({
        'success': True,
        'total': total,
        'message': f'Varredura iniciada em {total} empresa(s). Custo: R$ 0,00.',
    })


@procuracoes_bp.get('/api/procuracoes/varredura/status')
def status_varredura():
    from app.services.caixa_postal_service import get_monitor_status
    return jsonify(get_monitor_status())


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Procurações — Central Pendências e-CAC</title>
<style>""" + CSS + """</style>
</head>
<body>
""" + lateral('procuracoes') + """
<div class="wrap">
  <h1>Procurações</h1>
  <p class="sub">
    Descubra <b>quem a Receita recusa</b> antes de gastar. A sondagem usa o indicador da
    caixa postal, que é <b>gratuito</b> — quem recusar fica marcado aqui e deixa de
    consumir as chamadas <b>pagas</b> (lista e detalhe de mensagens).
  </p>

  <div class="card">
    <h2>Verificar agora</h2>
    <p class="dica">
      Passa por todas as empresas ativas perguntando à Receita se a procuração responde.
      <b>Custo: R$ 0,00</b> — nenhuma chamada paga é feita. Leva alguns minutos.
    </p>
    <button class="primario" id="btn-varrer" onclick="varrer()">
      Verificar todas as empresas — grátis</button>
    <div id="progresso"></div>
  </div>

  <div class="tiles" id="tiles"></div>

  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Empresa</th><th>CNPJ</th><th>Situação</th><th>Chamadas pagas</th>
            <th>Último erro da SERPRO</th><th>Ação</th>
          </tr>
        </thead>
        <tbody id="corpo"><tr><td colspan="6">Carregando…</td></tr></tbody>
      </table>
    </div>
  </div>

<script>
const BADGE = { ok:'ok', sem_procuracao:'bad', erro:'warn', desconhecida:'mute' };
const ROTULO = { ok:'Responde', sem_procuracao:'SEM procuração',
                 erro:'Recusada', desconhecida:'Ainda não verificada' };

function texto(v) { return (v === null || v === undefined || v === '') ? '—' : String(v); }

async function carregar() {
  const r = await fetch('/api/procuracoes');
  const d = await r.json();
  const c = d.contagem || {};
  document.getElementById('tiles').innerHTML = `
    <div class="tile"><span>Empresas</span><b>${d.empresas.length}</b></div>
    <div class="tile"><span>Respondem</span><b>${c.ok || 0}</b></div>
    <div class="tile"><span>Sem procuração</span><b>${c.sem_procuracao || 0}</b></div>
    <div class="tile"><span>Recusadas</span><b>${c.erro || 0}</b></div>
    <div class="tile"><span>Ainda não verificadas</span><b>${c.desconhecida || 0}</b></div>`;

  document.getElementById('corpo').innerHTML = d.empresas.map(e => {
    // Nem todo erro tem o formato da SERPRO: falha de certificado, rede ou
    // configuração chega como texto solto. Antes isso virava "—" e escondia
    // justamente o diagnóstico. Agora, sem código/texto, mostramos o BRUTO.
    let erro = '—';
    if (e.erro_texto || e.erro_codigo) {
      erro = `<code>${texto(e.erro_codigo)}</code> ${texto(e.erro_texto)}
              <br><small>${texto(e.erro_servico)} · HTTP ${texto(e.erro_http)} · ${texto(e.ultimo_erro_em)}</small>`;
    } else if (e.erro_bruto) {
      erro = `<code style="white-space:pre-wrap">${texto(e.erro_bruto).slice(0, 300)}</code>
              <br><small>${texto(e.erro_servico)} · ${texto(e.ultimo_erro_em)}</small>`;
    }
    const acao = e.situacao === 'sem_procuracao'
      ? `<button onclick="marcar('${e.cnpj}','ok')">Liberar</button>`
      : `<button onclick="marcar('${e.cnpj}','sem_procuracao')">Marcar sem procuração</button>`;
    return `<tr>
      <td>${texto(e.razao_social)}</td>
      <td>${texto(e.cnpj)}</td>
      <td><span class="badge ${BADGE[e.situacao] || 'mute'}">${ROTULO[e.situacao] || e.situacao}</span>
          ${e.marcado_manualmente ? '<br><small>marcada na mão</small>' : ''}</td>
      <td>${e.pode_gastar ? '<span class="badge ok">liberadas</span>'
                          : `<span class="badge bad">travadas</span><br><small>${texto(e.motivo_bloqueio)}</small>`}</td>
      <td class="${e.erro_texto ? 'erro' : ''}">${erro}</td>
      <td>${acao}</td>
    </tr>`;
  }).join('');
}

async function varrer() {
  const botao = document.getElementById('btn-varrer');
  const alvo = document.getElementById('progresso');
  botao.disabled = true;
  alvo.className = 'msg ok';
  alvo.textContent = 'Iniciando…';

  const r = await fetch('/api/procuracoes/varredura', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}',
  });
  const j = await r.json();
  if (!j.success) {
    alvo.className = 'msg bad'; alvo.textContent = j.message || 'Falhou.';
    botao.disabled = false; return;
  }
  alvo.textContent = j.message;
  acompanhar();
}

async function acompanhar() {
  const alvo = document.getElementById('progresso');
  const botao = document.getElementById('btn-varrer');
  try {
    const r = await fetch('/api/procuracoes/varredura/status');
    const s = await r.json();
    if (s.running) {
      const total = s.total || 0, feitas = s.checked || 0;
      alvo.className = 'msg ok';
      alvo.textContent = `Verificando ${feitas} de ${total}…`
        + (s.current_company_name ? ' (' + s.current_company_name + ')' : '');
      carregar();
      setTimeout(acompanhar, 2500);
      return;
    }
    alvo.className = 'msg ok';
    alvo.textContent = `Concluído: ${s.checked || 0} empresa(s) verificadas, `
      + `${s.errors || 0} com erro. Custo: R$ 0,00.`;
  } catch (e) {
    alvo.className = 'msg bad';
    alvo.textContent = 'Perdi o acompanhamento: ' + e.message;
  }
  botao.disabled = false;
  carregar();
}

async function marcar(cnpj, situacao) {
  await fetch('/api/procuracoes/marcar', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cnpj, situacao }),
  });
  carregar();
}

carregar();
</script>
</div>
""" + FIM + """
</body>
</html>
"""


@procuracoes_bp.get('/procuracoes')
def pagina_procuracoes():
    return render_template_string(PAGINA)
