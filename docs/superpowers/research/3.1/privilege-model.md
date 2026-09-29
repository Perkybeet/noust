# Modelo de acceso al servidor y de privilegios: investigación previa a decidir (ítems 44, 45, 46 y 47)

Rama `dev/3.1`, 2026-09-29. Investigación, no diseño cerrado: termina en registros de decisión
(sección 8) que el dueño acepta o corrige antes de implementar. Objetivo declarado del dueño:
ser mejor que la competencia allí donde se pueda.

**Cómo leer las etiquetas de evidencia.** `[código]` leído en el repositorio de esta rama.
`[lab]` ejecutado por mí en contenedores desechables (Ubuntu 24.04, OpenSSH 9.6p1, systemd 255,
imagen `noust-fleet-image` y `noust-integration`, mismos indicadores que `tests/integration/run.py`;
los contenedores ya no existen) o con el `systemd-run --user` de esta máquina. `[web]` leído en la
fuente citada. `[no verificado]` lo digo porque no pude comprobarlo hoy; no lo tomes por hecho.

**Relación con otros documentos de esta carpeta.** `server-management.md` (ítems 29, 32, 46)
ya diseña las comprobaciones y el "confirmar o revertir"; `fleet-central.md` propone el techo de
acceso por nodo (`--access`) y `--create-tunnel-user`; `ens.md` recoge G11 (cuenta de túnel) y G14
(compilaciones sin privilegios). Este documento aporta lo que faltaba: las pruebas reales de sshd
y de systemd, la comparación con la competencia y las decisiones. Donde coincide, lo digo; donde
corrige a la documentación actual del producto, lo marco con **CORRECCIÓN**.

---

## 0. Resumen y hallazgos que cambian la conversación

1. **Ítem 44 confirmado y peor de lo descrito.** Instalar y compilar corre como root
   (`deployers/base.py:545-590`, `_run` llama a `runner.stream/run` sin `user=`), con el entorno
   completo del proceso que lanza (`core/runner.py:759`, `dict(os.environ)`). En el laboratorio un
   `postinstall` de npm como root lee `/root/.ssh/id_ed25519`, `/etc/noust/web-token`, el `.env` de
   otra aplicación, escribe `/etc/cron.d`, hereda variables secretas y deja un demonio vivo tras el
   despliegue. Un `runuser -u <cuenta>` a secas (lo que ofrece hoy el runner) arregla la mitad, no
   la otra mitad (sección 2.2). La opción que bloquea todo lo probado es una unidad transitoria
   `systemd-run` con cuenta dedicada y sandbox, sin dependencias nuevas.
2. **CORRECCIÓN: la clave de la central no "puede reenviar un puerto y nada más" en la cuenta
   root.** `permitopen` y `permitlisten` no limitan el reenvío de sockets Unix hacia atrás. Con la
   línea exacta que instala `noust fleet authorize`, `ssh -R /etc/nologin:127.0.0.1:8443 root@nodo`
   **creó `/etc/nologin` como root** `[lab]` (E8): `pam_nologin` impide entonces el acceso a todo
   usuario no root. Puede crear un socket en cualquier ruta. Se cierra con
   `AllowStreamLocalForwarding no`, que solo se puede poner por usuario, es decir, con la cuenta de
   túnel dedicada (ítem 45) y un bloque `Match User`. La afirmación falsa está en
   `fleet/authorize.py` (docstring, líneas 6-27) y en `docs/CENTRAL.md` (líneas 27-36).
3. **`read_sshd_settings` no ve los bloques `Match`.** Ejecuta `sshd -T` sin `-C`
   (`fleet/authorize.py:187`), y `_parse_sshd` se detiene en el primer `Match`. Con la cuenta de
   túnel dedicada, que necesita `AllowTcpForwarding local` solo para ella, `sshd_blockers`
   daría un falso "sshd no permite el reenvío" `[lab]` (E2: el valor global es `no`, el de la
   cuenta `local`). Hay que evaluar con `sshd -T -C user=<cuenta>,host=x,addr=y`.
4. **`systemd-run` tiene cuatro trampas que rompen las reglas del proyecto si se usa a
   ciegas** `[lab]`: expande `${VAR}` en el argv (regla 1: "argv, nunca un shell"); matar al
   cliente **no** para la unidad; con `--pipe` y otra cuenta, `echo x > /dev/stderr` falla con
   `Permission denied`; y un fallo por tiempo o por memoria devuelve el código 1, no 137. Todas
   tienen remedio y se describen en 5.3.
5. **El techo de la central es una decisión de la flota, no del SSH.** El nodo acepta lo que la
   central afirme en `X-Noust-Actor-Scope` (hasta `admin`) y `X-Noust-Elevated: 1`
   (`web/auth.py:3705-3735`, `admit_fleet`). Una central comprometida es `admin` en todos los nodos
   sin necesitar ningún shell, y `admin` equivale a root (`docs/security.md`, secciones "Sudo mode"
   y "Untrusted repositories"; `ens.md` H9). Quitar el login de root no cambia eso; el techo
   `--access` de `fleet-central.md` sí.
6. **Docker Compose es su propio caso y hoy no tiene ninguna guarda**: la unidad corre como
   `User=root` (`templates/systemd/docker-compose.service.j2:14`) y el archivo compose viene del
   repositorio; `grep` no encuentra ningún rechazo de `privileged`, `docker.sock`, `pid: host`,
   `cap_add` ni montajes del anfitrión en `deployers/docker_compose.py` `[código]`. Es la clase de
   fallo de CVE-2025-64419 de Coolify.
7. **Todas las aplicaciones comparten `www-data`**, así que cada una lee el `.env` (0600) de las
   demás `[lab]` (sección 2.2, párrafo final). `docs/security.md` lo reconoce ("No defendido").
   Es el motivo por el que la cuenta de compilación debe ser **distinta** de la de servicio.
8. **Donde podemos ser mejores que todos**: ninguno de los once productos revisados combina
   (a) compilación aislada por el propio sistema, sin contenedores; (b) cuenta de túnel sin
   privilegios, sin home, con política en el servidor **y** en la clave; (c) comprobaciones con
   arreglos que exigen prueba de acceso y se revierten solos. Los que ofrecen "no root" (Coolify)
   exigen `sudo NOPASSWD: ALL`, que es root con otro nombre.

**Recomendación en una línea por ítem** (detalle en 8.2): 44 → unidad transitoria con cuenta
`noust-build` y sandbox, fallo cerrado, previews primero; 45 → cuenta `noust-tunnel` sin home ni
shell, política en `sshd` y en la clave, y techo `--access`; 47 → este documento; 46 → las
comprobaciones de `server-management.md` con el protocolo de 7.2 y `PermitRootLogin no` solo con
prueba de acceso de otra cuenta con sudo.

---

## 1. Cómo funciona hoy (verificado en el código)

| Aspecto | Hecho | Dónde |
|---|---|---|
| Quién compila | Root. `_run` no pasa `user=`; instala, compila y ejecuta ganchos (`pre_install`, `post_install`, `pre_build`, `post_build`) con `self.runner.stream` o `run`. 28 llamadas a `self._run(` en los despliegues (16 en Docker Compose) | `deployers/base.py:545-590`, `1308`, `1358`; `monorepo.py:1168-1305`; `python.py:237,261`; `php_fpm.py:1081` |
| Entorno de la compilación | `dict(os.environ)` del proceso Noust más `env_vars` de la aplicación. Nada se limpia: `SSH_AUTH_SOCK` de un agente reenviado o claves de nube del shell del operador llegan al `postinstall` | `core/runner.py:759`; `base.py:570` |
| Herramientas que asumen root | `COMPOSER_ALLOW_SUPERUSER=1`; `HOME` de pnpm apuntado al directorio de la aplicación | `php_fpm.py:1085`; `monorepo.py:1578-1579` |
| Migraciones y ganchos | `prisma migrate deploy`, `pnpm db:migrate` y similares también como root, con los secretos de la aplicación | `monorepo.py:1208-1247` |
| Previews | Se compilan como root con una copia del entorno de producción; "Building previews as the service user instead of root is not done in 2.2". Solo se aceptan autores de confianza (sin forks) | `managers/previews.py:26-31,1175,1262,1278` |
| Qué admite el runner | `run` y `stream`: `cwd`, `env`, `timeout` obligatorio, `input`, `stdin_path`, `user`, `secrets`; `capture_to_file`; `start` para procesos largos (túneles) con `terminate` sobre el grupo de procesos; `cancellable()` mata el árbol (`start_new_session`) solo dentro de un ámbito de cancelación. **No** hay `sandbox=` ni forma de fijar propiedades de sistemd | `core/runner.py:566-707,733-1192` |
| `user=` | Prefija `runuser -u <cuenta> --` en un solo sitio, `runuser_prefix`. `runuser` fija `HOME`, `SHELL`, `USER`, `LOGNAME`, no cambia el directorio y no limpia el resto del entorno (man de `runuser` y `[lab]`). Solo lo usan hoy los clientes de bases de datos (autenticación *peer*) | `runner.py:512-530,755-758`; `managers/database/postgres.py` |
| Cuenta de las aplicaciones | `service_user`/`service_group` = `www-data` por defecto, **una para todas** | `core/config.py:91-92,1808-1815` |
| Endurecimiento de las unidades | Aplicación: solo `NoNewPrivileges=true` y `PrivateTmp=true` (más `LimitNOFILE` y `MemoryMax/CPUQuota/TasksMax` opcionales). Consola: `User=root` con `NoNewPrivileges`, `ProtectKernelLogs`, `LockPersonality`, `RestrictRealtime`, y sin `ProtectSystem` a propósito. Compose: `User=root` sin nada | `templates/systemd/app.service.j2:39-40`, `noust-web.service.j2:24,47-58`, `docker-compose.service.j2:14` |
| Traspaso de propiedad | Tras compilar, `chown -R servicio:servicio` y `chmod -R u+rwX,g+rX,o+rX` sobre el árbol (root los ejecuta sobre lo que escribió el código del repositorio; `chown -R` no sigue enlaces) | `deployers/helpers/permissions.py:31-83`; `base.py:2333-2369` |
| Obtención del código | `git` como root con la clave `~/.ssh` de root y credenciales en `GIT_CONFIG_*` (no en argv). El release se exporta sin `.git` desde una caché | `validators/ssh.py:18`; `managers/source_manager.py:142-300`; `deployers/helpers/release_build.py:108-160` |
| Docker Compose | `docker compose ... build/up` como root por `_run`; unidad `User=root`; sin análisis del archivo compose | `deployers/docker_compose.py:1029,1574,1595`; plantilla anterior |
| Clave de la flota | Ed25519 por nodo, instalada en el `authorized_keys` de la cuenta `--ssh-user` (**por defecto `root`**) como `restrict,port-forwarding,permitopen="127.0.0.1:<puerto>",permitlisten="127.0.0.1:1",command="/usr/bin/false"` | `fleet/authorize.py:85,741-863`; `cli/commands/fleet.py:143-148,235-239` |
| Cliente SSH de la central | `-F /dev/null`, `BatchMode`, `StrictHostKeyChecking=yes`, `UserKnownHostsFile` propio, `IdentitiesOnly`, `ExitOnForwardFailure`, keepalive | `fleet/tunnels.py:136-193` |
| Lectura de sshd | `sshd -T` **sin** `-C`; `_parse_sshd` se detiene en `Match`; `sshd_blockers` mira `AllowTcpForwarding`, `DisableForwarding` y `PermitRootLogin` globales. No mira `AllowUsers`, `AllowGroups`, `AllowStreamLocalForwarding` ni `UsePAM` | `authorize.py:97-215` |
| Token de flota en el nodo | Solo desde loopback y sin cabeceras de proxy. El alcance efectivo lo dicta la central por `X-Noust-Actor-Scope` (ausente → `read`); no hay techo por nodo | `web/auth.py:3627-3735` |
| Documentación por defecto | `noust node add ... --ssh root@...` y `ssh -L ... root@central` | `docs/CENTRAL.md:106,143` |

