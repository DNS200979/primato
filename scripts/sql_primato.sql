-- =============================================================================
-- Módulo Primato — schema da Fase 1 (Módulos 1 e 2 da Especificação Funcional)
--
-- Rodar no SQL Editor do Supabase do projeto DEDICADO da Primato.
-- Idempotente: pode rodar de novo sem quebrar.
--
-- Escrita: o backend FastAPI usa a service key e valida o JWT ANTES de tocar
-- o banco (app/api/auth.py + app/services/acesso.py). As políticas de RLS
-- abaixo protegem o acesso direto pela anon key, que é o que um navegador
-- conseguiria alcançar.
-- =============================================================================

-- ── Equipe e perfis ──────────────────────────────────────────────────────────
-- Os quatro perfis do item 9 da especificação. `associado_id` só é preenchido
-- para o perfil 'associado' — é o vínculo que limita o escopo dele aos
-- próprios dados (app/services/acesso.py).
create table if not exists membros_primato (
  id            bigserial primary key,
  usuario_id    uuid not null,
  nome          text,
  email         text,
  papel         text not null check (papel in ('gestor','tecnico','auditor','associado')),
  associado_id  bigint,
  criado_em     timestamptz not null default now(),
  unique (usuario_id)
);

-- ── Associados ───────────────────────────────────────────────────────────────
create table if not exists associados (
  id                 bigserial primary key,
  nome               text not null,
  tipo_pessoa        text not null default 'fisica' check (tipo_pessoa in ('fisica','juridica')),
  cpf_cnpj           text,
  matricula_primato  text,                -- número do associado na cooperativa
  data_associacao    date,
  telefone           text,
  email              text,
  municipio          text,
  uf                 text,
  regiao             text,                -- cluster/unidade de atendimento
  situacao           text not null default 'ativo' check (situacao in ('ativo','inativo','suspenso')),
  observacao         text,
  criado_em          timestamptz not null default now(),
  atualizado_em      timestamptz not null default now()
);
create index if not exists idx_associados_nome on associados (nome);
create index if not exists idx_associados_doc  on associados (cpf_cnpj);

alter table membros_primato
  drop constraint if exists membros_primato_associado_fk;
alter table membros_primato
  add constraint membros_primato_associado_fk
  foreign key (associado_id) references associados(id) on delete set null;

-- ── Módulo 1 · Propriedades ──────────────────────────────────────────────────
create table if not exists propriedades (
  id                     bigserial primary key,
  associado_id           bigint not null references associados(id) on delete cascade,
  nome                   text not null,
  matricula              text,
  cartorio               text,
  car                    text,            -- número do CAR/SICAR
  ccir                   text,
  nirf                   text,
  municipio              text,
  uf                     text,
  latitude               double precision,
  longitude              double precision,
  bioma                  text,
  area_total_ha          numeric(12,4),
  area_reserva_legal_ha  numeric(12,4),
  area_app_ha            numeric(12,4),
  area_produtiva_ha      numeric(12,4),
  -- Posse: arrendada e parceria mudam quem colhe o resultado E quem pode
  -- reivindicar o crédito de carbono da área.
  posse                  text not null default 'propria'
                         check (posse in ('propria','arrendada','parceria','sociedade','comodato')),
  -- Contexto que torna exigível um documento condicional (motor_documentos).
  possui_captacao_agua   boolean not null default false,
  exige_licenciamento    boolean not null default false,
  geometria_geojson      jsonb,
  geometria_area_ha      numeric(12,4),
  geometria_origem       text,            -- sicar_kml | shapefile | desenho | car_recibo
  cruzamentos            jsonb,           -- última consulta às bases públicas
  cruzamentos_em         timestamptz,
  criado_em              timestamptz not null default now(),
  atualizado_em          timestamptz not null default now()
);
create index if not exists idx_propriedades_associado on propriedades (associado_id);
create index if not exists idx_propriedades_car on propriedades (car);

