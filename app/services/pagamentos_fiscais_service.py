"""Serviço para processamento e persistência de pagamentos fiscais.

Reconstituído fielmente do bytecode do exe.
Fonte da verdade: dis/services/pagamentos_fiscais_service.txt (1293 linhas)

Responsabilidades:
- Normalizar items do response SERPRO para formato do modelo
- Salvar/atualizar PagamentoFiscal e PagamentoFiscalDetalhe
- Orquestrar consulta em múltiplas empresas
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from app.extensions import db
from app.models import Company, PagamentoFiscal, PagamentoFiscalDetalhe
from app.services.serpro_pagamentos_service import SerproPagamentosService
from app.services.api_usage_service import ApiUsageService


class PagamentosFiscaisService:
    """Serviço de persistência de pagamentos fiscais.

    Reconstruído fielmente do bytecode.
    """

    def __init__(self):
        self.serpro = SerproPagamentosService()

    @staticmethod
    def _to_decimal(value) -> Decimal:
        """Converte valor para Decimal, retorna 0.00 se inválido."""
        if value in (None, "", False):
            return Decimal("0.00")
        try:
            return Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError):
            return Decimal("0.00")

    @staticmethod
    def _to_date(value) -> Optional[date]:
        """Converte valor para date. Tenta ISO e formatos comuns."""
        if not value:
            return None
        text = str(value).strip()

        # Tenta ISO com timezone
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            pass

        # Tenta formatos comuns
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text[:19], fmt).date()
            except ValueError:
                continue

        return None

    @staticmethod
    def _get(obj, *paths, default=None):
        """Busca valor em paths alternativos de um dict aninhado."""
        for path in paths:
            current = obj
            ok = True
            for key in path:
                if not isinstance(current, dict):
                    ok = False
                    break
                current = current.get(key)
            if ok and current not in (None, ""):
                return current
        return default

    @staticmethod
    def _default_data_inicio() -> str:
        """Retorna 1 de janeiro do ano corrente em ISO."""
        return date.today().replace(month=1, day=1).isoformat()

    @staticmethod
    def _default_data_fim() -> str:
        """Retorna data de hoje em ISO."""
        return date.today().isoformat()

    def _normalize_item(self, item: dict) -> dict:
        """Normaliza item do response SERPRO para campos do modelo."""
        receita_codigo = str(
            self._get(
                item,
                ("receitaPrincipal", "codigo"),
                ("receita_principal", "codigo"),
                ("codigoReceita",),
                ("receita_codigo",),
                default="",
            ) or ""
        ).strip().zfill(4)

        tipo_descricao = self._get(
            item, ("tipo", "descricao"), ("tipo_descricao",), default=None
        )
        tipo_abreviada = self._get(
            item, ("tipo", "descricaoAbreviada"), ("tipo_descricao_abreviada",), default=None
        )

        return {
            "numero_documento": str(
                self._get(item, ("numeroDocumento",), ("numero_documento",), default="")
            ).strip(),
            "tipo_codigo": self._get(
                item, ("tipo", "codigo"), ("tipo_codigo",), default=None
            ),
            "tipo_descricao": tipo_descricao,
            "tipo_descricao_abreviada": tipo_abreviada or tipo_descricao,
            "periodo_apuracao": self._to_date(
                self._get(item, ("periodoApuracao",), ("periodo_apuracao",), default=None)
            ),
            "data_arrecadacao": self._to_date(
                self._get(item, ("dataArrecadacao",), ("data_arrecadacao",), default=None)
            ),
            "data_vencimento": self._to_date(
                self._get(item, ("dataVencimento",), ("data_vencimento",), default=None)
            ),
            "receita_principal_codigo": receita_codigo,
            "receita_principal_descricao": self._get(
                item,
                ("receitaPrincipal", "descricao"),
                ("receita_principal", "descricao"),
                ("descricaoReceita",),
                ("receita_principal_descricao",),
                default=None,
            ),
            "referencia": self._get(item, ("referencia",), default=None),
            "valor_total": self._to_decimal(
                self._get(item, ("valorTotal",), ("valor_total",), default=0)
            ),
            "valor_principal": self._to_decimal(
                self._get(item, ("valorPrincipal",), ("valor_principal",), default=0)
            ),
            "valor_multa": self._to_decimal(
                self._get(item, ("valorMulta",), ("valor_multa",), default=0)
            ),
            "valor_juros": self._to_decimal(
                self._get(item, ("valorJuros",), ("valor_juros",), default=0)
            ),
            "source_json": item,
        }

    def _normalize_detalhe(self, detalhe: dict) -> dict:
        """Normaliza detalhe (desmembramento) de pagamento."""
        receita_codigo = str(
            self._get(
                detalhe,
                ("receitaPrincipal", "codigo"),
                ("receita_codigo",),
                default="",
            ) or ""
        ).strip().zfill(4)

        extensao_codigo = str(
            self._get(
                detalhe,
                ("receitaPrincipal", "extensaoReceita", "codigo"),
                ("extensao_receita_codigo",),
                default="",
            ) or ""
        ).strip()

        return {
            "sequencial": str(
                self._get(detalhe, ("sequencial",), default="")
            ).strip() or None,
            "receita_codigo": receita_codigo,
            "receita_descricao": self._get(
                detalhe,
                ("receitaPrincipal", "descricao"),
                ("receita_descricao",),
                default=None,
            ),
            "extensao_receita_codigo": extensao_codigo or None,
            "extensao_receita_descricao": self._get(
                detalhe,
                ("receitaPrincipal", "extensaoReceita", "descricao"),
                ("extensao_receita_descricao",),
                default=None,
            ),
            "periodo_apuracao": self._to_date(
                self._get(detalhe, ("periodoApuracao",), ("periodo_apuracao",), default=None)
            ),
            "data_vencimento": self._to_date(
                self._get(detalhe, ("dataVencimento",), ("data_vencimento",), default=None)
            ),
            "valor_total": self._to_decimal(
                self._get(detalhe, ("valorTotal",), ("valor_total",), default=0)
            ),
            "valor_principal": self._to_decimal(
                self._get(detalhe, ("valorPrincipal",), ("valor_principal",), default=0)
            ),
            "valor_multa": self._to_decimal(
                self._get(detalhe, ("valorMulta",), ("valor_multa",), default=0)
            ),
            "valor_juros": self._to_decimal(
                self._get(detalhe, ("valorJuros",), ("valor_juros",), default=0)
            ),
            "valor_saldo_total": self._to_decimal(
                self._get(detalhe, ("valorSaldoTotal",), ("valor_saldo_total",), default=0)
            ),
            "valor_saldo_principal": self._to_decimal(
                self._get(detalhe, ("valorSaldoPrincipal",), ("valor_saldo_principal",), default=0)
            ),
            "valor_saldo_multa": self._to_decimal(
                self._get(detalhe, ("valorSaldoMulta",), ("valor_saldo_multa",), default=0)
            ),
            "valor_saldo_juros": self._to_decimal(
                self._get(detalhe, ("valorSaldoJuros",), ("valor_saldo_juros",), default=0)
            ),
            "cib": self._get(detalhe, ("cib",), default=None),
            "source_json": detalhe,
        }

    def _salvar_detalhes_pagamento(
        self, pagamento: PagamentoFiscal, raw_item: dict
    ) -> None:
        """Salva desmembramentos (detalhes) de um pagamento."""
        detalhes = raw_item.get("desmembramentos") or []

        # Remove detalhes antigos
        PagamentoFiscalDetalhe.query.filter_by(
            pagamento_fiscal_id=pagamento.id
        ).delete()

        for detalhe in detalhes:
            normalized = self._normalize_detalhe(detalhe)
            if not normalized["receita_codigo"]:
                continue
            db.session.add(
                PagamentoFiscalDetalhe(
                    pagamento_fiscal_id=pagamento.id,
                    **normalized,
                )
            )

    def consultar_pagamentos_multiplas_empresas(
        self,
        company_ids: Iterable[int],
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
    ) -> dict:
        """Consulta e salva pagamentos de múltiplas empresas."""
        company_ids = [int(cid) for cid in company_ids if cid]

        if not company_ids:
            return {"success": False, "message": "Selecione ao menos uma empresa"}

        data_inicio = data_inicio or self._default_data_inicio()
        data_fim = data_fim or self._default_data_fim()

        companies = Company.query.filter(Company.id.in_(company_ids)).all()
        if not companies:
            return {"success": False, "message": "Nenhuma empresa encontrada"}

        total_novos = 0
        total_atualizados = 0
        empresas_processadas = []
        empresas_sem_retorno = []
        erros = []

        for company in companies:
            try:
                response = self.serpro.buscar_pagamentos(
                    company, data_inicio=data_inicio, data_fim=data_fim
                )
                items = response.get("items", []) or []

                ApiUsageService.register_usage(
                    route_type="consultar",
                    endpoint="/pagamentos",
                    company_id=company.id,
                )

                if not items:
                    empresas_sem_retorno.append({
                        "company_id": company.id,
                        "company_name": (
                            getattr(company, "razao_social", None)
                            or getattr(company, "nome", None)
                            or str(company.id)
                        ),
                    })
                    continue

                itens_validos = 0
                for raw_item in items:
                    normalized = self._normalize_item(raw_item)

                    if not normalized["numero_documento"] or not normalized["receita_principal_codigo"]:
                        continue

                    existing = PagamentoFiscal.query.filter_by(
                        company_id=company.id,
                        numero_documento=normalized["numero_documento"],
                    ).first()

                    if existing:
                        # Atualiza existente
                        existing.tipo_codigo = normalized["tipo_codigo"]
                        existing.tipo_descricao = normalized["tipo_descricao"]
                        existing.tipo_descricao_abreviada = normalized["tipo_descricao_abreviada"]
                        existing.periodo_apuracao = normalized["periodo_apuracao"]
                        existing.data_arrecadacao = normalized["data_arrecadacao"]
                        existing.data_vencimento = normalized["data_vencimento"]
                        existing.receita_principal_codigo = normalized["receita_principal_codigo"]
                        existing.receita_principal_descricao = normalized["receita_principal_descricao"]
                        existing.referencia = normalized["referencia"]
                        existing.valor_total = normalized["valor_total"]
                        existing.valor_principal = normalized["valor_principal"]
                        existing.valor_multa = normalized["valor_multa"]
                        existing.valor_juros = normalized["valor_juros"]
                        existing.source_json = normalized["source_json"]
                        existing.updated_at = datetime.utcnow()

                        db.session.flush()
                        self._salvar_detalhes_pagamento(existing, raw_item)
                        total_atualizados += 1
                    else:
                        # Cria novo
                        novo_pagamento = PagamentoFiscal(
                            company_id=company.id, **normalized
                        )
                        db.session.add(novo_pagamento)
                        db.session.flush()

                        self._salvar_detalhes_pagamento(novo_pagamento, raw_item)
                        total_novos += 1

                    itens_validos += 1

                empresas_processadas.append({
                    "company_id": company.id,
                    "company_name": (
                        getattr(company, "razao_social", None)
                        or getattr(company, "nome", None)
                        or str(company.id)
                    ),
                    "items_recebidos": len(items),
                    "items_validos": itens_validos,
                })

            except Exception as exc:
                db.session.rollback()
                message = str(exc)
                erros.append({
                    "company_id": company.id,
                    "company_name": (
                        getattr(company, "razao_social", None)
                        or getattr(company, "nome", None)
                        or str(company.id)
                    ),
                    "error": message,
                })

        # Commit se houve alterações
        if total_novos or total_atualizados:
            db.session.commit()

        # Retorno conforme lógica do exe
        if erros and not empresas_processadas and not empresas_sem_retorno:
            return {
                "success": False,
                "message": erros[0]["error"],
                "errors": erros,
            }

        if (
            total_novos == 0
            and total_atualizados == 0
            and empresas_sem_retorno
            and not erros
        ):
            return {
                "success": False,
                "message": "Consulta concluída, mas a API não retornou pagamentos para as empresas selecionadas.",
                "empresas_sem_retorno": empresas_sem_retorno,
            }

        # Monta mensagem de retorno conforme bytecode
        message = (
            f"Consulta concluída. {total_novos} pagamento(s) novo(s) "
            f"e {total_atualizados} atualizado(s)."
        )
        if erros:
            message += f" {len(erros)} empresa(s) com erro."

        return {
            "success": True,
            "message": message,
            "total_inseridos": total_novos,
            "total_atualizados": total_atualizados,
            "empresas_processadas": empresas_processadas,
            "empresas_sem_retorno": empresas_sem_retorno,
            "errors": erros,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
        }
