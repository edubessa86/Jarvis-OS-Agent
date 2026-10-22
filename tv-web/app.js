(() => {
  "use strict";
  const voiceButton = document.getElementById("voice-button");
  const stopButton = document.getElementById("stop-button");
  const stateText = document.getElementById("voice-state-text");
  const statusMessage = document.getElementById("status-message");
  const messageText = document.getElementById("message-text");
  const coreStatus = document.getElementById("core-status");
  const commandForm = document.getElementById("command-form");
  const commandInput = document.getElementById("command-input");
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let listening = false;
  let activated = false;

  function setMessage(text) { messageText.textContent = text; }
  function setListening(value) {
    listening = value;
    document.body.classList.toggle("listening", value);
    stateText.textContent = value ? (activated ? "Palavra de ativação detectada" : "Teste de voz ativo · diga “Jarvis”") : "Microfone inativo";
    voiceButton.disabled = value;
    stopButton.disabled = !value;
  }
  function stopListening() {
    if (recognition && listening) recognition.stop();
    setListening(false);
  }

  voiceButton.addEventListener("click", () => {
    if (!SpeechRecognition) {
      stateText.textContent = "Reconhecimento de voz indisponível";
      statusMessage.textContent = "Este navegador não expõe a API de reconhecimento de voz necessária. Não há escuta ativa.";
      setMessage("Não foi possível iniciar o teste de voz neste navegador. A interface continua disponível para demonstração por texto.");
      return;
    }
    try {
      recognition = new SpeechRecognition();
      recognition.lang = "pt-BR";
      recognition.continuous = true;
      recognition.interimResults = false;
      recognition.onstart = () => {
        setListening(true);
        statusMessage.textContent = "Teste ativo enquanto esta página permanece aberta. Diga “Jarvis” para testar a ativação.";
        setMessage("Estou testando a compatibilidade de voz. A integração real com o núcleo ainda não está conectada.");
      };
      recognition.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (!event.results[i].isFinal) continue;
          const transcript = event.results[i][0].transcript.trim();
          if (!transcript) continue;
          if (/\bjarvis\b/i.test(transcript)) {
            activated = true;
            stateText.textContent = "Palavra “Jarvis” detectada";
            setMessage("Palavra de ativação detectada. O próximo incremento conectará este evento ao gateway seguro de IA.");
          } else if (activated) {
            setMessage("Comando reconhecido: “" + transcript + "”. Ainda em modo de teste; nenhum comando foi executado.");
          }
        }
      };
      recognition.onerror = (event) => {
        stateText.textContent = "Erro de voz: " + (event.error || "desconhecido");
        statusMessage.textContent = "O navegador interrompeu ou recusou o reconhecimento. Verifique permissões e suporte.";
      };
      recognition.onend = () => {
        setListening(false);
        if (recognition) {
          statusMessage.textContent = "O reconhecimento terminou. A escuta contínua não é garantida; inicie outro teste manualmente.";
        }
      };
      recognition.start();
    } catch (_) {
      setListening(false);
      statusMessage.textContent = "Não foi possível iniciar o reconhecimento de voz neste navegador.";
      setMessage("Teste indisponível. Use a demonstração por texto para avaliar a interface.");
    }
  });

  stopButton.addEventListener("click", stopListening);

  commandForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const command = commandInput.value.trim();
    if (!command) return;
    setMessage("Comando de demonstração recebido: “" + command + "”. Nenhum dado foi enviado a um serviço externo.");
    commandInput.value = "";
  });

  window.addEventListener("pagehide", stopListening);
  coreStatus.textContent = "Núcleo desconectado · demonstração";
})();