---

## 2. Laboratorio: lo que se ejecutó y lo que salió

### 2.1 Entorno y límites

- sshd: contenedor Ubuntu 24.04 sin systemd, sshd en `127.0.0.1:2222` como root, servicios falsos
  de consola (TCP `127.0.0.1:8443`) y de socket Unix (`/run/fake.sock`, modo 0666 para que un
  rechazo solo pueda ser de sshd).
- systemd: contenedor privilegiado con systemd como PID 1 (igual que `run.py`), cuenta `noust-build`
  de sistema, `/etc/noust`, `/var/lib/noust`, `/root/.ssh` y el `.env` de otra aplicación como
  cebos, y un proyecto npm con un `postinstall` que lo intenta todo.
- `systemd-run --user` de esta máquina (WSL2, systemd 255) para la semántica de `--pipe`, `--wait`,
  `--collect`, códigos y tiempos. **No sirve para probar `ProtectSystem`**: el man de
  `systemd.exec` lo dice ("some sandboxing functionality is generally not available in user
  services ... ProtectSystem="), y allí `/home` siguió escribible con `ProtectSystem=strict`. Todo
  lo de sandbox de abajo sale del contenedor con PID 1 real.
- No probado: Debian 12, Ubuntu 22.04, Fedora, openSUSE, SELinux, ni un host real fuera de Docker.
  Las versiones mínimas salen de las etiquetas "Added in version" del man de systemd 255.

### 2.2 El mismo `postinstall` bajo tres regímenes `[lab]`

`evil.js` intenta 13 acciones (más la lectura de `uid`). `bloqueado` = la operación falló; `PERMITIDO` = se hizo.

| Acción | A. Hoy: root, entorno heredado | B. `runuser -u noust-build` | C. `systemd-run` con sandbox |
|---|---|---|---|
| Escribir `/root/pwned` | PERMITIDO | bloqueado (EACCES) | bloqueado (EACCES) |
| Leer `/root/.ssh/id_ed25519` | PERMITIDO | bloqueado | bloqueado |
| Leer `/etc/noust/web-token` y `/var/lib/noust/noust.db` | PERMITIDO | bloqueado (permisos) | bloqueado |
| Leer el `.env` de otra aplicación | PERMITIDO | bloqueado solo por el 0600 de su dueño; **`www-data` sí lo lee** | bloqueado |
| Leer el código publicado de otra aplicación | PERMITIDO | **PERMITIDO** (mundo legible) | oculto (ENOENT con `TemporaryFileSystem`) |
| Escribir `/etc/cron.d`, `/usr/local/bin` | PERMITIDO | bloqueado (EACCES) | bloqueado (EROFS) |
| Escribir fuera del release | PERMITIDO | bloqueado | bloqueado (EROFS) |
| Escribir dentro del release | PERMITIDO | PERMITIDO | PERMITIDO (y es lo único) |
| Variables `NOUST_*`/secretos heredadas | **PERMITIDO** (`NOUST_SECRET_TOKEN`) | **PERMITIDO** | ninguna |
| `/tmp` y `/var/tmp` persistentes entre compilaciones | PERMITIDO | **PERMITIDO** (`touch` ok) | privado y borrado al terminar |
| Demonio que sobrevive al despliegue | **sigue vivo como root** | no medido; sin cgroup propio, igual que A | 0 supervivientes (`KillMode=control-group`) |
| Red | sin restricción | sin restricción | sin restricción (política aparte, 5.4) |
| `os.networkInterfaces()` de Node | ok | ok | **falla** sin `AF_NETLINK` (error 97) |

Lectura: B (una línea con el runner de hoy) cierra los secretos de root pero deja entorno,
lectura del resto de aplicaciones, `/tmp` persistente y, sin cgroup propio, demonios. C los cierra
todos. Aparte, `runuser -u www-data -- cat .../shared/.env` leyó `OTHER_APP_SECRET=1` `[lab]`: las
aplicaciones que comparten `www-data` se leen entre sí. Compilar como `www-data`, aun con sandbox,
seguiría permitiendo leer `/proc/<pid>/environ` de las que corren con ese mismo uid (`[no
probado]`, consecuencia de que comparten uid). Por eso la cuenta de compilación tiene que ser
propia.

### 2.3 Comportamiento de `systemd-run` `[lab]`

| Prueba | Resultado | Consecuencia |
|---|---|---|
| Código de salida con `--wait` | Se propaga (`exit 3` → 3). El man lo confirma ("the return value will be propagated from the service") | El mapa habitual de `BuildError` funciona |
| Fallo por tiempo (`RuntimeMaxSec`), por memoria (`MemoryMax`) | `systemd-run` devuelve **1** y el resumen dice `code=killed/status=TERM` o `KILL`; `Result=timeout` / `oom-kill` | El `if exit_code == 137` de `OutOfMemoryError` (`base.py:1363`) nunca dispararía; hay que leer `Result` |
| Resultado legible por máquina | Sin `--collect`: `systemctl show -p Result -p ExecMainStatus -p ExecMainCode <unidad>` → `Result=timeout`, `ExecMainStatus=15`; `oom-kill`, 9. Luego `systemctl reset-failed` | Usar esto, no analizar el texto de `Finished with result:` |
| **Matar al cliente** (`SIGTERM` a `systemd-run`) | La unidad **sigue activa** (verificado con `User=` y con PID 1 real). `systemctl stop` la para | La cancelación y el tiempo de espera del runner deben hacer `systemctl stop <unidad>`; `RuntimeMaxSec` como red de seguridad |
| Cuando muere `noust-web` a mitad de un despliegue | Hoy `KillMode=control-group` mata `npm`; con una unidad transitoria seguiría | `-p BindsTo=noust-web.service -p After=noust-web.service` conservaría el comportamiento (`[no verificado]`, semántica estándar de `BindsTo=`) más un barrido de `noust-build-*` al arrancar |
| Entorno del llamante | No se hereda (`SECRET_TEST` ausente) `[lab]` con `systemd-run --user` y en C. Solo llega lo pasado con `-E`/`-p Environment=` | Se corrige la fuga de la sección 1 sin código extra |
| **`-E` con un secreto** | `su -s /bin/sh nobody -c 'systemctl show <unidad> -p Environment'` → `Environment=SECRET_IN_ENV=hunter2`. `-p EnvironmentFile=/run/x.env` (root, 0600) solo muestra `EnvironmentFiles=/run/x.env` | Secretos siempre por `EnvironmentFile=` en tmpfs, nunca por `-E`. `/proc/<pid>/environ` no era legible por otros |
| **`${VAR}` y `$$` en el argv** | `'a=${HOME}'` llegó como `a=` (vacío) y `'d=$$'` como `d=$`. `%h` y `%%` llegaron **sin tocar**. `--expand-environment=no` funciona (v254+) | Duplicar cada `$` como `$$` en toda la línea (vale desde siempre); **no** duplicar `%`. **Cuidado: `cron_manager.build_exec_start` duplica también `%` para unidades escritas a mano; reutilizarlo tal cual dejaría `%%` literal.** Una función de escape propia y una prueba con `$`, `%`, `;` |
| `--pipe` con `User=` distinto de root | `echo x > /dev/stderr` → `cannot create /dev/stderr: Permission denied` (rc 2). Con root, bien | Las tuberías las crea root (modo 0600). Los scripts que reabren `/dev/stdout` o `/dev/stderr` fallan. Ver alternativas abajo |
| `StandardOutput=file:` en `/tmp` | rc 209 (`fs.protected_regular` en directorio con sticky) | Usar un directorio propio, no `/tmp` |
| `StandardOutput=append:` con fichero de la cuenta | El `> /dev/stderr` funciona pero **trunca** el fichero (perdió `line1`/`err1`) | Tampoco es gratis |
| `StandardOutput=journal` | `> /dev/stderr` → `No such device or address` | Descartado |
| `--pty` desde un llamante sin terminal | Funciona; el reabrir va bien; `[ -t 1 ]` es cierto; las líneas llegan con `\r\n` | Válido como modo de compatibilidad (`CI=1`, `NO_COLOR=1`, `TERM=dumb`, quitar `\r`) |
| Procesos que la compilación deja atrás | 0 supervivientes al terminar la unidad | Mejora respecto a hoy |
| `LoadCredential=`/`SetCredential=` | En el contenedor `/run/credentials/<unidad>` no llegó a existir aunque el manager arrancó la unidad | `[no verificado]`: puede ser el `/run` con `noexec` de Docker. No basar el diseño en ello hasta probarlo en una VM |
| `IPAddressDeny=any` + `IPAddressAllow=127.0.0.0/8` | `curl` a una IP pública, bloqueado | Funciona en ese contenedor; el caso concreto `169.254.0.0/16` sin `Allow` `[no verificado]` |
| `PrivateNetwork=yes` | Solo `lo` | Válido para compilar sin red |
| `TemporaryFileSystem=/var/www/apps:ro` + `BindPaths=<release>` + `BindReadOnlyPaths=<shared>` | Otras aplicaciones: ENOENT; release escribible; `shared/` solo lectura | Mejor que `InaccessiblePaths` para ocultar "las demás" (el man: `InaccessiblePaths` no admite anidar rutas escribibles) |
| `SystemCallFilter=@system-service` | Node arranca | Nivel 2, medir con más herramientas |

### 2.4 sshd: la clave de la central en `root` y en una cuenta dedicada `[lab]`

Las mismas ocho pruebas con la línea exacta de Noust. `E1` = clave en `root` con los valores por
defecto de sshd. `E2` = clave en `noust-tunnel` (sin home, shell `nologin`), `PermitRootLogin no`,
`DisableForwarding yes` global y un `Match User noust-tunnel` con `DisableForwarding no`,
`AllowTcpForwarding local`, `AllowStreamLocalForwarding no`.

| Intento | E1 (clave en root) | E2 (cuenta dedicada) |
|---|---|---|
| `-L` a la consola | funciona | funciona |
| `-L` a otro puerto de loopback | rechazado | rechazado |
| `-L` a un socket Unix | rechazado (`permitopen` lo niega) | rechazado |
| `-R` TCP en otro puerto | rechazado (`permitlisten`) | rechazado |
| **`-R` a una ruta de socket Unix** | **concedido; creó `/tmp/rfwd.sock`, `/etc/nologin` y `/run/sshd-evil.sock` como root** (E8) | rechazado (`AllowStreamLocalForwarding no`) |
| `-R 127.0.0.1:1` | concedido (root puede abrir el puerto 1; el propio código lo prevé) | rechazado (una cuenta sin privilegios no abre <1024) |
| Comando | `command=` fuerza `/usr/bin/false` → rc 1 | igual; además `nologin` |
| SOCKS `-D` | rechazado | rechazado |
| Login de root con `PermitRootLogin no` | n/a | `Permission denied (publickey,keyboard-interactive)` |

Pruebas adicionales:

- **Política en el servidor, sin depender de la clave (E6).** Con `PermitOpen 127.0.0.1:8443`,
  `PermitListen none`, `AllowStreamLocalForwarding no`, `PermitTTY no` en el `Match`, una clave
  **sin ninguna opción** solo pudo llegar a la consola; el resto, rechazado, y el `-tt` falló
  ("PTY allocation request failed"). Es defensa en profundidad: si alguien pega la línea sin
  opciones, la cuenta sigue contenida.
- **Claves en un fichero de root, sin home (E5).** `useradd -r -M -d /nonexistent -s
  /usr/sbin/nologin` y `AuthorizedKeysFile /etc/ssh/noust/%u.keys` dentro del `Match`, con
  `StrictModes yes`: entra sin problema. La cuenta no posee nada y **no puede añadirse claves
  ni dejar `~/.ssh/rc`**.
- **Cuenta bloqueada (E3).** `useradd -r` deja el campo de contraseña en `!`. Con `UsePAM yes` da
  igual; con `UsePAM no`: `User noust-tunnel not allowed because account is locked` aunque la
  clave sea válida. Con `usermod -p '*'` funciona. Crear siempre la cuenta con `*`.
- **`AllowUsers` (E7).** Con `AllowUsers alice` global, la cuenta queda fuera:
  `not allowed because not listed in AllowUsers`. `sshd -T -C user=noust-tunnel,...` lo muestra
  antes de intentar.
- **Ficheros incluidos (E4).** Un `Match User` dentro de `sshd_config.d/00-x.conf` no se sale de
  su fichero; una directiva global posterior del fichero principal se aplica al resto de usuarios,
  y la del `Match` a la cuenta. Lo que dice la documentación de Ubuntu ("first value set ... files
  within `sshd_config.d/` will override those in the main configuration file") se confirma; por eso
  el fichero de Noust debe llamarse `00-...` (el `50-cloud-init.conf` de las imágenes de Ubuntu
  lleva `PasswordAuthentication yes`, y un arreglo en el fichero principal no surtiría efecto).

---

## 3. La competencia, exactamente

`[web]` salvo indicación. Filas de Noust al final.

| Producto | Cuenta SSH y root | Aislamiento de compilaciones | Cómo se autentica el plano de control | Incidentes publicados |
|---|---|---|---|---|
| **Coolify** | La instalación exige "root o cuenta con sudo" ([docs](https://coolify.io/docs/get-started/installation)). Cuenta no root es "experimental" y exige `NOPASSWD: ALL`; la propia doc avisa: "The account still has root-level access" ([docs](https://coolify.io/docs/knowledge-base/server/non-root-user)). El contenedor de Coolify entra por SSH como root al anfitrión, con la clave privada guardada en su base de datos | Docker en el anfitrión (Nixpacks, Dockerfile, buildpacks) `[conocimiento previo, no reverificado hoy]`; sin sandbox adicional | Clave SSH por servidor; cada operación se ejecuta por SSH como root | Enero 2026: 11 fallos de inyección de comandos con CVSS 10.0, todos hacia root (CVE-2025-66209 a 66213) ([The Hacker News](https://thehackernews.com/2026/01/coolify-discloses-11-critical-flaws.html)); CVE-2025-64419 (inyección vía `docker-compose.yaml`, 9,7), CVE-2025-64424 (campos de Git), **CVE-2025-64420 (usuarios de poco privilegio veían la clave privada de root)** ([Censys](https://censys.com/advisory/cve-2025-64424-cve-2025-64420-cve-2025-64419/)); CVE-2025-22605 (delimitador `EOF-COOLIFY-SSH` en el `here-doc`, exfiltró claves de todos los servidores) ([GHSA-9wqm](https://github.com/coollabsio/coolify/security/advisories/GHSA-9wqm-fg79-4748)). 52.890 instancias expuestas el 8-ene-2026 ([Censys vía WZ-IT](https://wz-it.com/en/blog/coolify-cve-security-vulnerabilities-update-2025-2026/)) |
| **Dokploy** | Exige root; no soporta despliegues no root ([docs](https://docs.dokploy.com/docs/core/remote-servers/instructions)) | Docker/Swarm con acceso al socket | Clave SSH que el usuario sube | Previews con RCE y acceso al entorno desde una PR de un fork (GHSA-h67g, corregido en 0.24.3; el hilo de HN describe seis meses hasta el arreglo: [HN 44548952](https://news.ycombinator.com/item?id=44548952)); CVE-2026-45661: recorrido de rutas en un ZIP que escribe en `/etc/cron.d` de un servidor remoto por SFTP (9,9, [GHSA-66v7](https://github.com/Dokploy/dokploy/security/advisories/GHSA-66v7-g3fh-47h3)); CVE-2026-72901: inyección en copia de volúmenes, root por el socket de Docker (9,9, [OpenCVE](https://app.opencve.io/cve/CVE-2026-72901)); CVE-2026-24841 (WebSocket del terminal; solo el resumen de [SentinelOne](https://www.sentinelone.com/vulnerability-database/cve-2026-24841/), no abierto) |
| **CapRover** | Instala con el socket de Docker montado; sin doc de SSH ni de no root ([best practices](https://caprover.com/docs/best-practices.html)) | Docker Swarm; construcciones en contenedores | Contraseña del panel (`captain42` por defecto, la doc pide cambiarla) | No encontré CVE ni GHSA publicados. Eso no prueba que no existan |
| **Laravel Forge** | Conecta como **root** al aprovisionar y **sigue usando root** después (firewall, PHP, aislamiento de sitios); el usuario `forge` es "super user". Clave SSH única por servidor, contraseñas SSH desactivadas, UFW, actualizaciones de seguridad semanales ([docs](https://laravel.com/forge/docs/servers/security)). La clave de Forge está en `/root/.ssh` y `/home/forge/.ssh` ([KB](https://laravel.com/forge/docs/knowledge-base/servers)) | **Aislamiento por sitio opcional**: un usuario del sistema por sitio con sudo limitado a recargar PHP-FPM; `forge` lee todos ([docs](https://laravel.com/forge/docs/sites/user-isolation)). Sin sandbox de compilación | Clave SSH por servidor desde el SaaS | No encontré incidentes públicos de plataforma |
| **Ploi** | Despliegues y terminal como `ploi`; las comprobaciones de salud como **root**, lo que obliga a reactivar el login de root en servidores endurecidos (petición "planned", [roadmap 1550](https://roadmap.ploi.io/projects/1-server-level-requests/items/1550-allow-server-health-checks-to-authenticate-as-the-ploi-user-instead-of-root)) | Usuario del sistema por sitio, pero cada uno puede listar `/home/*` y navegar fuera (petición de endurecimiento, [roadmap 1480](https://roadmap.ploi.io/projects/1-server-level-requests/items/1480-user-isolation-directory-privacy-hardening)) | Clave SSH desde el SaaS | Ninguno encontrado |
| **RunCloud** | El agente se instala como root; permite "Prevent root login" y "Passwordless login" desde el panel, sin salvaguarda documentada contra el bloqueo ([docs](https://runcloud.io/docs/ssh-service-hardening-on-runcloud)); abre el 34210 para el agente | Cada aplicación web "con su carpeta, base de datos y permisos" ([docs](https://runcloud.io/docs/understanding-web-applications-on-runcloud)); sin más detalle público | Agente con canal propio (puerto 34210) | Ninguno encontrado. La página de seguridad de RunCloud no da datos técnicos |
| **ServerPilot** | SSH como el usuario del sistema de la aplicación | Un usuario del sistema por aplicación; los procesos de una no leen los ficheros de otra ([docs](https://serverpilot.io/docs/sysusers/)) | No documentado en lo leído | Ninguno encontrado |
| **Cloudron** | Su propia doc recomienda `PermitRootLogin no`, cuenta con sudo, `PasswordAuthentication no` y sacar SSH del 22 al **202** ([docs](https://docs.cloudron.io/security/)) | **Contenedores por aplicación**: no root, sistema de ficheros de solo lectura, AppArmor; actualizaciones firmadas con GPG con claves fuera de línea | Sin plano de control externo obligatorio | Hilo público de escalada de privilegios por `/tmp` y el complemento Docker ([foro](https://forum.cloudron.io/topic/2222/impersonate-user-privilege-escalation/11), leído solo el resumen del buscador) |
| **Portainer** | El agente monta el socket de Docker (root de facto) | n/a (orquesta contenedores ya construidos) | Agente clásico: HTTPS con certificado generado en la instalación, puerto 9001 y `AGENT_SECRET` opcional. **Edge Agent: conexión saliente, sin puertos de entrada** ([docs](https://docs.portainer.io/admin/environments/add/docker/agent)) | CVE-2025-49593 (fuga de credenciales de registro), CVE-2024-29296 (enumeración de usuarios), CVE-2022-24961 (API del agente) ([CVEDetails](https://www.cvedetails.com/vulnerability-list/vendor_id-19294/product_id-50211/Portainer-Portainer.html)) |
| **Dokku** | Solo la cuenta `dokku`; cada clave lleva `no-agent-forwarding,no-user-rc,no-X11-forwarding,no-port-forwarding,no-pty` y comando forzado ([docs](https://dokku.com/docs/deployment/user-management/)) | Contenedores con herokuish, que suelta root con `setuidgid` por defecto; el usuario `dokku` tiene acceso a Docker `[conocimiento previo, no reverificado hoy]` | Claves SSH | Ninguno encontrado (búsqueda limitada) |
| **Kamal** | **root por defecto** por SSH, puerto 22; con cuenta propia hay que añadirla al grupo `docker` (equivale a root) ([docs](https://kamal-deploy.org/docs/configuration/ssh/)); `keys_only`, salto con `-J` | Docker | Claves SSH | Ninguno encontrado |
| **Noust hoy** | Root por SSH solo para la clave restringida de la central; la consola corre como root; CLI root local | Ninguno: root con el entorno heredado | Clave Ed25519 por nodo con `restrict`, token de flota solo por loopback, `StrictHostKeyChecking=yes`, sudo mode confirmado en ambos lados | Sin incidentes conocidos; los hallazgos 2 y 5 de la sección 0 son propios |

Lecciones que se repiten:

1. **Todas las cadenas de CVE de Coolify y Dokploy terminan en root porque el plano de control ejecuta
   todo como root por SSH o por el socket de Docker.** La inyección de comandos es el patrón, pero
   lo que convierte un fallo en compromiso total es la ausencia de una frontera de privilegios
   detrás del control de acceso. Noust ya cumple la regla 1 (argv sin shell), que evita la clase
   de fallo; le falta la frontera para cuando aparezca otra.
2. **Las previews de PR de código no de confianza son el caso peor** y Dokploy lo sufrió
   (GHSA-h67g). Noust ya limita a autores de confianza; añadir sandbox y quitar secretos de
   producción convierte una regla de política en una frontera.
3. Los que ofrecen "no root" lo hacen con `sudo NOPASSWD: ALL` (Coolify) o con el grupo `docker`
   (Kamal), es decir, la misma potencia con otro nombre. Una cuenta de túnel sin shell, sin home y
   sin ningún sudo es distinta en lo que importa.
4. Forge y Ploi documentan que **no pueden dejar de usar root** para tareas de gestión;
   Cloudron y RunCloud recomiendan quitarlo pero con la responsabilidad en el operador. Ninguno
   verifica antes de aplicar que el operador seguirá pudiendo entrar (sección 7).

---

## 4. Estándares y comunidad

- **Mozilla OpenSSH (Modern)**: `PermitRootLogin no`, `AuthenticationMethods publickey`, `LogLevel
  VERBOSE`. Razón textual: root no se admite "porque es difícil saber qué proceso pertenece a qué
  root"; con usuarios normales y `su`/`sudo` queda una pista de auditoría limpia
  ([Mozilla](https://infosec.mozilla.org/guidelines/openssh)). Recomienda `-J` en lugar de
  reenvío de agente.
- **CIS Ubuntu 24.04**: 5.1.8 "Ensure sshd DisableForwarding is enabled" (en v1.0.0 y v2.0.0);
  "sshd PermitRootLogin is disabled"; `MaxAuthTries ≤ 4`; `LoginGraceTime ≤ 1 minuto`
  ([Tenable 5.1.8](https://www.tenable.com/audits/items/CIS_Ubuntu_Linux_24.04_LTS_v1.0.0_L2_Server.audit:e36f4a50cc8cb8e178f39025f4ade116),
  [v2.0.0](https://www.tenable.com/audits/CIS_Ubuntu_Linux_24.04_LTS_v2.0.0_L2_Server)). **Punto
  clave para la cuenta de túnel**: CIS pide desactivar el reenvío globalmente y lo permite
  excepcionalmente; un `Match User noust-tunnel` cumple CIS y Noust a la vez (E2 lo prueba con
  `DisableForwarding yes` global). Los números de sección cambian entre distribuciones y
  versiones; cítalos con la versión del benchmark.
- **ENS (RD 311/2022, Anexo II)**, texto del BOE ([BOE-A-2022-7191](https://boe.es/boe/dias/2022/05/04/pdfs/BOE-A-2022-7191.pdf)):
  - `op.acc.1.3`: cada entidad, "usuario o proceso", tiene un identificador singular que permita
    saber qué acciones realizó. Una cuenta compartida (root) para la central lo incumple; una
    cuenta `noust-tunnel` propia, no.
  - `op.acc.4.1` "todo acceso estará prohibido, salvo autorización expresa", `op.acc.4.2` mínimo
    privilegio, `op.acc.4.5` política específica de acceso remoto con autorización expresa.
  - `op.acc.6.r8.1`: doble factor desde zonas no controladas (Internet); `op.acc.6.r9.2`: acceso
    remoto autorizado, cifrado, **inhabilitado cuando no se usa de forma constante** y con
    registros de auditoría.
  - `op.exp.2.1` retirar cuentas y contraseñas estándar, `.2.2` mínima funcionalidad, `.2.3`
    seguridad por defecto; `op.exp.3.2` mantener el mínimo privilegio; `op.exp.3.6` la configuración
    solo la edita personal autorizado.
  - `op.exp.4.r2.1`: **antes de aplicar configuraciones, parches y actualizaciones de seguridad se
    preverá un mecanismo para revertirlos.** Es exactamente el "confirmar o revertir" de 7.2.
  - CCN-STIC: identifiqué CCN-STIC-610A22 (RHEL 9, oct-2022) y CCN-STIC-620 (Oracle Linux 9), con los
    perfiles `ccn_basic`, `ccn_intermediate` y `ccn_advanced` del SCAP Security Guide
    ([Red Hat](https://access.redhat.com/compliance/ccn-stic)). **No encontré una guía CCN-STIC
    específica para Debian/Ubuntu ni confirmé "CCN-STIC-619".** La regla `sshd_disable_root_login`
    existe en el SSG, pero no verifiqué qué nivel CCN la selecciona (`[no verificado]`; comprobar
    con `oscap info --profile ccn_basic`). Para un auditor, lo demostrable es op.acc.1.3, op.acc.4.2
    y op.exp.4.r2.1, no un valor de `PermitRootLogin`.
- **Cadena de suministro npm**: la razón de fondo de aislar. Shai-Hulud 2.0 (nov-2025) comprometió
  más de 700 paquetes y creó más de 27.000 repositorios; usó scripts `preinstall`/`postinstall`
  para robar credenciales ([Zscaler](https://www.zscaler.com/blogs/security-research/shai-hulud-v2-poses-risk-npm-supply-chain),
  [HN 46032539](https://news.ycombinator.com/item?id=46032539)). pnpm 10.0.0 (7-ene-2025)
  desactivó por defecto los scripts de dependencias; npm 12.0.0 (8-jul-2026) también, con
  `npm approve-scripts` ([Stéphane Robert](https://blog.stephane-robert.info/en/post/npm-no-longer-runs-install-scripts/)).
  Pero **el `build` y los `prepare` propios del proyecto siguen ejecutándose**, y "PackageGate"
  (26-ene-2026) mostró que una dependencia Git con un `.npmrc` evadía `--ignore-scripts`
  (CVE-2025-69263/69264; [BleepingComputer](https://www.bleepingcomputer.com/news/security/hackers-can-bypass-npms-shai-hulud-defenses-via-git-dependencies/)).
  Conclusión: `--ignore-scripts` es defensa en profundidad opcional (rompe esbuild, sharp, Prisma),
  no sustituye a una frontera de privilegios. Noust no controla la versión de npm del servidor.
- **fail2ban** por defecto: `bantime 10m`, `findtime 10m`, `maxretry 5`, `ignoreip` vacío
  ([jail.conf](https://raw.githubusercontent.com/fail2ban/fail2ban/master/config/jail.conf)). Un
  arreglo automático debe poner en `ignoreip` la IP de la sesión del operador y, si se conoce, la
  de la central, o el propio arreglo puede bloquear al operador.
- **Actualizaciones desatendidas** en Debian/Ubuntu: la configuración por defecto instala solo
  seguridad; se comprueba con `apt-daily-upgrade.timer` y `/var/log/unattended-upgrades/`
  ([Debian Wiki](https://wiki.debian.org/UnattendedUpgrades)).
- **Docker y cortafuegos**: los puertos publicados por Docker "se desvían antes de pasar por las
  reglas de ufw" ([Docker](https://docs.docker.com/engine/network/packet-filtering-firewalls/)).
  Un "ufw activo" no significa que un contenedor no esté expuesto: la comprobación debe cruzar
  puertos en escucha con reglas reales.
- **Ubuntu 24.04** activa sshd por *socket activation*; un generador traduce `sshd_config` a
  `ssh.socket` ([notas de la versión](https://discourse.ubuntu.com/t/noble-numbat-release-notes/39890)).
  El puerto efectivo se lee de `ss -tlnp`, no solo de `sshd -T`.
- **Comunidad**: en "Why disable SSH root login with key only?" ([HN 32556165](https://news.ycombinator.com/item?id=32556165),
  resumen obtenido por la API de HN, no leído entero) los argumentos a favor son defensa en
  profundidad (la clave puede filtrarse) y trazabilidad; en contra, que las herramientas de
  producción acaban con `sudo` sin contraseña, que equivale a root. Ese segundo argumento es
  válido y por eso 7.2 exige comprobar que la otra cuenta **de verdad** puede usar `sudo`.

---

## 5. Aislar las compilaciones con systemd, sin dependencias nuevas

### 5.1 Qué corre código no confiable y con qué privilegios

| Fase | Ejemplos | Código que ejecuta | Debe ver |
|---|---|---|---|
| Obtener | `git clone/fetch`, exportar release | `git` (no el repositorio) | Credenciales de despliegue (clave, token) |
| **Instalar** | `npm ci`, `pip install`, `composer install`, scripts de ciclo de vida | Terceros y del repositorio | Red de paquetes; **no** secretos de producción |
| **Compilar** | `npm run build`, `next build`, `vite build` | Del repositorio | Variables `NEXT_PUBLIC_*` y afines; a veces red |
| **Liberar** | `prisma migrate deploy`, `artisan migrate`, `db:migrate` | Del repositorio con acceso a la base de datos | Secretos de la aplicación, red a la base de datos |
| Ganchos `pre_*`/`post_*` | Los de `BaseDeployer` | Del repositorio | Según la fase en que se llamen |
| Servir | La unidad de la aplicación | Del repositorio | Su propio `.env` |
| Compose | `docker compose build/up` | Del repositorio, **dentro del demonio root** | Todo lo que el archivo compose monte |

Las tres primeras son las de 44. **Liberar** es una cuarta categoría: necesita secretos, así que
no debe correr en el sandbox de compilación ni como root; debe correr con la identidad de
ejecución de la aplicación (misma cuenta, mismo `EnvironmentFile`, mismo sandbox que su unidad). Es
lo que hacen Heroku (release phase) y Kamal (`exec`).

### 5.2 Propiedades (nivel 1 = probado en el laboratorio; nivel 2 = medir antes de activar)

Se aplican con `systemd-run` desde el runner. Versiones mínimas de las etiquetas del man de systemd
255; las de `--pipe`, `--wait` y `--collect` son de `systemd-run`.

| Propiedad | Nivel | Desde | Para qué / matiz |
|---|---|---|---|
| `--unit=noust-build-<app>-<release>`, `--wait`, `--pipe`, `--collect` (o sin `--collect` y `systemctl show`) | 1 | 232 / 235 / 236 | Nombre determinista para poder pararla |
| `-p User= -p Group=` (cuenta estática `noust-build`) | 1 | 211 | **No `DynamicUser=`**: el man avisa de que los ficheros que deja el proceso pasan al siguiente que reciba ese uid, y el release lo lee después otra cuenta |
| `NoNewPrivileges=yes`, `PrivateTmp=yes`, `PrivateDevices=yes` | 1 | 187 / 209 | Impide ganar privilegios por setuid (no se distinguió con `su` en el laboratorio: pide contraseña en ambos casos); `/tmp` privado y borrado; `npm install` funcionó con los tres |
| `ProtectSystem=strict`, `ProtectHome=yes` | 1 | 214 | Con `strict` todo es de solo lectura salvo lo listado |
| `ReadWritePaths=<cache>`; `TemporaryFileSystem=<apps>:ro` + `BindPaths=<release>` + `BindReadOnlyPaths=<shared>` | 1 | 233 / 238 | Oculta las demás aplicaciones (ENOENT) |
| `InaccessiblePaths=` con las rutas de **`core/paths.py`** (config, estado, nombres `wasm` heredados, copias, registros) y `/run/docker.sock` | 1 | (antiguo) | Regla del proyecto: nunca escribir una ruta a mano. No admite anidar rutas escribibles |
| `ProtectKernelTunables/Modules/Logs=yes`, `ProtectControlGroups=yes`, `ProtectClock=yes`, `ProtectHostname=yes` | 1 | 232 / 244 / 245 / 242 | Sin coste conocido |
| `RestrictSUIDSGID=yes`, `RestrictNamespaces=yes`, `LockPersonality=yes`, `RestrictRealtime=yes` | 1 | 242 / 233 / 235 / 231 | |
| **`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK`** | 1 | 211 | **Sin `AF_NETLINK`, Node falló** (`uv_interface_addresses`, error 97) |
| `CapabilityBoundingSet=` (vacío) | 1 | | Con cuenta sin privilegios, nada que perder |
| `MemoryMax=`, `TasksMax=`, `CPUQuota=`, **`RuntimeMaxSec=`** | 1 | | Las tres primeras ya existen para las aplicaciones (`LIMIT_DIRECTIVES`); `MemoryMax` exige cgroup v2 (`stat -fc %T /sys/fs/cgroup` = `cgroup2fs`) `[no verificado en Leap 15.6]` |
| `EnvironmentFile=<0600, tmpfs>` | 1 | | Los secretos nunca por `-E` |
| `UMask=0022`, `WorkingDirectory=` | 1 | | |
| `BindsTo=noust-web.service` | 2 | | Ver 2.3 |
| `IPAddressDeny=169.254.0.0/16 fe80::/10` | 2 | 235 | Cierra el robo de credenciales del servicio de metadatos del proveedor |
| `SystemCallFilter=@system-service` | 2 | 187 | Node arrancó; medir con Composer, pip, Ruby |
| `ProtectProc=invisible`, `ProcSubset=pid` | 2 | 247 | Oculta procesos de otras cuentas |
| `InaccessiblePaths=/run/dbus` | 2 | | Cierra el bus del sistema; puede afectar a navegadores sin cabeza |
| `PrivateNetwork=yes` | opt-in | | Compilar sin red (fase "compilar" con red desactivada) |
| `MemoryDenyWriteExecute` | **no** | 231 | Rompe el JIT de Node y Python |
| `PrivatePIDs=` | no | 257 | No existe en Debian 12, Ubuntu 22.04/24.04 ni Leap 15.6 |

Versiones de systemd por distribución (cualquier elección de arriba ≤ 247 funciona en todas):

| Distribución | systemd | Fuente |
|---|---|---|
| Debian 12 | 252.39 | [paquete](https://packages.debian.org/bookworm/systemd) |
| Ubuntu 22.04 | 249.11 | [paquete](https://packages.ubuntu.com/jammy/systemd) |
| Ubuntu 24.04 | 255.4 | `[lab]` |
| openSUSE Leap 15.6 | 254.x | [avisos de SUSE](https://lists.suse.com/pipermail/sle-updates/2024-July/036140.html) |
| RHEL 9 y derivadas | 252 | `[no verificado hoy]` |
| Fedora 41 / 42 / 43 / 44 | 256 / 257 / 258 / 259 | [Fedora Packages](https://packages.fedoraproject.org/pkgs/systemd/systemd/) y comparativa de 2026 |
| Debian 13, Ubuntu 26.04 | 257, 259 | comparativas de 2026 |

Consecuencia: el mínimo real es 249 (Ubuntu 22.04). `--expand-environment=no` (254) no existe en
249/252, así que el escape `$$` es obligatorio en todas y suficiente en todas.

### 5.3 Semántica de la salida, los códigos y los tiempos

Recomendación para el adaptador del runner (una sola implementación junto a `runuser_prefix`):

1. **Salida.** `--pipe` conserva exactamente la semántica actual de `stream` (stdout y stderr
   mezclados en una tubería). Su defecto conocido es el de `> /dev/stderr` con otra cuenta (2.3).
   Ofrecer `--pty` como modo de compatibilidad por aplicación si un build lo necesita, con
   `CI=1 NO_COLOR=1 TERM=dumb` y quitando `\r`. `append:` trunca con `>` y `journal` no admite
   reabrir; se descartan.
2. **Código de salida.** Sin `--collect`, después de `--wait`: `systemctl show -p Result -p
   ExecMainStatus -p ExecMainCode`; luego `systemctl reset-failed`. Mapa: `timeout` → `EXIT_TIMEOUT`
   con `timed_out=True`; `oom-kill` → 137; muerto por señal → 128+n; `exit-code` → el estado.
3. **Tiempo y cancelación.** El plazo del runner sigue mandando, pero la unidad lleva
   `RuntimeMaxSec=plazo+margen` por si Noust muere. Al vencer o cancelar (`CommandCancelled`):
   `systemctl stop <unidad>` **además** de matar al cliente. Al arrancar la consola:
   `systemctl stop 'noust-build-*.service'` (limpieza de huérfanas).
4. **Argv.** Duplicar `$` como `$$` en cada elemento; **no** duplicar `%`. Probar con `$HOME`,
   `${X}`, `%h`, `;`, elementos que empiezan por `-`, `@`, `:` y `+`. La forma D-Bus de `systemd-run`
   pasa un vector, así que `;` y los prefijos de ejecutable no se reinterpretan (en la prueba,
   `;` y `-x` llegaron literales).
5. **Entorno.** Ninguno heredado. Lo que la compilación necesita (`PATH` fijo con rutas absolutas
   de `shutil.which`, `HOME`, cachés, variables de la aplicación) se escribe en un
   `EnvironmentFile` de 0600 en tmpfs, que se borra al terminar. `-E` solo para valores no
   secretos.
6. **`--dry-run`.** `is_read_only` ya trata cualquier `systemd-run` como mutante, así que
   `DryRunRunner` lo omite sin código nuevo (regla 1).

### 5.4 Trampas de compatibilidad, una por una

| Tema | Riesgo | Tratamiento |
|---|---|---|
| **Cachés** | Antes vivían en el `$HOME` de root; en el sandbox `$HOME` es de solo lectura | Directorio por aplicación, p. ej. `/var/cache/noust/build/<app>`, propiedad de `noust-build`, con `HOME`, `XDG_CACHE_HOME`, `npm_config_cache`, `npm_config_store_dir`, `YARN_CACHE_FOLDER`, `COMPOSER_HOME`/`COMPOSER_CACHE_DIR`, `PIP_CACHE_DIR`, `UV_CACHE_DIR`, `GOCACHE`. **Por aplicación y no compartida**: una caché común es un vector de contaminación entre aplicaciones. pnpm enlaza duro desde su almacén: si `/var/cache` y `/var/www` son sistemas de ficheros distintos, copia (más lento) |
| **Credenciales de Git** | La obtención usa la clave de `/root/.ssh` | **Se queda fuera del sandbox**: `git` como root con la clave y `GIT_CONFIG_*`, exporta el release sin `.git`, y **entonces** se compila. La compilación nunca ve la clave. Mejora posterior: sandbox también para `git` con `LoadCredential` (sin verificar aún) |
| **Registros privados** | `~/.npmrc`, `auth.json` de Composer, índices de pip de root dejan de existir | Fallo claro (`401`) con la explicación y un campo de "credenciales de compilación" por aplicación, entregado por `EnvironmentFile` (`NPM_TOKEN` con `${NPM_TOKEN}` en el `.npmrc` del proyecto). Las dependencias `git+ssh://` que hoy funcionan con la clave de root dejarán de hacerlo: decisión deliberada, usar HTTPS con token |
| **Propiedad del release** | El release lo crea root; el servicio lo lee `www-data`; la compilación lo escribe `noust-build` | `chown -R noust-build` justo antes de compilar y `hand_over_tree` (ya existe) al final. El release no está activo mientras se compila, así que el servicio no se ve afectado. Con `RestrictSUIDSGID` no puede crear setuid; `chown -R` no sigue enlaces |
| **`.env` en el release** | El enlace `.env -> shared/.env` apunta a un fichero 0600 de `www-data`, ilegible para `noust-build` | Las variables llegan por `EnvironmentFile` (lo que ya recibe la unidad), y `@next/env`/`dotenv` no verán el fichero. **Riesgo a medir** con fixtures de Next.js, Prisma y Laravel: EACCES no capturado |
| **Escrituras en rutas persistentes** | Un `postinstall` que escribe en `storage/` (`link_shared` lo prevé) | Fallará por EACCES/EROFS. Documentar: la compilación no escribe estado; eso va en "liberar" con la identidad de la aplicación |
| **Red** | La instalación necesita el registro; algunos `build` descargan datos (fuentes de Next.js, motores de Prisma) | Por defecto **con red** (paridad). Perfil estricto opt-in: instalar con red y sin secretos, compilar con `PrivateNetwork=yes` y con secretos. Bloquear el rango `169.254.0.0/16` por defecto (nivel 2). El loopback no se puede negar sin romper herramientas que abren puertos locales |
| **Dependencia `in place`** | Las aplicaciones 1.x compilan sobre el árbol vivo | Cambiar el dueño de un árbol que el servicio usa es peor que el mal que cura. Compilar con la cuenta de servicio y `ReadWritePaths=<app>` (sin las garantías de cuenta separada) y documentar `noust app migrate` como el camino. **Límite conocido, no ocultarlo** |
| **Previews** | Copian el entorno de producción y compilan código de una PR | Sandbox obligatoria desde el primer día; sin variables marcadas como secretas del padre en la fase de compilación; perfil de red estricto por defecto; siguen limitadas a autores de confianza |
| **Contenedores/WSL sin espacios de nombres de montaje** | `ProtectSystem` se ignora sin avisar | **Autoprueba**: al primer uso, una unidad canaria intenta escribir en un directorio protegido y leer un fichero en `InaccessiblePaths`; si no falla, **falla cerrado** con un error accionable y un ajuste explícito y auditado para desactivar. Regla 4: la guarda vive en el chokepoint |
| **`ProtectHome=yes`** | Si el árbol de aplicaciones o un origen está bajo `/home` | Documentado ya en la plantilla de la consola; el adaptador debe detectarlo y no aplicar `ProtectHome` a esa aplicación |

### 5.5 Dónde va en el código (reglas 1, 3 y 4)

- **Un solo punto de entrada.** `BaseDeployer._run` pasa a tener una fase (`build`, `release`,
  `privileged`); el valor por defecto es `build` (con sandbox). Todo uso como root exige
  `privileged=True` explícito. Prueba en `tests/test_architecture.py`: ningún despliegue llama a
  `self.runner.run/stream` sin pasar por `_run`. Es la guarda en el chokepoint.
- **Un solo constructor de argv.** Un `sandbox=` en `CommandRunner.run/stream` (junto a `user=`),
  con `sandbox_prefix(spec)` al lado de `runuser_prefix`, para que `FakeRunner` registre el mismo
  argv que ejecutaría el real (así lo hace hoy con `runuser`).
- **El especificador** (`SandboxSpec`: cuenta, rutas escribibles, ocultas, límites, red, fichero de
  entorno, nombre) lo construye el despliegue; el runner no sabe nada de aplicaciones.
- **Docker Compose**: se mantiene como caso propio (`privileged=True`), con un análisis del archivo
  compose que rechaza `privileged`, `pid: host`, `network_mode: host`, `cap_add`, `devices`,
  montajes fuera del directorio de la aplicación (incluido `docker.sock`), `security_opt` que
  desactive AppArmor/seccomp y `userns_mode: host`, salvo autorización explícita por aplicación
  con sudo mode y auditoría. Sería el primero entre los revisados en hacerlo.

### 5.6 El arnés que demuestra que el `postinstall` falla

Nuevo escenario en `tests/integration/run.py`, usando el patrón de `make_node_repo`/`commit_to`
y una fixture `tests/integration/fixtures/evil-postinstall` (`package.json` con
`"postinstall": "node evil.js"`). `evil.js` es el del laboratorio: por cada acción imprime una línea
JSON con `{acción, resultado, errno}`. El escenario comprueba:

1. **El despliegue tiene éxito** (el sandbox no rompe una compilación legítima) y `built.txt`
   aparece en el release con el propietario final del servicio.
2. Cada acción prohibida devuelve el errno esperado: `/root/pwned` EACCES o ENOENT, lectura de
   `/etc/noust/web-token` y del almacén EACCES, `.env` de otra aplicación ENOENT, `/etc/cron.d` y
   `/usr/local/bin` EROFS, fuera del release EROFS.
3. **Cebos en el anfitrión intactos**: `/root/pwned`, `/etc/cron.d/evil` no existen.
4. **Entorno**: la consola se arranca con `NOUST_SECRET_SENTINEL=x`; el informe dice `none`.
5. **Demonio**: tras el despliegue `pgrep -u noust-build` no encuentra nada.
6. **Salida**: las líneas del `postinstall` aparecen en el registro del despliegue (streaming).
7. **Rutas de fallo**: `exit 3` → `BuildError` con 3; `sleep` por encima del plazo → `timed_out`
   y unidad parada; consumo de memoria → `OutOfMemoryError` (137); cancelación → unidad inactiva
   (`systemctl is-active`); argv con `$HOME` llega literal.
8. **Control negativo** ("probar la prueba"): con la protección desactivada (`build.sandbox off`),
   la misma fixture **sí** escribe `/root/pwned`. Si no, el escenario no demuestra nada.
9. **Casos de compatibilidad con veredicto explícito**: `echo x > /dev/stderr` (hoy falla con
   `--pipe`; el escenario documenta el comportamiento elegido), una fixture de Next.js con
   `.env`, una de Prisma `generate`, una de Composer y una de `pip` con sdist.

Además, una prueba unitaria rápida con `FakeRunner`: el argv contiene las propiedades de nivel 1,
ninguna variable secreta va por `-E`, `$` se duplica y `%` no, y el nombre de la unidad es
determinista.

---

## 6. Cuenta de túnel dedicada

### 6.1 Qué necesita sshd

Fichero `/etc/ssh/sshd_config.d/00-noust-tunnel.conf` (el `00` gana al `50-cloud-init.conf`; el
`Match` no se sale del fichero, E4):

```
Match User noust-tunnel
    AuthorizedKeysFile /etc/ssh/noust/%u.keys
    DisableForwarding no
    AllowTcpForwarding local
    AllowStreamLocalForwarding no
    PermitOpen 127.0.0.1:<puerto de la consola>
    PermitListen none
    PermitTunnel no
    PermitTTY no
    X11Forwarding no
    AllowAgentForwarding no
    GatewayPorts no
    ForceCommand /usr/bin/false
```

- Cuenta: `useradd -r -M -d /nonexistent -s /usr/sbin/nologin noust-tunnel` y `usermod -p '*'
  noust-tunnel` (no `passwd -l`, que deja `!`).
- Claves en `/etc/ssh/noust/noust-tunnel.keys`, de root, 0644 en directorio de root: la cuenta no
  puede modificarlas. La línea sigue con `restrict,port-forwarding,permitopen=...,permitlisten=...,
  command=...` (política en la clave **y** en el servidor).
- `ForceCommand` en el `Match` no se probó en el laboratorio (`[no verificado]`; sí `command=` y
  `nologin`). Probarlo en el arnés.
- Validar con `sshd -t`; comprobar con `sshd -T -C user=noust-tunnel,host=x,addr=127.0.0.1`
  (lo que muestra la política real de la cuenta); recargar (no reiniciar: las sesiones vivas
  sobreviven).
- Si el `sshd_config` principal no tiene `Include /etc/ssh/sshd_config.d/*.conf`, añadir el bloque
  **al final** con marcas "Generated by Noust" (un `Match` debe ir el último).

### 6.2 Cambios en Noust

1. `fleet/authorize.py`: `--ssh-user` por defecto `noust-tunnel`; `--create-tunnel-user` (lo
   propone `fleet-central.md`) crea cuenta, fichero de claves y fichero `Match`; `root` solo con
   `--ssh-user root --i-understand` y un aviso que cite el hallazgo 2.
2. `read_sshd_settings(user=...)`: `sshd -T -C user=...,host=...,addr=...`; `sshd_blockers`
   evalúa la cuenta real. Añadir a los bloqueos `AllowUsers`/`AllowGroups` que la excluyan,
   `AllowStreamLocalForwarding` distinto de `no`, `UsePAM no` con cuenta bloqueada y `StrictModes`.
   `sshd -T` con `-C` requiere `user`, `host` y `addr`.
3. **CORRECCIÓN inmediata y barata** (independiente del resto): reescribir el docstring de
   `authorize.py` y `docs/CENTRAL.md` para decir la verdad sobre la clave en root: reenvía a un puerto
   **y puede crear sockets Unix como root**; recomendar la cuenta dedicada.
4. `noust fleet authorize --access read|deploy|admin` (el techo de `fleet-central.md`): el nodo
   recorta `X-Noust-Actor-Scope` a `min(claim, techo)` en `admit_fleet`, y ignora
   `X-Noust-Elevated` si el techo es menor que `admin`. Sin el techo, la cuenta de túnel reduce el
   riesgo SSH pero una central comprometida sigue siendo `admin` por la API.
5. Migración de un nodo ya autorizado con root: `authorize --ssh-user noust-tunnel` emite un código
   nuevo; la central necesita poder cambiar el `ssh_user` de un nodo sin volver a darlo de alta
   (`[no explorado]`: hoy el registro lo guarda del código de unión); después
   `deauthorize --ssh-user root`. Solo entonces `PermitRootLogin no` deja de estar bloqueado
   por la flota.

### 6.3 Trampas de la cuenta dedicada

| Trampa | Efecto | Prevención |
|---|---|---|
| `!` en el campo de contraseña y `UsePAM no` | La clave válida se rechaza (E3) | Crear con `*` |
| `AllowUsers`/`AllowGroups` global sin la cuenta | Rechazada (E7) | Detectar con `-C`; ofrecer añadirla, con guarda |
| `50-cloud-init.conf` u otro fichero anterior | Un valor "primero gana" anula el nuestro | Nombre `00-`, y verificar siempre con `sshd -T -C`, no leyendo ficheros |
| `/etc/nologin` | Impide el acceso de todo no root, túnel incluido | Es precisamente lo que abría el hueco de E8; con la cuenta dedicada no se puede crear |
| `pam_access`, `pam_time`, `faillock` | Bloqueos ajenos a sshd | Diagnóstico: el fallo del túnel ya se muestra literal |
| fail2ban | Reconexiones con una clave errónea pueden bloquear a la central | `ignoreip` con la IP de la central si es fija; documentar |
| SELinux (RHEL/Fedora) | `sshd_t` debe poder conectar al puerto de la consola | `[no verificado]`; probar en Fedora con SELinux en `enforcing` |
| Puerto de sshd | En Ubuntu 24.04 lo fija `ssh.socket` | Leer `ss -tlnp` además de `sshd -T` |
| Clave del operador en la cuenta | Compartir la cuenta con humanos rompe `op.acc.1.3` | Cuenta exclusiva del enlace |

### 6.4 Alternativas descartadas para el túnel

- **Agente con conexión saliente** (Portainer Edge, WireGuard, Cloudflare Tunnel): quita el SSH
  entrante, pero obliga a la central a aceptar conexiones (la joya de la corona expuesta), o a un
  componente nuevo. No para 3.1.
- **HTTPS con mTLS directo al nodo** (Portainer clásico, puerto 9001): abre un puerto público con
  la API de root. El túnel SSH mantiene la consola solo en loopback; es una ventaja a conservar.
- **Reutilizar la cuenta del operador** (`--ssh-user ubuntu`, modelo Forge/Ploi): la clave queda en
  una cuenta con shell, sudo y `authorized_keys` propio que puede editar.

---

## 7. Comprobaciones de hardening con arreglos que no bloquean al operador

Las comprobaciones concretas, su detección y su interfaz están en `server-management.md` (sección 3
y tabla de la línea ~937). Aquí quedan la matriz de riesgos y las invariantes, cruzadas con lo
verificado.

### 7.1 Matriz

| Comprobación | Detección (solo lectura, por el runner) | Arreglo de un clic | Guarda que lo hace seguro |
|---|---|---|---|
| Login de root por SSH | `sshd -T -C user=root,host=x,addr=y` → `permitrootlogin` | `PermitRootLogin prohibit-password` y, en un segundo paso, `no` | Para `no`: prueba de acceso (7.2) de **otra** cuenta, con sudo utilizable (`sudo -l -U <u>` muestra `NOPASSWD` o la cuenta tiene contraseña: `passwd -S` ≠ `L`/`NP`); la clave de la central no vive en root (o se migra antes, 6.2) |
| Contraseñas SSH | `passwordauthentication`, `kbdinteractiveauthentication`, `authenticationmethods` efectivos | `PasswordAuthentication no`, `KbdInteractiveAuthentication no` | Prueba de acceso por clave con huella vista en el registro (7.2); `StrictModes` no la rechazaría; sesión de confirmación desde una segunda conexión |
| Contraseña de root local | `passwd -S root` | Solo informa | Bloquearla sin otra cuenta con sudo deja al operador sin root desde la consola del proveedor |
| Contraseñas vacías | `PermitEmptyPasswords`; campo 2 vacío en `getent shadow` | Bloquear la cuenta (`usermod -L`) | No es root ni la cuenta de la sesión actual |
| Cortafuegos frente a puertos | `ss -tlnH`, `ufw status`, `firewall-cmd --state`, `nft list ruleset`; puertos publicados de Docker | Habilitar reglas mínimas | **Permitir antes de habilitar**: todos los puertos de `sshd -T`, de `ssh.socket` y el de la conexión establecida del operador; no borrar reglas; hombre muerto. Aviso: Docker se salta ufw |
| fail2ban | `fail2ban-client status sshd`; en Debian 12 no hay `auth.log` por defecto y hace falta `backend = systemd` | Instalar y activar `[sshd]` en `jail.d` | `ignoreip` con la IP de la sesión del operador y de la central |
| Actualizaciones desatendidas | `apt-config dump APT::Periodic`, `apt-daily-upgrade.timer`, `dnf-automatic` | Instalar y activar (solo seguridad) | Sin reinicio automático por defecto; avisar de que un servicio actualizado se reinicia |
| Actualizaciones de seguridad pendientes y reinicio | `apt-get -s`, `dnf updateinfo list security`, `zypper lp --category security`; `/var/run/reboot-required` | Instalar (como trabajo) | Nunca automático; mostrar el reinicio pendiente |
| Servicios expuestos | Misma lista de `ss` clasificada: bases de datos y Redis en `0.0.0.0`; **2375/2376 de Docker** | Enlazar a `127.0.0.1` (Noust ya gestiona las bases de datos) | Comprobar que ninguna aplicación local por red las usa |
| Docker | Usuarios del grupo `docker` (equivale a root) | Solo informa | |

### 7.2 Protocolo de aplicación segura (común a todo arreglo que toque el acceso)

Coincide con el de `server-management.md` (secciones 3, "Endurecer sshd de forma segura") y lo
amarra a `op.exp.4.r2.1` del ENS:

1. **Estado efectivo antes y después**, siempre con `sshd -T -C`, nunca leyendo ficheros (el
   `50-cloud-init.conf` anula lo que pongas en el principal).
2. **Prueba de acceso** para todo lo que pueda dejar fuera al operador: (a) estática: una cuenta que
   sshd deja entrar, con clave, shell válido, no bloqueada, con permisos que `StrictModes` acepta;
   (b) **dinámica**: el registro de sshd muestra `Accepted publickey for <u> ... ssh2:
   ED25519 SHA256:<huella>` con una huella que sigue en su `authorized_keys`, en la ventana elegida
   (`journalctl _COMM=sshd -o json`, filtrado en Python; el registro con `LogLevel VERBOSE` lo
   hace más fiable). La clave de la central no cuenta. Si no hay prueba dinámica, no se ofrece el
   arreglo, se ofrece "añade tu clave y abre otra sesión". Ninguno de los productos revisados
   hace esto.
3. **Un fichero propio** `00-noust-<tema>.conf`, escrito de forma atómica y con marca "Generated by
   Noust"; nunca se edita el `sshd_config` principal; se valida con `sshd -t` y, si falla, se
   borra y se muestra la salida literal.
4. **`reload`, no `restart`** (las sesiones abiertas sobreviven), y verificación del valor efectivo.
5. **Confirmar o revertir**: una unidad transitoria (`systemd-run --on-active=120s`) ejecuta la
   reversión salvo que el operador confirme **desde una conexión nueva** (consola web o segunda
   sesión). Para el cortafuegos, 180 s. Cancelar el temporizador es la confirmación.
6. **Nada que dependa de un solo canal**: si SSH se rompe, la consola sigue viva por su túnel y
   puede revertir; si ambos se rompen, el temporizador revierte.
7. **Se puede rehacer en seco** (`DryRunRunner`, `DryRunFileSystem`), queda en auditoría y es
   reversible (`noust server ssh revert`).
8. **En la flota** los arreglos pasan por la API del nodo, con `x-noust-requires-elevation` y con
   el techo `admin` (6.2); la central no abre un shell.

Qué **no** se puede garantizar, y hay que decirlo en la interfaz: que el operador tenga la clave
privada correspondiente (el registro solo demuestra que alguien la usó), que exista acceso por la
consola del proveedor, y que herramientas ajenas (copias, Ansible) no dependan del login de root.

---

## 8. Comparativa final y registros de decisión

### 8.1 Opciones frente a criterios

Escala: sí / parcial / no. "Deps" = dependencias nuevas de ejecución.

**Aislamiento de compilaciones**

| Opción | Frontera real | Coste de código | Deps | Compatibilidad | Riesgo residual | Veredicto |
|---|---|---|---|---|---|---|
| A. Sin cambios (root) | no | 0 | 0 | total | Compromiso total con un `postinstall` | Rechazada |
| B. `user=` con `runuser` | parcial (2.2) | mínimo | 0 | alta | Entorno heredado, lectura de otras apps, `/tmp` persistente, demonios | Insuficiente (útil solo como paso intermedio) |
| **C. Unidad transitoria + cuenta `noust-build`** | **sí** (2.2) | medio (runner, `_run`, arnés) | **0** (systemd ya es requisito) | media (cachés, credenciales, `.env`) | Red abierta; núcleo compartido | **Elegida** |
| D. C con `DynamicUser=` | sí | medio | 0 | baja | uids reciclados sobre el release | Rechazada |
| E. Contenedor o bubblewrap por compilación | sí | alto | Docker/bubblewrap | media | Docker root; no existe en todos | Rechazada por defecto (Compose sigue aparte) |
| F. Solo `--ignore-scripts` | no | bajo | 0 | baja (rompe esbuild, sharp, Prisma) | El `build` propio y PackageGate | Opcional, no sustituto |

**Cuenta de túnel**

| Opción | Reenvío de sockets Unix | Modificable por la cuenta | Auditoría (`op.acc.1.3`) | Coste | Veredicto |
|---|---|---|---|---|---|
| Clave en root (hoy) | **sí (hueco)** | n/a (root) | no | 0 | Solo con aviso y `--i-understand` |
| Cuenta del operador | depende | sí | no | bajo | Rechazada |
| **`noust-tunnel` sin home, política en sshd y en la clave** | **no** | **no** | **sí** | medio | **Elegida** |
| Agente saliente / mTLS | n/a | n/a | sí | alto | Fuera de 3.1 |

### 8.2 Registros de decisión

#### DR-1. Compilaciones e instalaciones sin privilegios (ítem 44; ENS G14)

- **Contexto.** Sección 1 y 2.2: root, entorno completo, sin frontera; Dokploy y Coolify muestran
  el coste de no tenerla; Shai-Hulud 2.0 y PackageGate muestran que el vector es real en 2025-2026.
- **Opciones.** A-F de 8.1.
- **Decisión.** C. Las fases instalar, compilar y ganchos corren en una unidad transitoria con la
  cuenta `noust-build` y las propiedades de nivel 1 de 5.2; la fase "liberar" (migraciones) corre
  con la identidad de la aplicación; la obtención del código sigue como root **fuera** del sandbox y
  exporta el release antes de compilar. **Falla cerrada**: si la autoprueba (5.4) no demuestra que el
  sandbox funciona, no se compila como root en silencio; hay un ajuste explícito y auditado para
  desactivarlo. Previews y aplicaciones nuevas primero; existentes con aviso de una versión.
- **Por qué.** Es la única opción probada que bloquea todas las acciones prohibidas de la prueba (2.2), no añade ninguna
  dependencia, reutiliza `systemd` que Noust ya exige, se integra en el punto único del runner
  (regla 1) y en `_run` (regla 4) y conserva el streaming y los códigos de salida.
- **Consecuencias.** Cachés y credenciales por aplicación; las `.env` llegan por
  `EnvironmentFile`; los registros privados con credenciales de root dejan de funcionar hasta que se
  configuren; el argv exige escape `$$`; la cancelación exige `systemctl stop`; `--pipe` con
  scripts que reabren `/dev/stderr` falla (modo `--pty` de compatibilidad); en `inplace` el límite
  es menor y se documenta; `COMPOSER_ALLOW_SUPERUSER` deja de hacer falta.
- **Verificación.** Arnés de 5.6 con control negativo, más las pruebas de argv con `FakeRunner`.
- **No decidido aquí.** Cuentas de servicio por aplicación (Forge, ServerPilot). Aumentaría la
  frontera entre aplicaciones en ejecución (hoy comparten `www-data` y leen los `.env` de las demás)
  y haría innecesaria una cuenta de compilación compartida. Candidata a 3.2.

#### DR-2. Cuenta de túnel sin privilegios y techo de la central (ítem 45; ENS G11)

- **Contexto.** La clave de la central vive en `authorized_keys` de root y `permitlisten` no limita
  los sockets Unix (2.4 E8); `read_sshd_settings` no ve `Match`; el nodo confía en el alcance que la
  central declare (sección 0, punto 5).
- **Opciones.** 8.1 (tabla de túnel) y 6.4.
- **Decisión.** `noust-tunnel` por defecto; sin home, shell `nologin`, contraseña `*`; claves en un
  fichero de root; política duplicada en `sshd` (`Match User`) y en la clave; techo `--access` en el
  nodo. `--ssh-user root` queda con `--i-understand` y aviso. La corrección del docstring y de
  `docs/CENTRAL.md` se hace ya, sin esperar al resto.
- **Por qué.** Cierra el hueco de sockets Unix, cumple `op.acc.1.3` y `op.acc.4.2`, permite
  `PermitRootLogin no` en los nodos, y sigue siendo compatible con CIS 5.1.8 gracias al `Match`.
- **Consecuencias.** `authorize` necesita privilegios para crear cuenta y fichero de `sshd`; nueva
  evaluación con `-C`; migración de los nodos existentes (código de unión nuevo, cambio de
  `ssh_user`); hay que probar SELinux; el techo por nodo cambia el contrato de `admit_fleet` y
  necesita versión mínima en la central.
- **Verificación.** Escenarios del arnés de flota (`fleet_run.py`) con `PermitRootLogin no`,
  `DisableForwarding yes` global, `AllowUsers` y `UsePAM no`; intento de `-R /etc/nologin` que debe
  fallar; nodo con techo `read` que rechaza un despliegue aunque la central diga `admin`.

#### DR-3. Login de root por SSH

- **Contexto.** Ni Noust ni la flota necesitan root por SSH tras DR-2; la CLI exige root **local**
  (con `sudo`); Mozilla, CIS, Cloudron y RunCloud lo desaconsejan; Forge y Ploi no pueden prescindir
  de él; el argumento contrario ("acaba en sudo sin contraseña") solo vale si esa cuenta lo tiene.
- **Decisión.** Noust no requiere `PermitRootLogin yes` en ningún caso. La comprobación lo marca
  como aviso; el arreglo ofrece `prohibit-password` y luego `no` **solo** con prueba de acceso de
  otra cuenta con sudo utilizable y sin la clave de la central en root. La documentación cambia
  `root@` por la cuenta de túnel y por `sudo`.
- **Consecuencias.** Herramientas ajenas que entren como root (copias, Ansible) pueden romperse:
  se avisa. Los proveedores que solo dan root en la imagen exigen crear antes una cuenta; el asistente
  la crea con clave, con `NOPASSWD` solo si el operador lo elige.

#### DR-4. Comprobaciones de hardening con arreglos sin bloqueo (ítem 46; ENS G13)

- **Contexto.** Sección 7; `server-management.md` ya tiene la interfaz.
- **Decisión.** Comprobaciones de solo lectura primero; cada arreglo es una transacción con el
  protocolo de 7.2 (efectivo con `-C`, prueba de acceso, fichero propio `00-`, `reload`, confirmar o
  revertir); se rechaza todo arreglo que no pueda demostrar la guarda; sin arreglo, "guiado" con la
  instrucción exacta. Los arreglos pasan por la API del nodo con elevación y techo `admin`.
- **Por qué.** Ninguno de los productos revisados verifica que el operador pueda volver a entrar;
  RunCloud lo deja a criterio del usuario. Es el punto más fácil de superar y responde
  directamente a `op.exp.4.r2.1`.
- **Consecuencias.** Más estado que gestionar (unidades de reversión, ficheros propios);
  tests con `DryRunRunner`; arnés con sshd real y un intento deliberado de bloqueo.

#### DR-5. Cuestiones abiertas (decide el dueño)

1. ¿`build.sandbox` activo por defecto en 3.1 para todas las aplicaciones, o solo para previews y
   nuevas con aviso en las existentes? Recomendación: previews y nuevas ya; existentes una versión
   después, con `noust doctor` que anticipe el efecto.
2. ¿Aceptamos el `--pty` como modo de compatibilidad, o preferimos `--pipe` a secas y documentar el
   límite de `/dev/stderr`? Recomendación: `--pipe` por defecto, `--pty` opt-in.
3. ¿Perfil de red estricto (instalar con red y sin secretos; compilar sin red y con secretos)
   por defecto en previews? Recomendación: sí en previews, opt-in en el resto, tras medir cuántas
   plantillas populares se rompen.
4. ¿Rechazo por defecto de `privileged` y `docker.sock` en Compose? Recomendación: sí, con
   excepción explícita por aplicación.
5. Cuentas de servicio por aplicación en 3.2.

---

## 9. Orden de implementación y riesgos

| Orden | Trabajo | Motivo | Riesgo |
|---|---|---|---|
| 1 | **Corregir docstring y `docs/CENTRAL.md`** (clave en root y sockets Unix); `read_sshd_settings` con `-C` | Corrige una afirmación falsa; sin dependencias | Ninguno |
| 2 | Adaptador `sandbox=` del runner, `_run` con fases, arnés de 5.6, autoprueba | Base de DR-1 | Compatibilidad de cachés, `.env` y `/dev/stderr`: medir con fixtures reales antes de activar |
| 3 | Sandbox para previews y aplicaciones nuevas; "liberar" con identidad de aplicación | El caso de mayor riesgo | Migraciones con red a la base de datos |
| 4 | `noust-tunnel`, `--access`, migración de nodos | DR-2 | SELinux; cambio de `ssh_user` en la central |
| 5 | Comprobaciones de solo lectura y arreglos de SSH con confirmar o revertir | DR-4 | Bloqueo del operador: el arnés debe provocarlo a propósito |
| 6 | Análisis de archivos compose | Cierra el último camino a root | Falsos positivos legítimos: excepción por aplicación |

---

## 10. Lo que no se pudo verificar y cómo comprobarlo

- Debian 12, Ubuntu 22.04, Fedora, openSUSE y RHEL: correr el arnés en cada imagen. Las versiones
  mínimas salen del man de 255.
- `LoadCredential=`/`SetCredential=`: en una VM real, no en Docker.
- `BindsTo=noust-web.service` para transitorias, `IPAddressDeny=169.254.0.0/16` sin `Allow`,
  `ForceCommand` dentro del `Match`, `ProtectProc=invisible` y `SystemCallFilter` con más
  herramientas.
- SELinux en `enforcing` con la consola en un puerto alto.
- Cambio de `ssh_user` de un nodo en la central sin re-alta.
- CapRover (sin CVE encontrados no significa que no haya) y Portainer (solo se leyó la página del
  agente); el detalle interno de RunCloud y ServerPilot (solo documentación pública).
- Números de CVE y de CVSS de 2026 tomados del buscador y de páginas secundarias; los identificadores
  de Dokploy y Coolify se comprobaron contra la página del aviso o de OpenCVE; Coolify da 9,9 o 10,0
  según la fuente para CVE-2025-64420.
- El resumen de los hilos de Hacker News viene de la API de HN, no de leer los comentarios enteros.
- No se encontró una guía CCN-STIC para Debian/Ubuntu; no se confirmó "CCN-STIC-619".

## 11. Fuentes

Coolify: [instalación](https://coolify.io/docs/get-started/installation),
[usuario no root](https://coolify.io/docs/knowledge-base/server/non-root-user),
[The Hacker News](https://thehackernews.com/2026/01/coolify-discloses-11-critical-flaws.html),
[Censys](https://censys.com/advisory/cve-2025-64424-cve-2025-64420-cve-2025-64419/),
[GHSA-9wqm-fg79-4748](https://github.com/coollabsio/coolify/security/advisories/GHSA-9wqm-fg79-4748),
[WZ-IT](https://wz-it.com/en/blog/coolify-cve-security-vulnerabilities-update-2025-2026/).
Dokploy: [servidores remotos](https://docs.dokploy.com/docs/core/remote-servers/instructions),
[GHSA-h67g-mpq5-6ph5](https://github.com/Dokploy/dokploy/security/advisories/GHSA-h67g-mpq5-6ph5),
[GHSA-66v7-g3fh-47h3](https://github.com/Dokploy/dokploy/security/advisories/GHSA-66v7-g3fh-47h3),
[CVE-2026-72901](https://app.opencve.io/cve/CVE-2026-72901), [HN 44548952](https://news.ycombinator.com/item?id=44548952).
CapRover: [best practices](https://caprover.com/docs/best-practices.html).
Forge: [seguridad](https://laravel.com/forge/docs/servers/security),
[aislamiento](https://laravel.com/forge/docs/sites/user-isolation),
[KB de servidores](https://laravel.com/forge/docs/knowledge-base/servers).
Ploi: [roadmap 1550](https://roadmap.ploi.io/projects/1-server-level-requests/items/1550-allow-server-health-checks-to-authenticate-as-the-ploi-user-instead-of-root),
[roadmap 1480](https://roadmap.ploi.io/projects/1-server-level-requests/items/1480-user-isolation-directory-privacy-hardening).
RunCloud: [endurecimiento SSH](https://runcloud.io/docs/ssh-service-hardening-on-runcloud),
[aplicaciones web](https://runcloud.io/docs/understanding-web-applications-on-runcloud).
ServerPilot: [usuarios del sistema](https://serverpilot.io/docs/sysusers/).
Cloudron: [seguridad](https://docs.cloudron.io/security/).
Portainer: [agente](https://docs.portainer.io/admin/environments/add/docker/agent),
[CVEDetails](https://www.cvedetails.com/vulnerability-list/vendor_id-19294/product_id-50211/Portainer-Portainer.html).
Dokku: [usuarios](https://dokku.com/docs/deployment/user-management/).
Kamal: [SSH](https://kamal-deploy.org/docs/configuration/ssh/).
sshd: [sshd_config](https://man.openbsd.org/sshd_config), [sshd](https://man.openbsd.org/sshd.8),
[notas de OpenSSH 7.8](https://www.openssh.org/txt/release-7.8) (introduce `PermitListen` y `permitlisten`),
[Ubuntu: OpenSSH](https://ubuntu.com/server/docs/how-to/security/openssh-server/),
[Ubuntu 24.04](https://discourse.ubuntu.com/t/noble-numbat-release-notes/39890),
[Mozilla](https://infosec.mozilla.org/guidelines/openssh).
CIS y ENS: [Tenable 5.1.8](https://www.tenable.com/audits/items/CIS_Ubuntu_Linux_24.04_LTS_v1.0.0_L2_Server.audit:e36f4a50cc8cb8e178f39025f4ade116),
[CIS Ubuntu 24.04 v2.0.0](https://www.tenable.com/audits/CIS_Ubuntu_Linux_24.04_LTS_v2.0.0_L2_Server),
[BOE-A-2022-7191](https://boe.es/boe/dias/2022/05/04/pdfs/BOE-A-2022-7191.pdf),
[Red Hat CCN-STIC](https://access.redhat.com/compliance/ccn-stic).
Cadena de suministro: [Zscaler](https://www.zscaler.com/blogs/security-research/shai-hulud-v2-poses-risk-npm-supply-chain),
[Stéphane Robert](https://blog.stephane-robert.info/en/post/npm-no-longer-runs-install-scripts/),
[BleepingComputer](https://www.bleepingcomputer.com/news/security/hackers-can-bypass-npms-shai-hulud-defenses-via-git-dependencies/).
Otros: [fail2ban](https://raw.githubusercontent.com/fail2ban/fail2ban/master/config/jail.conf),
[Debian Wiki](https://wiki.debian.org/UnattendedUpgrades),
[Docker y ufw](https://docs.docker.com/engine/network/packet-filtering-firewalls/),
[HN 32556165](https://news.ycombinator.com/item?id=32556165),
[paquete systemd Debian](https://packages.debian.org/bookworm/systemd),
[paquete systemd Ubuntu 22.04](https://packages.ubuntu.com/jammy/systemd).
Manuales locales de systemd 255 (`systemd-run(1)`, `systemd.exec(5)`, `runuser(1)`).
