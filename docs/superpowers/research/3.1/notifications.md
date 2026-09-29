# Notificaciones de Noust: auditoría y rediseño (ítem 49 del backlog)

Fecha: 2026-09-29. Rama: `dev/3.1`. Estado: investigación, sin cambios de código en el repositorio.

Encargo del propietario (ítem 49): revisar y rediseñar las notificaciones de **todos** los canales
(Telegram, correo, Slack, Discord, webhook), en inglés y español. En Telegram los mensajes son
pobres y repiten información dentro de un mismo mensaje (`deploy_rolled_back` además incrusta el
journal literal). Cada canal debe tener su formato propio, con el estilo de Noust: tranquilo,
profesional, el estado primero, una línea de lo ocurrido, los hechos (app, servidor/nodo, commit,
origen, duración), un enlace a la consola y la salida del sistema solo como extracto literal corto.
Con el nombre del nodo en los eventos de flota, tests de snapshot por canal y evento, y capturas.

Cómo se hizo: se leyó todo el pipeline, se ejecutaron los constructores reales (sin abrir un solo
socket) para producir la salida actual de cada evento, canal e idioma, se contrastaron las
plataformas con su documentación oficial y con el código de herramientas de referencia, y se
prototipó el diseño propuesto (fuera del repositorio) para comprobar límites, escapes y
duplicados con datos reales antes de escribirlo aquí.

Artefactos (en `/tmp`, no en el repositorio; se pierden al reiniciar, se regeneran con los
scripts indicados en el anexo B):

| Ruta | Contenido |
|---|---|
| `/tmp/noust-notif-before/{en,es}/<evento>/` | Salida actual: `telegram.txt`, `slack.json`, `discord.json`, `webhook.json`, `email.{subject,text}.txt`, `email.html`, `_event.json` |
| `/tmp/noust-notif-before/screens/*.png` | Capturas de la salida actual (correo real en Chromium; Telegram, Slack y Discord como maqueta) |
| `/tmp/noust-notif-proposed/{en,es}/<evento>/` | Salida propuesta, mismos ficheros |
| `/tmp/noust-notif-proposed/mocks/*.png` | Capturas de la propuesta: correo claro y oscuro (real), Telegram, Slack y Discord (maqueta) |
| `/tmp/noust-notif-render/` | Scripts: `render_before.py`, `proposed.py`, `mocks.py`, `mocks_before.py`, `shot.mjs` |

---

## 0. Resumen

**Lo que ocurre hoy.** Todo el sistema se reduce a un `NotificationEvent(kind, title, body, domain, ts)`
y cada canal recibe `título\ncuerpo` como texto plano. Los canales solo difieren en el sobre JSON.
No hay estado, ni hechos estructurados, ni nombre de servidor, ni enlace separado, ni formato.

**Problemas medidos** (detalle y citas literales en la sección 2):

1. **Duplicados dentro de un mensaje.** El commit sale en el título (`deployed a1b2c3d (main)`) y otra
   vez en el cuerpo (`Commit: a1b2c3d (main)`). En los trabajos de copia y restauración el título
   dice `Backup of X failed` y el cuerpo empieza por `Backup failed` (el `str()` de un
   `NoustError`, con su rótulo interno `  Details:`). En `deploy_rolled_back` el resultado se dice
   cuatro veces (título, `Release … active again`, `The application did not answer…`,
   `Health check attempts 1-15 failed`) y después vienen 40 líneas de journal.
2. **El truncado tira lo importante.** El cuerpo de un rollback mide 4577 caracteres. Telegram corta
   a 4096 y Discord a 2000 **por el final**, en mitad de una línea, y el final es el enlace a la
   consola: en Telegram y Discord el mensaje de rollback y el de fallo **no llevan enlace** (comprobado).
3. **Tipos de evento incorrectos.** Restaurar una copia se envía como `deploy_success` / `deploy_failed`.
   Actualizar, revertir a mano, activar una release y migrar a releases también salen como
   `deploy_success` (`shop.example.com deployed a1b2c3d`): quien consume el webhook no puede
   distinguirlos y quien apaga `deploy_success` apaga también las restauraciones.
4. **Certificados.** `expires in -80 day(s)` para uno ya caducado (sigue siendo `cert_expiring`),
   `day(s)` en inglés, y `shop.example.com, www.shop.example.com expires on …` (concordancia).
5. **Correo.** El HTML es un `<h2>` y un `<p>` con saltos `\n` crudos: el navegador los colapsa y
   `Trigger: webhook Commit: a1b2c3d (main)` sale en una sola línea (capturado). Sin marca, sin
   estructura, sin enlace clicable, sin `Date`, `Message-ID` ni `Auto-Submitted`. Existe **un segundo
   camino de correo** independiente (el informe de observaciones del monitor) con su propio HTML.
6. **Español a medias.** Toda frase que Noust construye por su cuenta sale en inglés en un mensaje
   español (`Release … did not pass its health check`, `systemd reports … failed: result exit-code…`,
   `Backup of … failed`, `Details:`, `... (truncated)`), además de `Trigger`/`Origen` con el valor
   crudo del enum (`webhook`, `cli`, `panel`) y `el canal telegram` en minúsculas.
7. **Ningún mensaje dice de qué servidor viene.** En una flota, dos nodos con el mismo problema son
   indistinguibles. Además los nodos solo tienen consola por túnel: sin `web.public_url` no hay
   enlace en ninguna de sus notificaciones (hallazgo útil abajo: `web.public_url` acepta una ruta y
   `https://central/n/<nodo>` produce enlaces válidos sin tocar el enrutador).
8. **Faltan eventos** que un operador espera: recuperación (servicio y disco), certificado caducado
   como estado propio, copia completada (opcional), nodo inalcanzable, alertas de CPU y memoria fuera
   del correo.

**Propuesta** (sección 4): un único modelo estructurado `Notification` (evento, código fino, estado,
título, resumen de una frase, hechos, un comando, extracto literal acotado, enlaces, servidor) y un
renderizador puro por canal. Telegram en HTML con botón de enlace, sin vista previa y silencioso
para lo que no exige atención; Slack en Block Kit con franja de color; Discord en embed con color,
campos y marca de tiempo; correo `multipart/alternative` con HTML de tablas y CSS en línea que se ve
bien en modo oscuro y una parte de texto propia; webhook JSON con `version: 1`, aditivo y estable.
Glifos de estado geométricos (`●` `◐` `▲` `✕` `○`), **sin emoji** (verificado contra
`emoji-data.txt`: ninguno tiene la propiedad Emoji, a diferencia de `✔ ✖ ⚠ ❌ ✅`). Reglas de
deduplicación comprobables y una batería de tests de snapshot (syrupy ya es dependencia de
desarrollo) más invariantes con Hypothesis para escapes y límites.

**Decisiones que necesito del propietario** (sección 5): glifos vs emoji en Telegram, franja de color
en Slack, recorte del prefijo de journald en Telegram, logo del correo, nombre del servidor y cómo
lo fija la flota, y qué eventos nuevos entran en 3.1.

---

## 1. Mapa del pipeline

### 1.1 Flujo

```
EMISORES                                                     TRANSPORTE                CANALES
────────                                                     ──────────                ───────
deployers/recorder.py  DeploymentRecorder._announce
  └ deploy_events.publish(DeployEvent)                        core/notifier.py
      └ core/deploy_notifications.on_deploy_event ─cola FIFO─▶ Notifier.notify(NotificationEvent)
web/server.py  JobNotificationSubscriber                        ├ notifications.enabled?
  ├ restore (completed/failed)   ─ notify_in_background ─cola─▶ ├ notifications.events[kind]?
  ├ backup job failed                                            └ para cada canal de CHANNELS:
  └ deploy/update/rollback que falló ANTES del recorder             _dispatch ─▶ _request_for
managers/backup_scheduler.py  _notify_backup_failed (síncrono)        ├ webhook   POST JSON
monitor/process_monitor.py    _publish_event (síncrono)               ├ slack     POST JSON
  ├ _notify_full_disks   (disk_threshold)                             ├ discord   POST JSON
  ├ _check_certificates  (cert_expiring)                              ├ telegram  POST sendMessage
  └ _report_services     (unit_failed)                                └ email     EmailNotifier._send (SMTP)
Notifier.test_channel  ◀ `noust notify test`, POST /api/config/notifications/{canal}/test

Camino aparte (segundo correo, no pasa por Notifier):
monitor/email_notifier.EmailNotifier.send_observation_alert (monitor.notify) y send_test_email
(`noust monitor test-email`, API del monitor)
```

Puntos de entrada en el código (línea actual):

