"""Contatos das empresas (WhatsApp/e-mail) e a verificação deles — 19o desvio.

Uma linha por empresa em `contatos_empresa`. O número de envio é o PRIMEIRO válido
entre `whatsapp` e `whatsapp_2` (mesma regra do `celularSql` do portal).

Verificação em dois níveis:
1. **Formato** (sempre, sem rede): `whatsapp_numero.validar`.
2. **Existe no WhatsApp** (opcional, uazapi `/chat/check`): só para os que passaram no
   formato. Se a uazapi não responder, o contato fica `ok` pelo formato e a tela mostra
   que a checagem online não aconteceu.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from app.extensions import db
from app.integracao_models import ContatoEmpresa, WhatsappEnvio
from app.models import Company
from app.services import uazapi_service as uazapi
from app.services import whatsapp_numero as num

STATUS = ('ok', 'invalido', 'inexistente', 'pendente')


def _contato_de(company: Company) -> Optional[ContatoEmpresa]:
    return ContatoEmpresa.query.filter_by(company_id=company.id).first()


def numero_de_envio(contato: Optional[ContatoEmpresa]) -> Dict[str, Any]:
    """Primeiro celular válido; se nenhum, devolve o primeiro preenchido com o motivo."""
    if not contato:
        return {'ok': False, 'numero': None, 'motivo': 'sem contato cadastrado', 'campo': None}
    primeiro_erro = None
    for campo in ('whatsapp', 'whatsapp_2'):
        valor = getattr(contato, campo)
        if not valor:
            continue
        v = num.validar(valor)
        if v['ok']:
            return {'ok': True, 'numero': v['numero'], 'motivo': '', 'campo': campo}
        primeiro_erro = primeiro_erro or {'ok': False, 'numero': None, 'motivo': v['motivo'], 'campo': campo}
    return primeiro_erro or {'ok': False, 'numero': None, 'motivo': 'WhatsApp em branco', 'campo': None}


def serializar(company: Company, contato: Optional[ContatoEmpresa],
               ultimo_envio: Optional[WhatsappEnvio] = None) -> Dict[str, Any]:
    envio = numero_de_envio(contato)
    return {
        'company_id': company.id,
        'razao_social': company.razao_social,
        'cnpj': company.cnpj,
        'empresa_ativa': bool(company.ativo),
        'whatsapp': contato.whatsapp if contato else None,
        'whatsapp_2': contato.whatsapp_2 if contato else None,
        'whatsapp_formatado': num.formatar(contato.whatsapp) if contato and contato.whatsapp else None,
        'email': contato.email if contato else None,
        'responsavel': contato.responsavel if contato else None,
        'ativo': bool(contato.ativo) if contato else True,
        'origem': contato.origem if contato else None,
        'observacao': contato.observacao if contato else None,
        'numero_envio': envio['numero'],
        'numero_envio_ok': envio['ok'],
        'numero_envio_motivo': envio['motivo'],
        'verificado_status': contato.verificado_status if contato else 'pendente',
        'verificado_motivo': contato.verificado_motivo if contato else None,
        'verificado_em': contato.verificado_em.isoformat() if contato and contato.verificado_em else None,
        'atualizado_em': contato.atualizado_em.isoformat() if contato and contato.atualizado_em else None,
        'ultimo_envio': ({
            'status': ultimo_envio.status, 'erro': ultimo_envio.erro, 'contexto': ultimo_envio.contexto,
            'criado_em': ultimo_envio.criado_em.isoformat(),
        } if ultimo_envio else None),
    }


def listar(apenas_ativas: bool = True) -> List[Dict[str, Any]]:
    consulta = Company.query
    if apenas_ativas:
        consulta = consulta.filter_by(ativo=True)
    empresas = consulta.order_by(Company.razao_social.asc()).all()
    contatos = {c.company_id: c for c in ContatoEmpresa.query.all()}
    ultimos: Dict[int, WhatsappEnvio] = {}
    for e in WhatsappEnvio.query.order_by(WhatsappEnvio.id.desc()).limit(2000).all():
        if e.company_id and e.company_id not in ultimos:
            ultimos[e.company_id] = e
    return [serializar(c, contatos.get(c.id), ultimos.get(c.id)) for c in empresas]


def resumo(itens: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        'empresas': len(itens),
        'com_numero_valido': sum(1 for i in itens if i['numero_envio_ok']),
        'sem_contato': sum(1 for i in itens if not i['whatsapp'] and not i['whatsapp_2']),
        'invalidos': sum(1 for i in itens if (i['whatsapp'] or i['whatsapp_2']) and not i['numero_envio_ok']),
        'verificados_ok': sum(1 for i in itens if i['verificado_status'] == 'ok'),
        'inexistentes': sum(1 for i in itens if i['verificado_status'] == 'inexistente'),
        'desativados': sum(1 for i in itens if not i['ativo']),
    }


def salvar(company_id: int, dados: Dict[str, Any], quem: Optional[str] = None,
           origem: str = 'manual') -> Dict[str, Any]:
    company = db.session.get(Company, company_id)
    if not company:
        raise ValueError('Empresa não encontrada.')
    contato = _contato_de(company)
    if not contato:
        contato = ContatoEmpresa(company_id=company.id, cnpj=company.cnpj)
        db.session.add(contato)

    for campo, tamanho in (('whatsapp', 20), ('whatsapp_2', 20), ('email', 200),
                           ('responsavel', 120), ('observacao', 500)):
        if campo in dados:
            valor = (str(dados[campo]).strip() if dados[campo] is not None else '')
            if campo.startswith('whatsapp'):
                valor = num.normalizar(valor)
            contato.__setattr__(campo, valor[:tamanho] or None)
    if 'ativo' in dados:
        contato.ativo = bool(dados['ativo'])
    contato.origem = origem
    contato.atualizado_por = quem

    # verificação de formato acontece na hora de salvar; a online é ação separada
    envio = numero_de_envio(contato)
    if not contato.whatsapp and not contato.whatsapp_2:
        contato.verificado_status, contato.verificado_motivo = 'pendente', 'sem WhatsApp'
    elif envio['ok']:
        contato.verificado_status, contato.verificado_motivo = 'ok', 'formato válido (não conferido online)'
    else:
        contato.verificado_status, contato.verificado_motivo = 'invalido', envio['motivo']
    contato.verificado_em = datetime.utcnow()
    db.session.commit()
    uazapi.limpar_cache_destinos()
    return serializar(company, contato)


def importar(itens: List[Dict[str, Any]], quem: Optional[str] = None) -> Dict[str, Any]:
    """Lote `[{cnpj, whatsapp, whatsapp_2?, email?, responsavel?}]` — casa por CNPJ."""
    ok, sem_cadastro, erros = 0, [], []
    for item in itens or []:
        cnpj = num.so_digitos(item.get('cnpj'))
        company = Company.query.filter_by(cnpj=cnpj).first() if len(cnpj) == 14 else None
        if not company:
            sem_cadastro.append(cnpj or str(item.get('cnpj')))
            continue
        try:
            salvar(company.id, {k: item.get(k) for k in ('whatsapp', 'whatsapp_2', 'email', 'responsavel')
                                if k in item}, quem=quem, origem='importado')
            ok += 1
        except Exception as exc:
            erros.append({'cnpj': cnpj, 'erro': str(exc)})
    return {'importados': ok, 'sem_cadastro': sem_cadastro, 'erros': erros}


def verificar(company_ids: Optional[List[int]] = None, online: bool = True) -> Dict[str, Any]:
    """Formato para todos; existência no WhatsApp (uazapi) para os válidos, se `online`."""
    consulta = ContatoEmpresa.query
    if company_ids:
        consulta = consulta.filter(ContatoEmpresa.company_id.in_(company_ids))
    contatos = consulta.all()

    validos: Dict[str, List[ContatoEmpresa]] = {}
    for c in contatos:
        envio = numero_de_envio(c)
        if not c.whatsapp and not c.whatsapp_2:
            c.verificado_status, c.verificado_motivo = 'pendente', 'sem WhatsApp'
        elif envio['ok']:
            c.verificado_status, c.verificado_motivo = 'ok', 'formato válido (não conferido online)'
            validos.setdefault(envio['numero'], []).append(c)
        else:
            c.verificado_status, c.verificado_motivo = 'invalido', envio['motivo']
        c.verificado_em = datetime.utcnow()

    online_feito = False
    online_erro = None
    if online and validos and uazapi.configurado():
        resultado = uazapi.verificar_numeros(list(validos.keys()))
        for numero, lista in validos.items():
            r = resultado.get(numero) or {}
            for c in lista:
                if r.get('existe') is True:
                    c.verificado_status, c.verificado_motivo = 'ok', 'existe no WhatsApp'
                    online_feito = True
                elif r.get('existe') is False:
                    c.verificado_status, c.verificado_motivo = 'inexistente', 'a uazapi diz que este número não tem WhatsApp'
                    online_feito = True
                elif r.get('erro'):
                    online_erro = r['erro']
    elif online and not uazapi.configurado():
        online_erro = 'uazapi não configurada — só o formato foi conferido'

    db.session.commit()
    uazapi.limpar_cache_destinos()
    return {'verificados': len(contatos), 'online': online_feito, 'online_erro': online_erro}


def enviar_teste(company_id: int, quem: Optional[str] = None,
                 texto: Optional[str] = None) -> Dict[str, Any]:
    company = db.session.get(Company, company_id)
    if not company:
        return {'ok': False, 'motivo': 'Empresa não encontrada.'}
    contato = _contato_de(company)
    envio = numero_de_envio(contato)
    if not envio['ok']:
        return {'ok': False, 'motivo': envio['motivo'], 'numero': None}
    corpo = texto or (f'🧪 *MENSAGEM DE TESTE* (envio manual, não é cobrança)\n\n'
                      f'Olá, {company.razao_social}. Este é um teste de conexão do escritório. '
                      f'Se você recebeu, o envio pelo WhatsApp está funcionando. Pode ignorar.')
    return uazapi.enviar(envio['numero'], corpo, company_id=company.id, contexto='teste',
                         enviado_por=quem, respeitar_janela=False, delay_ms=300)


def historico(company_id: Optional[int] = None, limite: int = 100) -> List[Dict[str, Any]]:
    consulta = WhatsappEnvio.query
    if company_id:
        consulta = consulta.filter_by(company_id=company_id)
    return [{
        'id': e.id, 'company_id': e.company_id, 'numero': e.numero, 'tipo': e.tipo,
        'contexto': e.contexto, 'status': e.status, 'erro': e.erro, 'mensagem_id': e.mensagem_id,
        'tentativas': e.tentativas, 'enviado_por': e.enviado_por, 'criado_em': e.criado_em.isoformat(),
        'texto': (e.texto or '')[:200],
    } for e in consulta.order_by(WhatsappEnvio.id.desc()).limit(limite).all()]
