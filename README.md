# MBV CarbonOS · Primato

**Perfil Regenerativo Digital** da Primato Cooperativa Agroindustrial —
uma visão por associado que reúne propriedade, produtividade, custo e
sustentabilidade, alimentada por módulos independentes e **complementar** aos
sistemas que a cooperativa já usa.

Construído pelo **Movimento Brasil Verde (MBV)** sobre o núcleo do MBV CarbonOS.

---

## O que já funciona (Fase 1)

### Módulo 1 — Propriedades rurais
- Cadastro do imóvel: matrícula, CAR/SICAR, CCIR, ITR, área total, Reserva
  Legal e APP, com conferência de que RL + APP não passam da área total.
- **Talhões com polígono próprio** — é o talhão que carrega cultura, manejo e,
  na Fase 2, a emissão. Granularidade que o inventário corporativo hoje não tem.
- Polígono por KML ou shapefile `.zip` (o arquivo que a consulta pública do
  SICAR deixa baixar), com divergência de área declarada, não escondida.
- **Cruzamento com bases públicas**: SIGEF (INCRA), terras indígenas (FUNAI),
  unidades de conservação (CNUC), processos minerários (ANM) e embargos
  (ICMBio). Base que não respondeu aparece como *não consultada* — nunca como
  "sem ocorrência".
- Documentos digitalizados com **alerta de vencimento**, com janela por tipo:
  licença ambiental avisa com 120 dias, outorga de água com 90, certidão com 30
  — porque renovar no órgão estadual leva meses, e um aviso de 30 dias chegaria
  quando já não dá tempo.
- Vínculo associado–propriedade, inclusive arrendada, em parceria ou sociedade.

### Módulo 2 — Cultura, lavoura e produtividade
- Safra por **talhão × cultura × ciclo** (principal, segunda, inverno).
- Produtividade em sacas/ha e kg/ha, **corrigida pela umidade** para a base de
  comercialização da cultura antes de qualquer comparação.
- Custos por insumo em 9 categorias (sementes, fertilizantes, **corretivos**,
  defensivos, **biológicos/CoinMax-TriMix**, mão de obra, maquinário,
  arrendamento, outros), com custo/ha, custo/saca e preço de equilíbrio.
- Receita **esperada × realizada**, margem por hectare e em percentual.
- Comparação com a média regional cadastrada e com a safra anterior do mesmo
  talhão e cultura.
- **Área controle × área regenerativa**: comparação ponderada por área, lida
  por margem — o protocolo pode entregar menos saca gastando bem menos.
- Série histórica por talhão, com tendência.

### Acesso
Quatro perfis, como a especificação define: **associado**, **técnico de campo**,
**gestor Primato** e **auditor**. O controle tem dois eixos: o que a pessoa pode
fazer e **sobre quem** — o associado só alcança o próprio cadastro. O técnico de
campo não vê o módulo financeiro/tributário; o auditor lê tudo e não altera dado
do associado.

---

## Próximas fases (ordem da especificação)

| Fase | Escopo |
|---|---|
| 2 — ESG | Módulo 4: emissões agrícolas por talhão (fertilizante nitrogenado, calcário, resíduos de plantio, mudança de uso do solo), remoções biogênicas, MRV com evidências, ativos CBE/CRVE e relatórios com hash SHA-256, no formato do inventário corporativo. |
| 3 — Fiscal | Módulo 3: regime e CNAE, faturamento, Anexo IX, Ato Cooperativo, IBS/CBS e custo tributário por hectare, com a Falavinha Next. |
| 4 — Consolidação | Módulo 5: visão 360°, linha do tempo por safra, benchmarking interno, Índice de Fidelização (Bronze/Prata/Ouro/Diamante) e dashboard corporativo. |

O Módulo 4 já tem o caminho aberto na Fase 1: a **quantidade** de cada insumo é
capturada junto com o custo, e `corretivos` é categoria própria justamente para
que a tonelada de calcário seja recuperável depois.

---

## Rodar

```bash
cp .env.example .env          # preencher com o Supabase da Primato
pip install -r requirements.txt
uvicorn app.api.main:app --port 8000 --reload
```

Painel em `http://localhost:8000`, API em `/docs`.

Antes do primeiro uso, rode `scripts/sql_primato.sql` no SQL Editor do Supabase
(idempotente) e cadastre o primeiro gestor em `membros_primato`:

```sql
insert into membros_primato (usuario_id, email, papel)
values ('<uuid da conta>', '<e-mail>', 'gestor');
```

### Testes

```bash
python -m pytest tests/ -q     # 43 testes
```

Não é cobertura ampla — é rede de regressão sobre os pontos em que um erro muda
um número que vai à mesa do associado ou ao dashboard da Diretoria.

---

## Arquitetura

SPA + FastAPI + Supabase (Postgres, Auth e Storage privado), na Vercel com
`"regions": ["gru1"]` — obrigatório: o WFS do SIGEF não responde a requisições
vindas dos Estados Unidos.

A regra mora em **motores puros**, sem banco e sem rede: `motor_documentos`,
`motor_produtividade`, `catalogo_documentos` e a política de `acesso`. É o que
os torna testáveis sem esperar o calendário nem subir infraestrutura.

Relatórios auditáveis usam SHA-256 sobre conteúdo serializado com chaves
ordenadas (`auditoria.hash_conteudo`) — sem isso, dois relatórios idênticos
teriam hashes diferentes e o hash não provaria nada.
