"""Tabela NCM → CST de PIS/COFINS do leitor de XML de NF-e (17o desvio).

O modelo do Integra Contador consulta um endpoint próprio (`getCST.php?ncm=`) que
devolve o CST do NCM e, quando não conhece o NCM, responde CST 1 (tributável). Aqui a
tabela vive no banco (`escritorio_ncm_cst`), editável na tela `/escritorio/ncm`.

⚠️ A CARGA INICIAL É PONTO DE PARTIDA, NÃO VERDADE FISCAL. Foi montada a partir das
listas legais mais comuns no varejo (medicamentos, perfumaria, bebidas frias,
combustíveis, pneus, veículos, cigarros e os principais itens de alíquota zero) e
PRECISA ser conferida pelo contador. Autopeças (anexos da Lei 10.485/2002) não foram
carregadas — cadastre os NCMs usados pelos clientes.

Regra de consulta: vence o prefixo mais longo cadastrado e ativo. Sem nenhum → CST 1.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List

from app.extensions import db

M = 'Monofásico'
Z = 'Alíquota zero'

# (prefixo NCM, CST, descrição, base legal)
CARGA_INICIAL = [
    # ---------------------------------------------------------------- monofásico (4)
    ('3001', 4, 'Glândulas e órgãos para usos opoterápicos', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3003', 4, 'Medicamentos (não em doses)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('30039056', 1, 'Exceção: 3003.90.56', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3004', 4, 'Medicamentos (em doses)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('30049046', 1, 'Exceção: 3004.90.46', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3002101', 4, 'Soros específicos (NCM antiga)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3002102', 4, 'Soros/antitoxinas (NCM antiga)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3002103', 4, 'Frações do sangue (NCM antiga)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3002201', 4, 'Vacinas humanas (NCM antiga)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3002202', 4, 'Vacinas humanas (NCM antiga)', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3006301', 4, 'Preparações opacificantes', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3006302', 4, 'Reagentes de diagnóstico', 'Lei 10.147/2000, art. 1º, I, a'),
    ('30066000', 4, 'Preparações anticoncepcionais', 'Lei 10.147/2000, art. 1º, I, a'),
    ('3303', 4, 'Perfumes e águas-de-colônia', 'Lei 10.147/2000, art. 1º, I, b'),
    ('3304', 4, 'Maquiagem e cuidados da pele', 'Lei 10.147/2000, art. 1º, I, b'),
    ('3305', 4, 'Preparações capilares', 'Lei 10.147/2000, art. 1º, I, b'),
    ('3307', 4, 'Barbear, desodorantes, banho', 'Lei 10.147/2000, art. 1º, I, b'),
    ('34011190', 4, 'Sabões de toucador', 'Lei 10.147/2000, art. 1º, I, b'),
    ('34012010', 4, 'Sabões de toucador (outras formas)', 'Lei 10.147/2000, art. 1º, I, b'),
    ('96032100', 4, 'Escovas de dentes', 'Lei 10.147/2000, art. 1º, I, b'),
    ('21069010', 4, 'Preparações para elaboração de bebidas (Ex 02)', 'Lei 13.097/2015, art. 14'),
    ('2201', 4, 'Águas minerais e gaseificadas', 'Lei 13.097/2015, art. 14'),
    ('2202', 4, 'Refrigerantes, isotônicos, energéticos', 'Lei 13.097/2015, art. 14'),
    ('2203', 4, 'Cervejas de malte', 'Lei 13.097/2015, art. 14'),
    ('4011', 4, 'Pneus novos de borracha', 'Lei 10.485/2002, art. 5º'),
    ('4013', 4, 'Câmaras de ar de borracha', 'Lei 10.485/2002, art. 5º'),
    ('8701', 4, 'Tratores', 'Lei 10.485/2002, art. 1º'),
    ('8702', 4, 'Veículos para transporte de 10+ pessoas', 'Lei 10.485/2002, art. 1º'),
    ('8703', 4, 'Automóveis', 'Lei 10.485/2002, art. 1º'),
    ('8704', 4, 'Veículos de carga', 'Lei 10.485/2002, art. 1º'),
    ('8705', 4, 'Veículos para usos especiais', 'Lei 10.485/2002, art. 1º'),
    ('8706', 4, 'Chassis com motor', 'Lei 10.485/2002, art. 1º'),
    ('8711', 4, 'Motocicletas', 'Lei 10.485/2002, art. 1º'),
    ('2710125', 4, 'Gasolinas', 'Lei 9.718/1998, art. 4º'),
    ('27101911', 4, 'Querosene de aviação', 'Lei 10.560/2002'),
    ('27101921', 4, 'Óleo diesel', 'Lei 9.718/1998, art. 4º'),
    ('271112', 4, 'Propano (GLP)', 'Lei 9.718/1998, art. 4º'),
    ('271113', 4, 'Butanos (GLP)', 'Lei 9.718/1998, art. 4º'),
    ('27111910', 4, 'Gás liquefeito de petróleo', 'Lei 9.718/1998, art. 4º'),
    ('220710', 4, 'Álcool etílico não desnaturado (combustível)', 'Lei 9.718/1998, art. 5º — revisar'),
    ('220720', 4, 'Álcool etílico desnaturado (combustível)', 'Lei 9.718/1998, art. 5º — revisar'),
    ('38260000', 4, 'Biodiesel', 'Lei 11.116/2005'),
    # ------------------------------------------------------ substituição tributária (5)
    ('24022000', 5, 'Cigarros', 'Lei 9.532/1997, art. 53 / Lei 11.196/2005'),
    # ---------------------------------------------------------------- alíquota zero (6)
    ('31', 6, 'Adubos e fertilizantes', 'Lei 10.925/2004, art. 1º, I'),
    ('0201', 6, 'Carnes bovinas frescas/refrigeradas', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0202', 6, 'Carnes bovinas congeladas', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0203', 6, 'Carnes suínas', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0204', 6, 'Carnes ovinas e caprinas', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0206', 6, 'Miudezas comestíveis', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0207', 6, 'Carnes de aves', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0209', 6, 'Toucinho e gorduras', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0210', 6, 'Carnes salgadas/defumadas', 'Lei 10.925/2004, art. 1º, XIX'),
    ('0302', 6, 'Peixes frescos ou refrigerados', 'Lei 10.925/2004, art. 1º, XX'),
    ('0303', 6, 'Peixes congelados', 'Lei 10.925/2004, art. 1º, XX'),
    ('0304', 6, 'Filés de peixe', 'Lei 10.925/2004, art. 1º, XX'),
    ('0401', 6, 'Leite fluido', 'Lei 10.925/2004, art. 1º, XI'),
    ('040210', 6, 'Leite em pó', 'Lei 10.925/2004, art. 1º, XII'),
    ('040221', 6, 'Leite em pó', 'Lei 10.925/2004, art. 1º, XII'),
    ('040229', 6, 'Leite em pó', 'Lei 10.925/2004, art. 1º, XII'),
    ('04051000', 6, 'Manteiga', 'Lei 10.925/2004, art. 1º, XXI'),
    ('04061010', 6, 'Queijo muçarela', 'Lei 10.925/2004, art. 1º, XVII'),
    ('04061090', 6, 'Queijos frescos', 'Lei 10.925/2004, art. 1º, XVII'),
    ('04062000', 6, 'Queijos ralados ou em pó', 'Lei 10.925/2004, art. 1º, XVII'),
    ('04069010', 6, 'Queijos de massa semidura', 'Lei 10.925/2004, art. 1º, XVII'),
    ('04069020', 6, 'Queijos de massa semidura', 'Lei 10.925/2004, art. 1º, XVII'),
    ('04069030', 6, 'Queijos de massa mole', 'Lei 10.925/2004, art. 1º, XVII'),
    ('0407', 6, 'Ovos', 'Lei 10.865/2004, art. 28, III'),
    ('07', 6, 'Hortícolas (in natura) — revisar preparados', 'Lei 10.865/2004, art. 28, III'),
    ('08', 6, 'Frutas (in natura) — revisar preparados', 'Lei 10.865/2004, art. 28, III'),
    ('0901', 6, 'Café', 'Lei 10.925/2004, art. 1º, XXII'),
    ('21011', 6, 'Extratos de café', 'Lei 10.925/2004, art. 1º, XXII'),
    ('1001', 6, 'Trigo', 'Lei 10.925/2004, art. 1º, XIV'),
    ('1101', 6, 'Farinha de trigo', 'Lei 10.925/2004, art. 1º, XIV'),
    ('10062', 6, 'Arroz descascado', 'Lei 10.925/2004, art. 1º, XVI'),
    ('10063', 6, 'Arroz semibranqueado/branqueado', 'Lei 10.925/2004, art. 1º, XVI'),
    ('07133319', 6, 'Feijão comum (preto)', 'Lei 10.925/2004, art. 1º, XVI'),
    ('07133329', 6, 'Feijão comum (branco)', 'Lei 10.925/2004, art. 1º, XVI'),
    ('07133399', 6, 'Feijão comum (outros)', 'Lei 10.925/2004, art. 1º, XVI'),
    ('07133590', 6, 'Feijão-fradinho', 'Lei 10.925/2004, art. 1º, XVI'),
    ('11062000', 6, 'Farinha de mandioca', 'Lei 10.925/2004, art. 1º, XVI'),
    ('19021', 6, 'Massas alimentícias não cozidas', 'Lei 10.925/2004, art. 1º, XV'),
    ('17011400', 6, 'Açúcar de cana', 'Lei 10.925/2004, art. 1º, XVIII'),
    ('17019900', 6, 'Açúcar refinado', 'Lei 10.925/2004, art. 1º, XVIII'),
    ('1507', 6, 'Óleo de soja', 'Lei 10.925/2004, art. 1º, XVIII'),
    ('15171000', 6, 'Margarina', 'Lei 10.925/2004, art. 1º, XXI'),
    ('33061000', 6, 'Dentifrícios', 'Lei 10.925/2004, art. 1º, XXIII'),
    ('48181000', 6, 'Papel higiênico', 'Lei 10.925/2004, art. 1º, XXIV'),
    ('4901', 6, 'Livros', 'Lei 10.865/2004, art. 28, VI'),
]


def so_digitos(valor) -> str:
    return re.sub(r'\D', '', str(valor or ''))


def garantir_carga_inicial() -> int:
    """Carrega a tabela padrão só quando ela está vazia. Devolve quantas linhas entraram."""
    from app.escritorio_models import EscritorioNcmCst
    if EscritorioNcmCst.query.first() is not None:
        return 0
    for ncm, cst, descricao, base in CARGA_INICIAL:
        db.session.add(EscritorioNcmCst(ncm=ncm, cst=cst, descricao=descricao,
                                        base_legal=base, ativo=True,
                                        atualizado_por='carga inicial'))
    db.session.commit()
    return len(CARGA_INICIAL)


def mapa_ativo() -> Dict[str, int]:
    from app.escritorio_models import EscritorioNcmCst
    return {r.ncm: r.cst for r in EscritorioNcmCst.query.filter_by(ativo=True).all()}


def cst_do_ncm(ncm: str, mapa: Dict[str, int] | None = None) -> int:
    """CST do NCM pelo prefixo mais longo; sem regra → 1 (tributável), como no modelo."""
    digitos = so_digitos(ncm)[:8]
    if not digitos:
        return 1
    mapa = mapa if mapa is not None else mapa_ativo()
    for tamanho in range(len(digitos), 1, -1):
        cst = mapa.get(digitos[:tamanho])
        if cst is not None:
            return int(cst)
    return 1


def cst_em_lote(ncms: Iterable[str]) -> Dict[str, int]:
    mapa = mapa_ativo()
    return {so_digitos(n): cst_do_ncm(n, mapa) for n in ncms if so_digitos(n)}


def listar() -> List[dict]:
    from app.escritorio_models import EscritorioNcmCst
    linhas = EscritorioNcmCst.query.order_by(EscritorioNcmCst.ncm).all()
    return [{'id': r.id, 'ncm': r.ncm, 'cst': r.cst, 'descricao': r.descricao or '',
             'base_legal': r.base_legal or '', 'ativo': bool(r.ativo),
             'atualizado_por': r.atualizado_por or '',
             'atualizado_em': r.atualizado_em.strftime('%d/%m/%Y %H:%M') if r.atualizado_em else ''}
            for r in linhas]
