# Publicação HTTPS da interface UltimateJarvis TV

## Objetivo
Publicar somente os arquivos estáticos de `tv-web/` no GitHub Pages, com HTTPS, para validar a interface no navegador da Samsung CU8000. Este deploy **não** cria um gateway, não conecta um modelo de IA e não ativa escuta permanente.

## Como ativar após integrar a branch
1. Abra **Settings → Pages** no repositório `edubessa86/Jarvis-OS-Agent`.
2. Em **Build and deployment**, selecione **GitHub Actions** como origem, se ainda não estiver selecionada.
3. Integre esta alteração à branch `main`.
4. Acesse **Actions → Deploy UltimateJarvis TV Web** e acompanhe a execução.
5. Quando concluir, abra a URL publicada exibida no job `deploy` (normalmente `https://edubessa86.github.io/Jarvis-OS-Agent/`).

## Teste na TV
1. Confirme que a TV e o controle remoto estão funcionando e abra o navegador da TV.
2. Digite a URL HTTPS publicada.
3. Teste navegação por controle remoto, legibilidade a alguns metros e demonstração por texto.
4. Se o navegador solicitar microfone, conceda permissão apenas para o teste e pressione explicitamente o botão de voz.
5. Registre se a API de voz existe, se a permissão é concedida, se a sessão encerra sozinha e se ela retoma após interrupção.
6. Pressione parar e saia da página para confirmar que a captura termina.

## Limites e segurança
- O workflow publica apenas `tv-web/`; não publica o repositório inteiro.
- A página é estática e não tem segredos, backend, chamadas a IA ou comandos de sistema.
- A API Web Speech pode não existir no navegador Tizen ou pode depender de serviço externo.
- Não trate a detecção de “Jarvis” como wake word confiável ou sempre ativa.
- Não adicione tokens de provedores ao HTML/JavaScript. O gateway autenticado será um incremento separado.
