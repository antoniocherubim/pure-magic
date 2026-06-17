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
- gerar prompt do Executor para outro agente externo
- aplicar apenas operações `write_file`
- rodar checks definidos no contrato
- registrar logs e artefatos em `work/`
- decidir continuidade com Reviewer

Nesta fase de desenvolvimento, o projeto já está preparado para um Executor externo via prompt estruturado, enquanto Planner e Reviewer podem continuar locais ou também ser substituídos depois.

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
- `agent_loop/agents.py`: papéis de Planner, Reviewer e ponte com Executor externo
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
5. Gerar prompt do Executor
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

Conectar um Executor externo que consuma `ExternalExecutorBridge.build_prompt(...)` e devolva o JSON estruturado esperado. Depois disso, o mesmo padrão pode ser expandido para plugar Planner e Reviewer remotos sem mudar o núcleo do harness.
