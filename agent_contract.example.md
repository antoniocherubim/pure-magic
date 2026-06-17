---
objective: Implementar uma função de saudação simples com testes pytest
checks:
  - pytest
constraints:
  - Não modificar arquivos em .git/
  - Não executar git push
  - Não alterar .env
max_iterations: 3
allowed_installs: []
allow_overwrite: false
task_name: hello-greeting
---

# Agent Contract

Este arquivo define o contrato entre o operador humano e o orquestrador de agentes.

## Campos

| Campo | Descrição |
|-------|-----------|
| `objective` | Objetivo da tarefa em linguagem natural |
| `checks` | Comandos de verificação executados após cada iteração |
| `constraints` | Proibições explícitas para os agentes |
| `max_iterations` | Limite de iterações do ciclo |
| `allowed_installs` | Pacotes permitidos para instalação (vazio = nenhum) |
| `allow_overwrite` | Se `true`, permite sobrescrever arquivos existentes |
| `task_name` | Nome usado na branch `agent/<task-name>` |

Copie este arquivo para `agent_contract.md` no repositório alvo antes de executar o runner.
