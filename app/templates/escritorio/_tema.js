// Tema claro/escuro compartilhado entre o painel e as telas (mesma chave).
(function(){
  var CHAVE = 'escritorio_tema';
  var botao = document.getElementById('themeToggle');
  function aplicar(tema){
    var claro = tema === 'light';
    document.body.classList.toggle('light', claro);
    if (botao) {
      botao.textContent = claro ? '🌙' : '☀️';
      botao.title = claro ? 'Ativar modo escuro' : 'Ativar modo claro';
    }
  }
  var salvo = 'dark';
  try { salvo = localStorage.getItem(CHAVE) === 'light' ? 'light' : 'dark'; } catch(e){}
  aplicar(salvo);
  if (botao) botao.addEventListener('click', function(){
    var proximo = document.body.classList.contains('light') ? 'dark' : 'light';
    aplicar(proximo);
    try { localStorage.setItem(CHAVE, proximo); } catch(e){}
  });
})();

function mostrarAlerta(tipo, titulo, mensagem){
  var box = document.getElementById('alertBox');
  box.className = 'alert-box alert-' + (tipo || 'warning');
  document.getElementById('alertIcon').textContent = tipo === 'success' ? '✔' : tipo === 'error' ? '✖' : '⚠';
  document.getElementById('alertTitle').textContent = titulo || '';
  document.getElementById('alertMsg').textContent = mensagem || '';
  document.getElementById('alertOverlay').classList.add('show');
}
function fecharAlerta(){ document.getElementById('alertOverlay').classList.remove('show'); }
document.addEventListener('keydown', function(e){ if (e.key === 'Escape') fecharAlerta(); });

// Ações ainda não ligadas: avisa sem inventar que há "dados de exemplo".
document.addEventListener('click', function(e){
  var alvo = e.target.closest('[data-acao]');
  if (!alvo) return;
  e.preventDefault();
  var acao = alvo.getAttribute('data-acao') || 'Esta ação';
  var detalhe = alvo.getAttribute('data-acao-msg')
    || document.body.getAttribute('data-acao-msg')
    || 'A lista já usa dados reais do cadastro/memória; esta ação específica ainda não foi ligada.';
  mostrarAlerta('warning', 'Ação pendente',
    '"' + acao + '" ainda não está ligado.\n' + detalhe);
});
document.addEventListener('submit', function(e){
  if (e.target.hasAttribute('data-livre')) return;   // ex.: trocar competência (GET)
  e.preventDefault();
  var detalhe = document.body.getAttribute('data-acao-msg')
    || 'A lista já usa dados reais; o envio deste formulário ainda não foi ligado.';
  mostrarAlerta('warning', 'Ação pendente',
    'O envio deste formulário ainda não está ligado.\n' + detalhe);
});