| Pieza | Fichero:línea |
|---|---|
| Modelo `NotificationEvent`, `EVENT_KINDS`, `CHANNELS`, límites | `core/notifier.py:117-147`, `:427` |
| Texto común `título\ncuerpo` y corte por límite | `core/notifier.py:484` `_message_text` |
| Constructores por canal | `_webhook_request:549`, `_slack_request:614`, `_discord_request:633`, `_telegram_request:655`, `_send_email:1049` |
| Filtros, aislamiento de fallos, prueba | `Notifier.notify:841`, `test_channel:874`, `_dispatch:980` |
| Cola FIFO de todo el proceso | `NOTIFICATION_QUEUE` (`core/notifier.py:1137`, el hilo aún se llama `wasm-notify`) |
| Título y cuerpo de un despliegue | `core/deploy_notifications.py:90` `_title`, `:178` `_body`, `:159` `_console_link`, `:223` `_send` |
| Restauración, copia manual y fallo previo al recorder | `web/server.py:518` `deployment_notification`, `:696` `_unrecorded_failure` |
| Copias programadas | `managers/backup_scheduler.py:695` `_notify_backup_failed`, `:755` `run_schedule` |
| Monitor | `monitor/process_monitor.py:464` `_publish_event`, `:618`, `:703`, `:753` |
| Catálogo en/es | `core/messages.py` (40 claves, `message()` y `plural()`) |
| Correo del monitor | `monitor/email_notifier.py:261` `_send`, `:318`, `:425` |

### 1.2 Eventos actuales

`EVENT_KINDS` tiene 8 tipos. Cada uno es un interruptor en `notifications.events.<tipo>`.

| `kind` | Quién lo emite | Cuándo | Construcción del mensaje |
|---|---|---|---|
| `deploy_started` | recorder (`start`) vía `deploy_notifications` | al abrir cada despliegue; **desactivado por defecto** | título `Deploying {domain}`; cuerpo: origen, commit, enlace |
| `deploy_success` | recorder (`SUCCEEDED`); **también** `web/server` para una restauración correcta | al cerrar el despliegue | título `{domain} deployed {commit} ({branch})`; cuerpo: origen, commit, enlace |
| `deploy_failed` | recorder (`FAILED`); `web/server` para restauración fallida y para un despliegue que falló antes del recorder | idem | título `{domain} failed to deploy`; cuerpo: origen, commit, `str(error)` completo, enlace |
| `deploy_rolled_back` | recorder (`ROLLED_BACK`, la excepción es `RolledBackError`) | idem | título `{domain} rolled back`; cuerpo igual que el fallo (incluye las 40 líneas de journal) |
| `cert_expiring` | monitor, `_check_certificates` | menos de 14 días, **incluye ya caducados**, una vez al día por certificado | título con días; cuerpo: dominios, fecha, `noust cert renew` |
| `unit_failed` | monitor, `_report_services` | al cruzar a fallo, una vez por caída; 3 subtipos (`failed`, `crash_loop`, `stopped_on_failure`) | título con la unidad; cuerpo: frase de Noust sobre lo que dijo systemd + orden `systemctl`/`journalctl` |
| `disk_threshold` | monitor, `_notify_full_disks` | al cruzar el 90 %, una vez por cruce | título con % redondeado; cuerpo con % con un decimal |
| `backup_failed` | `backup_scheduler` (3 casos: falta la programación, falla la copia local, falla la subida) y `web/server` (trabajo de copia manual fallido) | al ocurrir | título del catálogo; cuerpo: `str(NoustError)` (`mensaje\n  Details: …`) o un párrafo largo |
| `test` | `Notifier.test_channel` | botón de la consola, `noust notify test` | título y cuerpo del catálogo, con el nombre del canal en minúsculas |

No existen hoy (huecos): recuperación de servicio o disco, certificado caducado como evento propio,
fallo de renovación, copia completada, nodo inalcanzable o clave de host cambiada, alerta de CPU o
memoria (solo el correo de observaciones del monitor, un camino distinto), actualización de Noust
disponible, y distinción entre desplegar, actualizar, revertir, activar release y migrar.

### 1.3 Canales

| Canal | Configuración (`notifications.channels.*`) | Petición | Límite que aplica hoy | Escape hoy |
|---|---|---|---|---|
| `webhook` | `webhook.webhook_url` | POST JSON `{event, title, body, domain, ts}` | ninguno | ninguno (el endpoint es del operador) |
| `slack` | `slack.webhook_url` | POST `{"text": "título\ncuerpo"}` | 40 000 (`_MESSAGE_LIMITS`) | `&` `<` `>` |
| `discord` | `discord.webhook_url` | POST `{"content": …, "allowed_mentions": {"parse": []}}` | 2000 | `@everyone`/`@here` con espacio de ancho cero |
| `telegram` | `telegram.bot_token` + `telegram.chat_id` (validados) | POST `sendMessage {chat_id, text}`, **sin `parse_mode`** | 4096 | ninguno (texto plano) |
| `email` | `email.enabled` + SMTP en `monitor.smtp.*` y destinatarios en `monitor.email_recipients` | `EmailNotifier._send(EmailContent)`: asunto `[Noust] título`, texto = `título\ncuerpo`, HTML = `<h2>`+`<p>` | ninguno | `html.escape` |

Común: `notifications.enabled` (false por defecto), `notifications.language` (`en`/`es`),
`notifications.events.*`, `notifications.allow_private_hosts`, `web.public_url` (https; base de los
enlaces), guarda SSRF con revalidación en cada redirección, plazo de 10 s, secretos fuera de los
logs, fallo de un canal aislado del resto, entrega en una cola FIFO por proceso.

Frentes de configuración: consola (Ajustes > Notificaciones: `panel/src/features/settings/`),
API (`GET/PUT /api/config/notifications/telegram`, `POST …/telegram/chats`,
`POST …/{canal}/test`), CLI (`noust config set notifications.*`, `noust notify test <canal>`,
`noust notify telegram-chats`), y el correo del monitor (`noust monitor test-email`, API del monitor).

Lo que **no** debe tocar el rediseño (está bien y tiene tests): guarda SSRF, plazos, cola FIFO,
aislamiento de fallos, redacción de secretos, `test_channel` sin devolver cuerpos remotos salvo
Telegram, validación de token y `chat_id`, defusión de menciones.

### 1.4 Idiomas

`notifications.language` (`en`/`es`, validado en `core/config.py`) se lee al construir el mensaje.
`core/messages.py` es un catálogo tipado con paridad de claves y de marcadores comprobada por
`tests/test_messages.py`. Regla vigente: la evidencia de otros programas (probes, journal,
stderr de rclone o certbot) se queda literal en inglés, y el catálogo solo traduce la frase que la
rodea. La regla es correcta y se mantiene; lo que falla es el límite entre "evidencia" y "frase de
Noust" (ver N9 en la sección 2).

### 1.5 Fleet

Cada nodo es un Noust completo y notifica **desde su propio proceso** con su propia configuración;
la central no reenvía notificaciones (rule 3: no reimplementa nada de lo que hace un nodo). Por
tanto el nombre del servidor tiene que estar en el nodo. Hoy no está en ningún mensaje de los
cinco canales (solo el informe de observaciones del monitor imprime `socket.gethostname()`). Las consolas de los nodos son
de solo bucle local, así que `web.public_url` de un nodo suele estar vacío y no hay enlace. Se
comprobó que `_validate_public_url` conserva la ruta (`https://central.example.com/n/web-2/` →
`https://central.example.com/n/web-2`) y que el enrutador de la consola acepta `/n/<nodo>/…`
(`panel/src/app/nodeRoute.ts`), de modo que **con `web.public_url = https://<central>/n/<nodo>` en el
nodo, `_console_link` ya produce enlaces correctos sin cambiar código**. Falta que `noust fleet
authorize` o `noust node add` lo sugieran o lo fijen.

---

## 2. Salida actual (antes)

Generada con los constructores reales (`deploy_notifications._send`, `web.server.deployment_notification`,
`backup_scheduler.run_schedule` con almacén y gestores simulados, los métodos del monitor con
`ServiceHealth`/`DiskUsage`/`CertificateInfo` de ejemplo) y un `Notifier` real con un opener que solo
captura. Datos de ejemplo: `shop.example.com`, commit `a1b2c3d` en `main`, disparado por `webhook`,
`web.public_url = https://console.example.com`. 46 renders (23 escenarios × 2 idiomas) más los
mensajes de prueba y los dos correos del monitor.

### 2.1 Citas literales

**Despliegue correcto (el commit sale dos veces, el resultado una sola)** — `en/deploy_success/telegram.txt`:

```
shop.example.com deployed a1b2c3d (main)
Trigger: webhook
Commit: a1b2c3d (main)

https://console.example.com/apps/shop.example.com/deployments/42
```

Slack recibe `{"text": "<lo mismo>"}` y Discord `{"content": "<lo mismo>", "allowed_mentions": {"parse": []}}`.
El webhook recibe `{"event": "deploy_success", "title": "shop.example.com deployed a1b2c3d (main)",
"body": "Trigger: webhook\nCommit: a1b2c3d (main)\n\nhttps://…", "domain": "shop.example.com", "ts": "…"}`.

**Correo del mismo evento** — asunto `[Noust] shop.example.com deployed a1b2c3d (main)`; HTML completo:

```html
<!DOCTYPE html><html><body style="font-family: system-ui, sans-serif; color: #222;"><h2>shop.example.com deployed a1b2c3d (main)</h2><p>Trigger: webhook
Commit: a1b2c3d (main)

https://console.example.com/apps/shop.example.com/deployments/42</p></body></html>
```

Los `\n` dentro de `<p>` no son saltos en HTML: captura
`/tmp/noust-notif-before/screens/en-deploy_success-email-light.png` (`Trigger: webhook Commit: a1b2c3d (main)`
en una línea, URL sin enlace, cero estructura, cero marca).

