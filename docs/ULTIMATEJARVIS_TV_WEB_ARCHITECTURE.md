# UltimateJarvis — Samsung TV Web Architecture

## Target
Samsung UN65CU8000GXZD, Crystal UHD CU8000 (2023), using the built-in Tizen browser. The selected delivery path is a full-screen web interface, not a native Tizen package.

## Architecture
- **TV client:** static HTML/CSS/JS optimized for 10-foot viewing and remote navigation. It holds no provider credentials and performs no privileged actions.
- **Voice compatibility adapter:** optional Web Speech API probe, explicitly started by a user gesture. It is a compatibility experiment, not a promise of local or always-on recognition.
- **HTTPS gateway (future):** authenticated backend that validates requests, rate-limits use, keeps provider credentials in server-side environment variables, and exposes a narrow assistant endpoint.
- **UltimateJarvis Core:** existing local-first Windows/Python core remains separate. Its task policy, workspace confinement and tool allowlist are not bypassed by the TV client.

## Wake-word decision
A true wake-word detector needs access to a continuous audio stream and a compatible runtime. Web Speech recognition is not equivalent to an offline wake-word engine: support, audio processing location, permissions, background behavior and session lifetime vary by browser. The first increment therefore requires a deliberate button press to start the *test only*. It recognizes “Jarvis” only if the browser exposes SpeechRecognition and keeps the session alive. No automatic microphone activation is attempted.

If this does not work on the CU8000, keep the same UI and evaluate a supported native Tizen audio capability or a separate trusted voice endpoint in a later decision. Do not claim that the TV is listening while no active audio session exists.

## Secrets and request safety
- Never embed API keys in browser JavaScript, HTML, source maps, Git history or URL query strings.
- Keep provider credentials in server-side environment variables or a secrets manager.
- Require HTTPS, authenticated sessions or a device-specific pairing flow, request-size limits and rate limits before connecting the gateway.
- Treat all recognized speech as untrusted input. The first live integration is text-only and cannot invoke operating-system commands, filesystem access, purchases, smart locks or other consequential actions.
- Use allowlisted actions and explicit confirmation for sensitive operations. No unrestricted shell or code execution from TV requests.
- Avoid retaining raw audio. Make any external speech processing and its privacy implications visible.

## Delivery increments
1. **TV web prototype (current):** static interface, demo text, explicit voice compatibility test; no backend or secrets.
2. **Hardware/browser validation:** test on the physical TV using HTTPS, document recognition support, latency, remote navigation and session termination.
3. **Secure gateway:** server-side provider integration, authentication, rate limits, no secrets on client.
4. **Wake-word decision:** choose a verified offline detector or supported audio API only after physical validation. A persistent wake word may be impossible within browser restrictions.
5. **Core integration:** send post-activation text to the existing UltimateJarvis Core through a narrow authenticated interface; preserve local-first routing and policy enforcement.

## Acceptance criteria for increment 1
- UI loads without third-party scripts or external requests.
- Text demo works without network access.
- No provider credentials exist in the client.
- Voice is not activated on page load.
- Unsupported recognition is reported honestly.
- No command executes outside the demo.