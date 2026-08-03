from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Iterable, List, Optional, Dict, Tuple

import io
import re
import zipfile
import unicodedata

from app.extensions import db
from app.models import Company, PagamentoFiscal, ReceitaContaDePara


class DominioExportService:
    """Serviço para exportar dados para layout 6100 do Domínio (Thomson Reuters)"""

    USUARIO_PADRAO = 'GERENTE'

    @staticmethod
    def _valor_str(valor: Decimal) -> str:
        """
        Formata Decimal para string do layout 6100.
        Remove ponto/vírgula (centavos sem separador).

        Args:
            valor: Valor em Decimal

        Returns:
            String formatada
        """
        valor = Decimal(valor) if valor else Decimal(0)
        return f"{valor:.2f}".replace('.', ',')

    def _linha_6100(
        self,
        data: str,
        conta_debito: str,
        conta_credito: str,
        valor: Decimal,
        codigo_historico: str,
        descricao_historico: str
    ) -> str:
        """
        Gera UMA linha no layout 6100 (posicional).

        Formato: |6100|data|conta_débito|conta_crédito|valor|código_histórico|descrição_histórico|usuário|||\n
        Args:
            data: Data em formato DD/MM/YYYY
            conta_debito: Conta de débito (ex: 1.1.1.00.00)
            conta_credito: Conta de crédito
            valor: Valor em Decimal
            codigo_historico: Código do histórico
            descricao_historico: Descrição do histórico

        Returns:
            String da linha formatada
        """
        valor_str = self._valor_str(valor)
        return f"|6100|{data}|{conta_debito}|{conta_credito}|{valor_str}|{codigo_historico}|{descricao_historico}|{self.USUARIO_PADRAO}|||"

    def _base_query(
        self,
        company_id: Optional[int] = None,
        company_ids: Optional[Iterable[int]] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        codigo_receita: Optional[str] = None,
        apenas_nao_exportados: bool = False
    ):
        """
        Constrói query base de PagamentoFiscal com filtros aplicáveis.

        Args:
            company_id: ID da empresa específica
            company_ids: Múltiplos IDs de empresa
            data_inicio: Data de arrecadação mínima
            data_fim: Data de arrecadação máxima
            codigo_receita: Código de receita específica
            apenas_nao_exportados: Filtrar apenas não exportados

        Returns:
            Query objeto
        """
        query = PagamentoFiscal.query.join(
            Company, Company.id == PagamentoFiscal.company_id
        )

        if company_id:
            query = query.filter(PagamentoFiscal.company_id == company_id)
        elif company_ids:
            ids = [int(x) for x in company_ids if x]
            if ids:
                query = query.filter(PagamentoFiscal.company_id.in_(ids))

        if codigo_receita:
            query = query.filter(
                PagamentoFiscal.receita_principal_codigo == str(codigo_receita).strip().zfill(4)
            )

        if data_inicio:
            query = query.filter(PagamentoFiscal.data_arrecadacao >= data_inicio)

        if data_fim:
            query = query.filter(PagamentoFiscal.data_arrecadacao <= data_fim)

        if apenas_nao_exportados:
            query = query.filter(PagamentoFiscal.exportado.is_(False))

        query = query.order_by(
            PagamentoFiscal.company_id.asc(),
            PagamentoFiscal.data_arrecadacao.asc(),
            PagamentoFiscal.id.asc()
        )

        return query

    def gerar_txt(self, company_id: int, pagamento_ids: Iterable[int]) -> str:
        """
        Gera TXT para empresa e pagamentos específicos.

        Args:
            company_id: ID da empresa
            pagamento_ids: IDs de pagamentos

        Returns:
            String com conteúdo TXT
        """
        return self.gerar_txt_filtrado(company_id=company_id, pagamento_ids=list(pagamento_ids))

    def gerar_txt_filtrado(
        self,
        company_id: Optional[int] = None,
        company_ids: Optional[Iterable[int]] = None,
        pagamento_ids: Optional[Iterable[int]] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        codigo_receita: Optional[str] = None,
        apenas_nao_exportados: bool = False
    ) -> str:
        """
        Gera TXT filtrado com múltiplas condições.

        Args:
            company_id: ID da empresa
            company_ids: Múltiplos IDs de empresa
            pagamento_ids: IDs específicos de pagamentos
            data_inicio: Data de arrecadação mínima
            data_fim: Data de arrecadação máxima
            codigo_receita: Código de receita
            apenas_nao_exportados: Filtrar apenas não exportados

        Returns:
            String com TXT completo
        """
        # Constrói query base
        query = self._base_query(
            company_id=company_id,
            company_ids=company_ids,
            data_inicio=data_inicio,
            data_fim=data_fim,
            codigo_receita=codigo_receita,
            apenas_nao_exportados=apenas_nao_exportados
        )

        # Filtra por IDs específicos se fornecido
        if pagamento_ids:
            query = query.filter(PagamentoFiscal.id.in_(list(pagamento_ids)))

        # Executa query
        pagamentos = query.all()
        if not pagamentos:
            raise ValueError('Nenhum pagamento encontrado para exportação')

        # Carrega empresas relacionadas
        company_map = {
            c.id: c for c in Company.query.filter(
                Company.id.in_({p.company_id for p in pagamentos})
            ).all()
        }

        # Carrega mapeamentos de receita->conta
        deparas = ReceitaContaDePara.query.filter(
            ReceitaContaDePara.company_id.in_(company_map.keys())
        ).all()

        depara_map: Dict[Tuple[int, str], ReceitaContaDePara] = {
            (d.company_id, d.receita_codigo): d for d in deparas
        }

        # Agrupa pagamentos por empresa
        grouped = defaultdict(list)
        for pagamento in pagamentos:
            grouped[pagamento.company_id].append(pagamento)

        # Monta linhas
        linhas = []
        exportados_ids = []
        now = datetime.utcnow()

        for current_company_id, company_pagamentos in grouped.items():
            company = company_map.get(current_company_id)
            if not company:
                raise ValueError(f'Empresa {current_company_id} não encontrada')

            # Cabeçalho da empresa
            linhas.append(f"|0000|{company.cnpj}|")

            # Processa cada pagamento
            for pagamento in company_pagamentos:
                depara = depara_map.get((pagamento.company_id, pagamento.receita_principal_codigo))
                if not depara:
                    raise ValueError(
                        f"De-para não encontrado para a empresa {company.razao_social} "
                        f"na receita {pagamento.receita_principal_codigo}"
                    )

                if not pagamento.data_arrecadacao:
                    raise ValueError(
                        f"Pagamento {pagamento.numero_documento} sem data de arrecadação"
                    )

                data_str = pagamento.data_arrecadacao.strftime('%d/%m/%Y')
                valor_principal = Decimal(pagamento.valor_principal or 0)
                valor_multa = Decimal(pagamento.valor_multa or 0)
                valor_juros = Decimal(pagamento.valor_juros or 0)

                # Valor principal
                if valor_principal > 0:
                    linhas.append('|6000|X||||')
                    historico_desc = depara.historico_principal or depara.historico_juros or \
                                    pagamento.receita_principal_descricao or pagamento.receita_principal_codigo or ''
                    linhas.append(self._linha_6100(
                        data_str,
                        depara.conta_debito_valor_principal,
                        depara.conta_credito_valor_principal,
                        valor_principal,
                        depara.historico_principal or '',
                        historico_desc
                    ))

                # Juros
                if valor_juros > 0:
                    if not depara.conta_debito_juros:
                        raise ValueError(
                            f"Conta débito juros não configurada para a empresa {company.razao_social} "
                            f"na receita {pagamento.receita_principal_codigo}"
                        )
                    linhas.append('|6000|X||||')
                    historico_desc = depara.historico_principal or depara.historico_juros or \
                                    pagamento.receita_principal_descricao or pagamento.receita_principal_codigo or ''
                    linhas.append(self._linha_6100(
                        data_str,
                        depara.conta_debito_juros,
                        depara.conta_credito_valor_principal,
                        valor_juros,
                        depara.historico_principal or '',
                        historico_desc
                    ))

                # Multa
                if valor_multa > 0:
                    if not depara.conta_debito_multa:
                        raise ValueError(
                            f"Conta débito multa não configurada para a empresa {company.razao_social} "
                            f"na receita {pagamento.receita_principal_codigo}"
                        )
                    linhas.append('|6000|X||||')
                    historico_desc = depara.historico_principal or depara.historico_juros or \
                                    pagamento.receita_principal_descricao or pagamento.receita_principal_codigo or ''
                    linhas.append(self._linha_6100(
                        data_str,
                        depara.conta_debito_multa,
                        depara.conta_credito_valor_principal,
                        valor_multa,
                        depara.historico_principal or '',
                        historico_desc
                    ))

                # Marca como processado
                exportados_ids.append(pagamento.id)

        # Atualiza flags de exportação no BD
        if exportados_ids:
            PagamentoFiscal.query.filter(PagamentoFiscal.id.in_(exportados_ids)).update(
                {
                    PagamentoFiscal.exportado: True,
                    PagamentoFiscal.exported_at: now
                },
                synchronize_session=False
            )
            db.session.commit()

        return '\n'.join(linhas) + '\n'

    def _sanitize_path_part(self, value: str) -> str:
        """
        Sanitiza string para nome de arquivo/diretório.
        Remove acentos e caracteres inválidos.

        Args:
            value: String a sanitizar

        Returns:
            String sanitizada
        """
        value = value or ''
        value = value.strip()
        if not value:
            return 'SEM_NOME'

        # Remove acentos
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')

        # Remove caracteres especiais
        value = re.sub(r'[\\/:*?"<>|]+', '_', value)

        # Normaliza espaços
        value = re.sub(r'\s+', ' ', value).strip()

        return value or 'SEM_NOME'

    def _mesano_from_pagamentos(self, pagamentos: List[PagamentoFiscal]) -> str:
        """
        Extrai mês/ano predominante de lista de pagamentos.

        Args:
            pagamentos: Lista de pagamentos

        Returns:
            String MMYYYY
        """
        datas_validas = [p.data_arrecadacao for p in pagamentos if p.data_arrecadacao]

        if not datas_validas:
            raise ValueError('Não foi possível determinar MESANO: pagamentos sem data de arrecadação')

        primeira = datas_validas[0]
        return primeira.strftime('%m%Y')

    def _montar_linhas_empresa(
        self,
        company: Company,
        company_pagamentos: List[PagamentoFiscal],
        depara_map: Dict[Tuple[int, str], ReceitaContaDePara]
    ) -> Tuple[List[str], List[int]]:
        """
        Monta todas as linhas 6100 de uma empresa.

        Args:
            company: Objeto Company
            company_pagamentos: Pagamentos da empresa
            depara_map: Mapa (company_id, receita_codigo) -> ReceitaContaDePara

        Returns:
            Tupla (linhas, ids_exportados)
        """
        linhas = [f"|0000|{company.cnpj}|"]
        exportados_ids = []

        for pagamento in company_pagamentos:
            depara = depara_map.get((pagamento.company_id, pagamento.receita_principal_codigo))
            if not depara:
                raise ValueError(
                    f"De-para não encontrado para a empresa {company.razao_social} "
                    f"na receita {pagamento.receita_principal_codigo}"
                )

            if not pagamento.data_arrecadacao:
                raise ValueError(
                    f"Pagamento {pagamento.numero_documento} sem data de arrecadação"
                )

            data_str = pagamento.data_arrecadacao.strftime('%d/%m/%Y')
            valor_principal = Decimal(pagamento.valor_principal or 0)
            valor_multa = Decimal(pagamento.valor_multa or 0)
            valor_juros = Decimal(pagamento.valor_juros or 0)

            # Valor principal
            if valor_principal > 0:
                linhas.append('|6000|X||||')
                historico_desc = depara.historico_principal or depara.historico_juros or \
                                pagamento.receita_principal_descricao or pagamento.receita_principal_codigo or ''
                linhas.append(self._linha_6100(
                    data_str,
                    depara.conta_debito_valor_principal,
                    depara.conta_credito_valor_principal,
                    valor_principal,
                    depara.historico_principal or '',
                    historico_desc
                ))

            # Juros
            if valor_juros > 0:
                if not depara.conta_debito_juros:
                    raise ValueError(
                        f"Conta débito juros não configurada para a empresa {company.razao_social} "
                        f"na receita {pagamento.receita_principal_codigo}"
                    )
                linhas.append('|6000|X||||')
                historico_desc = depara.historico_principal or depara.historico_juros or \
                                pagamento.receita_principal_descricao or pagamento.receita_principal_codigo or ''
                linhas.append(self._linha_6100(
                    data_str,
                    depara.conta_debito_juros,
                    depara.conta_credito_valor_principal,
                    valor_juros,
                    depara.historico_principal or '',
                    historico_desc
                ))

            # Multa
            if valor_multa > 0:
                if not depara.conta_debito_multa:
                    raise ValueError(
                        f"Conta débito multa não configurada para a empresa {company.razao_social} "
                        f"na receita {pagamento.receita_principal_codigo}"
                    )
                linhas.append('|6000|X||||')
                historico_desc = depara.historico_principal or depara.historico_juros or \
                                pagamento.receita_principal_descricao or pagamento.receita_principal_codigo or ''
                linhas.append(self._linha_6100(
                    data_str,
                    depara.conta_debito_multa,
                    depara.conta_credito_valor_principal,
                    valor_multa,
                    depara.historico_principal or '',
                    historico_desc
                ))

            # Marca como processado
            exportados_ids.append(pagamento.id)

        return linhas, exportados_ids

    def _buscar_pagamentos_e_dependencias(
        self,
        company_id: Optional[int] = None,
        company_ids: Optional[Iterable[int]] = None,
        pagamento_ids: Optional[Iterable[int]] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        codigo_receita: Optional[str] = None,
        apenas_nao_exportados: bool = False
    ) -> Tuple[List[PagamentoFiscal], Dict[int, Company], Dict[Tuple[int, str], ReceitaContaDePara], dict]:
        """
        Busca pagamentos e suas dependências (empresas, de-para).

        Returns:
            Tupla (pagamentos, company_map, depara_map, grouped_by_company)
        """
        query = self._base_query(
            company_id=company_id,
            company_ids=company_ids,
            data_inicio=data_inicio,
            data_fim=data_fim,
            codigo_receita=codigo_receita,
            apenas_nao_exportados=apenas_nao_exportados
        )

        if pagamento_ids:
            query = query.filter(PagamentoFiscal.id.in_(list(pagamento_ids)))

        pagamentos = query.all()
        if not pagamentos:
            raise ValueError('Nenhum pagamento encontrado para exportação')

        # Carrega empresas
        company_map = {
            c.id: c for c in Company.query.filter(
                Company.id.in_({p.company_id for p in pagamentos})
            ).all()
        }

        # Carrega de-para
        deparas = ReceitaContaDePara.query.filter(
            ReceitaContaDePara.company_id.in_(company_map.keys())
        ).all()

        depara_map: Dict[Tuple[int, str], ReceitaContaDePara] = {
            (d.company_id, d.receita_codigo): d for d in deparas
        }

        # Agrupa por empresa
        grouped = defaultdict(list)
        for pagamento in pagamentos:
            grouped[pagamento.company_id].append(pagamento)

        return pagamentos, company_map, depara_map, grouped

    def _marcar_pagamentos_como_exportados(self, exportados_ids: List[int]) -> None:
        """
        Marca pagamentos como exportados.

        Args:
            exportados_ids: Lista de IDs de pagamentos
        """
        if not exportados_ids:
            return

        now = datetime.utcnow()
        PagamentoFiscal.query.filter(PagamentoFiscal.id.in_(exportados_ids)).update(
            {
                PagamentoFiscal.exportado: True,
                PagamentoFiscal.exported_at: now
            },
            synchronize_session=False
        )
        db.session.commit()

    def _mesano_data(self, data) -> str:
        """
        Formata data em MMYYYY.

        Args:
            data: Data

        Returns:
            String MMYYYY
        """
        if not data:
            raise ValueError('Pagamento sem data de arrecadação para definição de MESANO')
        return data.strftime('%m%Y')

    def _agrupar_pagamentos_por_mesano(self, company_pagamentos: List[PagamentoFiscal]):
        """
        Agrupa pagamentos de uma empresa por mês/ano.

        Args:
            company_pagamentos: Pagamentos da empresa

        Returns:
            Dict {mesano: [pagamentos]}
        """
        grouped = defaultdict(list)

        for pagamento in company_pagamentos:
            if not pagamento.data_arrecadacao:
                raise ValueError(
                    f"Pagamento {pagamento.numero_documento} sem data de arrecadação"
                )

            mesano = self._mesano_data(pagamento.data_arrecadacao)
            grouped[mesano].append(pagamento)

        return grouped

    def gerar_txts_zipados_filtrado(
        self,
        company_id: Optional[int] = None,
        company_ids: Optional[Iterable[int]] = None,
        pagamento_ids: Optional[Iterable[int]] = None,
        data_inicio: Optional[str] = None,
        data_fim: Optional[str] = None,
        codigo_receita: Optional[str] = None,
        apenas_nao_exportados: bool = False,
        nome_arquivo_txt: str = 'arquivo_dominio.txt'
    ) -> bytes:
        """
        Gera ZIP com TXTs filtrados, um por empresa/mês.

        Args:
            (filtros como gerar_txt_filtrado)
            nome_arquivo_txt: Nome do arquivo dentro de cada pasta

        Returns:
            Bytes do ZIP
        """
        _, company_map, depara_map, grouped = self._buscar_pagamentos_e_dependencias(
            company_id=company_id,
            company_ids=company_ids,
            pagamento_ids=pagamento_ids,
            data_inicio=data_inicio,
            data_fim=data_fim,
            codigo_receita=codigo_receita,
            apenas_nao_exportados=apenas_nao_exportados
        )

        exportados_ids = []
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for current_company_id, company_pagamentos in grouped.items():
                company = company_map.get(current_company_id)
                if not company:
                    raise ValueError(f'Empresa {current_company_id} não encontrada')

                # Agrupa por mês/ano
                pagamentos_por_mesano = self._agrupar_pagamentos_por_mesano(company_pagamentos)

                razao_social_segura = self._sanitize_path_part(company.razao_social)
                nome_txt_seguro = self._sanitize_path_part(nome_arquivo_txt)

                # Para cada mês
                for mesano, pagamentos_mes in pagamentos_por_mesano.items():
                    linhas, ids_empresa_mes = self._montar_linhas_empresa(
                        company=company,
                        company_pagamentos=pagamentos_mes,
                        depara_map=depara_map
                    )

                    # Caminho interno do ZIP: RAZAO/MESANO/arquivo.txt
                    caminho_interno = f"{razao_social_segura}/{mesano}/{nome_txt_seguro}"
                    conteudo_txt = '\n'.join(linhas) + '\n'

                    zf.writestr(caminho_interno, conteudo_txt.encode('utf-8'))

                    exportados_ids.extend(ids_empresa_mes)

        # Marca como exportados
        self._marcar_pagamentos_como_exportados(exportados_ids)

        # Retorna bytes do ZIP
        zip_buffer.seek(0)
        return zip_buffer.getvalue()

    def gerar_txts_zipados(self, company_id: int, pagamento_ids: Iterable[int]) -> bytes:
        """
        Gera ZIP com TXTs para empresa e pagamentos específicos.

        Args:
            company_id: ID da empresa
            pagamento_ids: IDs de pagamentos

        Returns:
            Bytes do ZIP
        """
        return self.gerar_txts_zipados_filtrado(
            company_id=company_id,
            pagamento_ids=list(pagamento_ids)
        )