**Rollback** (`deploy_rolled_back`, 4577 caracteres de cuerpo; primeras y últimas líneas de
`en/deploy_rolled_back/telegram.txt`, 4101 bytes):

```
shop.example.com rolled back
Trigger: webhook
Commit: a1b2c3d (main)

Release 20260929-104449 did not pass its health check; release 20260928-173010 is active again
  Details: The application did not answer the health check.

Health check attempts 1-15 failed: <urlopen error [Errno 111] Connection refused>

Last lines of the journal of shop-example-com:
Sep 29 10:45:12 web-1 systemd[1]: Started shop-example-com.service - Noust: shop.example.com (nextjs).
Sep 29 10:45:13 web-1 npm[48211]: > shop@1.4.2 start
… (40 líneas de journal, cuatro ciclos de reinicio casi idénticos) …
Sep 29 10:45:30 web-1 npm[48455]: Error: Could not find a production build in the '.next' directory. Try building your app with 'next
... (truncated)
```

El enlace de la consola **no está** (`grep console.example.com` da 0 en Telegram y en Discord; 1 en
Slack y en correo, que no cortan). Discord corta a 2000 en `Sep 29 10:45:1\n... (truncated)`.
Captura: `/tmp/noust-notif-before/screens/en-deploy_rolled_back-telegram.png` (2448 px de alto).
En español el título es `Se ha vuelto a la versión anterior de shop.example.com` y todo lo demás
del cuerpo sigue en inglés.

**Copia programada fallida** (título y primera línea del cuerpo dicen lo mismo):

```
Scheduled backup failed: shop.example.com
Backup of shop.example.com failed
  Details: tar: /var/www/apps/shop-example-com/shared/uploads: Cannot write: No space left on device
```

**Restauración fallida, en español** (`kind` = `deploy_failed`; el cuerpo es `str(NoustError)`):

```
No se ha podido restaurar shop.example.com
Restore failed
  Details: tar: Unexpected EOF in archive
tar: Error is not recoverable: exiting now
```

**Certificado ya caducado, en español** (sigue siendo `cert_expiring`):

```
El certificado de shop.example.com caduca en -80 días
shop.example.com, www.shop.example.com caduca el 2026-07-11. Renuévalo con: noust cert renew shop.example.com
```

**Unidad en bucle de reinicios, en español** (frase de Noust en inglés en medio de un mensaje español):

```
La unidad shop-example-com está en bucle de reinicios
systemd restarted shop-example-com 3 time(s) since the previous check (9 automatic restarts in total); it is activating/auto-restart, last run: result exit-code, main process exit status 1.
Revísalo con: systemctl status shop-example-com y journalctl -u shop-example-com -n 50
```

**Disco** (92 % en el título, 91.7 % en el cuerpo; sin servidor, sin enlace, sin espacio libre):

```
Disk usage at 92% on /
/ is 91.7% full, past the 90% alert threshold. A full disk stops deployments, logs and databases on this machine.
```

**Mensaje de prueba, en español**: `Si recibes esto, el canal telegram está bien configurado.` (nombre
del canal crudo, sin servidor, sin hora).

**Informe de observaciones del monitor** (segundo camino de correo): HTML con `<table border="1">`,
cabeceras `Severity/Process/User/CPU/Memory/Why` en inglés también en la versión española, y el texto
plano con `Signal:`, `User:`, `CPU:`, `Detail:` en inglés en español.

### 2.2 Hallazgos

| Id | Hallazgo | Evidencia | Gravedad |
|---|---|---|---|
| N1 | El commit se repite (título y cuerpo) | `deploy_success` | media |
| N2 | El resultado se repite 3-4 veces en fallo y rollback | rollback: título, `Release … active again`, `The application did not answer…`, `Health check attempts…` | alta |
| N3 | Título y primera línea del cuerpo idénticos en copias/restauraciones (`str(NoustError)` con rótulo `  Details:` interno filtrado al usuario) | `backup_*`, `restore_failed` | alta |
| N4 | Muro de journal: 40 líneas (4.5 kB) sin priorizar, cuatro ciclos repetidos | rollback y fallo | alta |
| N5 | El truncado corta por el final y pierde el enlace y mitad de una línea; marcador `... (truncated)` sin traducir | Telegram 4096, Discord 2000 | alta |
| N6 | Restauración enviada como `deploy_success/failed`; actualizar, revertir, activar y migrar salen como "deployed" | `web/server.py:475`, recorder sin `operation` | alta |
| N7 | Certificados: días negativos, `day(s)`, concordancia rota, sin estado "caducado" | `cert_expired_80d` | media |
| N8 | Sin nombre de servidor en ningún canal (en flota los mensajes de dos nodos son idénticos); sin enlace en nodos | todos | alta (flota) |
| N9 | Frases de Noust en inglés dentro de mensajes en español: `did not pass its health check`, `systemd reports … failed: result exit-code, main process exit status 1`, `Backup of … failed`, `Failed to upload the backup…`, `Health check attempts 1-15 failed`, `Details:`; origen crudo (`webhook`, `cli`, `panel`); `el canal telegram` | `es/*` | media |
| N10 | Correo: HTML sin estructura, saltos perdidos, URL sin enlace, sin marca; sin `Date`, `Message-ID`, `Auto-Submitted`, sin nombre en `From`; texto en base64 | correo | alta |
| N11 | Dos implementaciones de correo (canal de eventos y observaciones del monitor), dos sitios de configuración (`notifications.channels.email.enabled` y `monitor.smtp.*`), dos mensajes de prueba | `email_notifier.py:318,425` | media (regla 3) |
| N12 | Cifras inconsistentes y frases largas: 92 % / 91.7 %; `backup_schedule_missing` es un muro de 345 caracteres con jerga de versión ("as 2.1 took it") y se envía como `backup_failed` aunque no es un fallo | `disk_threshold`, `backup_schedule_missing` | baja |
| N13 | Telegram sin `parse_mode`: sin negrita, sin monoespaciado para la salida, con vista previa del enlace de la consola (no se desactiva) | `_telegram_request` | media |
| N14 | Escapes: Slack `&<>` bien; Telegram y Discord sin formato que interpretar (hoy no hay error de escape porque no hay formato); en cuanto se use HTML de Telegram hay que escapar `<urlopen error …>` que ya está en la evidencia; el texto de Slack va como mrkdwn, así que `_` `*` `~` de un journal o de un nombre de rama se interpretan | evidencia con `<urlopen error …>` | latente |
| N15 | Duplicado potencial entre incidentes: el monitor no consulta si hay un despliegue en curso, así que un rollback puede ir seguido de un `unit_failed` de la misma unidad (por lectura del código; no reproducido en un servidor real) | `process_monitor.py` sin referencia a despliegues | por confirmar |

---

## 3. Plataformas y referentes

### 3.1 Telegram (Bot API, `sendMessage`)

Fuente: <https://core.telegram.org/bots/api> (secciones *Formatting options* y *sendMessage*); límites
de envío: <https://core.telegram.org/bots/faq>.

- `parse_mode=HTML`: etiquetas `b/strong`, `i/em`, `u/ins`, `s/strike/del`, `span class="tg-spoiler"`,
  `a href`, `code`, `pre` (con `code class="language-…"`), `blockquote` (y expandible), `tg-emoji`.
  Hay que escapar `<`, `>` y `&` como `&lt; &gt; &amp;`; solo se soportan las entidades con nombre
  `&lt; &gt; &amp; &quot;` y todas las numéricas.
- Anidado: "pre and code entities can't contain other entities"; negrita, cursiva, subrayado,
  tachado y spoiler pueden contener cualquier otra salvo `pre` y `code`; las citas no se anidan.
