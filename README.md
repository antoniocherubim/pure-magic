# Local Autonomous Coding Loop (MVP)

Orquestrador Python que coordena três agentes (**Planner**, **Executor**, **Reviewer**) localmente, usando OpenAI API (fase futura), subprocess e git CLI, com regras de segurança e logs incrementais.

## Arquitetura

```
runner.py (CLI)
    └── agent_loop/
            ├── runner.py    # ciclo de iterações
            ├── agents.py    # Planner, Executor, Reviewer
            ├── tools.py     # git, subprocess, arquivos (segurança)
            ├── prompts.py   # templates e validação JSON
            ├── config.py    # limites e defaults
            └── models.py    # dataclasses internas
```

Fluxo por iteração:

1. Ler e validar `agent_contract.md`
2. Verificar git status
3. Criar branch `agent/<task-name>` (exceto em dry-run)
4. **Planner** → plano atômico
5. **Executor** → JSON com `operations`, `commands`, `summary`
6. Validar e aplicar operações (ou simular em dry-run)
7. Executar comandos de verificação
8. **Reviewer** → `CONTINUE` / `OBJECTIVE_COMPLETE` / `REVISE`
9. Registrar em `work/agent_log.md`

## Instalação

```bash
pip install -r requirements.txt
```

## Uso (dry-run)

```bash
# Copie o contrato de exemplo para o repo alvo
cp agent_contract.example.md agent_contract.md

# Execute 1 iteração simulada (sem mutações)
python runner.py --dry-run --repo .
```

Opções:

| Flag | Descrição |
|------|-----------|
| `--repo PATH` | Repositório alvo (default: `.`) |
| `--contract PATH` | Arquivo de contrato (default: `agent_contract.md`) |
| `--dry-run` | Simula sem alterar disco/git (default) |
| `--no-dry-run` | Aplica mudanças reais |
| `--task-name NAME` | Nome da branch `agent/<name>` |

## Contrato do agente

Veja [`agent_contract.example.md`](agent_contract.example.md). Campos obrigatórios:

- `objective`
- `checks`
- `constraints`
- `max_iterations`

## Testes

```bash
pytest
```

## Segurança

Todas as interações com o sistema passam por `agent_loop/tools.py`:

- Comandos perigosos (`sudo`, `rm -rf`, `git push`, etc.) são bloqueados
- Paths protegidos (`.env`, `.git/`) não podem ser alterados
- Branch de trabalho obrigatória com prefixo `agent/`
- Push remoto proibido

## Roadmap

- [ ] Integração real com OpenAI API
- [ ] Aplicação e revert de operações por iteração
- [ ] Suporte a `modify_file` / `delete_file`
- [ ] Commits locais opcionais na branch de agente
- [ ] Testes de integração com mocks da API
