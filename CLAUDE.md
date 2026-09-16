# Módulo Primato — guia para o Claude Code

Perfil Regenerativo Digital da **Primato Cooperativa Agroindustrial**.
Repositório Git PRÓPRIO (`github.com/DNS200979/primato`), separado do módulo
Cooperativas/CONFAF e do ERP CarbonFree. A pasta mora dentro de `Coperativas/`
por conveniência de trabalho, mas está no `.gitignore` de lá — os dois
repositórios nunca se misturam.

Base: `Docs/Software_MBV_Primato_Especificacao_Funcional.pdf` (Capital
Regenerativo, set/2026) e `Docs/Relatório Primato 2025.pdf` (inventário
corporativo de GEE, ano-base 2025, apoio Biofílica Ambipar).

## Estado

**Fase 1 entregue** — Módulos 1 e 2 da especificação. Fases seguintes na ordem
que a própria especificação define: 2 = ESG (Módulo 4), 3 = fiscal (Módulo 3),
4 = Perfil Regenerativo + Índice de Fidelização + dashboard (Módulo 5).

## Mapa

- `app/api/main.py` — entrypoint (`api/index.py` para a Vercel).
- Motores PUROS (sem banco, sem rede — é onde mora a regra):
  - `services/motor_documentos.py` — vencimento e regularidade da propriedade.
  - `services/motor_produtividade.py` — produtividade, custo, receita, margem,
    comparações e série histórica.
  - `services/catalogo_documentos.py` — catálogo documental (RASCUNHO).
  - `services/acesso.py` — os quatro perfis (política pura + resolução no banco).
- Rotas: `associados.py`, `propriedades.py`, `safras.py`, `catalogos.py`.
- Herdado do CarbonOS sem mudança de regra: `util_br`, `consulta_ponto`,
  `geometria_rural`, `cruzamentos_geo`, `car_recibo`, `app/dados/*.gz`.
- Frontend: `painel.html` (SPA, Supabase Auth, sessão `primato_sessao`).
- Banco: `scripts/sql_primato.sql` (idempotente, com RLS).

## Regras do domínio

- **Umidade antes de comparar.** Toda produtividade é corrigida para a umidade
  padrão de comercialização da cultura. Comparar peso úmido com peso úmido
  inventa ganho de até 7%. Sem umidade informada, o número passa como veio e o
  resultado sai marcado `umidade_informada: False`.
- **Vencer ≠ desatualizar.** Outorga vencida é irregularidade; CCIR do exercício
  anterior é pendência de emissão. Misturar os dois ensina a ignorar alerta.
- **Ausência de dado nunca vira "em dia".** Documento que vence sem data
  cadastrada fica `sem_data`; o envio exige a validade justamente por isso.
- **Condicional só é cobrado quando a condição existe.** Outorga só é exigida de
  quem tem captação; pendência falsa derruba o índice de quem não deve nada.
- **Controle × regenerativo se lê por MARGEM**, não só por produtividade: o
  protocolo pode entregar menos saca gastando bem menos.
- **Quantidade de insumo é dado do inventário.** `corretivos` é categoria
  separada de `fertilizantes` porque a tonelada de calcário alimenta o Módulo 4.
- **Escopo é o segundo eixo do acesso.** Perfil `associado` só alcança o próprio
  cadastro (`acesso.exigir_acesso_ao_associado` em toda rota que recebe um id).
- **Técnico de campo não vê o módulo fiscal**; **auditor lê tudo e não altera
  dado do associado** (mas registra os próprios achados).
- Base pública que não respondeu vira `consultado: False` — nunca "sem ocorrência".

## Validação (rodar sempre)

- `../../.venv/bin/python -m pytest tests/ -q` (o venv do ERP tem as deps).
- `../../.venv/bin/python -m py_compile <arquivo.py>`
- JS inline: extrair os `<script>` sem `src` e `node --check`.
- **Ordem de rotas**: caminho literal de UM segmento (`/referencias`) tem que
  ser declarado ANTES de `/{id}`, senão o FastAPI tenta converter o literal em
  inteiro e devolve 422. Já aconteceu aqui.

## Convenções

pt-BR em código e UX; nunca remover campo de resposta REST; trilha de auditoria
nunca bloqueia a ação primária; ao corrigir bug de regra, acrescentar o teste
dizendo qual defeito ele impede de voltar. Arquivos servidos por rota explícita
— nunca `StaticFiles` no diretório do projeto.

## Pendências

- Infra própria: projeto Supabase e projeto Vercel da Primato (envolve custo —
  decisão do cliente). Sem eles, o sistema roda local com um `.env`.
- Catálogo documental e culturas são RASCUNHO: validar prazos, obrigatoriedade
  e peso da saca com a Diretoria de Insumos Agrícolas.
- Índice de Fidelização: metodologia e peso de cada componente a definir com a
  Diretoria (item 11 da especificação). Nada foi implementado por isso.
- Leitura documental por IA ainda não ligada aqui (o núcleo existe no CONFAF).
- Integração CAR/SICAR "sem duplicidade de cadastro" (item 9) depende de saber
  quais sistemas a Primato já usa — item 11 da especificação.
