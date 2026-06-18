# Local Autonomous Coding Loop

Harness local de engenharia para desenvolvimento assistido por agentes em um repositório, com ciclo controlado de Planner, Executor e Reviewer usando `subprocess`, `git` e prompts estruturados.

## Posicionamento

Este projeto não tenta ser um framework pesado de agentes. A direção é construir uma camada de `harness engineering`: uma infraestrutura local, auditável e segura para coordenar trabalho de agentes sobre uma base de código.

O foco principal está em:

- contratos claros de entrada e saída
- execução determinística de comandos
- aplicação segura de mudanças no repositório
- verificação automática por iteração
- trilha de auditoria com logs, diffs e decisões
- facilidade para trocar o agente que atua como Planner, Executor ou Reviewer

Em outras palavras, o valor central está menos no modelo e mais no loop operacional confiável ao redor dele.

## Escopo atual

Esta implementação inicial foca em:

- ler `agent_contract.md`
- validar restrições e limites
- exigir branch `agent/<task-name>`
- gerar prompt do Planner
- gerar prompt do Executor
- aplicar apenas operações `write_file`
- rodar checks definidos no contrato
- registrar logs e artefatos em `work/`
- decidir continuidade com Reviewer

Nesta fase, Planner, Executor e Reviewer são modelados como agentes simétricos em `agent_loop/agents.py`, com suporte a stub local, provider/responder injetado e client OpenAI opcional.

## Quick start (smoke run)

**Pré-requisitos:** Python 3.12+, `git` no PATH.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp agent_contract.example.md agent_contract.md
python runner.py --repo . --dry-run
```

**O que esperar:**

- Banner no terminal com repositório, contrato, modo (`stub` ou `openai`), `dry_run` e branch alvo
- Exit code `0`
- Criação de `work/agent_log.md` e `work/iterations/<n>/` (última iteração) com prompts, responses e `meta.json`

**Próximo passo:** para mutações reais, use `--no-dry-run` somente com repositório git limpo (exceto `agent_contract.md` permitido).

## Modo stub

Sem `OPENAI_API_KEY`, o harness roda em **modo stub** — útil para validar o loop localmente sem custo de API:

| Agente | Comportamento default |
|--------|----------------------|
| **Planner** | Plano genérico de 3 tarefas (ler contrato, preparar mudança mínima, rodar checks) |
| **Executor** | Nenhuma operação `write_file`; propõe `commands` iguais a todos os `checks` do contrato |
| **Reviewer** | `OBJECTIVE_COMPLETE` se todos os checks passarem; `REVISE` se algum falhar |

O stub **não implementa features** — apenas exercita o harness, grava artefatos e simula o ciclo. Para codificação real, use modo OpenAI ou injete responders/providers customizados via `run_loop(...)`.

## Modo OpenAI

Com `OPENAI_API_KEY` configurada, Planner, Executor e Reviewer usam o mesmo client OpenAI e o modelo resolvido (`OPENAI_MODEL`, `--model`, ou default `gpt-4o-mini`).

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL=gpt-4o-mini   # opcional
python runner.py --repo . --no-dry-run
```

Ou inline:

```bash
OPENAI_API_KEY=... python runner.py --repo . --cost-limit 2.5
```

A API key **nunca** vai por CLI — somente via env. O campo `cost_limit` (contrato/env/CLI) limita o custo estimado acumulado por iteração.

Chamadas programáticas a `run_loop(...)` seguem a mesma precedência: passe `overrides=HarnessOverrides(...)` ou `dry_run=...` apenas quando quiser sobrescrever contrato/env; omitir ambos deixa a resolução usar contrato, env e defaults.

## Contrato mínimo

Campos obrigatórios no frontmatter de `agent_contract.md`:

| Campo | Descrição |
|-------|-----------|
| `objective` | Objetivo em linguagem natural |
| `checks` | Lista de comandos permitidos (match exato com `commands` do Executor) |
| `constraints` | Regras de segurança |
| `max_iterations` | Limite de iterações do loop |
| `task_name` | Slug da tarefa; branch será `agent/<task_name>` |

Exemplo mínimo:

```yaml
---
objective: Add a helper and verify tests pass
checks:
  - python -m pytest
constraints:
  - Never run sudo
max_iterations: 3
task_name: my-task
---
```

Copie [`agent_contract.example.md`](agent_contract.example.md) para começar — inclui campos opcionais comentados no corpo.

## Configuração operacional

Precedência por campo: **CLI > contrato > env > defaults**.

