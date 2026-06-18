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

## Artefatos por iteração

Cada iteração grava material de auditoria em `work/iterations/<n>/`, inclusive quando planner, executor ou reviewer falham:

```text
work/
  agent_log.md
  iterations/
    1/
      meta.json
      planner_prompt.txt
      planner_response.json
      executor_request.json
      executor_response.json
      reviewer_prompt.txt
      reviewer_response.json
      commands.json
      diff.patch
```

O `agent_log.md` referencia explicitamente os arquivos da iteração. Em falhas, o diretório contém prompts/responses parciais e `meta.json` com `failed_stage` e `error`.

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

## Uso

```bash
cp agent_contract.example.md agent_contract.md
python runner.py --repo . --dry-run
```

`--dry-run` executa uma iteração simulada e grava `work/agent_log.md`.

Fluxo esperado:

1. Ler contrato
2. Validar estado do repositório
3. Garantir branch `agent/<task-name>`
4. Gerar plano mínimo
5. Montar `ExecutorRequest`
6. Receber operações estruturadas
7. Aplicar mudanças com segurança
8. Rodar checks
9. Registrar diff, decisão e log da iteração

## Contrato

O contrato aceita frontmatter simples. Campos obrigatórios:

- `objective`
- `checks`
- `constraints`
- `max_iterations`
- `task_name`

## Segurança

O loop bloqueia:

- `sudo`
- `rm -rf`
- `git push`
- troca para `main` ou `master`
- alterações em `.env`
- alterações dentro de `.git/`

Também exige repositório limpo antes de mutações reais.

## Próximo passo recomendado

Rodar o loop com `OPENAI_API_KEY` configurada para usar Planner, Executor e Reviewer via API, ou injetar providers/responders customizados em `run_loop(...)` para integrações externas.