- MarkdownV2 exige escapar 18 caracteres (`_ * [ ] ( ) ~ ` > # + - = | { } . !`). **HTML necesita
  escapar tres**: es el modo elegido (regla 4: el escape lo hace el motor de plantillas, no la memoria).
- Límite: "1-4096 characters after entities parsing", es decir, se cuenta el texto ya sin etiquetas y
  con las entidades como un carácter.
- `link_preview_options: {"is_disabled": true}` apaga la vista previa; `disable_notification`
  hace el aviso silencioso; `reply_markup.inline_keyboard` admite botones con `url`.
- Ritmo: un mensaje por segundo por chat, 20 por minuto en un grupo, unos 30 por segundo en difusión.
- Si Telegram no puede interpretar las entidades responde 400 `can't parse entities`: la
  recomendación es reintentar **una vez** en texto plano para no perder nunca una alerta.

### 3.2 Slack (webhook entrante, Block Kit)

Fuentes: <https://docs.slack.dev/reference/block-kit/blocks>,
<https://docs.slack.dev/messaging/formatting-message-text>,
<https://docs.slack.dev/reference/methods/chat.postMessage>,
<https://docs.slack.dev/legacy/legacy-messaging/legacy-secondary-message-attachments>,
<https://docs.slack.dev/reference/block-kit/block-elements/button-element>.

- Hasta 50 bloques por mensaje. `header`: texto `plain_text` de 150 caracteres como máximo.
  `section`: texto hasta 3000 y hasta 10 `fields` de 2000. `context`: hasta 10 elementos.
- mrkdwn: `*negrita*`, `_cursiva_`, `~tachado~`, `` `código` ``, bloque con tres acentos graves, `>` cita,
  enlaces `<url|texto>`. Hay que convertir `&`, `<`, `>` en entidades (mismo escape que ya hace el
  código) y no existe un escape para `*` `_` `~`: los valores no fiables (rama, mensaje de commit,
  línea de journal) se neutralizan con un espacio de ancho cero tras el carácter de control, igual que ya
  se hace con `@everyone` en Discord.
- Con `blocks`, el `text` superior se usa solo como texto de respaldo de las notificaciones. Se
  recomienda no pasar de 4000 caracteres (Slack trunca a partir de 40 000; el módulo cita este segundo
  número, el primero es el que importa para que el mensaje se lea).
- Los `attachments` son "legacy" ("we recommend you stick with layout blocks") pero siguen admitiendo
  `color` (franja lateral), `fallback` y `blocks` dentro. Es la única forma de dar color a un mensaje de
  webhook. Lo usan Uptime Kuma y Grafana (esta última sin bloques).
- Un botón con `url` sigue generando una carga de interacción que hay que reconocer ("you'll still
  receive an interaction payload and will need to send an acknowledgement response"): en una app de
  solo webhook no hay quien la reciba. **No usar botones; un enlace mrkdwn en un bloque `context`.**
- Un mensaje por segundo por canal aproximadamente.

### 3.3 Discord (webhook, embeds)

Fuentes: <https://docs.discord.com/developers/resources/message> y
<https://docs.discord.com/developers/resources/webhook#execute-webhook>.

- `content` hasta 2000. Hasta 10 embeds. Embed: `title` 256, `description` 4096, hasta 25 `fields`
  (`name` 256, `value` 1024), `footer.text` 2048, `author.name` 256, y **6000 caracteres en total**
  entre título, descripción, campos, pie y autor de todos los embeds del mensaje.
- `color` es un entero, `timestamp` ISO 8601, `url` convierte el título en enlace. Markdown funciona
  en `description` y en los valores de campo, no en el título.
- `allowed_mentions` (`parse`, `roles`, `users`) y `flags` (`SUPPRESS_EMBEDS`,
  `SUPPRESS_NOTIFICATIONS` = `1 << 12`).
- Sin verificar: qué texto muestra la notificación push móvil de un mensaje solo de embed. Se
  comprobará en la galería en vivo.

### 3.4 Correo

Fuentes: RFC 2046 §5.1.4 (`multipart/alternative`: la parte preferida va **la última**, así que
texto plano primero y HTML después);
<https://developers.google.com/workspace/gmail/design/css> (Gmail admite `<style>` en `<head>` con
selectores de clase, elemento e ID y consultas de medios de anchura, y no dice nada de modo oscuro);
la app de Gmail con cuentas no Google (GANGA) **no** aplica `<style>` incrustado, por lo que el CSS
esencial va en línea; Gmail recorta el HTML de más de 102 KB; Outlook de escritorio usa el motor de
Word (sin `border-radius`, flex ni grid; maquetar con tablas; según fuentes secundarias Microsoft
prevé dejarlo en octubre de 2026, por confirmar). Modo oscuro: `<meta name="color-scheme"
content="light dark">` y `@media (prefers-color-scheme: dark)`; las apps de Gmail suelen aplicar su
propia inversión, así que el diseño no debe depender de blanco y negro puros. RFC 5322: el mensaje
debe llevar `Date` y `From`, y `Message-ID` es recomendable; RFC 3834 / registro IANA:
`Auto-Submitted: auto-generated` marca un mensaje automático (Proxmox lo añade en su cliente SMTP:
<https://lists.proxmox.com/pipermail/pve-devel/2024-January/061300.html>).

Lo que hoy genera `EmailNotifier._send` (`MIMEMultipart("alternative")` + `MIMEText`), comprobado:
sin `Date`, sin `Message-ID`, `From` sin nombre, partes en base64, asunto RFC 2047 correcto.
`email.message.EmailMessage` con `policy.SMTP`, `set_content` y `add_alternative` da quoted-printable
para el texto y permite añadir `Date`, `Message-ID` y `Auto-Submitted` en dos líneas; sigue siendo
biblioteca estándar, sin dependencias nuevas.

### 3.5 Webhook genérico

Referentes de esquema estable: Vercel (`type`, `id`, `createdAt`, `payload{…}`, firma HMAC en
cabecera; <https://vercel.com/docs/webhooks/webhooks-api>), Standard Webhooks (cabeceras
`webhook-id`, `webhook-timestamp`, `webhook-signature`; cuerpo con `type`, `timestamp`, `data`; tipos
`a.b.c`; <https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md>) y
CloudEvents 1.0 (`id`, `source`, `specversion`, `type` obligatorios; `time`, `subject`, `data`
opcionales; <https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md>). El contrato actual
de Noust (`event`, `title`, `body`, `domain`, `ts`) está documentado en el docstring del módulo y en
tests; romperlo dentro de 3.x no tiene justificación, así que la propuesta es **aditiva**.

### 3.6 Cómo lo hacen las herramientas de referencia

Verificado leyendo su código o su documentación:

| Herramienta | Qué hace bien | Qué copiar |
|---|---|---|
| **Uptime Kuma** (`server/notification-providers/telegram.js`, `discord.js`, `slack.js`) | Telegram: `link_preview_options.is_disabled = true`, plantillas con modo HTML o MarkdownV2 y escape propio. Discord: embed con título con estado, color verde/rojo, campos (servicio, URL, hora, error), `timestamp`. Slack: `header`, `section` con dos campos (mensaje y hora), botones, y `attachments[].color` con los bloques dentro | Estado en el título; color = estado; campos cortos; sin vista previa; franja de color en Slack |
| **Coolify** (`app/Notifications/Application/DeploymentFailed.php`) | Un mensaje por canal a partir de los mismos datos (proyecto, entorno, nombre, dominio). Telegram: una línea de texto y **un botón "Deployment logs"** con la URL; Discord: título con estado, color, campos y enlace a los logs; asunto de correo `Coolify: Deployment failed of <app>.` | Botón de enlace en Telegram; el enlace lleva a los logs, no se pegan los logs |
| **Vercel** (webhooks) | Contrato estable y pequeño: `type` (`deployment.error`, `deployment.succeeded`…), `id` de la entrega, `createdAt`, `payload` con `links.deployment`, `target` | Eventos con nombre jerárquico y un `id` por entrega; los enlaces como datos |
| **Grafana Alerting** | Un modelo (estado `firing`/`resolved`, etiquetas, anotaciones `summary`/`description`, `StartsAt`/`EndsAt`, URLs de silencio y de panel) y plantillas por contacto; color de la franja lateral configurable; los contactos de Slack usan attachments | Estado como dato de primera clase, mensajes de resuelto, una frase de resumen separada de los detalles |
| **Sentry** | Solo se consultó que sus alertas de Slack llevan botones de acción (Resolver, Archivar, Asignar) y que unfurlean sus URLs | Nada aún; no se verificó su maquetación |
| **GitHub Actions** | No consultado | — |

Conclusión común: un modelo de datos, un renderizador por canal, el estado visible en la primera
línea, color solo donde el canal lo permite, el enlace como botón o campo y nunca los logs enteros.

---

## 4. Diseño propuesto

### 4.1 Principios

1. **Un solo `Notification`** estructurado (regla 3: hoy el "qué se dice" se decide en cuatro
   sitios y el "cómo se ve" en un `f"{title}\n{body}"`). Los emisores describen hechos; los
   renderizadores los maquetan; nadie concatena texto para un canal.
2. **El estado va primero**, en color, forma y palabra (la regla del panel: cada estado se cuenta de
   tres maneras para que sobreviva a la escala de grises).
3. **Una línea de lo ocurrido**, hechos cortos, **un** comando siguiente, un enlace, y la salida del
   sistema solo como extracto literal acotado (regla del panel: un error del sistema nunca se
   parafrasea).
4. **Lo importante nunca se corta**: cabecera, hechos y enlace tienen presupuesto fijo; solo el
   extracto cede espacio.
5. **Cada canal con su formato nativo**, con el escape en un único sitio por canal (regla 4).
6. **Traducir lo que Noust dice, dejar literal lo que dicen otros programas**, con la frontera en
   la estructura (`summary` y etiquetas vs `excerpt`), no en el texto.
7. **Nada de emoji**; glifos geométricos de texto.

### 4.2 Modelo

Solo biblioteca estándar; vive en módulos nuevos (`core/notification.py` modelo y extracto,
`core/notification_render.py` renderizadores puros, `core/notification_compose.py` compositores) y
`core/notifier.py` queda como transporte (SSRF, plazos, cola, filtros). `NotificationEvent` se
conserva como envoltorio de compatibilidad (`Notification.from_event`) para migrar emisor a emisor
y una prueba de arquitectura exige que al final de 3.1 nadie lo construya fuera del notificador.

```python
class State(str, Enum):           # (str, Enum): el repositorio soporta 3.10
    OK = "ok"; PROGRESS = "progress"; WARNING = "warning"; FAILED = "failed"; INFO = "info"

@dataclass(frozen=True)
class Fact:     key: str; label: str; value: str; mono: bool = False      # key estable, label traducido
@dataclass(frozen=True)
class Link:     rel: str; label: str; url: str                            # rel="console"
@dataclass(frozen=True)
class Excerpt:  label: str; lines: tuple[str, ...]; omitted: int = 0      # líneas enteras, literales

@dataclass(frozen=True)
class Notification:
    kind: str                # interruptor (EVENT_KINDS): "deploy_rolled_back"
    code: str                # estable y fino: "deploy.rolled_back", "backup.upload_failed"
    state: State
    locale: Locale
    title: str               # frase de estado: "Rolled back"          (traducida)
    subject: str             # de qué va: dominio, unidad, punto de montaje, canal de prueba
    summary: str             # una frase                                (traducida)
    server: str              # nombre del servidor/nodo, siempre
    facts: tuple[Fact, ...] = ()
    command: Fact | None = None      # LA orden siguiente (mono)
    excerpt: Excerpt | None = None
    links: tuple[Link, ...] = ()
    ts: datetime = ...       # UTC
    id: str = ...            # uuid4: `id` del webhook y base del Message-ID
```

Los **compositores** (uno por `code`) reciben datos estructurados (`DeployEvent`, `CertificateInfo`,
`DiskUsage`, `ServiceHealth`, la excepción) más `Locale` y la identidad del servidor, y devuelven un
`Notification`. Cuestan lo mismo que hoy porque las frases ya están en `messages.py`; lo que cambia
es que se parten en piezas (título de estado, resumen, etiquetas) en lugar de frases largas con
huecos (`unit_failed_body`, `cert_expiring_body`, `disk_threshold_body` desaparecen).

**Extracto** (`make_excerpt`, probado en el prototipo): solo líneas enteras y literales; se quitan
secuencias ANSI y caracteres de control (no es reformular); cola por defecto (la causa de un fallo
está al final del journal); máximo 8 líneas y 1000 caracteres en chat, 12 y 1200 en correo y webhook;
línea de más de 200 caracteres cortada con `…`; marcador traducido `N líneas anteriores omitidas`.
La fuente es `NoustError.output` si existe y si no `.details`. El `.message` del error se descarta de
chat y correo (el resumen de Noust, traducido, lo sustituye) y viaja en el JSON del webhook.

### 4.3 Estados y glifos (decisión: texto, sin emoji)

| Estado | Glifo | Forma en el panel | Color (claro / oscuro, tokens del panel) | Embed / franja | ¿Aviso sonoro? |
|---|---|---|---|---|---|
| `ok` | `●` U+25CF | punto (`running`) | `#16784a` / `#4dc47e` | `#4dc47e` | no |
| `progress` | `◐` U+25D0 | arco (`deploying`) | `#8c5800` / `#e3a73c` | `#e3a73c` | no |
| `warning` | `▲` U+25B2 | triángulo (`warning`) | `#8c5800` / `#e3a73c` | `#e3a73c` | sí |
| `failed` | `✕` U+2715 | cruz (`failed`) | `#c12c24` / `#ff736a` | `#cf3a30` | sí |
| `info` | `○` U+25CB | anillo (`stopped`) | `#636363` / `#9e9e9e` | `#9e9e9e` | no |

Comprobado contra `emoji-data.txt` de Unicode 15.1: `25B2`, `25CF`, `25CB`, `25D0`, `25A0`, `2715`,
`2713`, `21BA` **no** tienen la propiedad Emoji; `2714`, `2716`, `26A0`, `274C`, `2705`, `25B6`, `25C0`
sí (se renderizarían como emoji a color). Un test fija una lista de prohibidos. Los colores son los
tokens de `panel/src/styles/tokens.css`, así el correo y las franjas coinciden con la consola.
Telegram no admite color: la forma y la palabra cargan con el estado (la misma regla del panel).
La alternativa (emoji `🟢🟡🔴` en Telegram) es la decisión D1.

Correspondencia código a estado: `deploy.started` progress; `deploy.succeeded` ok;
`deploy.failed` failed; `deploy.rolled_back` warning (la app sigue arriba, con la versión anterior);
`restore.completed` ok; `restore.failed` failed; `backup.failed` failed; `backup.upload_failed`
warning (queda la copia local); `backup.schedule_missing` warning; `cert.expiring` warning;
`cert.expired` failed; `unit.failed`, `unit.crash_loop`, `unit.stopped_on_failure` failed;
`disk.threshold` warning; `test` info.

### 4.4 Taxonomía de eventos

Se mantienen los 8 interruptores existentes y se corrigen los errores de clasificación con el
mínimo de interruptores nuevos. `code` separa lo fino (`deploy.rolled_back`) de lo que el operador
enciende y apaga (`kind`).

| Cambio | Detalle | Por qué |
|---|---|---|
| Nuevos `restore_success`, `restore_failed` | dejan de viajar como `deploy_*` | N6 |
| `DeployEvent.operation` (`deploy`, `update`, `rollback`, `activate`, `migrate`) | cambia el título ("Deployed", "Updated", "Reverted to release X", "Release activated", "Migrated to releases"); mismo `kind` | N6 |
| `cert_expiring` con estado `failed` y código `cert.expired` cuando los días son negativos | "Expired 80 days ago" en vez de "expires in -80 day(s)" | N7 |
| `backup_failed` con tres `code` y estados distintos | fallo (failed), subida fallida (warning), programación ausente (warning, y sin jerga de versión) | N12 |
| Recuperación: `unit.recovered`, `disk.recovered` bajo el interruptor del fallo (`unit_failed`, `disk_threshold`) | el monitor ya guarda las transiciones (`_failed_units`, `_alerted_disks`); un mensaje `ok` cierra el aviso | huecos |
| Opcional `backup_success` (desactivado por defecto) | latido diario | huecos |
| Fase 2 de flota: `node_unreachable`, `node_recovered`, `node_host_key_changed` | eventos de la central | huecos |

Los kinds nuevos son cuatro sitios que deben coincidir: `notifier.EVENT_KINDS`,
`DEFAULT_CONFIG["notifications"]["events"]`, `panel/src/features/settings/notifications.ts`
(`EVENT_KINDS` y etiquetas i18n) y la documentación; el test de acuerdo que ya existe (de
`tests/test_notifier.py`) cubre los dos primeros y habría que extenderlo al panel.

### 4.5 Disposición por canal

Todos los ejemplos están **generados por el prototipo** con los límites verificados
(`/tmp/noust-notif-proposed/`). Datos: `shop.example.com` en el servidor `web-1`, enlace bajo
`https://console.example.com/n/web-1/…`.

#### Telegram

`parse_mode: "HTML"`, `link_preview_options.is_disabled: true`, `disable_notification` para
`ok`/`progress`/`info`, y un botón inline (`reply_markup`) como el "Deployment logs" de Coolify en lugar
de pegar la URL. Una línea de estado con glifo, negrita y sujeto; una frase; hechos en `Etiqueta: valor`
(el servidor siempre el primero); comando en `<code>`; extracto en `<pre>` con etiqueta en cursiva.
Presupuesto: lo fijo cabe en unos 1500 caracteres con todos los valores acotados, el extracto tiene
tope propio; **el límite de 4096 no se puede alcanzar** (el test con Hypothesis lo demuestra, la
salvaguarda recorta primero el extracto). Escape: una función `esc()` (`html.escape(quote=False)`)
en el texto y `html.escape(quote=True)` en `href`, solo enlaces `http(s)`. Si Telegram responde 400
`can't parse entities` se reenvía una vez en texto plano.

Desplegado (inglés, silencioso):

```
● <b>Deployed</b> · shop.example.com
The new release is live.

<b>Server:</b> web-1
<b>Commit:</b> a1b2c3d (main): Fix cart total
<b>Started by:</b> Webhook
<b>Duration:</b> 42 s
[botón: Open in the console]
```

Desplegado (español, silencioso):

```
● <b>Desplegado</b> · shop.example.com
La nueva versión ya está en servicio.

<b>Servidor:</b> web-1
<b>Commit:</b> a1b2c3d (main): Fix cart total
<b>Iniciado por:</b> Webhook
<b>Duración:</b> 42 s
[botón: Abrir en la consola]
```

Revertido (inglés, con aviso; el extracto es el mismo que hoy pero de 8 líneas y sin la hora y el
host del journal, ver D3):

```
▲ <b>Rolled back</b> · shop.example.com
The new release did not answer its health check, so the previous one is serving again.

<b>Server:</b> web-1
<b>Release:</b> 20260929-104449
<b>Commit:</b> a1b2c3d (main): Rework checkout
<b>Started by:</b> Webhook
<b>Duration:</b> 1 min 12 s

<i>Journal of shop-example-com, last 8 lines</i>
<pre>systemd[1]: Started shop-example-com.service - Noust: shop.example.com (nextjs).
npm[48455]: &gt; shop@1.4.2 start
npm[48455]: &gt; next start -p 3004
npm[48455]: Error: Could not find a production build in the '.next' directory. Try building your app with 'next build' before starting th…
systemd[1]: shop-example-com.service: Main process exited, code=exited, status=1/FAILURE
systemd[1]: shop-example-com.service: Failed with result 'exit-code'.
systemd[1]: shop-example-com.service: Scheduled restart job, restart counter is at 4.
systemd[1]: Started shop-example-com.service - Noust: shop.example.com (nextjs).
… 35 earlier lines omitted</pre>
[botón: Open in the console]
```

Revertido (español):

```
▲ <b>Revertido</b> · shop.example.com
La nueva versión no ha respondido a la comprobación de salud, así que la anterior vuelve a estar en servicio.

<b>Servidor:</b> web-1
<b>Versión:</b> 20260929-104449
<b>Commit:</b> a1b2c3d (main): Rework checkout
<b>Iniciado por:</b> Webhook
<b>Duración:</b> 1 min 12 s

<i>Registro de shop-example-com, últimas 8 líneas</i>
<pre>… (las mismas 8 líneas, literales) …
… 35 líneas anteriores omitidas</pre>
[botón: Abrir en la consola]
```

Certificado (inglés / español):

```
▲ <b>Certificate expiring</b> · shop.example.com
Expires in 7 days (6 Oct 2026).

<b>Server:</b> web-1
<b>Covers:</b> shop.example.com, www.shop.example.com
<b>Renew with:</b> <code>noust cert renew shop.example.com</code>
[botón: Open in the console]
```

```
▲ <b>Certificado a punto de caducar</b> · shop.example.com
Caduca en 7 días (6 oct 2026).

<b>Servidor:</b> web-1
<b>Cubre:</b> shop.example.com, www.shop.example.com
<b>Renuévalo con:</b> <code>noust cert renew shop.example.com</code>
[botón: Abrir en la consola]
```

Servicio en bucle de reinicios (inglés / español):

```
✕ <b>Service crash-looping</b> · shop.example.com
systemd restarted it 3 times since the last check and it still does not stay up.

<b>Server:</b> web-1
<b>Unit:</b> <code>shop-example-com</code>
<b>Restarts:</b> 9
<b>Inspect with:</b> <code>journalctl -u shop-example-com -n 50</code>

<i>Journal of shop-example-com, last 5 lines</i>
<pre>npm[48455]: &gt; next start -p 3004
npm[48455]: Error: listen EADDRINUSE: address already in use :::3004
systemd[1]: shop-example-com.service: Main process exited, code=exited, status=1/FAILURE
systemd[1]: shop-example-com.service: Failed with result 'exit-code'.
systemd[1]: shop-example-com.service: Scheduled restart job, restart counter is at 9.</pre>
[botón: Open in the console]
```

```
✕ <b>Servicio en bucle de reinicios</b> · shop.example.com
systemd lo ha reiniciado 3 veces desde la última comprobación y sigue sin arrancar.

<b>Servidor:</b> web-1
<b>Unidad:</b> <code>shop-example-com</code>
<b>Reinicios:</b> 9
<b>Revísalo con:</b> <code>journalctl -u shop-example-com -n 50</code>

<i>Registro de shop-example-com, últimas 5 líneas</i>
<pre>… (las mismas 5 líneas, literales) …</pre>
[botón: Abrir en la consola]
```

Prueba: `○ <b>Test notification</b> · Telegram` / `If you can read this, the Telegram channel is configured correctly.`
y `○ <b>Notificación de prueba</b> · Telegram` / `Si ves esto, el canal Telegram está bien configurado.`,
ambos con `Server: web-1` y sin botón.

#### Slack

`attachments: [{color, fallback, blocks}]` sin `text` superior (la franja lateral es la única forma de
color en un webhook y el `fallback` cubre la notificación móvil). Bloques: `header` (glifo, estado y
sujeto, 150 máx.), `section` con la frase, `section` con `fields` (dos columnas, servidor primero),
`section` con el comando, `section` con el extracto en bloque de código, y `context` con el
enlace `<url|Open in the console>` y `Noust · web-1`. Sin botones (ver 3.2). Valores no fiables con
`&<>` escapados y espacio de ancho cero tras `* _ ~ `` `; extracto con el bloque de código sin poder
cerrarse a sí mismo (las tres comillas invertidas del contenido se sustituyen). Presupuesto:
cabecera 150, secciones 3000, extracto acotado a 2500.

Revertido (inglés; recortado el extracto):

````json
{"attachments": [{
  "color": "#e3a73c",
  "fallback": "▲ Rolled back · shop.example.com",
  "blocks": [
    {"type": "header", "text": {"type": "plain_text", "text": "▲ Rolled back · shop.example.com"}},
    {"type": "section", "text": {"type": "mrkdwn", "text": "The new release did not answer its health check, so the previous one is serving again."}},
    {"type": "section", "fields": [
      {"type": "mrkdwn", "text": "*Server*\nweb-1"},
      {"type": "mrkdwn", "text": "*Release*\n20260929-104449"},
      {"type": "mrkdwn", "text": "*Commit*\na1b2c3d (main): Rework checkout"},
      {"type": "mrkdwn", "text": "*Started by*\nWebhook"},
      {"type": "mrkdwn", "text": "*Duration*\n1 min 12 s"}]},
    {"type": "section", "text": {"type": "mrkdwn", "text": "_Journal of shop-example-com, last 8 lines_\n```Sep 29 10:45:28 web-1 systemd[1]: Started …\n… 35 earlier lines omitted```"}},
    {"type": "context", "elements": [
      {"type": "mrkdwn", "text": "<https://console.example.com/n/web-1/apps/shop.example.com/deployments/42|Open in the console>"},
      {"type": "mrkdwn", "text": "Noust · web-1"}]}
  ]}]}
````

En español cambian solo los textos (`▲ Revertido · shop.example.com`, la frase, `*Servidor*`,
`*Versión*`, `*Iniciado por*`, `*Duración*`, `_Registro de shop-example-com, últimas 8 líneas_`,
`<url|Abrir en la consola>`). Los otros tres eventos siguen la misma plantilla
(`/tmp/noust-notif-proposed/{en,es}/*/slack.json`).

#### Discord

Un embed: `title` con glifo, estado y sujeto (256), `url` = consola (el título es el enlace, no se
repite), `description` = frase + comando + extracto en bloque de código (4096), `color` entero con el
color del estado, `fields` en línea (servidor primero), `footer` `Noust · web-1`, `timestamp` ISO 8601.
Se conserva `allowed_mentions: {parse: []}` y la defusión de `@everyone`; los valores no fiables se
escapan con barra invertida (Discord sí la admite). `flags: 4096` (`SUPPRESS_NOTIFICATIONS`) para
`ok`/`progress`/`info`. Presupuesto: suma de título, descripción, campos y pie por debajo de 6000
(comprobado: el mayor de los ejemplos suma 1691 caracteres de JSON).

```json
{"embeds": [{
  "title": "▲ Rolled back · shop.example.com",
  "url": "https://console.example.com/n/web-1/apps/shop.example.com/deployments/42",
  "description": "The new release did not answer its health check, so the previous one is serving again.\n\n*Journal of shop-example-com, last 8 lines*\n```\nSep 29 10:45:28 web-1 systemd[1]: Started …\n… 35 earlier lines omitted\n```",
  "color": 14919484,
  "fields": [
    {"name": "Server", "value": "web-1", "inline": true},
    {"name": "Release", "value": "20260929-104449", "inline": true},
    {"name": "Commit", "value": "a1b2c3d (main): Rework checkout", "inline": true},
    {"name": "Started by", "value": "Webhook", "inline": true},
    {"name": "Duration", "value": "1 min 12 s", "inline": true}],
  "footer": {"text": "Noust · web-1"},
  "timestamp": "2026-09-29T10:45:51+00:00"}],
 "allowed_mentions": {"parse": []}}
```

#### Correo

`multipart/alternative` con la parte de texto primero y el HTML al final. Cabeceras: `Subject`
`[Noust] <Estado>: <sujeto> (<servidor>)` (estado primero; el prefijo `[Noust]` se conserva para
las reglas de filtrado que ya exista; sin glifo ni emoji en el asunto), `From` con nombre ASCII
(`Noust (web-1)`, sin `·` que obliga a RFC 2047), `Date`, `Message-ID`, `Auto-Submitted:
auto-generated`, `X-Auto-Response-Suppress: All`, y `X-Noust-Event`/`X-Noust-Server` para filtrar.
Opcional: `In-Reply-To`/`References` para que `deploy_started` y su desenlace formen un hilo.

Texto plano (parte propia, no el texto del chat), rollback en español:

```
▲ Revertido · shop.example.com
La nueva versión no ha respondido a la comprobación de salud, así que la anterior vuelve a estar en servicio.

Servidor: web-1
Versión: 20260929-104449
Commit: a1b2c3d (main): Rework checkout
Iniciado por: Webhook
Duración: 1 min 12 s

Registro de shop-example-com, últimas 8 líneas:
  Sep 29 10:45:28 web-1 systemd[1]: Started shop-example-com.service - Noust: shop.example.com (nextjs).
  … (8 líneas literales, con su fecha y host, el correo no recorta el journal) …
  35 líneas anteriores omitidas

Abrir en la consola: https://console.example.com/n/web-1/apps/shop.example.com/deployments/42

-- 
Enviado por Noust · web-1 · 2026-09-29 10:45 UTC
Cambia lo que recibes en Ajustes > Notificaciones
```

HTML: tablas y CSS en línea para todo lo esencial (Gmail en cuentas no Google ignora `<style>`),
`max-width` 600, fuente del sistema, `<meta name="color-scheme" content="light dark">` y un bloque
`@media (prefers-color-scheme: dark)` con los tokens oscuros del panel (fondo `#111111`, tarjeta
`#171717`, texto `#ededed`, acento `#7152de`); sin depender de blanco y negro puros para resistir
la inversión forzada de las apps de Gmail. Tarjeta con barra superior de 4 px en el color del
estado; línea de estado (glifo + palabra en el color del estado), sujeto en 22 px, frase, tabla de
hechos (etiqueta gris, valor), extracto en `<pre>` con fondo hundido, botón "Abrir en la consola"
(celda con `bgcolor` y enlace en bloque, válido en Outlook) y pie con servidor, hora UTC y a dónde
ir para cambiar lo que se recibe. Texto de vista previa oculto con la frase (preheader). Sin imágenes
remotas. La URL aparece **solo** en la parte de texto (no se duplica bajo el botón). Tamaño de los
ejemplos: 3.3-6.4 kB, lejos de los 102 KB donde Gmail recorta. Capturas en claro y oscuro:
`/tmp/noust-notif-proposed/mocks/*-email-{light,dark}.png`.

Logo: la marca dice "no reescribir noust con una tipografía; usar el fichero del wordmark" y las
imágenes remotas y los SVG no valen en correo. El prototipo usa un texto `noust` como cabecera
provisional. Lo correcto es el wordmark en PNG (2x) con la tinta en un gris medio que se lea
sobre blanco y sobre `#111` y el casco en violeta, incrustado con `cid:` (`multipart/related`
dentro de la alternativa), con `alt="noust"`. Decisión D4.

#### Webhook JSON (v1, aditivo)

Las claves actuales se conservan con su significado; se añaden campos y una versión. Contrato:
las claves de `version: 1` no se eliminan ni cambian de tipo; pueden aparecer claves nuevas
opcionales; un consumidor debe ignorar `event`/`code` que no conozca. `event` es la clave estable
(el `title` deja de ser una frase parseable: pasa a `Estado: sujeto`).

| Clave | Tipo | Notas |
|---|---|---|
| `version` | entero | `1` |
| `id` | cadena (uuid4) | una por entrega; permite deduplicar en el receptor |
| `event` | cadena | el `kind` de siempre (interruptor) |
| `code` | cadena | fino y estable: `deploy.rolled_back`, `cert.expired`… |
| `state` | `ok`/`progress`/`warning`/`failed`/`info` | |
| `locale` | `en`/`es` | idioma de `title`, `summary` y `facts[].label` |
| `title` | cadena | `Rolled back: shop.example.com` |
| `summary` | cadena | una frase |
| `body` | cadena | **heredado**: texto plano de resumen, hechos, extracto y enlace, sin la línea de título |
| `domain` | cadena o null | como hoy |
| `server` | cadena | nombre del servidor/nodo |
| `ts` | cadena | ISO 8601 UTC con `Z` |
| `facts` | lista de `{key, label, value}` | `key` estable (`commit`, `duration`, `release`, `trigger`, `server`…) |
| `links` | lista de `{rel, label, url}` | `rel: "console"` |
| `excerpt` | objeto o null | `{label, lines[], omitted}`; líneas literales |

Ejemplo real generado: `/tmp/noust-notif-proposed/en/deploy_rolled_back/webhook.json`. Una firma HMAC
(cabeceras al estilo Standard Webhooks) queda fuera de 3.1 y, si se añade, será la ocasión de pasar a
`version: 2` con el sobre `type`/`timestamp`/`data`.

### 4.6 Reglas de deduplicación (todas comprobables)

| Regla | Enunciado | Cómo se comprueba |
|---|---|---|
| D-1 | El título es **estado + sujeto** y nada más: no lleva commit, rama, servidor ni duración (viven en los hechos) | test de composición: el título no contiene el valor de ningún hecho |
| D-2 | El resumen es una sola frase y no repite el estado ni ningún valor de hecho (nada de "Deploy failed" bajo "Deploy failed") | idem, con normalización |
| D-3 | El `message` de una `NoustError` no se muestra si el compositor tiene resumen propio; si aporta algo nuevo, va como **primera línea del extracto**, nunca como párrafo | test con las excepciones reales de backup, restauración y health gate |
| D-4 | El extracto no contiene ninguna línea igual (normalizada) al resumen, a un hecho o al comando; las líneas idénticas consecutivas se pliegan | test sobre el catálogo de ejemplos |
| D-5 | El servidor aparece **una vez** en el cuerpo (hecho `Server`); asunto, pie y nombre del remitente del correo son sobre, no cuerpo | test por canal con lista de excepciones de sobre |
| D-6 | El enlace aparece **una vez** por mensaje: Telegram botón, Slack `context`, Discord URL del título, correo botón (más la parte de texto) | test por canal |
| D-7 | Un hecho que el extracto ya lleva se omite (`Last exit` cuando hay journal) | revisión + test del catálogo |
| D-8 | Invariante global por canal: al quitar el marcado y partir en líneas, ninguna línea no trivial se repite | **el mismo test para los 5 canales y todo el catálogo** |
| D-9 | Entre mensajes: un incidente, un aviso. `deploy_started` sigue apagado por defecto; el `unit_failed` de una unidad cuya app tiene un despliegue en curso o terminado hace menos de N segundos se suprime (por confirmar, N15) | test del monitor con un candado de app simulado |

### 4.7 Cambios de código necesarios (mapa)

| Área | Cambio |
|---|---|
| `deployers/deploy_events.py` `DeployEvent` | campos con valor por defecto: `operation`, `duration_s`, `commit_message`, `release_id`, `error_message`, `error_output` (además de `error`, que se conserva para GitHub statuses) |
| `deployers/recorder.py` | recibe `operation` en la construcción; en `finish_failure` separa `NoustError.message` de `output or details` y los pasa por el `scrubber` por separado; la duración ya la calcula `finish_deployment` (`duration_s` en la fila) |
| Health gate y los sitios que levantan `RolledBackError`/`DeploymentError` con su evidencia (`base.py:2031`, `lifecycle.py:1293`, `bluegreen.py`, `monorepo.py`, `docker_compose.py`) | poner el journal en `output=` y dejar en `details=` el resumen de probes; `web/jobs.py:88` ya concatena `mensaje + output`, así que la consola no pierde nada (revisar los tests que miran `details`) |
| `web/server.py`, `managers/backup_scheduler.py`, `monitor/process_monitor.py` | usan los compositores; restauración con sus kinds; el monitor lee `server.name` una vez |
| `core/messages.py` | claves para títulos de estado, resúmenes, etiquetas de hechos y pies, con la paridad en/es ya comprobada; sale el enum crudo (`webhook` a `Webhook`), se traduce "Started by" |
| `core/notifier.py` | renderizadores puros; `_message_text` y `_MESSAGE_LIMITS` se sustituyen por el presupuesto por renderizador; Telegram con `parse_mode`, vista previa apagada, botón, silencio y reintento en texto plano; correo con `EmailMessage`, `Date`, `Message-ID`, `Auto-Submitted`, `From` con nombre |
| `monitor/email_notifier.py` | `_send` pasa a ser el transporte compartido; el informe de observaciones se re-viste con el mismo diseño y sus etiquetas salen del catálogo (N9, N11) |
| Config | `server.name` (por defecto el nombre corto del host) y los interruptores nuevos en `DEFAULT_CONFIG`; panel: `EVENT_KINDS` y etiquetas; docs |
| Flota | `noust fleet authorize` / `noust node add` fijan `server.name` en el nodo y sugieren `web.public_url = https://<central>/n/<nodo>` (hoy ya funciona a mano) |
| Consola (opcional) | `POST /api/config/notifications/preview` que devuelve los cinco payloads sin enviar: los renderizadores son puros; alimenta la galería y el ajuste |
| Trivial | el hilo de la cola aún se llama `wasm-notify` |

Sin dependencias nuevas: todo es biblioteca estándar, así que no se toca `pyproject.toml`, `setup.py`,
`obs/debian.control` ni el spec del RPM.

### 4.8 Plan de tests

`syrupy>=4.6` ya está en las dependencias de desarrollo y hay `tests/__snapshots__/*.ambr`.

1. **Catálogo canónico** (`tests/notifications_support.py`): una función que devuelve un
   `Notification` por cada `code` (16, o 19 con las recuperaciones y `backup_success` opcionales) más
   variantes: sin enlace, sin commit, con contexto de vista previa, extracto largo, texto hostil
   (`<b>`, `&`, `<!channel>`, `@everyone`, acentos graves, `_ * ~`, ANSI, RTL, 10 000 caracteres,
   emoji en una rama), nodo con nombre largo. La misma función alimenta snapshots, invariantes,
   galería y documentación.
2. **Snapshots**: matriz `code × canal × idioma` (16 × 5 × 2 = 160, hasta 190 con los opcionales, unos 250 con variantes). Un
   fichero `.ambr` por canal e idioma para que el diff de un PR sea legible; el HTML del correo va
   en ficheros `.html` propios (extensión de fichero único de syrupy) para poder abrirlos en el
   navegador. Cualquier cambio de texto es un diff revisable, que es justo lo que el propietario
   quiere ver.
3. **Invariantes con Hypothesis** (ya es dependencia): por canal, con texto arbitrario en dominio,
   rama, mensaje de commit y líneas de extracto.
   - Telegram: HTML equilibrado con solo etiquetas permitidas, sin `<`, `>` ni `&` sueltos, ≤ 4096
     tras entidades, y el botón y los hechos **siempre** presentes.
   - Slack: `header` ≤ 150, `section` ≤ 3000, campos ≤ 2000 y ≤ 10, ≤ 50 bloques, sin `<`/`>` sin
     escapar fuera de los enlaces propios.
   - Discord: suma ≤ 6000, campos ≤ 1024, `allowed_mentions.parse == []`, ningún `@everyone`.
   - Correo: texto sin marcado, HTML que parsea con `html.parser` sin el texto hostil sin escapar,
     alternativa en orden texto-HTML, cabeceras `Date`, `Message-ID`, `Auto-Submitted` presentes,
     y cero `<img src="http…">`.
   - Webhook: JSON serializable que cumple el contrato de la sección 4.5 (validador mínimo escrito
     a mano; no se añade `jsonschema`).
   - D-8 (ninguna línea repetida) sobre todo el catálogo y todos los canales.
4. **Composición** (sin renderizar): cada emisor produce el `Notification` esperado (campos y
   estados, no texto); sustituye a los `assert "Trigger: webhook" in body` de hoy y desacopla el
   contenido del formato. Cubre el `DeployEvent` real de un rollback, con el `RolledBackError` real.
5. **i18n**: la paridad del catálogo ya existe; se añade que ninguna frase de Noust queda en inglés
   en `es` (lista de palabras prohibidas fuera del extracto: `did not`, `failed`, `Details:`,
   `is active`) y que la etiqueta del canal de prueba lleva mayúscula propia.
6. **Glifos**: los cinco puntos de código no están en una lista fija de puntos con propiedad Emoji.
7. **Arquitectura**: nadie fuera del notificador construye `NotificationEvent` (al terminar la
   migración) y ningún módulo importa `subprocess`; los tests no abren sockets (ya lo fuerza
   `conftest.py`).
8. **Compatibilidad**: los tests actuales de `tests/test_notifier.py`, `test_notify_escaping.py`,
   `test_deploy_notifications.py`, `test_web_notifications_wiring.py`,
   `test_web_job_notifications_recorded.py` y `test_email_notifier.py` (unas 2400 líneas) se
   revisan en el mismo PR: los que afirman texto pasan a snapshots o a composición; los de SSRF,
   secretos, cola y filtros no cambian.

### 4.9 Capturas para revisión

Un script `scripts/notification_gallery.py` (no es pytest, igual que `scripts/console_server.py`):

- **Correo**: HTML real en Chromium (el Playwright que ya instala la suite del panel), en claro y
  oscuro y a 390 px, como se hizo aquí.
- **Telegram, Slack, Discord**: maquetas HTML fieles pero aproximadas a partir de los mismos
  payloads (Telegram HTML es un subconjunto de HTML y se pinta tal cual; Slack y Discord se dibujan a
  mano); deben rotularse como aproximación.
- **Modo `--live`** (variables de entorno con destinos de prueba, nunca en CI): envía el catálogo
  a un chat, un canal de Slack, un canal de Discord y un buzón reales, para las capturas definitivas
  y para resolver lo que no se puede verificar sin el cliente (franja de Slack sin `text` superior,
  texto push de Discord, tema oscuro de Gmail y Outlook).
- Salida en `docs/assets/notifications/` (como `docs/assets/console/`), y las mismas imágenes en el
  README de la sección de notificaciones.

---

## 5. Decisiones abiertas (con recomendación)

| Id | Decisión | Recomendación |
|---|---|---|
| D1 | Telegram: glifos geométricos `● ▲ ✕` (sin color) o emoji de color `🟢 🟡 🔴` | Glifos: coherente con el panel (forma y palabra, color solo donde el canal lo da) y sin depender del sistema. El cambio a emoji es una tabla de una línea |
| D2 | Slack: `attachments` con `color` y bloques dentro (franja) o solo `blocks` | Franja; es la señal que más se lee en un canal ocupado. Verificar en vivo que el `fallback` y la ausencia de `text` superior se comportan; si no, `blocks` puro |
| D3 | Telegram: quitar del extracto del journal el prefijo `Sep 29 10:45:30 web-1 ` (la cabecera ya da servidor y hora) | Sí, solo en Telegram y solo si **todas** las líneas lo llevan: `pre` no ajusta líneas y en un móvil el extracto se lee cortado. Correo, Slack, Discord y webhook lo dejan literal |
| D4 | Logo del correo: texto provisional, PNG `cid:` con el wordmark, o sin marca | PNG `cid:` en gris medio (funciona en claro y oscuro) con `alt="noust"` |
| D5 | Nombre del servidor: `server.name` (por defecto el host) o `notifications.server_name`; quién lo fija en flota | `server.name` (sirve también a consola y correo); `fleet authorize` lo fija en el nodo y `node add` avisa si el nombre del registro difiere |
| D6 | Eventos nuevos en 3.1: `restore_*`, `cert.expired`, recuperaciones, `backup_success` opcional; los de flota (`node_*`) a una segunda fase | Los tres primeros son correcciones de errores de clasificación, entran; `backup_success` apagado por defecto; `node_*` con la fase de flota |
| D7 | Webhook: aditivo `version: 1` o sobre nuevo estilo Standard Webhooks / CloudEvents | Aditivo; el sobre nuevo cuando se añada firma |
| D8 | Enlace en Telegram: botón o línea de texto | Botón (Coolify), con línea de texto solo en el reintento de texto plano |
| D9 | Aviso silencioso para `ok`/`progress`/`info` | Sí por defecto; ajustable después si algún operador quiere el pitido en los éxitos |
| D10 | Idioma por canal (correo en español, Slack en inglés) | Fuera de alcance: se mantiene `notifications.language` global |

---

## 6. Plan de implementación

Orden de magnitud, no compromiso.

| Fase | Contenido | Tamaño |
|---|---|---|
| A. Núcleo y despliegues | Modelo, extracto, renderizadores, compositores de `deploy_*`; `DeployEvent` ampliado; `output=` en el health gate; transporte adaptado (Telegram, Slack, Discord, correo, webhook); tests de snapshot e invariantes; catálogo en/es | el grueso, unos 2-3 días |
| B. Resto de emisores | Monitor (disco, certificados, unidades, recuperaciones), copias, restauraciones con kinds propios; `server.name`, enlace de nodo, interruptores en panel y docs; cabeceras del correo; informe de observaciones re-vestido | 1-2 días |
| C. Galería y pulido | `notification_gallery.py`, modo `--live`, capturas en `docs/assets/`, vista previa en la consola (opcional), wordmark `cid:` | 1 día |
| Después | Eventos de flota, firma HMAC del webhook, Telegram editando el mensaje "Deploying" en su desenlace, coalescencia de incidentes | según prioridad |

---

## Anexo A. Reglas del proyecto y cómo las respeta la propuesta

- Regla 1: nada de esto ejecuta procesos; el extracto del journal del monitor (si se añade) se lee
  con `ServiceManager.logs`, que ya pasa por el `CommandRunner`.
- Regla 2: sin `except Exception`; los errores de entrega siguen siendo `_DELIVERY_ERRORS`, y el
  reintento de Telegram captura solo el `HTTPError` 400 con ese `description`.
- Regla 3: un modelo, un renderizador por canal, un solo camino de correo (el informe de
  observaciones deja de tener el suyo).
- Regla 4: el escape está en la frontera de cada renderizador y los límites en el presupuesto del
  renderizador, no en cada emisor.
- Español: frases completas con marcadores, nunca fragmentos; la evidencia de otros programas se
  queda literal.

## Anexo B. Cómo reproducirlo

Con el venv del repositorio (`/home/yago/Documents/GitHub/wasm/.venv/bin/python`) y sin abrir sockets:

```bash
python /tmp/noust-notif-render/render_before.py   # salida actual -> /tmp/noust-notif-before
python /tmp/noust-notif-render/proposed.py        # propuesta y comprobación de límites -> /tmp/noust-notif-proposed
python /tmp/noust-notif-render/mocks.py > /tmp/noust-notif-render/jobs.json
(cd panel && node /tmp/noust-notif-render/shot.mjs "$(cat /tmp/noust-notif-render/jobs.json)")   # capturas
```

`render_before.py` sustituye el opener por uno que captura, el correo por un transporte falso y la
resolución DNS por una constante; no se envió nada a ninguna red. El prototipo `proposed.py` **no**
forma parte del repositorio.

## Anexo C. Lo que no se pudo verificar

- Cómo pinta Slack una franja de `attachments` con bloques y sin `text` superior, y el texto de la
  notificación push de Discord para un mensaje solo de embed (se resuelve en la galería en vivo).
- Modo oscuro real de Gmail y Outlook (solo se comprobó en Chromium con `prefers-color-scheme`).
- Fecha en que Outlook de escritorio deja el motor de Word (fuente secundaria).
- Maquetación de Sentry y de GitHub Actions (no se consultaron).
- N15, la doble alerta unidad-rollback, por lectura de código y no en un servidor real.