-- Talhões: a especificação pede polígono da propriedade E DE CADA TALHÃO.
-- É o talhão que carrega cultura, manejo e emissão — a granularidade que o
-- inventário corporativo hoje não tem.
create table if not exists talhoes (
  id                bigserial primary key,
  propriedade_id    bigint not null references propriedades(id) on delete cascade,
  nome              text not null,
  area_ha           numeric(12,4),
  geometria_geojson jsonb,
  geometria_area_ha numeric(12,4),
  manejo            text not null default 'convencional'
                    check (manejo in ('convencional','regenerativo','transicao')),
  -- Marca o par do piloto: qual talhão é o controle desta área regenerativa.
  talhao_controle_id bigint references talhoes(id) on delete set null,
  tipo_solo         text,
  declividade_pct   numeric(6,2),
  observacao        text,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now()
);
create index if not exists idx_talhoes_propriedade on talhoes (propriedade_id);

-- Documentos. Vínculo polimórfico: associado, propriedade ou talhão.
create table if not exists documentos_primato (
  id             bigserial primary key,
  associado_id   bigint references associados(id) on delete cascade,
  propriedade_id bigint references propriedades(id) on delete cascade,
  talhao_id      bigint references talhoes(id) on delete set null,
  safra_id       bigint,
  tipo           text not null,           -- chave de catalogo_documentos.DOCUMENTOS
  nome_arquivo   text,
  caminho        text,                    -- chave no bucket privado
  mime           text,
  tamanho        bigint,
  hash_sha256    text,
  -- Campos lidos do documento (a IA transcreve; as regras decidem)
  numero         text,
  orgao          text,
  emissao        date,
  validade       date,
  exercicio      integer,
  extracao       jsonb,
  origem_leitura text,                    -- ia | heuristica | manual
  enviado_por    uuid,
  criado_em      timestamptz not null default now(),
  removido_em    timestamptz
);
create index if not exists idx_doc_propriedade on documentos_primato (propriedade_id);
create index if not exists idx_doc_validade on documentos_primato (validade)
  where removido_em is null;

-- ── Módulo 2 · Safras ────────────────────────────────────────────────────────
-- Uma linha por talhão × safra. `area_ha` é redundante com talhoes.area_ha de
-- propósito: talhão pode ser plantado parcialmente, e a produtividade tem que
-- ser dividida pela área EFETIVAMENTE plantada.
create table if not exists safras (
  id                bigserial primary key,
  talhao_id         bigint not null references talhoes(id) on delete cascade,
  propriedade_id    bigint not null references propriedades(id) on delete cascade,
  associado_id      bigint not null references associados(id) on delete cascade,
  safra             text not null,        -- "2026/2027"
  ciclo             text default 'principal' check (ciclo in ('principal','segunda','terceira','inverno')),
  cultura           text not null,
  variedade         text,
  area_ha           numeric(12,4),
  manejo            text not null default 'convencional'
                    check (manejo in ('convencional','regenerativo','transicao')),
  data_plantio      date,
  data_colheita     date,
  -- Planejado (entrada da safra)
  produtividade_esperada_sacas_ha numeric(10,2),
  preco_esperado_saca             numeric(12,2),
  -- Realizado (colheita)
  producao_kg       numeric(14,2),
  umidade_pct       numeric(5,2),
  preco_realizado_saca            numeric(12,2),
  situacao          text not null default 'planejada'
                    check (situacao in ('planejada','em_curso','colhida','cancelada')),
  observacao        text,
  criado_em         timestamptz not null default now(),
  atualizado_em     timestamptz not null default now(),
  unique (talhao_id, safra, ciclo, cultura)
);
create index if not exists idx_safras_associado on safras (associado_id, safra);
create index if not exists idx_safras_propriedade on safras (propriedade_id);

alter table documentos_primato
  drop constraint if exists documentos_primato_safra_fk;
alter table documentos_primato
  add constraint documentos_primato_safra_fk
  foreign key (safra_id) references safras(id) on delete set null;

-- Custos e insumos da safra. `quantidade`+`unidade` não são enfeite: é daí
-- que sai o kg de N e a tonelada de calcário do inventário do Módulo 4.
create table if not exists lancamentos_safra (
  id             bigserial primary key,
  safra_id       bigint not null references safras(id) on delete cascade,
  categoria      text not null,
  descricao      text,
  produto        text,
  quantidade     numeric(14,4),
  unidade        text,                    -- kg | t | L | sc | h | un
  valor          numeric(14,2),
  fornecedor     text,
  da_cooperativa boolean not null default false,   -- comprado na rede Primato
  linha_primato  text,                    -- CoinMax | TriMix | outra
  data           date,
  nota_fiscal    text,
  documento_id   bigint references documentos_primato(id) on delete set null,
  criado_em      timestamptz not null default now()
);
create index if not exists idx_lancamentos_safra on lancamentos_safra (safra_id);

