"""Tela `/usuarios` — administração de acessos (13o desvio).

Exclusiva de administrador (o guard de `security.py` barra o resto em
`permissoes.PREFIXOS_ADMIN`).

O que dá para fazer aqui:
* criar usuário (CPF + nome + papel);
* escolher **rotinas** (quais telas) e **empresas** (quais clientes);
* gerar o **link de primeiro acesso** e o de **recuperação de senha** — o admin copia a
  URL e entrega ao usuário. Sem SMTP: nada a configurar, funciona no ato;
* ativar/desativar e remover.

O token só aparece **uma vez**, no momento em que é gerado: no arquivo fica apenas o
sha256 dele.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template_string, request

from app.models import Company
from app.ui import CSS, topo
from app.services import permissoes, usuarios_service

usuarios_bp = Blueprint('usuarios', __name__)


@usuarios_bp.get('/usuarios')
def tela_usuarios():
    return render_template_string(PAGINA)


@usuarios_bp.get('/api/usuarios')
def listar_usuarios():
    empresas = [
        {'id': c.id, 'nome': c.razao_social, 'cnpj': c.cnpj}
        for c in Company.query.order_by(Company.razao_social.asc()).all()
    ]
    itens = []
    for u in usuarios_service.listar():
        u = dict(u)
        u['convite_pendente'] = usuarios_service.convite_pendente(u['id'])
        itens.append(u)

    return jsonify({
        'usuarios': itens,
        'rotinas': permissoes.catalogo(),
        'empresas': empresas,
    })


@usuarios_bp.post('/api/usuarios')
def criar_usuario():
    dados = request.get_json(silent=True) or {}
    try:
        criado = usuarios_service.criar(
            usuario=dados.get('usuario', ''),
            nome=dados.get('nome', ''),
            papel=dados.get('papel', usuarios_service.PAPEL_OPERADOR),
            empresas=dados.get('empresas', usuarios_service.TODAS),
            rotinas=dados.get('rotinas', usuarios_service.TODAS),
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    return jsonify({'success': True, 'usuario': criado,
                    'message': 'Usuário criado. Gere o link de primeiro acesso.'})


@usuarios_bp.put('/api/usuarios/<usuario_id>')
def atualizar_usuario(usuario_id: str):
    dados = request.get_json(silent=True) or {}
    campos = {k: dados[k] for k in ('nome', 'papel', 'ativo', 'empresas', 'rotinas')
              if k in dados}
    try:
        atualizado = usuarios_service.atualizar(usuario_id, **campos)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    return jsonify({'success': True, 'usuario': atualizado, 'message': 'Acessos salvos.'})


@usuarios_bp.delete('/api/usuarios/<usuario_id>')
def remover_usuario(usuario_id: str):
    try:
        usuarios_service.remover(usuario_id)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400
    return jsonify({'success': True, 'message': 'Usuário removido.'})


@usuarios_bp.post('/api/usuarios/<usuario_id>/link')
def gerar_link(usuario_id: str):
    dados = request.get_json(silent=True) or {}
    tipo = 'recuperacao' if dados.get('tipo') == 'recuperacao' else 'primeiro_acesso'
    try:
        convite = usuarios_service.gerar_convite(usuario_id, tipo=tipo)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    url = request.host_url.rstrip('/') + '/definir-senha?token=' + convite['token']
    return jsonify({
        'success': True,
        'url': url,
        'expira_em': convite['expira_em'],
        'horas': convite['horas'],
        'message': ('Link gerado. Copie e entregue ao usuário — ele não será mostrado '
                    'de novo.'),
    })


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Usuários e acessos — Central Pendências e-CAC</title>
<style>""" + CSS + """</style>
</head>
<body>
""" + topo() + """
<div class="wrap">
  <h1>Usuários e acessos</h1>
  <p class="sub">
    Cada usuário enxerga só as <b>rotinas</b> e as <b>empresas</b> liberadas aqui. A
    restrição vale no servidor: mesmo digitando a URL na mão, a API recusa.
  </p>

  <div class="card">
    <h2>Novo usuário</h2>
    <div class="grade">
      <div><label class="linha" for="n-usuario">Usuário (CPF)</label>
        <input id="n-usuario" placeholder="somente números"></div>
      <div><label class="linha" for="n-nome">Nome</label>
        <input id="n-nome" placeholder="Nome de quem vai usar"></div>
      <div><label class="linha" for="n-papel">Papel</label>
        <select id="n-papel">
          <option value="operador">Operador</option>
          <option value="admin">Administrador</option>
        </select></div>
      <div><button class="primario" onclick="criar()">Criar usuário</button></div>
    </div>
    <div id="m-novo"></div>
  </div>

  <div class="card">
    <h2>Usuários</h2>
    <div style="overflow-x:auto"><table>
      <thead><tr>
        <th>Usuário</th><th>Papel</th><th>Senha</th><th>Rotinas</th><th>Empresas</th>
        <th>Último acesso</th><th>Ações</th>
      </tr></thead>
      <tbody id="corpo"><tr><td colspan="7">Carregando…</td></tr></tbody>
    </table></div>
    <div id="m-lista"></div>
  </div>

  <p class="rodape"><a href="/">Voltar ao painel</a></p>
</div>

<dialog id="dlg">
  <h2 id="dlg-titulo">Acessos</h2>
  <div id="dlg-corpo"></div>
  <div style="margin-top:16px;text-align:right">
    <button onclick="document.getElementById('dlg').close()">Cancelar</button>
    <button class="primario" onclick="salvarAcessos()">Salvar acessos</button>
  </div>
</dialog>

<script>
let ESTADO = { usuarios: [], rotinas: [], empresas: [] };
let EDITANDO = null;

function msg(id, texto, bom) {
  const el = document.getElementById(id);
  el.className = 'msg ' + (bom ? 'ok' : 'bad');
  el.textContent = texto;
}

async function carregar() {
  const r = await fetch('/api/usuarios');
  if (!r.ok) { msg('m-lista', 'Falha ao carregar (' + r.status + ').', false); return; }
  ESTADO = await r.json();
  desenhar();
}

function resumo(valor, catalogo) {
  if (valor === 'todas') return '<span class="badge ok">todas</span>';
  const n = (valor || []).length;
  if (!n) return '<span class="badge mute">nenhuma</span>';
  return '<span class="badge warn">' + n + ' de ' + catalogo.length + '</span>';
}

function desenhar() {
  const corpo = document.getElementById('corpo');
  if (!ESTADO.usuarios.length) {
    corpo.innerHTML = '<tr><td colspan="7">Nenhum usuário.</td></tr>';
    return;
  }
  corpo.innerHTML = ESTADO.usuarios.map(u => {
    const senha = u.senha_definida
      ? '<span class="badge ok">definida</span>'
      : (u.convite_pendente
          ? '<span class="badge warn">link pendente</span>'
          : '<span class="badge mute">sem senha</span>');
    const papel = u.papel === 'admin'
      ? '<span class="badge adm">admin</span>'
      : '<span class="badge mute">operador</span>';
    const ativo = u.ativo ? '' : ' <span class="badge mute">inativo</span>';
    return `<tr>
      <td><b>${u.usuario}</b>${ativo}<br><span style="color:#6b7280">${u.nome || ''}</span></td>
      <td>${papel}</td>
      <td>${senha}</td>
      <td>${resumo(u.rotinas, ESTADO.rotinas)}</td>
      <td>${resumo(u.empresas, ESTADO.empresas)}</td>
      <td>${u.ultimo_login ? new Date(u.ultimo_login).toLocaleString('pt-BR') : '—'}</td>
      <td>
        <button onclick="abrirAcessos('${u.id}')">Acessos</button>
        <button onclick="gerarLink('${u.id}', '${u.senha_definida ? 'recuperacao' : 'primeiro_acesso'}')">
          ${u.senha_definida ? 'Link de nova senha' : 'Link de 1º acesso'}</button>
        <button onclick="alternar('${u.id}', ${!u.ativo})">${u.ativo ? 'Desativar' : 'Reativar'}</button>
        <button class="perigo" onclick="remover('${u.id}', '${u.usuario}')">Remover</button>
      </td></tr>`;
  }).join('');
}

async function criar() {
  const dados = {
    usuario: document.getElementById('n-usuario').value.trim(),
    nome: document.getElementById('n-nome').value.trim(),
    papel: document.getElementById('n-papel').value,
    empresas: [], rotinas: [],
  };
  if (dados.papel === 'admin') { dados.empresas = 'todas'; dados.rotinas = 'todas'; }
  const r = await fetch('/api/usuarios', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(dados),
  });
  const j = await r.json();
  msg('m-novo', j.message || (j.success ? 'Criado.' : 'Falhou.'), !!j.success);
  if (j.success) {
    document.getElementById('n-usuario').value = '';
    document.getElementById('n-nome').value = '';
    carregar();
  }
}

function abrirAcessos(id) {
  EDITANDO = ESTADO.usuarios.find(u => u.id === id);
  const admin = EDITANDO.papel === 'admin';
  document.getElementById('dlg-titulo').textContent = 'Acessos de ' + EDITANDO.usuario;

  if (admin) {
    document.getElementById('dlg-corpo').innerHTML =
      '<p style="color:#6b7280;font-size:13px;line-height:1.55">Administrador enxerga '
      + 'todas as rotinas e todas as empresas — não há o que restringir. Para limitar, '
      + 'mude o papel para <b>operador</b> na tabela.</p>';
  } else {
    const rot = ESTADO.rotinas.map(r => {
      const marcado = EDITANDO.rotinas === 'todas' || (EDITANDO.rotinas || []).includes(r.chave);
      const trava = r.admin ? ' disabled title="Só administrador"' : '';
      return `<label><input type="checkbox" class="rot" value="${r.chave}"
        ${marcado && !r.admin ? 'checked' : ''}${trava}> ${r.nome}${r.admin ? ' 🔒' : ''}</label>`;
    }).join('');
    const emp = ESTADO.empresas.map(e => {
      const marcado = EDITANDO.empresas === 'todas' || (EDITANDO.empresas || []).includes(e.id);
      return `<label><input type="checkbox" class="emp" value="${e.id}"
        ${marcado ? 'checked' : ''}> ${e.nome}</label>`;
    }).join('');
    document.getElementById('dlg-corpo').innerHTML = `
      <p style="font-size:13px;color:#6b7280;margin:0 0 4px">
        Rotinas marcadas com 🔒 são exclusivas de administrador.</p>
      <b style="font-size:13px">Rotinas</b>
      <div class="caixas">${rot}</div>
      <div style="margin:14px 0 4px"><b style="font-size:13px">Empresas</b>
        <button onclick="marcarTodas(true)">Todas</button>
        <button onclick="marcarTodas(false)">Nenhuma</button></div>
      <div class="caixas" style="max-height:230px;overflow:auto">${emp || '<i>Nenhuma empresa no banco.</i>'}</div>`;
  }
  document.getElementById('dlg').showModal();
}

function marcarTodas(valor) {
  document.querySelectorAll('.emp').forEach(c => c.checked = valor);
}

async function salvarAcessos() {
  if (!EDITANDO) return;
  const corpo = { };
  if (EDITANDO.papel !== 'admin') {
    corpo.rotinas = Array.from(document.querySelectorAll('.rot:checked')).map(c => c.value);
    corpo.empresas = Array.from(document.querySelectorAll('.emp:checked')).map(c => Number(c.value));
  }
  const r = await fetch('/api/usuarios/' + EDITANDO.id, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corpo),
  });
  const j = await r.json();
  document.getElementById('dlg').close();
  msg('m-lista', j.message || 'Salvo.', !!j.success);
  carregar();
}

async function alternar(id, ativo) {
  const r = await fetch('/api/usuarios/' + id, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ ativo }),
  });
  const j = await r.json();
  msg('m-lista', j.message || 'Salvo.', !!j.success);
  carregar();
}

async function remover(id, nome) {
  if (!confirm('Remover o usuário ' + nome + '? Ele perde o acesso na hora.')) return;
  const r = await fetch('/api/usuarios/' + id, { method: 'DELETE' });
  const j = await r.json();
  msg('m-lista', j.message || 'Removido.', !!j.success);
  carregar();
}

async function gerarLink(id, tipo) {
  const r = await fetch('/api/usuarios/' + id + '/link', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ tipo }),
  });
  const j = await r.json();
  if (!j.success) { msg('m-lista', j.message || 'Falhou.', false); return; }

  const el = document.getElementById('m-lista');
  el.className = '';
  el.innerHTML = `<div class="link-box">
      <b>Link gerado — copie agora, ele não aparece de novo.</b>
      <code id="url-gerada">${j.url}</code>
      <button class="primario" onclick="copiar()">Copiar link</button>
      <span style="font-size:12px;color:#6b7280"> vale ${j.horas} h · uso único</span>
    </div>`;
  carregar();
}

function copiar() {
  const texto = document.getElementById('url-gerada').textContent;
  navigator.clipboard.writeText(texto).then(
    () => alert('Link copiado.'),
    () => prompt('Copie o link:', texto));
}

carregar();
</script>
</body>
</html>
"""
