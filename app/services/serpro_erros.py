"""Erros SERPRO que acontecem ANTES de qualquer requisição sair do Central.

⚠️ DESVIO INTENCIONAL (17o, correções de 15/09/2026) — não existe no exe.

`ErroAntesDoEnvio` = problema local (configuração incompleta, recurso não implementado,
certificado ilegível). Nada foi enviado, então:

* não custa nada;
* NÃO diz nada sobre a procuração da empresa — `ProcuracaoService.registrar_erro`
  guarda a mensagem, mas não conta para a trava de 24 h (senão um ajuste de
  Configurações travaria a carteira inteira).

Herda de `ValueError` para não mudar o tratamento de quem já captura `ValueError`.
"""


class ErroAntesDoEnvio(ValueError):
    """Falha local: a requisição à SERPRO não chegou a ser feita."""


PROCURADOR_NAO_IMPLEMENTADO = (
    'Procurador PF está ligado em Configurações, mas o envio por procurador ainda não foi '
    'implementado no Central (módulo 5 da reconstrução). Nada foi enviado à SERPRO. '
    'Desligue "Procurador PF" para usar o certificado A1 do escritório.'
)
