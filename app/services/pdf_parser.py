import PyPDF2
import re
import io
import logging

logger = logging.getLogger(__name__)


class PDFParser:

    def parse_pdf_content(self, pdf_content):
        pdf_file = None
        try:
            if isinstance(pdf_content, str):
                with open(pdf_content, 'rb') as file:
                    pdf_file = io.BytesIO(file.read())
            else:
                pdf_file = io.BytesIO(pdf_content)

            reader = PyPDF2.PdfReader(pdf_file)
            paginas = [(page.extract_text() or '') for page in reader.pages]
            text = ''.join(self._remover_cabecalho_repetido(paginas))
            text = text.replace('*', '')
            return self._parse_text_to_json(text)
        except Exception as e:
            logger.error('Error parsing PDF: %s', e)
            raise
        finally:
            if pdf_file:
                pdf_file.close()

    @staticmethod
    def _remover_cabecalho_repetido(paginas):
        """Tira o cabeçalho que se repete a partir da 2a página.

        ⚠️ DESVIO INTENCIONAL (16o) — o exe NÃO faz isto, e por isso lia errado.

        Toda página do relatório começa com o mesmo bloco:

            MINISTÉRIO DA ECONOMIA ...
            SECRETARIA ... Autor pedido: 35.736.034/0001-23. Contratante: 35.736.034/0001-23
            PROCURADORIA-GERAL ... 30/07/2026 23:32:13
            INFORMAÇÕES DE APOIO PARA EMISSÃO DE CERTIDÃO
            CNPJ: 08.108.132 - FULANO

        Quando uma seção de omissão atravessa a quebra de página, esse bloco cai no
        MEIO do texto capturado. Como o extrator procura qualquer numero de 4 digitos, ele pegava
        o **0001** do CNPJ (três vezes: autor, contratante e a linha do CNPJ) e o
        **2026** da data — virando pendências fantasma do tipo "DCTFWeb 0001 —".

        A 1a página é preservada inteira: é dela que saem CNPJ, razão social,
        endereço e data/hora do relatório.
        """
        if len(paginas) < 2:
            return paginas

        linhas = [pagina.split(chr(10)) for pagina in paginas]
        comuns = 0
        while True:
            if any(len(bloco) <= comuns for bloco in linhas):
                break
            referencia = linhas[0][comuns].strip()
            if not referencia:
                break
            if any(bloco[comuns].strip() != referencia for bloco in linhas[1:]):
                break
            comuns += 1

        if comuns == 0:
            return paginas
        return [paginas[0]] + [chr(10).join(bloco[comuns:]) for bloco in linhas[1:]]

    def _parse_text_to_json(self, text):
        data = {}
        datahora = re.search(r'\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}', text)
        if datahora:
            data['datahora'] = datahora.group()

        cnpj_match = re.search(r'CNPJ:\s([\d./-]+)\s-\s(.+)', text)
        if cnpj_match:
            data['CNPJ'] = cnpj_match.group(1).replace('/', '').replace('-', '').replace('.', '')
            data['nome'] = cnpj_match.group(2).strip()

        cnpjcomp_match = re.search(r'CNPJ\s*:\s*(\d{2}\.\d{3}\.\d{3}\/\d{4}-\d{2})', text)
        if cnpjcomp_match:
            data['cnpjCompleto'] = cnpjcomp_match.group(1).replace('/', '').replace('-', '').replace('.', '')

        situacao_match = re.search(r'Situação:\s(.+?)(?:\s|$)', text)
        if situacao_match:
            data['situacao'] = situacao_match.group(1).strip()

        natureza_match = re.search(
            r'Natureza Jurídica:\s*(\d{3}-\d)\s*-\s*([^:\n]*?(?=Data de Abertura:|$))', text)
        if natureza_match:
            data['NaturezaJuridica'] = {
                'codigo': natureza_match.group(1),
                'natureza': natureza_match.group(2).strip(),
            }

        simples_pattern = (r'Opção pelo Simples Nacional\s*Inclusão\s+Exclusão\s*'
                           r'((?:\d{2}/\d{2}/\d{4}(?:\s+\d{2}/\d{2}/\d{4})?\s*)+)')
        simples_match = re.search(simples_pattern, text)
        if simples_match:
            date_lines = re.findall(r'(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}/\d{2}/\d{4}))?',
                                    simples_match.group(1))
            latest_dates = date_lines[-1]
            data['simples_nacional'] = {
                'inclusao': latest_dates[0],
                'exclusao': latest_dates[1] if latest_dates[1] else None,
            }

        simei_pattern = (r'Opção pelo SIMEI\s*Inclusão\s+Exclusão\s*'
                         r'((?:\d{2}/\d{2}/\d{4}(?:\s+\d{2}/\d{2}/\d{4})?\s*)+)')
        simei_match = re.search(simei_pattern, text)
        if simei_match:
            date_lines = re.findall(r'(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}/\d{2}/\d{4}))?',
                                    simei_match.group(1))
            latest_dates = date_lines[-1]
            data['SIMEI'] = {
                'inclusao': latest_dates[0],
                'exclusao': latest_dates[1] if latest_dates[1] else None,
            }

        endereco_match = re.search(r'Endereço:\s(.+?)Bairro:', text, re.DOTALL)
        if endereco_match:
            data['endereco'] = endereco_match.group(1).strip()

        responsavel_match = re.search(r'Responsável:\s([\d.-]+)\s-\s(.+)', text)
        if responsavel_match:
            data['responsavel'] = {
                'CPF': responsavel_match.group(1),
                'nome': responsavel_match.group(2).strip(),
            }

        data['pendencias'] = self._extract_pendencias(text)
        data.update(self._extract_debitos(text))
        data['parcelamentos_pgfn'] = self._extract_parcelamentos_pgfn(text)
        return data

    def _extract_parcelamentos_pgfn(self, text):
        """DESVIO INTENCIONAL do exe (pedido do Jean, 31/07/2026).

        O exe descarta as seções de parcelamento e de PGFN do PDF: o
        `_extract_pendencias()` original só reconhece "Omissão de ...". Resultado: uma
        empresa como a SANDRA ISIDIO, que tem parcelamento ativo com parcela em atraso,
        aparecia com "0 pendências" no painel.

        Aqui extraímos as três informações que o Jean pediu:
          1. que existe parcelamento ativo;
          2. se há menção de parcelas em aberto/atraso;
          3. o valor envolvido.

        A API da SERPRO não cobre a PGFN, então esses dados só existem no texto do PDF.
        """
        itens = []

        # 1) "SIMPLES NACIONAL - EM PARCELAMENTO" + "Parcelas em atraso  N"
        for m in re.finditer(
                r'([A-ZÀ-Ú][A-ZÀ-Ú \-\.]*?)\s*-\s*EM PARCELAMENTO'
                r'(?:\s*Parcelas em atraso\s*(\d+))?', text):
            item = {
                'origem': 'RFB',
                'tipo': 'PARCELAMENTO_ATIVO',
                'descricao': f'{m.group(1).strip()} - EM PARCELAMENTO',
                'parcelas_em_atraso': int(m.group(2)) if m.group(2) else None,
                'valor': None,
                'numero': None,
                'situacao': None,
            }
            itens.append(item)

        # 2) "Parcelamento: <numero>  Valor Suspenso: <valor>"
        for m in re.finditer(
                r'Parcelamento:\s*([\d./\-]+)\s*Valor Suspenso:\s*([\d.,]+)', text):
            itens.append({
                'origem': 'RFB',
                'tipo': 'PARCELAMENTO_VALOR',
                'descricao': f'Parcelamento {m.group(1)}',
                'parcelas_em_atraso': None,
                'valor': self._valor_para_float(m.group(2)),
                'numero': m.group(1),
                'situacao': None,
            })

        # 3) PGFN — "Inscrição: <n>  Situação: <codigo> - <texto>"
        for m in re.finditer(
                r'Inscrição:\s*([\d.\-]+)\s*Situação:\s*(\d+)\s*-\s*([A-ZÀ-Ú][^\n]{0,60})',
                text):
            itens.append({
                'origem': 'PGFN',
                'tipo': 'INSCRICAO_PGFN',
                'descricao': f'Inscrição {m.group(1)}',
                'parcelas_em_atraso': None,
                'valor': None,
                'numero': m.group(1),
                'situacao': f'{m.group(2)} - {m.group(3).strip()}',
            })

        return itens

    @staticmethod
    def _valor_para_float(valor):
        try:
            return float(str(valor).replace('.', '').replace(',', '.'))
        except (ValueError, AttributeError):
            return None

    def _extract_pendencias(self, text):
        pendencias = {}

        def extract_pendencia_section(section_name):
            pattern = f'{section_name}' + r'\s.*?\(.*?\)(.+?)(?:Omissão|Pendência|$)'
            match = re.search(pattern, text, re.DOTALL)
            if match:
                raw_data = match.group(1).replace('\n', ' ')

                # DESVIO INTENCIONAL (16o) — o exe usa
                #     (\d{4}(?: - (?:[A-Z]{3}\s*)+)?|\b[A-Z]{3}\b)
                # que aceita QUALQUER número de 4 dígitos como ano. Com isso o "0001"
                # de um CNPJ e o "2026" de uma data viravam pendência fantasma. Aqui:
                #   * o ano não pode estar colado em / . ou - (recorta CNPJ e data);
                #   * tem de ser plausível (19xx/20xx).
                # A remoção do cabeçalho repetido já evita a maior parte; esta é a
                # segunda barreira, para número solto no meio da seção.
                entries = re.findall(
                    r'(?<![\d./-])((?:19|20)\d{2})(?![\d./-])'
                    r'(?:\s*-\s*((?:[A-Z]{3}[\s,]*)+))?',
                    raw_data,
                )

                # O mesmo ano pode aparecer repetido (seção quebrada em duas páginas):
                # junta os meses e não devolve linha duplicada.
                juntos = {}
                for ano, meses in entries:
                    alvo = juntos.setdefault(ano, [])
                    for mes in re.findall(r'[A-Z]{3}', meses or ''):
                        if mes not in alvo:
                            alvo.append(mes)
                return [{'ano': ano, 'meses': meses} for ano, meses in juntos.items()]
            return []

        # DESVIO INTENCIONAL (16o) — o exe procura 'Omissão de DASSNSIMEI', mas o título
        # no PDF é 'Omissão de DASN SIMEI' (com espaço): essa omissão NUNCA era
        # capturada. A chave guardada continua 'DASSNSIMEI', para não mudar o formato do
        # dado que já está no banco.
        for section, titulo in (('DEFIS', 'DEFIS'), ('PGDAS-D', 'PGDAS-D'),
                                ('DCTF', 'DCTF'), ('DCTFWeb', 'DCTFWeb'),
                                ('GFIP', 'GFIP'), ('DASSNSIMEI', 'DASN SIMEI'),
                                ('ECF', 'ECF'), ('EFD-CONTRIB', 'EFD-CONTRIB')):
            result = extract_pendencia_section(f'Omissão de {titulo}')
            if result:
                pendencias[section] = result

        return pendencias

    def _extract_debitos(self, text):
        debitos = {}

        _VALORES = (r'\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)'
                    r'\s+(DEVEDOR|A ANALISAR-A VENCER)')
        _DATA_LONGA = r'\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})'
        _COMPETENCIA = r'\s+(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})'

        patterns = {
            'debitosIRRF': r'(0561-07 - IRRF.*?)' + _DATA_LONGA + _VALORES,
            'debitosMAED': r'(5440-01 - MAED - DCTFWEB.*?)' + _DATA_LONGA + _VALORES,
            'debitosINSS': r'(1099-01 - CP-SEGUR.*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1082': r'(1082-01 - CP-SEGUR.*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1138': r'(1138-01 - CP-PATRONAL*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1646': r'(1646-01 - CP-PATRONAL*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1200': r'(1200-01 - CP-TERCEIROS*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1170': r'(1170-01 - CP-TERCEIROS*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1176': r'(1176-01 - CP-TERCEIROS*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS1225': r'(1225-01 - CP-TERCEIROS*?)' + _COMPETENCIA + _VALORES,
            'debitosINSS122521': r'(1225-21 - CP-TERCEIROS*?)' + _COMPETENCIA + _VALORES,
            'debitosSN': r'(SIMPLES NAC\.\s*\n\s*MEI|SIMPLES NAC\.)\s*'
                         r'(\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})' + _VALORES,
            'debitosMEI': r'(MEI)' + _COMPETENCIA + _VALORES,
        }

        for key, pattern in patterns.items():
            items = [self._create_debito_dict(m) for m in re.finditer(pattern, text)]
            if items:
                debitos[key] = items

        return debitos

    def _create_debito_dict(self, match):
        return {
            'receita': match.group(1).strip(),
            'periodo_apuracao': match.group(2),
            'data_vencimento': match.group(3),
            'valor_original': float(match.group(4).replace('.', '').replace(',', '.')),
            'saldo_devedor': float(match.group(5).replace('.', '').replace(',', '.')),
            'multa': float(match.group(6).replace('.', '').replace(',', '.')),
            'juros': float(match.group(7).replace('.', '').replace(',', '.')),
            'saldo_devedor_total': float(match.group(8).replace('.', '').replace(',', '.')),
            'situacao': match.group(9),
        }
