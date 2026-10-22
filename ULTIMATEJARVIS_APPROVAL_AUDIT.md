# UltimateJarvis — aprovação e auditoria local

Esta etapa adiciona execução humana-aprovada somente para as ferramentas classificadas como `read_only`.

## Executar com revisão

Com Ollama instalado e ativo:

~~~powershell
ollama pull qwen2.5:3b
py -3 -m ultimatejarvis run --workspace "$env:USERPROFILE\UltimateJarvisWorkspace" "Leia notes.txt e resuma o conteúdo"
~~~

O fluxo exige duas confirmações:
1. Digitar exatamente `APROVAR` para iniciar a revisão.
2. Digitar exatamente `SIM` para cada etapa que deseja autorizar.

Recusar uma etapa impede sua execução. Todas as etapas são revalidadas contra a allowlist e o risco antes de qualquer solicitação de aprovação. Nesta versão, somente ferramentas de leitura podem passar pela validação; não há shell, escrita, automação arbitrária nem acesso a caminhos fora do workspace.

## Auditoria

Os eventos ficam no SQLite local `%LOCALAPPDATA%\UltimateJarvis\tool-audit.sqlite3` no Windows. Consulte os eventos recentes:

~~~powershell
py -3 -m ultimatejarvis audit --limit 50
~~~

A auditoria registra ferramenta, argumentos, estado e um resumo técnico, não o conteúdo retornado de arquivos. Os argumentos podem incluir nomes de arquivos e diretórios; proteja o arquivo de auditoria. O banco é local, sem criptografia automática. O log é append-only pela interface pública do módulo, mas um usuário com acesso direto ao arquivo SQLite pode modificá-lo.

Esta é uma barreira de aprovação da aplicação, não um sandbox do sistema operacional. A etapa seguinte continua sendo desenhar isolamento real para tarefas de maior risco.