| Campo | CLI | Contrato | Variável de ambiente | Default |
|-------|-----|----------|----------------------|---------|
| Modelo OpenAI | `--model` | — | `OPENAI_MODEL` | `gpt-4o-mini` |
| Iterações | `--max-iterations` | `max_iterations` | `AGENT_MAX_ITERATIONS` | `5` |
| Timeout de checks | `--command-timeout-sec` | `command_timeout_sec` | `AGENT_COMMAND_TIMEOUT_SEC` | `120` |
| Cost limit | `--cost-limit` | `cost_limit` | `AGENT_COST_LIMIT` | `5.0` |
| Dry-run | `--dry-run` / `--no-dry-run` | `dry_run` (opcional) | `AGENT_DRY_RUN` | `true` |

Exemplo de override em cadeia: contrato com `max_iterations: 10`, env `AGENT_MAX_ITERATIONS=8`, CLI `--max-iterations 3` → resultado **3**.

```bash
python runner.py --model gpt-4o-mini --cost-limit 2.5 --command-timeout-sec 60
python runner.py --help
```

## Layout de artefatos

Cada iteração grava material de auditoria em `work/iterations/<n>/`:

```text
work/
  agent_log.md
  iterations/
    N/
      meta.json                    # status, failed_stage, error, lista de arquivos
      planner_prompt.txt
      planner_response.json        # ou {"error": "..."}
      executor_request.json
      executor_response.json
      reviewer_prompt.txt
      reviewer_response.json
      commands.json
      diff.patch
      repeat_signal.json           # quando repetição cega é detectada
      apply_operations_error.json  # falha ao aplicar write_file
      checks_error.json            # falha ao rodar comandos
      diff_error.json              # falha ao coletar diff
```

**Como ler uma falha:**

1. Abra `work/iterations/<n>/meta.json` — veja `failed_stage` e `error`
2. Abra o artefato do estágio (ex.: `planner_response.json`, `checks_error.json`)
3. Consulte `work/agent_log.md` para o histórico completo

O `agent_log.md` referencia explicitamente os arquivos de cada iteração.

## Contrato do Executor

O harness trata o Executor como um agente de primeira classe (`ExecutorAgent`).

Entrada enviada ao Executor:

```json
{
  "objective": "objetivo atual",
  "plan": {
    "summary": "plano mínimo",
    "tasks": ["tarefa 1", "tarefa 2"]
  },
  "constraints": ["regra 1", "regra 2"],
  "allowed_commands": ["pytest"],
  "branch": "agent/minha-tarefa",
  "iteration": 1,
  "repo_path": "/caminho/do/repo",
  "executor_prompt": "prompt final que o executor deve seguir"
}
```

Saída esperada do Executor:

```json
{
  "operations": [
    {
      "type": "write_file",
      "path": "src/example.py",
      "content": "conteudo completo do arquivo"
    }
  ],
  "commands": ["pytest"],
  "summary": "resumo curto das mudanças"
}
```

Regras desse contrato:

- `commands` deve conter apenas strings que coincidam **exatamente** (após remover espaços nas pontas) com algum item de `allowed_commands` / `checks` do contrato; variantes como `python -m pytest` não são aceitas se o contrato listar só `pytest`
- `operations` aceita apenas `write_file` nesta fase
- `path` deve ser relativo ao repositório
- `content` deve trazer o conteúdo completo do arquivo
- o Executor não aplica nada diretamente; ele apenas propõe
- o harness continua responsável por validar, aplicar, testar e revisar

## Estrutura

```text
runner.py
agent_loop/
  __init__.py
  agents.py
  config.py
  models.py
  prompts.py
  runner.py
  tools.py
tests/
agent_contract.example.md
```

Responsabilidade dos módulos:

- `runner.py`: entrada CLI
- `agent_loop/runner.py`: loop principal de execução
- `agent_loop/agents.py`: `PlannerAgent`, `ExecutorAgent` e `ReviewerAgent`
- `agent_loop/tools.py`: operações seguras de git, subprocess, arquivos e logs
- `agent_loop/prompts.py`: contratos de prompt e validação de payloads
- `agent_loop/models.py`: modelos internos e registros de iteração
- `agent_loop/config.py`: limites, defaults e regras de segurança

## Segurança

O loop bloqueia:

- `sudo`
- `rm -rf`
- `git push`
- troca para `main` ou `master`
- alterações em `.env`
- alterações dentro de `.git/`

Também exige repositório limpo antes de mutações reais.

## Integração programática

Rodar o loop com `OPENAI_API_KEY` configurada para usar Planner, Executor e Reviewer via API, ou injetar providers/responders customizados em `run_loop(...)` para integrações externas.
