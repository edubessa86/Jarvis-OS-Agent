# UltimateJarvis TV Web Prototype

Primeiro incremento da interface web para a Samsung UN65CU8000GXZD (Tizen).

## Abrir localmente
Abra `index.html` em um navegador moderno para avaliar a interface. Para testes de voz, navegadores podem exigir HTTPS ou localhost; abrir o arquivo diretamente pode impedir a captura de áudio. Hospedagem segura ainda não está configurada.

## O que este protótipo faz
- Interface 16:9 com tipografia grande e foco visual para TV.
- Demonstração por texto, sem enviar comandos para serviços externos.
- Teste opcional de reconhecimento de fala pelo Web Speech API, iniciado somente após ação explícita do usuário.
- Detecção experimental da palavra “Jarvis” no texto reconhecido.
- Indicadores claros quando a voz não está disponível.

## O que não faz
- Não mantém microfone ativo automaticamente ao abrir.
- Não garante funcionamento em segundo plano, suspensão, modo de espera ou após desligar a TV.
- Não chama APIs de IA, não controla a TV/casa e não executa tarefas.
- Não contém chaves, tokens ou segredos.

## Privacidade
A implementação de Web Speech varia por navegador e pode enviar áudio para um serviço de reconhecimento externo. Não use o teste para conversas privadas. Para uso real, a escolha de STT/wake-word deve considerar processamento local, consentimento, indicadores de gravação e retenção mínima.

## Próximo incremento
1. Testar no navegador real da CU8000 via HTTPS.
2. Registrar suporte real a microfone e Web Speech API.
3. Criar gateway HTTPS autenticado no servidor, com segredos em variáveis de ambiente.
4. Conectar apenas texto após ativação confirmada; nunca expor chave de provedor no cliente.
5. Não declarar wake-word permanente até passar teste explícito de duração, suspensão e retomada.