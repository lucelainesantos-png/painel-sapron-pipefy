# Painel Sapron × Pipefy

Painel colaborativo cruzando cards da Fase 3 do PIPE 1 (Implantação/Mãe) com a central de atividades do Sapron.

## Componentes

- **`web/`** — site estático (hospedado no Coolify) com Supabase Auth (Google, restrito a `@seazone.com.br`) + realtime nos checks e OBS.
- **`step1_pipefy.py`** — puxa cards da Fase 3 via GraphQL.
- **`step2_sapron.py`** — consulta o Sapron MCP via `npx mcp-remote`.
- **`step3_publish.py`** — publica os dados no Supabase (tabelas `pipefy_cards`, `pipefy_activities`, `pipefy_meta`).
- **`refresh.py`** — orquestra os 3 passos; roda de hora em hora via Windows Task Scheduler (task `SeazonePainelSapronPipefy`).

## Deploy

Site estático → Coolify (build_pack: nixpacks, base_directory: `/web`).  
Backend → Supabase project `vistoria-acompanhamento` (`lrupjlgqckbimrvxbnku`).

## Credenciais (fora do git)

- `.pipefy_token` — JWT do Pipefy
- `.supabase_service_key` — service role key do Supabase (usada só pelo refresh Python)
- `config.py` — junta tudo

Nenhum desses vai pro repo (ver `.gitignore`).