-- Médias de referência (regional e da cooperativa) para comparação.
create table if not exists referencias_produtividade (
  id          bigserial primary key,
  cultura     text not null,
  safra       text not null,
  uf          text,
  regiao      text,
  sacas_ha    numeric(10,2) not null,
  fonte       text,                       -- CONAB | DERAL | Primato | IBGE
  criado_em   timestamptz not null default now(),
  unique (cultura, safra, uf, regiao)
);

-- ── Trilha de auditoria ──────────────────────────────────────────────────────
create table if not exists eventos_primato (
  id            bigserial primary key,
  usuario_id    uuid,
  usuario_email text,
  entidade      text not null,
  entidade_id   bigint,
  acao          text not null,
  motivo        text,
  detalhe       jsonb,
  criado_em     timestamptz not null default now()
);
create index if not exists idx_eventos_entidade
  on eventos_primato (entidade, entidade_id, criado_em desc);

-- ── RLS ──────────────────────────────────────────────────────────────────────
-- O backend escreve com a service key (ignora RLS) e já validou o JWT. Estas
-- políticas fecham a porta do acesso direto por anon key: só quem está em
-- `membros_primato` lê, e o associado lê apenas o próprio cadastro.
do $$
declare t text;
begin
  foreach t in array array['membros_primato','associados','propriedades','talhoes',
                           'documentos_primato','safras','lancamentos_safra',
                           'referencias_produtividade','eventos_primato']
  loop
    execute format('alter table %I enable row level security', t);
  end loop;
end $$;

drop policy if exists p_membro_le_proprio on membros_primato;
create policy p_membro_le_proprio on membros_primato
  for select using (usuario_id = auth.uid());

-- Helper: papel do usuário corrente.
create or replace function primato_papel() returns text
language sql stable security definer set search_path = public as $$
  select papel from membros_primato where usuario_id = auth.uid() limit 1;
$$;

create or replace function primato_associado_id() returns bigint
language sql stable security definer set search_path = public as $$
  select associado_id from membros_primato where usuario_id = auth.uid() limit 1;
$$;

drop policy if exists p_associados_leitura on associados;
create policy p_associados_leitura on associados for select using (
  primato_papel() in ('gestor','tecnico','auditor')
  or (primato_papel() = 'associado' and id = primato_associado_id())
);

do $$
declare t text;
begin
  foreach t in array array['propriedades','safras'] loop
    execute format('drop policy if exists p_%s_leitura on %I', t, t);
    execute format($f$create policy p_%s_leitura on %I for select using (
        primato_papel() in ('gestor','tecnico','auditor')
        or (primato_papel() = 'associado' and associado_id = primato_associado_id()))$f$, t, t);
  end loop;
end $$;

drop policy if exists p_talhoes_leitura on talhoes;
create policy p_talhoes_leitura on talhoes for select using (
  primato_papel() in ('gestor','tecnico','auditor')
  or exists (select 1 from propriedades p where p.id = talhoes.propriedade_id
             and p.associado_id = primato_associado_id())
);

drop policy if exists p_documentos_leitura on documentos_primato;
create policy p_documentos_leitura on documentos_primato for select using (
  primato_papel() in ('gestor','tecnico','auditor')
  or associado_id = primato_associado_id()
);

drop policy if exists p_lancamentos_leitura on lancamentos_safra;
create policy p_lancamentos_leitura on lancamentos_safra for select using (
  primato_papel() in ('gestor','tecnico','auditor')
  or exists (select 1 from safras s where s.id = lancamentos_safra.safra_id
             and s.associado_id = primato_associado_id())
);

drop policy if exists p_referencias_leitura on referencias_produtividade;
create policy p_referencias_leitura on referencias_produtividade
  for select using (primato_papel() is not null);

-- Trilha: só gestor e auditor leem.
drop policy if exists p_eventos_leitura on eventos_primato;
create policy p_eventos_leitura on eventos_primato for select using (
  primato_papel() in ('gestor','auditor')
);

-- ── Bucket privado de documentos ─────────────────────────────────────────────
insert into storage.buckets (id, name, public)
values ('documentos-primato', 'documentos-primato', false)
on conflict (id) do nothing;
