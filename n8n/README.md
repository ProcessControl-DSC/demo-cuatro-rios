# n8n workflows · Cuatro Ríos

Two n8n workflows relay customer orders to `pc_ai_order_intake` (`POST /pc_ai_order_intake/messaging`,
token in the `X-Order-Intake-Token` header, stored in the n8n credential "Odoo pedidos Cuatro Ríos").

| File | Channel | Entry |
|---|---|---|
| `telegram_workflow.json` | Telegram bot (text, voice notes, shared phone) | Telegram trigger |
| `phone_workflow.json` | Phone call through Twilio | `/webhook/twilio-llamada` (Twilio voice webhook, POST) |

`transcriber.py` is the local speech-to-text service used by the Telegram voice notes
(mlx-whisper large-v3-turbo on 127.0.0.1:8765, reached from the container as `host.docker.internal`).

Phone flow: Twilio only waits 5 seconds for an answer, so `/twilio-pedido` answers "one moment" at once,
calls Odoo afterwards and keeps the answer in the workflow static data; Twilio polls `/twilio-respuesta`
until it is ready and reads it aloud. Twilio trial accounts do not allow `<Record>`, so speech is
recognised with `<Gather input="speech">`.

Import: `n8n import:workflow --input=<file>`, then `n8n publish:workflow --id=<id>` and restart n8n.
Credential ids in the files are those of the demo instance: re-link them after importing elsewhere.
