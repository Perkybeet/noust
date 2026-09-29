# La página Servidor como gestor de la VPS (ítems 29, 32 y 46)

Investigación para 3.1, 2026-09-29, rama `dev/3.1`. Solo lectura sobre el repositorio: no hay
decisiones tomadas ni código escrito. Toca también los ítems 33 (la central gestiona la flota),
37 (Settings por servidor), 40 (revisión de IA de todas las páginas) y 41 (Overview).

Convenciones del documento: los argv van como listas (`["apt-get","-s",...]`) porque todo pasa
por `CommandRunner`; "verificado" significa que lo comprobé en esta máquina (Ubuntu 24.04,
systemd 255, apt 2.8.3) o en la documentación citada; "verificar en el arnés" es lo que no pude
comprobar y debe cubrir `tests/integration/run.py` antes de darlo por bueno.

---

## 0. Resumen y decisiones propuestas

1. **Un `noust server` y un `/api/server` nuevos, con las acciones.** `/api/system/*` sigue siendo
   observabilidad pura (su docstring lo declara y ahí se retiró el `kill`); todo lo que cambia la
   máquina va en un router nuevo con `require_elevated`, jobs y CLI gemelo.
2. **La página pasa de 1 columna de 5 secciones (3599 px de alto a 1440, 4284 px a 390) a 7 pestañas
   con URL**: Resumen, Actualizaciones, Seguridad, Almacenamiento, Servicios, Registros, Sistema.
   El Resumen cabe en una pantalla; nada de lo que hoy hay se pierde, se reparte.
3. **Actualizaciones**: lista con las de seguridad marcadas, aplicar todas o solo seguridad como
   job **dentro de una unidad transitoria de systemd** (si el paquete `noust` se actualiza, el
   postinst reinicia `noust-web` y mataría un job normal), "hace falta reiniciar" con el motivo,
   servicios con librerías viejas, actualizaciones automáticas nativas de cada distro.
4. **Reinicio y apagado no son jobs sino acciones programadas** (un job moriría con la máquina):
   `shutdown -r +N`, cancelables, con comprobaciones previas (¿volverá el servidor? ¿vuelve
   `noust-web`? ¿hay un deploy en marcha? ¿`fstab` roto?) y un evento "el servidor volvió".
5. **Cortafuegos frente a sockets reales** con la advertencia de Docker (los puertos publicados se
   saltan ufw), calculada con `psutil` + `docker ps`, no con parseo de iptables.
6. **SSH con "commit confirmado"**: verificar con pruebas estáticas y con el registro de accesos
   que una clave funciona, escribir un drop-in `00-noust.conf` (primer valor gana), `sshd -t`,
   `reload`, verificar con `sshd -T`, y revertir solo si el operador no confirma en 120 s.
7. **Una sola lista de comprobaciones de endurecimiento** (ítem 46): 30 comprobaciones con
   evidencia, gravedad y arreglo (un clic, guiado o ninguno), compartida por Resumen, Seguridad,
   `noust health` y la lista "Necesita atención" del Overview (ítem 41).
8. **Decisión de seguridad que hay que tomar (sección 5.3)**: `fleet_refusal` ya dice que una
   central comprometida no debe poder "dejar fuera al operador, ampliar la exposición del nodo ni
   acuñarse una credencial que sobreviva a la revocación". Añadir una clave SSH, cambiar sshd o el
   cortafuegos desde la central rompe esa promesa (y "la central nunca tiene shell"). Propongo
   rechazarlo por defecto para tokens de flota, con opt-in del operador del nodo.
9. **Por nodo sale casi gratis**: `/server` no está en `CENTRAL_ONLY_PATHS`, el proxy reenvía toda
   `/api/...`, y la elevación se publica con `x-noust-requires-elevation`. Lo que sí hay que
   diseñar: `GET /api/server/summary` barato y cacheado para la vista de flota (ítem 33) y el
   estado "reiniciando (esperado)" en la central.

Prioridades (detalle en la sección 7): **MUST** = pestañas, actualizaciones, reinicio,
cortafuegos+puertos, SSH+fail2ban, disco+swap, hora/hostname, registros de cualquier unidad,
comprobaciones de endurecimiento, CLI y nodo. **SHOULD** = análisis con `du`, timers/sockets,
persistencia del journal, tabla de fin de vida, ventanas de reinicio. **NO**: matar procesos,
cambiar el puerto SSH, gestionar usuarios del sistema, redimensionar particiones.

---

## 1. Lo que existe hoy

### 1.1 La página `/server`

Ruta `panel/src/routes/_console/server.tsx` → `ServerPage` (`panel/src/features/server/ServerPage.tsx`).
Sin pestañas, una columna (`flex flex-col gap-8`) con cinco secciones apiladas. Medida real: las
capturas de `panel/e2e/__screens__/{dark,light}/server-*.png` (de la 2.0.1, mismo diseño que el
código actual) tienen **3599 px de alto tanto a 1440 como a 1920 de ancho, y 4284 px a 390**.

| Orden | Sección | Datos | Qué muestra |
|---|---|---|---|
| 1 | Salud | `GET /api/system/health` → `collect_health_report` | Veredicto (sano / necesita atención / crítico), razones (avisos y problemas; un certificado enlaza a `/domains?q=`), seis comprobaciones: espacio en disco **solo del directorio de apps**, Nginx, Apache, Aplicaciones, Certificados SSL, Memoria. `CommandHint noust health`. |
| 2 | Sistema | `GET /api/system` y `/api/system/version` | Cinco `StatTile` (host+OS, kernel+uptime, CPU%+núcleos+carga, memoria, versión de Noust y si hay actualización) y una tabla de discos (montaje, dispositivo, usado, total, %) con `psutil.disk_partitions()`. |
| 3 | Red | `GET /api/system/network` | Interfaces: estado, direcciones, enviado, recibido. |
| 4 | Procesos | `GET /api/system/processes?sort_by&limit=25` | 25 filas ordenables. La línea de comandos solo la ve una credencial `admin` (`sees_command_lines`). |
| 5 | Monitor de recursos | `/api/monitor/*` | Estado de la unidad `noust-monitor` (instalar, habilitar, arrancar, parar), umbrales, hallazgos abiertos y "enviar correo de prueba". |

Observaciones sobre lo que hay (de las capturas y del código):

- **La tabla de discos es ruido**: la captura enseña 16 filas idénticas de bind mounts de
  Docker Desktop (`/mnt/wsl/docker-desktop-bind-mounts/...`, todas `/dev/sdf`) antes de llegar a la
  siguiente sección. En un servidor con Docker pasa lo mismo con los `overlay` si `psutil` los
  lista. Hace falta filtrar (tipo de sistema de ficheros, dispositivo repetido, prefijos
  `/var/lib/docker`, `/run`, `/snap`) y añadir inodos.
- **La memoria del API ya trae swap** (`swap_total_gb`, `swap_used_gb`, `swap_percent`) y la
  página no la enseña.
- **"Disk Space" de la salud mide `Config().apps_directory`**, no el peor montaje: un `/var` lleno
  con `/var/www` en otro disco no se ve.
- **Ninguna acción sobre la máquina**: la única cosa que se puede pulsar es la del monitor. Los
  procesos son de solo lectura por diseño (D5).
- **Los endpoints de ciclo de vida del monitor** (`/api/monitor/install|uninstall|enable|disable|start|stop|scan`)
  exigen scope `admin` (todo `POST` lo exige en `required_scope`), pero **no llaman a
  `require_elevated`**: parar o desinstalar el monitor no pide modo sudo. Conviene alinearlo al
  tocar la página.
- El nombre de las comprobaciones llega en inglés desde `health.py` ("Disk Space") y la consola
  lo reformula con un `switch` (`checkName`); al añadir 30 comprobaciones eso no escala: cada una
  necesita un `id` estable y la consola traduce por `id`, no por el texto.

### 1.2 API existente

| Ruta | Scope | Modo sudo | Notas |
|---|---|---|---|
| `GET /api/system` | read | no | hostname, os (`/etc/os-release` PRETTY_NAME), kernel, uptime, cpu (muestra de 0,1 s), memoria+swap, discos. |
| `GET /api/system/{cpu,memory,disks,network,processes,version,health,machine}` | read | no | Síncronos (`def`, hilo aparte). `machine` también viaja por SSE cada 5 s. |
| `GET /api/monitor/{status,config,metrics,processes,observations}` | read | no | |
| `POST /api/monitor/{scan,install,uninstall,enable,disable,start,stop,test-email}`, `POST .../observations/{id}/acknowledge` | admin (automático) | **no** | |
| `GET /api/services[?noust_only=false]`, `GET /api/services/{name}`, `.../logs`, `.../config` | read (config y logs de unidades propias: admin) | no | |
| `POST /api/services/{name}/{start,stop,restart,enable,disable}` | admin | no | `_refuse_own_unit` rechaza las unidades de Noust; una unidad ajena no pasa `ServiceManager._require_managed`. |
| `POST /api/services` (crear), `PUT /api/services/{name}/config`, `DELETE /api/services/{name}` | admin | **sí** | |
| Jobs: `web/jobs.py`, `JobType` (deploy, update, backup, restore, rollback, push, cert_*, service_action, site_action, delete, migrate, zero_downtime, custom), `get_job_manager().create_job(...)` → `202 JobAcceptedResponse`. Máximo 3 a la vez, persistidos con su log; un reinicio del panel los marca `failed` ("Interrupted by a panel restart"). | | | |

Política de scope (`web/auth.py::required_scope`): todo `GET` es `read`; toda mutación es `admin`
salvo dos rutas `deploy`. Un `GET` que enumera superficie de ataque (claves SSH, reglas,
`sshd -T`) **no** se protege solo: hay que llamar a `ensure_scope(..., "admin")` o colgar un
`Depends(require_scope("admin"))` de todo el router, como hace `audit.py`.

### 1.3 CLI existente

No existe `noust server`. Lo más cercano: `noust health [--json]` (mismo `collect_health_report`
que el API), `noust service create|list [--all]|status|start|stop|restart|logs [-f]|delete`,
`noust monitor status|scan|run|install|enable|disable|uninstall|test-email|config`, `noust web ...`,
`noust diagnose` (por app), `noust fleet|node|central`. Ninguna gestiona la VPS. Los comandos son
grupos Click en `cli/commands/*.py` registrados en `COMMAND_MODULES` de `cli/app.py`, con
`json_option()` y `pass_context`; un grupo nuevo es una línea más en ese diccionario.

### 1.4 La página Servicios (`/services`) y su relación con esto

`features/services/` (lista con búsqueda y el interruptor "Mostrar todas las unidades", detalle con
`KeyValueList`, `LogViewer` por WebSocket `useLogStream`, `UnitEditor`, alta con
`CreateServiceDialog`). Es la vista de **las unidades que Noust creó** (workers, colas); "mostrar
todas" añade las ajenas **de solo lectura**: `ServiceManager` es el punto de control (regla 4) y
se niega a actuar sobre lo que no es suyo. Por eso "gestionar servicios de la VPS" (nginx, docker,
sshd, fail2ban, postgres) **no existe hoy**: hay que añadir una capacidad nueva y estrecha (5.2),
no aflojar `_require_managed`.

### 1.5 Piezas que hay que reutilizar (regla 3)

| Pieza | Dónde | Para qué |
|---|---|---|
| `PACKAGE_MANAGERS`, `detect_package_manager()` (apt-get, dnf, zypper, pacman por presencia del ejecutable) | `cli/commands/setup.py` (~140) | Es la tabla de familias. Vive en un módulo del CLI y `core/dependencies.py`, `managers/database/base.py` y `cli/commands/web.py` reescriben sus propias llamadas a `apt-get`. Moverla a `core/packages.py` es prerrequisito. |
| `read_sshd_settings`, `sshd_blockers`, `SshdSettings` (usa `sshd -T`, cae al fichero) | `fleet/authorize.py` | Lectura de sshd y bloqueos para el túnel de la central. La parte de SSH se construye encima y **no puede contradecirla**. |
| `READ_ONLY_PROGRAMS` / `READ_ONLY_SUBCOMMANDS` / `is_read_only` | `core/runner.py` (~1120) | Lo que `--dry-run` deja ejecutar. Hoy no conoce `needrestart`, `ufw status`, `fail2ban-client status`, `timedatectl`, `swapon --show`, `ss`... Toda sonda nueva debe añadirse **estrechamente** (`is_read_only` acepta el comando si *cualquier* argumento está en el conjunto). |
| `ReleaseManager.prune(keep)` | `deployers/releases.py` (742) | Limpieza de releases: la única implementación. |
| `BackupManager.get_storage_usage()` | `managers/backup_manager.py` (3151) | Tamaño de copias por app. |
| `PREVIOUS_TAG = "wasm-previous"` | `deployers/docker_compose.py` (80) | La imagen de vuelta atrás de cada servicio Compose. **`docker image prune -a` la borraría** (ningún contenedor la usa tras la actualización). |
| `ensure_elevated` / `require_elevated`, `x-noust-requires-elevation` | `web/api/deps.py`, `web/api/openapi.py` | Modo sudo; la central lo pregunta sola por el mapa de OpenAPI del nodo. |
| `fleet_refusal`, `FLEET_REFUSED_PREFIXES` | `web/auth.py` (3748) | Lo que un token de flota nunca puede hacer (5.3). |
| `JobManager`, `JobContext.update/log`, `useFollowedJob`, `LogViewer`, `SystemOutput`, `CommandHint`, `DataTable`, `Section`, `StatTile`, `LinkTabs`, `SegmentedControl`, `Drawer`, `ConfirmDialog`, `StatusPill` | `web/jobs.py`, `panel/src/components/*` | Todo lo necesario para la UI; no hace falta ningún componente nuevo salvo un medidor de disco por montaje. |
| `machine.py::read_machine` y el evento SSE `machine` | `web/machine.py` | El pulso de 5 s del topbar; el Resumen lo reutiliza, no lo duplica. |
| `_disk_info`, `_memory_info`, `_os_name`, `_uptime` | `web/api/system.py` | Viven en el router (regla 3): moverlos a `managers/host/` para que CLI y API llamen lo mismo. |

---

## 2. La competencia

### 2.1 Qué ofrece cada uno y cómo lo reparte

Marcado: ● sí, ◐ parcial o limitado, ○ "no lo encontré en la documentación que pude leer" (no
"no existe"). La matriz es **orientativa**: sale de la documentación oficial y de las páginas de
producto; no probé ninguno de ellos en una instalación real. Fuentes al final.

| | Forge | Ploi | RunCloud | CloudPanel | ServerPilot | Cockpit | Webmin/Virtualmin | Coolify | Dokploy | Plesk | WHM/cPanel | Hestia | aaPanel |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Actualizaciones del SO (lista) | ○ (SSH) | ◐ (guías) | ○ | ○ | ◐ auto | ● (PackageKit, seguridad arriba con CVE) | ● módulo | ● (apt/dnf/zypper, semanal) | ○ | ● | ● | ● (Server > Updates) | ○ |
| Solo seguridad | ◐ automáticas semanales (Ubuntu) | ◐ auto | ◐ | ○ | ● diarias | ● (según distro) | ◐ | ○ ("no diferencia seguridad") | ○ | ● | ● | ◐ | ○ |
| Automáticas | ● | ● | ○ | ○ | ● | ● (none/security/all + frecuencia) | ● programadas | ○ (a propósito) | ○ | ● | ● | ● por defecto | ○ |
| Reinicio necesario / servicios viejos | ◐ ("Reboot server") | ◐ guía | ○ | ○ | ○ | ● (Tracer en Fedora; `/run/reboot-required`) | ○ | ◐ aviso manual | ○ | ○ | ○ | ○ | ○ |
| Reiniciar/apagar desde la UI | ● | ◐ | ● | ◐ (instancias cloud) | ○ | ● | ● | ○ | ○ | ○ | ● | ○ | ○ |
| Cortafuegos | ● UFW ("solo lo creado en Forge aparece") | ● UFW | ● firewalld (botón Deploy; **borra lo hecho por CLI**) | ● UFW | ◐ fijo | ● zonas y puertos | ● | ○ | ○ | ● | ● (CSF) | ● iptables + IPset | ● |
| Fail2ban | ○ | ● por defecto | ● lista de IP bloqueadas + borrar | ○ | ○ | ○ | ● módulo | ○ | ○ | ● jails, IP en ban | ◐ cPHulk | ● | ● integrado |
| Claves SSH | ● (una por servidor) | ● | ● ("SSH vault") | ● | ● | ● (ver claves) | ● | ● (claves privadas) | ◐ | ● | ● | ◐ | ● |
| Endurecer sshd desde la UI | ○ (deshabilita contraseñas al aprovisionar) | ● puerto | ● "Passwordless login" y "Prevent root login" (sin comprobar nada) | ○ | ○ | ○ | ◐ | ○ | ○ | ◐ | ● "SSH Password Auth Tweak" | ○ | ● puerto, root, "riesgos SSH" |
| Disco / limpieza | ◐ KB (growpart, `journalctl --vacuum`) | ◐ guías "Disk space tips" | ○ | ○ | ○ | ● Almacenamiento | ● | ● limpieza Docker automática | ● limpieza Docker diaria, por tipo | ○ | ○ | ○ | ○ |
| Swap | ○ | ◐ guía | ○ | ○ | ○ | ○ | ◐ | ○ | ○ | ○ | ○ | ○ | ◐ |
| Hora / hostname | ● zona horaria (`timedatectl`) | ○ | ○ | ○ | ○ | ● (hostname, hora, perfil de rendimiento) | ● | ○ | ○ | ● | ● | ● | ● |
| Registros | ● (de acciones) | ● | ○ | ○ | ○ | ● (filtros por fecha, gravedad, servicio) | ● | ◐ | ● | ● | ● | ● | ● |
| Servicios del sistema | ◐ (daemons propios) | ◐ | ○ | ○ | ○ | ● (Servicios, Objetivos, Sockets, Temporizadores, Rutas) | ● | ○ | ○ | ● | ● | ● (CPU y memoria por servicio) | ● |
| Monitorización | ● (carga, disco, memoria; solo plan Business) | ● | ● | ● | ○ | ● | ● | ◐ | ● (refresco 20 s, retención 2 días) | ● | ● | ● | ● |
| Comprobación de riesgos | ○ | ◐ "insights" | ○ | ○ | ○ | ○ | ○ | ○ | ○ | ◐ | ● **Security Advisor** (verde/amarillo/gris) | ○ | ● 16 riesgos con un clic |

### 2.2 Cómo lo disponen

- **Forge**: pestañas por servidor (Resumen, Sitios, Red, Observar, Ajustes...). "Red" = cortafuegos
  UFW con "Añadir regla" (puerto o rango `8000:8010`, IP de origen, permitir/denegar); "Ajustes" =
  nombre, SSH, zona horaria, etiquetas y "zona de peligro" (archivar, transferir, borrar);
  "Observar" = monitores de carga/disco/memoria por correo. Lo que enseña es solo lo que Forge
  creó (las reglas de ufw a mano no salen). Recomienda **no** hacer `do-release-upgrade`, y publica
  avisos de CVE de kernel con "Reboot Server" como acción (la de abril de 2026, CVE-2026-31431:
  el parche de `kmod` estaba instalado por las actualizaciones automáticas pero **no surtía efecto
  hasta reiniciar y Forge no reinicia solo**). Es el caso de uso de "instalado pero no activo".
- **RunCloud**: Ajustes del servidor > Seguridad con dos tarjetas, Firewalld (tabla tipo, protocolo,
  puerto, IP, acción; buscador; botón *Deploy* que aplica; avisa de que **lo añadido por CLI se
  sobrescribe**) y Fail2ban (lista de IP con buscador, copiar y borrar; avisa de que un exceso de
  intentos bloquea la IP propia). Pestaña SSH con dos interruptores, "Passwordless login" y
  "Prevent root login", **sin ninguna comprobación previa ni aviso de bloqueo** (su guía lo
  reconoce implícitamente al recomendar el "SSH vault" antes de usarlos).
- **CloudPanel**: menú lateral Seguridad con dos pestañas, Cortafuegos (UFW; tipo, rango, origen,
  descripción) y Basic Auth delante del puerto 8443; recomienda limitar el 22 y el 8443 a IP fijas.
- **Coolify** (v4.0.0-beta.419+): Servidor > Seguridad > "Server Patching": botón de comprobar,
  lista paquete y versión (sin distinguir seguridad), "Actualizar" por paquete o "Actualizar todo",
  salida en una ventana que hay que dejar abierta, aviso de que Docker/kernel reinician servicios
  o exigen reinicio (manual), comprobación semanal y notificación. Es el único competidor de
  *despliegue* con esto, y es lo más flojo que uno podría copiar: sin seguridad marcada, sin
  "hace falta reiniciar", sin job que sobreviva a cerrar la ventana. Su documentación general dice
  además que "Coolify no gestiona la seguridad ni las actualizaciones del servidor".
- **Dokploy**: Ajustes > Servidor: dominio, limpieza de Docker diaria con casillas por tipo
  (imágenes, volúmenes, contenedores parados, caché de builds, datos de monitorización), refresco
  de métricas de 20 s y retención de 2 días, terminal, "buscar actualización" de Dokploy. Sin
  actualizaciones del SO, cortafuegos ni SSH.
- **Cockpit** (la referencia para systemd): Resumen con Salud (¿hay actualizaciones?), Uso (CPU y
  memoria), Configuración (hostname, hora, perfil de rendimiento, claves SSH del host); Servicios
  con filtros y pestañas de Objetivos, Sockets, Temporizadores y Rutas; Registros con filtros por
  fecha, gravedad y servicio; Almacenamiento; Red con cortafuegos por zonas; Actualizaciones
  (PackageKit) con **las de seguridad arriba, enlazadas a su CVE**, salida en vivo, aviso de
  reinicio, y "actualizaciones automáticas" con tipo (ninguna, seguridad, todas) y frecuencia
  sobre `dnf-automatic`, `yum-cron` o `unattended-upgrades`. Detecta servicios a reiniciar con
  Tracer (solo Fedora) y en el resto reinicia la máquina entera. Su propio diseño dice "una
  interfaz sencilla sobre un mecanismo existente, sin inventar lógica" y admite que la
  actualización solo-seguridad no es fiable en todas las distros.
- **Webmin/Virtualmin**: módulos por categoría (Sistema: "Bootup and Shutdown", "Software Package
  Updates" con comprobación programada, correo y aplicar todas o algunas, "System Logs";
  Redes: "Linux Firewall", fail2ban). Exhaustivo y poco guiado: cada módulo es una página larga.
- **Plesk**: Herramientas y ajustes > Seguridad > "IP Address Banning (Fail2Ban)" (activar, jails
  por servicio, tiempos de ban, IP de confianza, lista de IP con desbanear) y Cortafuegos.
- **WHM/cPanel**: **Security Center**: "Security Advisor" (escáner con resultado por colores
  verde/amarillo/gris y qué hacer), "Host Access Control", "cPHulk", "SSH Password Authorization
  Tweak".
- **HestiaCP**: Ajustes del servidor > Updates (automáticas por defecto, versiones de componentes),
  Firewall (reglas + IPsets para listas blancas/negras), Servicios (estado, CPU y memoria,
  reiniciar), monitor de tareas con gráficas y registros del sistema.
- **aaPanel**: menú Seguridad con Firewall ("liberar puerto"), SSH (activar/bloquear, cambiar
  puerto, **comprobar el riesgo del servicio SSH**, aviso por correo al entrar `root`), y un
  análisis de 16 riesgos con arreglo en un clic; integra fail2ban y un WAF.
- **ServerPilot**: solo un interruptor "Automatic Security Updates" (diario, desde los repos de
  Ubuntu y de ServerPilot) y un cortafuegos fijo (22, 80, 443, UDP 443).

### 2.3 Lecciones

1. Lo que casi todos dan: cortafuegos por puertos, fail2ban con lista y desbaneo, claves SSH,
   actualizaciones automáticas de seguridad. **Casi nadie comprueba que no te deja fuera** (RunCloud
   y aaPanel no lo hacen) y **nadie compara el cortafuegos con los sockets que escuchan** ni avisa
   del bypass de Docker. Ahí Noust puede ser mejor que todos.
2. El competidor de despliegue más cercano (Coolify) trae parcheo pero sin seguridad marcada ni
   "reinicio necesario"; el de hosting (cPanel) trae un **asesor de seguridad con colores** que es
   justo el ítem 46. Cockpit aporta el patrón de UX de actualizaciones: seguridad arriba, CVE
   enlazado, salida en vivo, avisar de reinicio/servicios.
3. El patrón de RunCloud (*Deploy* que **sobrescribe lo hecho a mano**) y el de Forge (solo muestra
   lo suyo) son los dos fallos a evitar: Noust debe **leer el estado real** del cortafuegos y
   marcar qué reglas puso él (comentario `noust:`), sin borrar nada que no sea suyo.
4. Todos los paneles son "una página larga por módulo" salvo Cockpit y Forge (pestañas). La
   petición del dueño (sin páginas verticales infinitas) se resuelve con pestañas, un Resumen que
   cabe en una pantalla y tarjetas resumen que abren un panel lateral o una vista, no secciones
   apiladas.

---

## 3. Implementación por funcionalidad

### 3.0 Principios comunes

- **Un adaptador por familia** con la misma interfaz (`pending`, `refresh`, `upgrade`,
  `reboot_required`, `stale_services`, `auto_status`, `auto_enable`, `clean_cache`) en
  `managers/host/updates.py`: `AptBackend`, `DnfBackend` (dnf4 y dnf5), `ZypperBackend`, y
  `UnsupportedBackend` (pacman, apk, sistema transaccional: solo estado y un aviso claro). La
  familia sale de `core/packages.py` (la tabla movida de `setup.py`). El API devuelve un objeto
  `capabilities` (`packages`, `firewall`, `sshd`, `fail2ban`, `docker`, `container`,
  `transactional`, `init`) y cada pestaña se degrada con un mensaje, no con un error.
- **Entorno de todas las sondas y acciones**: `LC_ALL=C` (apt, dnf y zypper traducen su salida y
  el parseo se rompe), `TERM=dumb`; para apt además `DEBIAN_FRONTEND=noninteractive`,
  `APT_LISTCHANGES_FRONTEND=none` y `NEEDRESTART_MODE=l` (solo listar).
- **Bloqueos**: apt `-o DPkg::Lock::Timeout=300` (apt ≥ 1.9.11: Debian 11, Ubuntu 20.04; en versiones
  anteriores la opción se ignora sin error) y, antes de empezar, una comprobación con `psutil` de
  procesos `apt|apt-get|dpkg|unattended-upgr|packagekitd|dnf|rpm|zypper|cloud-init`. dnf espera
  indefinidamente con "Waiting for process with pid N to finish" (lo corta el timeout del
  runner). zypper sale con **7** (`ZYPP_LOCKED`) salvo que se le dé `ZYPP_LOCK_TIMEOUT=120`
  ([libzypp envars](https://doc.opensuse.org/projects/libzypp/HEAD/zypp-envars.html): por defecto 0,
  negativo espera siempre).
- **Timeouts** (constantes con nombre, como `INSTALL_TIMEOUT` de `core/dependencies.py`):
  sonda 20 s; simulación/lista 120 s; refresco de metadatos 180 s; instalación de paquetes 600 s
  (una sola); actualización completa 3600 s; `du` por ruta 120 s; `needrestart -b` 60 s.
- **Nada lento en la ruta de la petición.** Una `ServerFacts` en caché (TTL y refresco por hilo
  como `metrics_collector`, y bajo demanda) guarda lo caro: `apt-get -s` (~1 s), `needrestart -b`
  (1-3 s), `docker system df` (~1 s), `sshd -T` (~50 ms). El API responde de la caché con
  `checked_at` y el botón "Comprobar ahora" lanza el refresco como job. `du` nunca en línea.
- **`--dry-run`**: añadir a `READ_ONLY_SUBCOMMANDS` **solo** lo estrictamente de lectura, p. ej.
  `apt-get: {-s, --simulate}`, `dnf: {check-update, check-upgrade, updateinfo, needs-restarting}`,
  `zypper: {list-updates, lu, list-patches, lp, patch-check, ps, needs-rebooting}`,
  `needrestart: {-b}`, `ufw: {status, show}`, `firewall-cmd: {--state, --list-all, --get-active-zones}`,
  `fail2ban-client: {status, get, ping}`, `timedatectl: {show, status, list-timezones}`,
  `swapon: {--show}`, `systemctl: +{list-timers, --failed, is-system-running}`,
  `docker: +{df}` (**no** `system`: `docker system prune` también lo contiene)... y cada valor que
  se cuele en argv (IP, jail, unidad, comentario) validado antes. **Antes de añadir nada hay que
  endurecer `is_read_only`**: hoy acepta el comando si *cualquier* argumento coincide
  (`argv[1:]`), así que `ufw allow 22 comment status` se clasificaría como lectura y **se
  ejecutaría de verdad bajo `--dry-run`**. Debe mirar el subcomando (el primer argumento
  posicional tras las opciones) y, para `apt-get`, la opción `-s/--simulate`; con su prueba.
- **Ninguna ruta acepta un comando ni una ruta de fichero del cliente.** Acciones = enumeraciones
  (`"journal"`, `"apt-cache"`, `"docker-build-cache"`), enteros, IP (`ipaddress`), nombres de
  unidad validados. Los ficheros de configuración los escribe el manager por `get_fs()`
  (atómico, copia `.noust-bak`, `DryRunFileSystem`).

### 3.1 Actualizaciones pendientes, con las de seguridad marcadas

Resultado por paquete: `name`, `installed`, `candidate`, `security` (bool), `advisory` (id y
gravedad si la distro los da), `kernel` (bool), `origin`. Más `kept_back[]`, `holds[]`,
`checked_at`, `lists_age` (antigüedad de los metadatos).

**Debian/Ubuntu**

- Refresco (job, 180 s): `["apt-get","update","-q","-o","APT::Color=0","-o","Acquire::Retries=1","--error-on=any"]`.
  Sin `--error-on=any`, apt sale 0 aunque un repositorio falle ("W: Failed to fetch") y las listas
  quedan viejas sin que nadie se entere; con la opción sale distinto de 0 y la salida se enseña
  literal. Edad de las listas: mtime de `/var/lib/apt/periodic/update-success-stamp` o de
  `/var/lib/apt/lists`.
- Lista (120 s, lectura, funciona sin root): `["apt-get","-s","-o","Debug::NoLocking=1","--with-new-pkgs","upgrade"]`.
  Se usa `--with-new-pkgs` (es lo que hace `apt upgrade`): instala dependencias nuevas, nunca
  quita, y no deja "retenidos" los meta-paquetes de kernel que un `upgrade` a secas sí retiene.
  Parseo de líneas `Inst` (formato **verificado** con `apt-get -s install --reinstall bash`):
  `Inst bash [5.2.21-2ubuntu4] (5.2.21-2ubuntu4 Ubuntu:24.04/noble [amd64])` →
  `^Inst (\S+)(?: \[([^\]]+)\])? \((\S+) (.*?)(?: \[([^\]]+)\])?\)$`, orígenes separados por `, `.
  **Seguridad** = algún origen cuyo `suite` acaba en `-security` (`Ubuntu:24.04/noble-security`,
  `Debian-Security:12/stable-security`, `UbuntuESM:.../focal-infra-security`).
  Retenidos: bloque "The following packages have been kept back:" (líneas con 2 espacios). Trampa
  de Ubuntu: las **actualizaciones por fases** (phased updates) aparecen en `apt list --upgradable`
  y **no** en `apt-get upgrade`, así que la lista de `apt list` no coincide con lo que se
  instalaría; por eso no se usa `apt list`, cuya CLI además
  [no es estable](https://manpages.debian.org/testing/apt/apt.8.en.html) ("designed as an end-user
  tool and it may change behavior between versions").
  Retenciones del operador: `["apt-mark","showhold"]`.
  Alternativa solo Ubuntu: `/usr/lib/update-notifier/apt-check` (`--human-readable`, `-p` nombres);
  no vale para Debian y no se necesita.
- Paquetería rota: `["dpkg","--audit"]` con salida no vacía = hay paquetes a medio configurar →
  acción "Reparar": `["dpkg","--configure","-a"]` (900 s) y `["apt-get","-f","install","-y"]`.

**Fedora/RHEL (dnf4: RHEL 8/9, Alma, Rocky, Fedora ≤ 40; dnf5: Fedora ≥ 41, RHEL 10)**

- Detección de dnf5: `["dnf","--version"]` (contiene "dnf5") o `readlink -f /usr/bin/dnf`.
- Refresco: `["dnf","-q","-y","makecache","--refresh"]` (300 s).
- Lista completa: `["dnf","-q","-y","check-update","--refresh"]` → salida `nombre.arq  versión  repo`,
  **código 100 = hay actualizaciones, 0 = ninguna, 1 = error**
  ([dnf5 check-upgrade](https://dnf5.readthedocs.io/en/latest/commands/check-upgrade.8.html)).
  dnf4 puede partir una línea larga en dos cuando hay terminal: se ejecuta sin TTY (el runner lo
  hace) y el parser une líneas con menos de 3 campos. Dejar de leer en la sección "Obsoleting Packages".
- Subconjunto de seguridad: **el mismo comando con `--security`** (`["dnf","-q","-y","check-update","--security"]`,
  dnf5: `check-upgrade --security`); seguridad = pertenece al subconjunto. Los identificadores de
  aviso y su gravedad: `["dnf","-q","updateinfo","list","--security"]` (dnf4, líneas
  `RHSA-2025:1234 Important/Sec. paquete-ver.arq`) o `["dnf5","advisory","list","--security","--json"]`
  (dnf5 admite `--json` en `advisory list`, `list`, `history` y `repo`, según su
  [manual](https://man.archlinux.org/man/extra/dnf5/dnf5-advisory.8.en); verificar en el arnés).
- Trampas: en RHEL sin registrar falla con un mensaje de suscripción (mostrarlo literal); `dnf`
  espera si `packagekitd` tiene el bloqueo (Cockpit lo deja); `yum` en RHEL 8/9 es un enlace a dnf.

**openSUSE**

- Refresco: `["zypper","--non-interactive","--quiet","refresh"]` (180 s).
- Sonda barata: `["zypper","--non-interactive","--quiet","patch-check"]` → **código 100 = hay
  parches, 101 = hay parches de seguridad**, 0 = ninguno
  ([man](https://manpages.opensuse.org/Tumbleweed/zypper/zypper.8.en.html)).
- Lista: `["zypper","--non-interactive","--xmlout","--no-refresh","list-updates","-t","package","-t","patch"]`;
  XML con `<update name edition edition-old kind="package|patch" category="security|recommended|optional|feature" severity restart interactive pkgmanager>`
  ([formato](https://en.opensuse.org/openSUSE:Standards_Zypper_Xml)). Seguridad =
  `category="security"` en un parche; `restart="true"` y `pkgmanager="true"` (pila de zypper) se
  muestran.
- **Tumbleweed no tiene "seguridad"** (es continua): `ID=opensuse-tumbleweed` → solo "todo"
  (`zypper dup`) y se explica. **Sistemas transaccionales** (MicroOS, Leap Micro: raíz de solo
  lectura, `/usr/sbin/transactional-update`) → `UnsupportedBackend` con estado y reinicio, sin
  acciones (decisión D3).

### 3.2 Aplicar todas o solo las de seguridad, como job

Regla: el job **no ejecuta el gestor de paquetes él mismo**. Lanza una unidad transitoria y la
sigue. Motivo (**el riesgo nº 1 de todo el diseño**): el paquete `noust` está en el mismo
repositorio que se actualiza; su `postinst` reinicia `noust-web`, y eso mata el proceso que
ejecuta el job, deja dpkg a medias y, con el mecanismo actual, el job pasa a `failed`
("Interrupted by a panel restart"). Es el mismo fallo que documenta el mundo Debian cuando una
unidad que ejecuta la actualización es reiniciada por la propia actualización
([lista de apt/deity](https://lists.debian.org/deity/2017/05/msg00021.html)).

1. El job crea `noust-os-update-<id>` con
   `["systemd-run","--unit=noust-os-update-<id>","--description=Noust OS update","-p","StandardOutput=journal","-p","StandardError=journal","-p","RuntimeMaxSec=3600","--","/usr/bin/noust","server","updates","run","--job","<id>"]`
   (`systemd-run` vuelve en cuanto arranca la unidad; sin `--collect` para poder leer el resultado
   con `systemctl show -p ActiveState,Result,ExecMainStatus`, y `reset-failed` al terminar).
2. `noust server updates run` es **la misma implementación que el CLI en primer plano**: llama a
   `UpdatesManager.apply(scope, on_line)`, que ejecuta el gestor por `runner.stream(...)` y guarda
   `/var/lib/noust/os-updates/<id>.json` (paquetes antes/después, código de salida, reinicio
   necesario, servicios a reiniciar, conffiles conservados).
3. El job de la consola solo hace `journalctl --unit=noust-os-update-<id> --follow --output=cat`
   por `runner.stream` hacia `JobContext.log`, y sondea `systemctl show`. Si `noust-web` se
   reinicia a mitad, al arrancar **reconcilia**: los jobs `os_update` `running` cuyo unit sigue
   activo se reengancharían en vez de marcarse `failed` (cambio en `jobs.py`, hoy marca todo).
4. El proceso hijo importa todo lo necesario **antes** de tocar paquetes (un `import` diferido
   tras reemplazarse el árbol de Python daría `ImportError`).

Argv de la acción (todos con el entorno de 3.0):

| | Todas | Solo seguridad |
|---|---|---|
| Debian/Ubuntu | `["apt-get","-y","-q","-o","DPkg::Lock::Timeout=300","-o","Dpkg::Options::=--force-confdef","-o","Dpkg::Options::=--force-confold","-o","Dpkg::Use-Pty=0","--with-new-pkgs","upgrade"]` | `["apt-get","-y","-q", ... ,"install","--only-upgrade", *paquetes_con_origen_security]` (apt no tiene "solo seguridad": se instala la lista explícita salida de la simulación; apt resuelve las dependencias) |
| Fedora/RHEL | `["dnf","-y","upgrade","--refresh"]` | `["dnf","-y","upgrade","--security"]` |
| openSUSE Leap | `["zypper","--non-interactive","update"]` (paquetes) | `["zypper","--non-interactive","patch","--category","security"]` |
| Tumbleweed | `["zypper","--non-interactive","dup"]` | no existe |

Detalles que no se pueden olvidar:

- **`--force-confold`** mantiene la configuración que el operador tocó (y `confdef` toma la del
  mantenedor si no la tocó): sin ellas, dpkg **pregunta** por un conffile modificado y el job se
  cuelga en silencio. El resultado lista los conffiles conservados.
- **`dist-upgrade`/`full-upgrade` puede quitar paquetes.** Se ofrece como segunda acción, solo
  tras simular y enseñar las líneas `Remv` con confirmación reforzada (`allow_removals=true`).
- **needrestart en modo lista** (`NEEDRESTART_MODE=l`): en Ubuntu ≥ 22.04 `apt` muestra un
  diálogo "Daemons using outdated libraries" si va en interactivo, y con una configuración
  `$nrconf{restart} = 'a'` reiniciaría servicios por su cuenta (Docker incluido). Según su
  [manual](https://manpages.debian.org/testing/needrestart/needrestart.1.en.html), si está en
  interactivo pero se ejecuta sin terminal "cae al modo solo lista", y `NEEDRESTART_MODE`
  "sobrescribe la configuración". Su comportamiento con `DEBIAN_FRONTEND=noninteractive` **ha
  cambiado entre versiones** (el
  [error #2004203](https://bugs.launchpad.net/ubuntu/+source/needrestart/+bug/2004203) hacía que
  no aplicara el modo automático en apt no interactivo y solo se corrigió en Ubuntu 22.04 con
  `needrestart 3.5-5ubuntu2.5`, en sept. de 2025). Por eso Noust **no depende de needrestart
  para reiniciar nada**: fija `NEEDRESTART_MODE=l` para que el comportamiento sea idéntico en
  todas las versiones, y los reinicios los decide el operador en la lista de "estos servicios
  necesitan reiniciarse" (3.3).
- **dpkg entrecortado**: si el job falla, la última línea del journal y `dpkg --audit` se enseñan
  literales con el botón "Reparar" (3.1).
- **Docker**: una actualización de `docker-ce`/`containerd` reinicia Docker y con él todas las
  aplicaciones Compose. Antes de aplicar, la simulación se cruza con una lista de paquetes de
  impacto (`docker*`, `containerd*`, `nginx*`, `apache2*|httpd*`, `postgresql*`, `mariadb*|mysql*`,
  `openssh*`, `libc6|glibc`, `linux-image*|kernel*`, `systemd*`, `noust`) y el diálogo dice qué
  se va a reiniciar.
- **Noust se actualiza el último y aparte** (por la unidad transitoria, con el diálogo avisando
  de que la consola se reiniciará y la sesión persiste); el resto se puede excluir con la lista
  explícita en apt y `--exclude=noust*` en dnf.
- **Pre-vuelo**: sin job de despliegue/copia en marcha (`get_job_manager()`), sin bloqueo del
  gestor, espacio libre en `/` ≥ 1 GiB (`/var/cache`), y `dpkg --audit` limpio.
- **Después**: `HealthGate` de aplicaciones (`resolve_states` de `core/app_state.py`): si tras
  actualizar hay apps caídas se avisa con el diagnóstico existente (`noust diagnose`), no se
  revierte (no se puede).

### 3.3 "Hace falta reiniciar" y servicios con librerías viejas

Objeto: `reboot{required, reasons[], since}` y `stale_services[]`, con `since` = mtime del
indicador cuando existe (para escalar de aviso a crítico tras N días: el caso Forge de
CVE-2026-31431).

**Debian/Ubuntu**

- Indicador: `/var/run/reboot-required` y `/var/run/reboot-required.pkgs` (un paquete por línea,
  con repetidos). **Solo se crea si está `update-notifier-common` (Ubuntu) o el gancho de kernel de
  `unattended-upgrades` (`/etc/kernel/postinst.d/unattended-upgrades`); en un Debian mínimo puede
  no existir tras actualizar el kernel** (falso negativo,
  [linux-audit](https://linux-audit.com/check-required-reboot-debian-ubuntu-others/)).
- Por eso se combinan: fichero **o** `needrestart -b` con `NEEDRESTART-KSTA` 2 o 3 **o** kernel en
  ejecución (`os.uname().release`) distinto del más nuevo instalado (`/boot/vmlinuz-*` ordenado por
  versión natural). Motivos: el `.pkgs` y `KCUR`→`KEXP`, microcódigo con `NEEDRESTART-UCSTA`.
- Servicios: `["needrestart","-b"]` (root, 60 s). Formato **de su
  [README.batch](https://doc.duckcorp.org/cgi-bin/dwww/usr/share/doc/needrestart/README.batch.md)**:
  `NEEDRESTART-VER`, `-KCUR`, `-KEXP`, `-KSTA` (0 desconocido, 1 sin novedad, 2 actualización
  compatible con la ABI pendiente, 3 cambio de versión pendiente), `-SVC: nombre.service` (uno por
  servicio), `-CONT` (contenedores), `-SESS: usuario @ sesión`. En modo `-b` "nunca muestra
  diálogos ni reinicia nada".
- Si no está instalado: acción "Mejorar la detección" = `["apt-get","install","-y","needrestart"]`
  (la instalación lleva su gancho de apt, inocuo con `NEEDRESTART_MODE=l`).

**Fedora/RHEL**

- Reinicio: `["dnf","needs-restarting","-r"]` → **código 1 = hace falta, 0 = no**
  ([plugin dnf4](https://dnf-plugins-core.readthedocs.io/en/latest/needs_restarting.html));
  las líneas `* paquete` son los motivos. En dnf5 está integrado (`dnf5 needs-restarting`, con
  `-r` aceptado por compatibilidad y `--json`); en EL 8/9 mínimo hay que instalar
  `dnf-utils`/`dnf-plugins-core`. Sin el plugin: comparar `os.uname().release` con
  `rpm -q kernel-core --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n'` el más nuevo.
- Servicios: `["dnf","needs-restarting","-s"]` → nombres `foo.service`, uno por línea.

**openSUSE**

- Reinicio: `["zypper","needs-rebooting"]` (**código 102** = hace falta, según las tablas de salida
  de zypper; verificar en el arnés), y como respaldo `/run/reboot-needed` y la lista de paquetes que
  lo provocan en `/etc/zypp/needreboot`. En un Leap antiguo sin ese subcomando, solo el fichero.
- Servicios: `["zypper","ps","-sss"]` = "-s" tres veces lista solo los **nombres de servicio**
  ([`-s`: corta, dos veces solo procesos de un servicio, tres solo sus nombres](https://lists.opensuse.org/zypp-devel/2015-09/)).
  Versiones viejas piden `lsof` instalado o imprimen errores.
- Tumbleweed y Leap 16 traen `os-update` (3.4), que hace lo mismo con `RESTART_SERVICES`.

**Reiniciar los servicios señalados**: `systemctl restart <unidad>` desde una **capacidad
estrecha** nueva (`SystemUnits`, 5.2) con lista de denegación: `noust-web` (se reinicia el último y
diferido con `systemd-run --on-active=2s systemctl restart noust-web` para que la respuesta
salga), `docker`/`containerd` (avisa del efecto: reinicia todo salvo `live-restore`), `ssh`/`sshd`
(antes `sshd -t`), y **nunca** `dbus`, `systemd-logind`, `networking`, `NetworkManager`,
`systemd-networkd`, `getty@*` (needrestart tampoco los reinicia por defecto).

### 3.4 Actualizaciones automáticas de seguridad: estado, activar, desactivar

Principio: **mecanismos nativos de cada distro** (auditables y esperados), y Noust los lee y los
enciende; no inventa un temporizador propio. Coincide con Cockpit y con lo que hacen Forge
("service built in to the operating system") y ServerPilot.

- **Debian/Ubuntu (`unattended-upgrades`)**:
  - Estado: `["dpkg-query","-W","-f=${db:Status-Abbrev}","unattended-upgrades"]` (`ii `);
    `["apt-config","dump"]` filtrado a `APT::Periodic::Update-Package-Lists`,
    `APT::Periodic::Unattended-Upgrade` y `Unattended-Upgrade::Allowed-Origins` (**verificado**: en
    esta máquina salen `"1"` y los orígenes `${distro_id}:${distro_codename}`, `-security` y ESM);
    temporizadores `apt-daily.timer` y `apt-daily-upgrade.timer` (`systemctl is-enabled`); último
    resultado en `/var/log/unattended-upgrades/`; simulación:
    `["unattended-upgrade","--dry-run","-d"]` (verificar la opción en el arnés; la
    [wiki de Debian](https://wiki.debian.org/UnattendedUpgrades) solo menciona `-d`).
  - Activar: instalar `unattended-upgrades` y escribir `/etc/apt/apt.conf.d/20auto-upgrades` (el
    mismo fichero que escribe `dpkg-reconfigure`) con `APT::Periodic::Update-Package-Lists "1";` y
    `APT::Periodic::Unattended-Upgrade "1";`. Los orígenes por defecto ya son la versión base,
    `-security` y ESM (**verificado** en Ubuntu 24.04 con `apt-config dump`); `-updates` no está.
    Desactivar: `"0"`.
    En `apt.conf.d` gana el fichero de número mayor; en sshd es al revés (3.7).
  - Reinicio automático: `Unattended-Upgrade::Automatic-Reboot` **desactivado** por defecto y sin
    interruptor en la primera versión (una VPS con apps de clientes no se reinicia sola).
- **Fedora/RHEL (`dnf-automatic`)**: paquete `dnf-automatic` (dnf4) o `dnf5-plugin-automatic`
  (Fedora ≥ 41; temporizador `dnf5-automatic.timer`). Estado: `systemctl is-enabled
  dnf-automatic-install.timer dnf-automatic.timer dnf5-automatic.timer` y lectura de
  `/etc/dnf/automatic.conf` (`upgrade_type = default|security`, `apply_updates`, `download_updates`,
  `reboot = never|when-changed|when-needed`; emisores `stdio, email, motd`,
  [manual](https://dnf.readthedocs.io/en/latest/automatic.html)). Activar solo seguridad: fijar
  `upgrade_type = security` en `[commands]` (edición de una línea, con copia) y habilitar
  `dnf-automatic-install.timer` (descarga e instala). dnf5 lee además
  `/usr/share/dnf5/dnf5-plugins/automatic.conf` y sobrescribe con `/etc/dnf/automatic.conf`
  ([dnf5-automatic](https://dnf5.readthedocs.io/en/latest/dnf5_plugins/automatic.8.html)).
  Fuera: `yum-cron` (EL7).
- **openSUSE**: `os-update` (Tumbleweed, Leap 15.6 y 16.0; `os-update.timer` diario;
  `/etc/os-update.conf` con `UPDATE_CMD=security|up|dup|auto` y `REBOOT_CMD=none|reboot|rebootmgr|auto`,
  [man](https://manpages.opensuse.org/Tumbleweed/os-update/os-update.8.en.html)). Se activa con
  `UPDATE_CMD=security`, `REBOOT_CMD=none` y `systemctl enable --now os-update.timer`. En Leap ≤
  15.5 no hay mecanismo nativo comparable (YaST/cron): solo estado y una guía.
- **Qué añade Noust**: lo que los mecanismos nativos no hacen: el resultado (últimas ejecuciones,
  paquetes, si pide reinicio) en la pestaña, una notificación al canal del operador cuando quedan
  actualizaciones de seguridad sin aplicar N días o cuando hay reinicio pendiente, y el aviso de
  "las automáticas instalaron el parche pero no está activo hasta reiniciar".

### 3.5 Reinicio y apagado, con confirmación y programación

- Programar: `["shutdown","-r","+N","Reboot requested from the Noust console by <actor>"]`
  (N minutos, `now` = `+0`; **por defecto `+1` si no se da hora**; hace falta el argumento de hora
  para poder poner un mensaje, [man](https://manpages.debian.org/testing/systemd-sysv/shutdown.8.en.html)),
  o `HH:MM` local. Apagado real: `["shutdown","-P","+N", ...]`. Cancelar: `["shutdown","-c"]`.
  Estado: leer `/run/systemd/shutdown/scheduled` (campos `USEC`, `MODE`, `WALL_MESSAGE`; sin
  proceso) o `shutdown --show` (**verificado** en systemd 255: "No scheduled shutdown."; en
  systemd anteriores a 250 puede no existir, de ahí el fichero). `systemctl reboot` inmediato solo
  desde el CLI con `--now`; desde el API siempre con un margen de un minuto para que la respuesta
  llegue y el operador pueda cancelar.
- **No es un job.** Un job vive en el proceso que el reinicio mata. Es una **acción programada** con
  estado visible en el Resumen y arriba de `/server` ("Reinicio programado a las 21:40 por yago.
  [Cancelar]"). El API guarda `boot_id` (`/proc/sys/kernel/random/boot_id`, verificado también en
  `hostnamectl --json` como `BootID`) y la hora prevista en la base de datos.
- **Al volver**: el arranque de `noust-web` compara el `boot_id`; si cambió, publica el evento
  `notice` "El servidor volvió a las 21:43 (tardó 2 min 10 s). Aplicaciones: 14/17 en marcha.
  Unidades fallidas: 2" y lo manda por el notificador (`server_rebooted`). Si el operador
  reinició a mano, el evento sale igual (el `boot_id` cambió de todos modos).
- **Pre-vuelo** (bloquea o exige confirmación, con el motivo verbatim):
  1. Job de Noust en marcha (`deploy`, `backup`, `os_update`...) o bloqueo del gestor de paquetes
     activo: reiniciar a mitad de dpkg deja el sistema roto.
  2. **¿Volverá `noust-web`?** `systemctl is-enabled noust-web` (o el nombre heredado). Si la consola
     corre con `noust web start` sin unidad, tras el reinicio se pierde (ítem 4 del backlog): se
     ofrece `noust web enable` antes.
  3. Unidades de aplicaciones gestionadas que **no están habilitadas** (`is-enabled`) y contenedores
     sin política de reinicio (`docker inspect -f '{{.HostConfig.RestartPolicy.Name}}'` distinto de
     `always|unless-stopped`): no volverán.
  4. **`fstab` roto**: `["findmnt","--verify"]` (**verificado**: "Success, no errors or warnings
     detected"); un error o una entrada de red sin `nofail` deja la máquina en modo emergencia
     tras el reinicio, y en una VPS eso es ir a la consola del proveedor.
  5. Kernel nuevo sin `initrd` correspondiente en `/boot` (lista de `vmlinuz-*` y `initrd*`).
- **Apagar** (`poweroff`): en una VPS **no se puede volver a encender desde Noust** (solo desde el
  panel del proveedor). Confirmación de las que se escriben (el nombre del host) y texto claro. El
  token de flota lo tiene **prohibido** (5.3).
- **Desde la central**: `node_proxy` ve pasar el `POST .../power/reboot` y marca el nodo como
  "reiniciando (esperado, hasta hora + 10 min)" para que el túnel caído no salga como incidente
  ni dispare la alerta de "nodo inalcanzable"; al reconectar, la central enseña el evento de vuelta.
- Ventana de mantenimiento (SHOULD): `["shutdown","-r","04:00"]` y, si se quiere "solo si está
  libre", un temporizador propio que ejecute `noust server reboot --if-idle`.

### 3.6 Cortafuegos frente a sockets que escuchan, y el aviso de Docker

**Qué escucha de verdad** (no depende del cortafuegos): `psutil.net_connections("inet")` filtrando
`status == CONN_LISTEN` (TCP) y los UDP con `laddr` y sin `raddr`, con `pid`→nombre, unidad
(leyendo `/proc/<pid>/cgroup`) y aplicación de Noust cuando la unidad es de una. Es lo que pidió
el dueño como `ss -Hltnup` y da lo mismo sin proceso ni parseo (`ss -Hltnup`
[verificado](https://manpages.debian.org/testing/iproute2/ss.8.en.html) en esta máquina: columnas
`Netid State Recv-Q Send-Q Local:Port Peer:Port Process`; `users:(("nginx",pid=1,fd=6))`; sin
root solo se ven los procesos propios; **no hay JSON**). `ss` queda como comprobación cruzada.

Exposición por socket: dirección `127.0.0.1`/`::1` → local; `0.0.0.0`/`::` → todas las interfaces;
IP concreta → esa interfaz. Tabla del panel: puerto, protocolo, quién, exposición, **veredicto del
cortafuegos** (`bloqueado`, `abierto a todos`, `abierto solo a X`, `sin cortafuegos`,
`publicado por Docker: se salta el cortafuegos`) y el puerto marcado si está fuera de la línea base
(22, 80, 443, el puerto SSH real, el de la consola si no es loopback) y es de una base de datos o
un panel (3306, 5432, 6379, 27017, 9200, 11211, 5672, 2375...): **crítico**.

**Detección de cortafuegos** (en este orden; si hay dos activos, aviso de conflicto):

- ufw: `["ufw","status","verbose"]` (necesita root): `Status: active|inactive`, `Default: deny
  (incoming), allow (outgoing), deny (routed)`.
- firewalld: `["firewall-cmd","--state"]` (stdout `running`, **código 252 = no corre**,
  [man](https://firewalld.org/documentation/man-pages/firewall-cmd.html)).
- nftables/iptables a pelo: `["nft","-j","list","ruleset"]` (JSON) solo para decir "hay reglas y la
  política de `input` es accept/drop"; nunca se edita.
- Valores por defecto: Debian/Ubuntu sin cortafuegos o ufw inactivo (Ubuntu lo trae desactivado);
  Fedora, RHEL y openSUSE con firewalld.

**ufw**

- Reglas: `["ufw","show","added"]` devuelve las reglas como comandos normalizados
  (`ufw allow 22/tcp`) y es lo que se puede reutilizar para borrar; `["ufw","status","numbered"]`
  solo para enseñar el orden. **Borrar por especificación** (`["ufw","delete","allow","22/tcp"]`),
  no por número: los números se desplazan entre lectura y borrado.
- v4 y v6 salen como dos filas (`(v6)`); se agrupan en una regla lógica.
- Añadir: `["ufw","allow","proto","tcp","from",ip,"to","any","port",puerto,"comment","noust: <texto>"]`
  con IP por `ipaddress`, puerto entero, comentario de un juego de caracteres cerrado. Las reglas de
  Noust llevan `noust:` en el comentario: solo esas se pueden borrar sin doble confirmación.
- Activar: `["ufw","--force","enable"]` **solo tras la guarda** (abajo) y con
  `["ufw","default","deny","incoming"]` + `["ufw","default","allow","outgoing"]` ya puestos.
  `--force` salta el aviso interactivo "may disrupt existing ssh connections"
  ([man](https://manpages.debian.org/testing/ufw/ufw.8.en.html)); las conexiones ya establecidas
  siguen (conntrack), lo que se rompe son las **nuevas**.
- `ufw limit` (6 conexiones/30 s) **no** para el SSH de la flota: el túnel de la central se
  reconecta y podría contarse como abuso. Se ofrece `allow`.

**firewalld**

- Zona: `["firewall-cmd","--get-active-zones"]`, `["firewall-cmd","--get-default-zone"]`,
  `["firewall-cmd","--list-all","--zone=<z>"]` (claves `services: ...`, `ports: 8080/tcp`,
  `rich rules:`), y lo mismo con `--permanent`: **si runtime y permanente difieren se avisa**
  ("esta regla se perderá al recargar o reiniciar").
- Añadir: `["firewall-cmd","--permanent","--zone=<z>","--add-port=8080/tcp"]` +
  `["firewall-cmd","--reload"]`; **temporal con `--timeout=600`** (se borra sola: el mecanismo
  natural para "abrir 10 minutos"); por origen con `--add-rich-rule` (cadena única y validada
  campo a campo); nunca `--panic-on`.
- Fedora Server y RHEL suelen traer zona `public` con `ssh` y `cockpit` abiertos: el 9090 de
  Cockpit sale en la tabla de puertos.

**Docker y el bypass**. Los puertos publicados por Docker pasan por la tabla `nat` antes de las
cadenas `INPUT` que usa ufw, así que "traffic to and from that container gets diverted before it
goes through the ufw firewall settings"
([Docker: packet filtering](https://docs.docker.com/engine/network/packet-filtering-firewalls/)); con
firewalld Docker crea una zona `docker` con destino `ACCEPT`. Además Docker publica por defecto en
`0.0.0.0` y `[::]` y solo escucha en loopback si se pide `127.0.0.1:` explícito
([publicación de puertos](https://docs.docker.com/engine/network/port-publishing/); antes de Docker
28.0 incluso los puertos en localhost eran alcanzables desde el mismo segmento L2). El arreglo
común es una regla en `DOCKER-USER`/`after.rules` ([ufw-docker](https://github.com/chaifeng/ufw-docker)),
invasivo y frágil: **no se automatiza**.

- Detección: `["docker","ps","--format","{{json .}}"]` → campo `Ports`
  (`0.0.0.0:5435->5432/tcp, [::]:5435->5432/tcp`, `80/tcp`); solo cuentan los que tienen IP de host
  `0.0.0.0` o `::` (o una IP pública). **No basta con `ss`**: con el proxy de usuario de Docker
  desactivado no hay socket, solo un DNAT invisible para `psutil` y `ss`. Se atribuye a la
  aplicación de Noust por la etiqueta `com.docker.compose.project`.
- Aviso: "Docker publica el 5435 (postgres de *arenna_postgres*) en todas las interfaces. ufw está
  activo pero **no lo bloquea**." Es exactamente el hallazgo real del dueño (ítem 8).
- Arreglos ofrecidos, todos **guiados**: (1) cambiar la publicación a `127.0.0.1:5435:5432` en el
  Compose (para una app de Noust, con un enlace a sus ajustes y el diff; para un contenedor
  ajeno, la instrucción); (2) el `daemon.json` `"ip": "127.0.0.1"` (afecta a todos, exige reiniciar
  Docker: no es un clic); (3) `ufw-docker`/`DOCKER-USER` como lectura recomendada.

**Guarda anti-bloqueo** (`Firewall.apply`, el único sitio que cambia reglas, regla 4):

1. Antes de `enable`, de cambiar la política o de borrar/denegar: los **puertos de `sshd -T`** (todos
   los `port`), el de la consola si escucha fuera de loopback, y el `ssh` de las conexiones
   establecidas ahora (`psutil.net_connections` en el puerto sshd: **IP del operador y de la
   central**) deben quedar permitidos. Si no, se rechaza con la lista de lo que faltaría.
2. **Interruptor de hombre muerto**: el cambio se aplica con una unidad transitoria programada que
   lo deshace en 180 s (`systemd-run --on-active=180 --unit=noust-firewall-revert -- /usr/bin/noust
   server firewall revert`), a menos que el operador pulse "Sigue funcionando" (cancela el
   temporizador). En firewalld las altas ya llevan `--timeout`; los borrados usan el temporizador.
3. La guarda nunca deja borrar una regla con comentario `noust:` que cubra el SSH o la consola.

### 3.7 SSH, claves, sshd y fail2ban

**Configuración efectiva**: `["sshd","-T"]` (root; sale con error si la configuración es inválida,
el código exacto hay que verificarlo en el arnés)
sale como `clave valor` en minúsculas, una línea por valor (`port`, `hostkey`, `authorizedkeysfile`
repiten). Ya existe `read_sshd_settings` (con caída al fichero) en `fleet/authorize.py`; se extiende,
no se duplica. Para evaluar un bloque `Match` para un usuario:
`["sshd","-T","-C","user=root,host=localhost,addr=127.0.0.1"]` (el
[manual](https://manpages.debian.org/testing/openssh-server/sshd.8.en.html) define `addr`, `user`,
`host`, `laddr`, `lport`, `rdomain`; pasar los tres primeros juntos, verificar en el arnés).
Reglas de sshd que importan:

- **"For each keyword, the first obtained value will be used"** y `Include` procesa los ficheros
  en orden léxico
  ([sshd_config(5)](https://manpages.debian.org/testing/openssh-server/sshd_config.5.en.html)).
  Debian 12, Ubuntu 22.04+ y RHEL 9 ponen el `Include /etc/ssh/sshd_config.d/*.conf` **al
  principio** (de memoria; Fedora y openSUSE: verificar en el arnés), así que gana el fichero que
  **sortea antes**:
  un `50-cloud-init.conf` (Ubuntu en la nube, con `PasswordAuthentication yes`) o
  `10-binarylane.conf` ([caso real](https://support.binarylane.com.au/support/solutions/articles/11000135607-why-your-ssh-hardening-changes-aren-t-working-on-binarylane))
  **anula** un cambio hecho en `sshd_config` o en un `99-*.conf`. Noust escribe
  **`/etc/ssh/sshd_config.d/00-noust.conf`** (con la marca "Generated by Noust") y **después
  verifica con `sshd -T` que el valor efectivo cambió**; si no cambió (otro `Include` posterior,
  o el fichero principal antes del `Include`), revierte y dice qué fichero gana (búsqueda de la
  palabra clave en los ficheros incluidos, en Python).
- `PermitRootLogin` por defecto `prohibit-password`; `PasswordAuthentication` y
  `KbdInteractiveAuthentication` por defecto `yes`; con `UsePAM yes` la contraseña puede entrar
  **por la ruta interactiva de PAM aunque `PasswordAuthentication no`** (aviso del propio manual y
  de la [guía de Mozilla](https://infosec.mozilla.org/guidelines/openssh)). Por eso "desactivar
  contraseñas" escribe **las dos**. Para OpenSSH < 8.7 la clave equivalente es
  `ChallengeResponseAuthentication`: se decide mirando cuál aparece en `sshd -T`.
- Nombre del servicio: `ssh` en Debian/Ubuntu (alias `sshd`), `sshd` en RHEL/SUSE. En Ubuntu ≥
  22.10 sshd va **activado por socket** (`ssh.socket`): `Port` y `ListenAddress` los define el
  socket y un cambio de puerto necesita `daemon-reload` + `restart ssh.socket`
  ([ejemplo](https://dev.to/sai_surapaneni/understanding-ssh-socket-based-activation-in-ubuntu-2404-28m)).
  Las opciones de autenticación solo necesitan `reload`, que **no cierra las sesiones abiertas**.
  **Noust no cambia el puerto** (NO en 3.1).

**Claves autorizadas** (lectura por Python puro, sin procesos):

- Cuentas: `root` más las de `getent passwd` con UID entre `UID_MIN` y `UID_MAX` de
  `/etc/login.defs` (**verificado** 1000-60000), shell listada en `/etc/shells` y home existente;
  grupos `sudo` (Debian/Ubuntu), `wheel` (RHEL/SUSE), `admin` (verificado con `getent group sudo wheel
  admin`). Ficheros según `authorizedkeysfile` de `sshd -T` (con `%h`, `%u`, `%U`); si hay
  `AuthorizedKeysCommand`, se avisa de que las claves también pueden venir de ahí.
- Parseo de cada línea: opciones (con comillas), tipo, blob, comentario; la huella `SHA256:` se
  calcula en Python (`base64(sha256(blob))` sin relleno) y el tamaño de una RSA del blob. Se puede
  cruzar con `ssh-keygen -lf`: **verificado** que OpenSSH 9.6 acepta líneas con opciones
  (`restrict,port-forwarding,permitopen="127.0.0.1:8443",command="/usr/bin/false" ssh-ed25519 ...`).
- Clasificación: **operador** (sin opciones restrictivas), **restringida** (opciones), **de la
  central** (contiene el marcador `permitlisten="127.0.0.1:1"` que pone `fleet/authorize.py`: se
  enseña como "Central *hub-1*: solo reenvía el puerto de la consola, no da shell"),
  **root deshabilitado por la nube** (`command="echo 'Please login as the user ...'` de las imágenes
  de Ubuntu en la nube; de memoria, verificar en el arnés), **débil** (RSA < 2048, DSA).
- Añadir clave: pegar la pública, validar, añadir al fichero con permisos 600/700 y dueño
  correctos, sin duplicados. Borrar: por huella, con copia. **Guardas**: no borrar la última clave
  de operador si la contraseña está desactivada; no borrar una clave de la central (se hace desde
  `noust node remove`); avisar si `StrictModes` la rechazaría (home o `~/.ssh` con escritura de
  grupo/otros, `authorized_keys` no del usuario): es el fallo más común de "puse la clave y no
  entra" y Forge lo documenta con los `chmod` a mano
  ([KB de Forge](https://laravel.com/forge/docs/knowledge-base/servers.md)).
- Contraseña de root: `["passwd","-S","root"]` (verificado el formato con `passwd -S $USER`:
  `yago P 2026-02-10 0 99999 7 -1`; `P` usable, `L` bloqueada, `NP` sin contraseña) o el campo 2 de
  `/etc/shadow` leído en Python (`!`/`*` = bloqueada). **No usar el módulo `spwd`**: Python 3.13 lo
  eliminó y Noust soporta 3.10+.

**Endurecer sshd de forma segura** (`SshdConfig.apply(cambios)`, único escritor):

1. Estado efectivo antes (`sshd -T`) y bloqueos existentes (`sshd_blockers` de la flota).
2. **Prueba de que la clave funciona**, antes de tocar contraseñas:
   a. *Estática*: hay al menos una clave de operador para una cuenta que sshd deja entrar (mirando
      `AllowUsers/AllowGroups/PermitRootLogin` efectivos), y `StrictModes` no la rechazaría.
   b. *Dinámica*: el registro de accesos muestra `Accepted publickey for <usuario> ... ssh2: ED25519
      SHA256:<huella>` con esa huella en los últimos 30 días. Fuente: journal por
      `["journalctl","_COMM=sshd","--since=-30d","-o","json","--no-pager","-n","5000"]` filtrado en
      Python (no `-g`: exige PCRE2), o `/var/log/auth.log` si no hay journald. Con `LogLevel
      VERBOSE` se registra además la huella de cada acceso (Mozilla), por eso el arreglo de
      "sensible SSH" lo fija. **La clave de la central no cuenta** (está restringida con
      `command="/usr/bin/false"`).
   c. Si no hay evidencia dinámica: se **rechaza desactivar contraseñas** y se ofrece "Añade tu
      clave pública primero" (y la instrucción de abrir otra sesión con ella).
3. Escribir `00-noust.conf`, luego `["sshd","-t"]` (código 0). Si falla, se borra el fichero y se
   muestra la salida literal.
4. `["systemctl","reload","ssh"]` (o `sshd`, timeout 20 s) y **verificar con `sshd -T`** el valor
   efectivo (ver la regla del primer valor).
5. **Confirmar o revertir** (estilo *commit confirmed*): una unidad transitoria
   `noust-ssh-revert` restaura el estado anterior en 120 s salvo confirmación
   (`POST /api/server/ssh/confirm`). El operador abre una **segunda** sesión y vuelve a pulsar
   "Funciona". Si la consola es alcanzable pero la sesión SSH nueva no, la consola sigue viva por
   el túnel ya abierto y el operador puede revertir a mano; si no vuelve, revierte solo.
6. Interacción con la flota (ítem 45): **`PermitRootLogin no` se rechaza si la clave de la central
   está en `/root/.ssh/authorized_keys`** ("el túnel de la central entra por root; autoriza antes
   una cuenta de túnel"). Nunca se toca `AllowTcpForwarding` (el túnel lo necesita, aunque CIS
   pida `no`).

Ajustes ofrecidos (uno a uno, con "antes → después" efectivos): desactivar contraseñas (las dos
claves), `PermitRootLogin prohibit-password` y, como segundo paso, `no`; `PermitEmptyPasswords no`;
paquete "valores sensatos" (`MaxAuthTries 4`, `LoginGraceTime 30`, `X11Forwarding no`,
`ClientAliveInterval 300`, `ClientAliveCountMax 2`, `LogLevel VERBOSE`), tomados de
[CIS/Ubuntu](https://ubuntu.com/aws/docs/aws-how-to/instances/cis-hardening/) y Mozilla. Los
algoritmos criptográficos se dejan a los valores por defecto de OpenSSH (tocarlos rompe clientes);
solo se avisa si el operador activó algo más débil que el defecto.

**fail2ban**

- Presente y vivo: `["fail2ban-client","ping"]` (`Server replied: pong`); jails:
  `["fail2ban-client","status"]` (`Jail list:\tsshd, nginx-http-auth`); por jail
  `["fail2ban-client","status","<jail>"]` → `Currently banned:`, `Total banned:`, `Banned IP list:`
  (expresiones regulares sobre la salida; el manual no documenta JSON ni códigos de salida,
  [man](https://manpages.debian.org/testing/fail2ban/fail2ban-client.1.en.html)). Ajustes:
  `["fail2ban-client","get","<jail>","bantime"]` (y `findtime`, `maxretry`, `ignoreip`).
- Acciones: `["fail2ban-client","set","<jail>","unbanip","<ip>"]` (la IP por `ipaddress`, el jail
  de la lista leída), `banip`, `addignoreip` (solo en ejecución: para que dure va al fichero).
  Recarga tras comprobar la configuración con `["fail2ban-client","-t"]`.
- Instalar y configurar (job): `["apt-get","install","-y","fail2ban","python3-systemd"]` en
  Debian/Ubuntu (**en Debian 12 no hay `/var/log/auth.log` por defecto y `python3-systemd` solo
  se recomienda**; sin `backend = systemd` el jail `sshd` no tiene qué leer y fail2ban no
  arranca, [caso](https://github.com/fail2ban/fail2ban/issues/3645)); `["dnf","install","-y","fail2ban"]`
  (en RHEL/Alma/Rocky exige **EPEL**, así que es una confirmación aparte); `["zypper","--non-interactive","install","fail2ban"]`.
  Se escribe `/etc/fail2ban/jail.d/noust.local`:
  `[DEFAULT] ignoreip = 127.0.0.1/8 ::1 <IP del operador y de la central>`, `bantime = 1h`,
  `findtime = 10m`, `maxretry = 5`, `banaction` según el cortafuegos (ufw, `firewallcmd-rich-rules`
  con firewalld, `nftables-multiport` en su defecto), `[sshd] enabled = true`, `backend = systemd`,
  `port = <puertos de sshd -T>`. Luego `-t`, `systemctl enable --now fail2ban` y verificación con
  `status sshd`. RunCloud avisa de que un exceso de intentos bloquea tu IP: por eso `ignoreip`
  lleva **las IP de quien está conectado ahora** (consola y sesiones SSH establecidas).
- Sustitutos reconocidos como "protección de fuerza bruta presente": `crowdsec`, `sshguard`
  (`systemctl is-active`).

### 3.8 Disco, limpieza y swap

**Uso por montaje** (sin procesos): `psutil.disk_partitions(all=False)` + `disk_usage`, más
**inodos** con `os.statvfs` (`f_files`/`f_ffree`; un disco con 10 % libre y 0 inodos falla igual),
tipo de sistema de ficheros y `ro`. Se **filtran** `overlay`, `squashfs`, `tmpfs`, `devtmpfs`,
montajes con el mismo dispositivo repetido (el caso WSL/Docker Desktop de la captura) y todo bajo
`/var/lib/docker`, `/run`, `/snap`. Topología y "el disco creció y la partición no":
`["lsblk","-J","-b","-o","NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,RO"]` (JSON); si el disco es mayor que la
suma de particiones se enseña la guía (`growpart` + `resize2fs`/`xfs_growfs`, como el
[KB de Forge](https://laravel.com/forge/docs/knowledge-base/servers.md)), **sin ejecutarla** (P3).

**Qué ocupa el espacio.** Una lista cerrada de candidatos con dos costes: rápidos (sin `du`) y
lentos (`du`, en un job de "Analizar", cacheado 10 min):

| Candidato | Cómo se mide | Limpieza |
|---|---|---|
| Journal | `["journalctl","--disk-usage"]` ("Archived and active journals take up 753.0M...", **verificado**) o sumando `/var/log/journal` en Python | `["journalctl","--rotate"]` y `["journalctl","--vacuum-size=200M"]` (o `--vacuum-time=14d`; solo borra journals **archivados**, no baja del tamaño del activo) |
| Caché de paquetes | Python sobre `/var/cache/apt/archives`, `/var/cache/dnf` (dnf5: `/var/cache/libdnf5`), `/var/cache/zypp` | apt: `["apt-get","clean"]`; dnf: `["dnf","clean","packages"]`; zypper: `["zypper","clean","--all"]` |
| Paquetes huérfanos y kernels viejos | simulación `["apt-get","-s","autoremove","--purge"]` (líneas `Remv`); dnf: `["dnf","autoremove","--assumeno"]` | `["apt-get","-y","autoremove","--purge"]` **solo tras enseñar la lista y confirmar** (`--purge` borra configuración); zypper: `["zypper","-n","purge-kernels"]` |
| Docker | `["docker","system","df","--format","json"]` (líneas JSON `{"Type":"Images","Size":"47.43GB","Reclaimable":"29.98GB (63%)",...}`; **verificado**: los tamaños salen como texto legible en base 10, no bytes) | ver abajo |
| Releases de Noust | `ReleaseManager.list()` por app | `ReleaseManager.prune(keep)` (la única implementación) |
| Copias de seguridad | `BackupManager.get_storage_usage()` | la retención del propio `BackupManager` |
| Logs de Noust (`/var/log/noust`, `job-logs`, `deploy-logs`) | Python | política de retención propia (SHOULD) |
| Volcados de memoria y temporales | `/var/crash`, `/var/lib/systemd/coredump`, `/tmp`, `/var/tmp` | solo informar (P3) |
| Snap (Ubuntu) | `snap list --all` con revisiones `disabled` | `["snap","remove","<n>","--revision","<r>"]` (P3) |
| Bases de datos | `/var/lib/postgresql`, `/var/lib/mysql`, `/var/lib/mongodb` | solo tamaño, ninguna acción |

Para cada ruta lenta: `["ionice","-c3","nice","-n","19","du","-sx","-B1","--","<ruta>"]`, 120 s cada
una, dos en paralelo como mucho (`-x` para no cruzar montajes). Nada de `du -d1 /` por defecto.

**Docker con cuidado.** `docker system prune` quita contenedores parados, redes sin uso, imágenes
"dangling" y caché de builds; `--volumes` añade **volúmenes** (los datos de las bases de datos) y `-a` todas las
imágenes sin contenedor
([docs](https://docs.docker.com/reference/cli/docker/system/prune/)). Reglas de Noust:

1. **Nunca `system prune` de un botón, nunca `--volumes` por defecto.**
2. Caché de builds, seguro: `["docker","builder","prune","-f","--filter","until=168h"]`.
3. Imágenes sin nombre: `["docker","image","prune","-f"]` (las `wasm-previous` **no** son
   "dangling": la etiqueta las mantiene, ese es el propósito del comentario de `PREVIOUS_TAG`).
4. **Nunca `image prune -a`**: borraría las `:wasm-previous` (ningún contenedor las usa tras
   actualizar) y con ellas el "vuelta atrás" de las apps Compose. Las imágenes sin uso se listan
   (`["docker","image","ls","--format","{{json .}}"]` menos las de contenedores y las
   `wasm-previous`) y se borran **una a una** (`["docker","image","rm","<id>"]`, sin `-f`).
5. Contenedores parados: lista con su antigüedad y borrado individual; aviso de que los de una app
   Compose parada se recrean con `up`, y los volúmenes se conservan.
6. **Volúmenes**: solo lista con tamaños y borrado individual con confirmación escrita.
7. Podman: `podman system df` análogo (P3).

**Swap** (job donde crea o quita):

- Estado: `psutil.swap_memory()`, `["swapon","--show","--bytes","--raw","--noheadings"]` (**verificado**:
  `/dev/sdc partition 8589934592 0 -2`, columnas `NAME TYPE SIZE USED PRIO`), swappiness por
  lectura de `/proc/sys/vm/swappiness`. Ubuntu Server ya crea `/swap.img`; zram (Fedora) sale como
  `/dev/zram0`: se muestran, no se duplican.
- **No aplicable en contenedores**: `["systemd-detect-virt","-c"]` (**verificado** `wsl`; salida
  `none` con código 1 si no es contenedor). En LXC/OpenVZ `swapon` da "Operation not permitted":
  se oculta el botón y se explica.
- Tamaño propuesto: RAM ≤ 2 GiB → 2 GiB; ≤ 8 GiB → 4 GiB; más → 4 GiB. Rechazo si tras crearlo
  quedarían menos de 2 GiB libres o menos del 15 %.
- Crear (`/swapfile`), cada paso por el runner o por `get_fs()`:
  1. Sistema de ficheros de destino (`["findmnt","-no","FSTYPE","-T","/"]`): ext4 y xfs →
     `fallocate`; **btrfs** → `["btrfs","filesystem","mkswapfile","--size","2G","/swapfile"]`
     (btrfs-progs ≥ 6.1; núcleo ≥ 5.0; a mano: `truncate -s 0`, `chattr +C`, `fallocate`, requiere
     un solo dispositivo y perfil de datos, [doc](https://btrfs.readthedocs.io/en/latest/Swapfile.html));
     ZFS, tmpfs, NFS, overlay → rechazar.
  2. Crear el fichero **vacío con modo 0600 antes** de reservar espacio (evita una ventana legible):
     `["fallocate","-l","2147483648","/swapfile"]`. Si `swapon` dice que "tiene huecos" (un
     fallocate en algunos sistemas de ficheros), caída a `["dd","if=/dev/zero","of=/swapfile","bs=1M","count=2048","status=none"]`
     (900 s; según [swapon(8)](https://manpages.debian.org/testing/mount/swapon.8.en.html) es "la solución más
     portable", xfs admite ficheros de swap preasignados desde Linux 4.18).
  3. `["mkswap","/swapfile"]`, `["swapon","/swapfile"]`, línea `/swapfile none swap sw 0 0` en
     `/etc/fstab` (atómico, con copia, comentario `# noust-swap`), `["findmnt","--verify"]`.
  4. Swappiness: `["sysctl","-w","vm.swappiness=10"]` y `/etc/sysctl.d/99-noust-swap.conf`.
  5. Fallo en cualquier paso → `swapoff`, borrar el fichero, restaurar `fstab`.
- Quitar: solo **el fichero que Noust creó** (marca en `fstab`): `["swapoff","/swapfile"]` (puede
  fallar con ENOMEM si el uso supera la RAM libre: se comprueba antes), quitar la línea, borrar. Nunca
  una partición ni `/swap.img` (guarda de propiedad en el punto de control).
- Trampa: `cloud-init` con `swap:` en su configuración recrea el swap en cada arranque.

### 3.9 Sistema: hora, hostname, OS, registros, procesos

- **Hora**: `["timedatectl","show"]` (**verificado**: `Timezone`, `LocalRTC`, `CanNTP`, `NTP`,
  `NTPSynchronized`, `TimeUSec`, `RTCTimeUSec`; existe desde systemd 239, comprobar en RHEL 8;
  respaldo: `timedatectl status`). Zona: `["timedatectl","set-timezone","<zona>"]`, validada
  contra `zoneinfo.available_timezones()`. NTP: `["timedatectl","set-ntp","true"]`, que **falla con
  "NTP not supported" si no hay timesyncd/chrony**; entonces se ofrece instalar `chrony` (apt, dnf,
  zypper). Desfase real: `["chronyc","-c","tracking"]` (CSV) o `timedatectl timesync-status`.
  Un reloj desviado rompe TLS, certbot **y los códigos TOTP de la consola** (`core/totp.py`);
  cambiar la zona desplaza los temporizadores `noust-cron-*` y `noust-backup-*` (`OnCalendar` local):
  el diálogo lista los que cambian.
- **Hostname**: leer `os.uname().nodename` y `/etc/hostname`; detalle en
  `["hostnamectl","--json=short"]` (**verificado** en systemd 255: `Hostname`, `StaticHostname`,
  `PrettyHostname`, `Chassis`, `KernelRelease`, `OperatingSystemPrettyName`, `MachineID`, `BootID`;
  **`hostnamectl show` no existe** y `--json` apareció en systemd 250 (de memoria), así que **no en Ubuntu 22.04** con
  systemd 249: respaldo `hostnamectl status`). Cambiar: `["hostnamectl","set-hostname","<n>"]` con
  nombre en minúsculas, etiquetas DNS de ≤ 63 caracteres
  ([hostnamectl](https://manpages.debian.org/testing/systemd/hostnamectl.1.en.html): "7-bit ASCII
  lower-case, sin espacios ni puntos"), **y actualizar la línea `127.0.1.1` de `/etc/hosts`** en
  Debian/Ubuntu (si no, `sudo: unable to resolve host`). **`cloud-init` restablece el hostname en
  cada arranque** salvo `preserve_hostname: true` (drop-in en `/etc/cloud/cloud.cfg.d/`): se detecta
  y se ofrece. Renombrar el host no renombra el nodo en la flota (el nombre lo pone la central).
- **OS, kernel, fin de vida**: `/etc/os-release` (`ID`, `VERSION_ID`, `SUPPORT_END` si existe),
  `os.uname()`, `["systemd-detect-virt"]`, `/etc/machine-id`, `psutil.boot_time()`. Tabla de fin de
  vida incluida en Noust y revisada en cada versión, con datos de
  [endoflife.date](https://endoflife.date/): Debian 11 → 2026-08-31 (fin de LTS; ELTS de pago hasta
  2031-06-30), Debian 12 → 2028-06-30, RHEL 8 → 2029-05-31, RHEL 9 → 2032-05-31, Leap 15.6 →
  2026-04-30, Leap 16.0 → 2027-10-31, Fedora 42 → 2026-05-27 (ya pasado), Fedora 43 →
  2026-12-09, Ubuntu 24.04 → 2029-05-31. **Los valores de Ubuntu 20.04 y 22.04 que devolvió la
  API (2022 y 2024) no parecen el fin del soporte estándar (de memoria, mayo de 2025 y abril
  de 2027): verificar qué campo se lee antes de copiarlos.** Hoy (2026-09-29) Debian 11 y Leap
  15.6 están **ya fuera de soporte**, exactamente lo que el chequeo debe gritar. No se ofrece actualizar de versión (Forge lo desaconseja de forma expresa:
  "recomendamos aprovisionar un servidor nuevo").
- **Estado del sistema**: `["systemctl","is-system-running"]` (`running|degraded|...`; **verificado**
  `running`) y `["systemctl","--failed","--no-legend","--plain","--no-pager"]` (unidades fallidas de
  todo el sistema, hoy solo se ven las de Noust): entra en el Resumen como "Sistema degradado: 2
  unidades".
- **Registros de cualquier unidad**:
  `["journalctl","--unit=<u>","-n","<N≤1000>","--no-pager","-o","json","--since=...","-p","<0-7>"]`,
  con `--unit=` pegado (nunca un nombre que empiece por `-`), paginación por cursor
  (`--cursor`, `--after-cursor`), kernel `-k`, arranque anterior `-b -1`, `--list-boots`. **El
  campo `MESSAGE` puede llegar como una lista de enteros** si no es UTF-8 (se decodifica) y cada
  línea se recorta a 4 KiB. Búsqueda: `-g` exige PCRE2 (se comprueba), o filtro en Python. En
  vivo se reutiliza el WebSocket `/ws/logs`. Unidades propias de Noust: las reglas actuales
  (`OWN_JOURNAL_UNITS` → admin); **toda otra unidad ajena también admin**, porque el journal del
  sistema lleva IP, usuarios y a veces secretos de terceros. Selector: fallidas primero, luego las
  activas (`_list_units(["*"])` ya existe).
- **Procesos**: `/api/system/processes` se mantiene y se añade `?group=unit` (suma de RSS y CPU por
  unidad leyendo `/proc/<pid>/cgroup`, y la aplicación de Noust cuando la unidad es de una).
  **Sigue sin haber `kill`**: la decisión D5 es correcta (un `SIGKILL` desde el panel a `sshd` o al
  pid 1 fue el motivo); la acción es "parar la unidad" y pasa por la capacidad estrecha de 5.2.

### 3.10 Comprobaciones de endurecimiento con arreglos (ítem 46)

Una sola fuente, `managers/host/hardening.py`, con una lista de `Check(id, group, severity,
status, evidence, fix)`. `status`: `pass | warn | fail | unknown | n/a`. `evidence` es texto
literal del sistema (una línea de `sshd -T`, la salida de `ufw status`). `fix`: `one_click` (con la
acción y si necesita sudo/job), `guided` (pasos y el `CommandHint`) o `none`. No hay una
"puntuación": las puntuaciones al estilo Lynis invitan a maquillarla; se cuenta por gravedad, como el
Security Advisor de cPanel (verde/amarillo/gris). Los `id` son estables y la consola traduce por `id`.
Un operador puede **aceptar un riesgo** con motivo y fecha (tabla nueva, auditado): lo pide el ENS
como excepción documentada (ítem 30).

| Id | Comprobación | Cómo se detecta | Gravedad | Arreglo |
|---|---|---|---|---|
| ssh.root_password | Se puede entrar como root con contraseña | `permitrootlogin yes` y (`passwordauthentication yes` o kbd-interactive con PAM) y `passwd -S root` = `P` | crítica | un clic: `PermitRootLogin prohibit-password` |
| ssh.password_auth | Contraseñas SSH activas | `passwordauthentication yes` o `kbdinteractiveauthentication yes` | aviso | un clic **con prueba de clave** (3.7) |
| ssh.root_login | root puede entrar por SSH | `permitrootlogin` ≠ `no` | aviso | un clic si hay clave de operador no root evidenciada y la clave de la central no vive en root; si no, guiado |
| ssh.empty_passwords | Contraseñas vacías | `permitemptypasswords yes` o campo vacío en `/etc/shadow` | crítica | un clic |
| ssh.keys | Claves débiles, permisos que `StrictModes` rechaza, ninguna clave de operador | parseo de `authorized_keys` | aviso | guiado (quitar/añadir clave) |
| ssh.defaults | `MaxAuthTries` > 6, `LoginGraceTime` > 120, `X11Forwarding yes`, sin `ClientAlive*` | `sshd -T` | baja | un clic (paquete sensato) |
| ssh.loglevel | Sin `LogLevel VERBOSE` (no hay huella por acceso) | `sshd -T` | info | un clic |
| f2b.missing | Ni fail2ban ni crowdsec/sshguard | `fail2ban-client ping`, `is-active` | aviso (crítica si el SSH escucha en público y hay contraseñas) | un clic: instalar y configurar |
| f2b.no_sshd_jail | fail2ban sin jail `sshd` o sin fichero que leer | `status`, `File list:` vacío | aviso | un clic: `backend = systemd` |
| fw.inactive | Sin cortafuegos activo con sockets públicos fuera de línea base | 3.6 | crítica/aviso | un clic con guarda y hombre muerto |
| fw.public_listener | Base de datos, Redis, memcached, API de Docker (2375) en todas las interfaces | psutil | crítica | guiado por caso (enlazar a loopback / regla) |
| fw.docker_bypass | Puertos de Docker publicados en `0.0.0.0` con ufw activo | `docker ps` | crítica si base de datos/panel | guiado: `127.0.0.1:` en el Compose |
| fw.ipv6_mismatch | Servicios en `::` con `IPV6=no` en ufw | `/etc/default/ufw` | info | guiado |
| fw.console_public | La consola escucha fuera de loopback sin TLS | `web.host` y sockets | crítica | enlace a Ajustes |
| upd.security_pending | Actualizaciones de seguridad pendientes | 3.1 | crítica si hay críticas/importantes o ≥ 7 días (por la edad de las listas y de `reboot-required`), aviso si no | un clic: aplicar seguridad |
| upd.reboot_required | Hace falta reiniciar (kernel, `kmod`, `glibc`...) | 3.3 | aviso; crítica pasados 7 días | un clic: programar reinicio |
| upd.stale_services | Servicios con librerías viejas | `needrestart -b`, `needs-restarting -s`, `zypper ps -sss` | aviso | un clic: reiniciar los elegidos |
| upd.auto_disabled | Sin actualizaciones automáticas de seguridad | 3.4 | aviso | un clic |
| upd.pkg_broken | dpkg/rpm a medio configurar | `dpkg --audit` | crítica | un clic: reparar (job) |
| upd.lists_stale | Listas de paquetes de más de 7 días | mtime del sello | info | un clic: refrescar |
| os.eol | Versión del SO fuera de soporte o a < 6 meses | tabla | crítica / aviso | guiado (migrar a un servidor nuevo) |
| time.unsynced | Reloj sin sincronizar | `NTPSynchronized=no` | aviso | un clic: `set-ntp true` o instalar chrony |
| mem.no_swap | RAM ≤ 2 GiB y sin swap | 3.8 | aviso | un clic: crear swap |
| disk.full | `df` > 85 % o inodos > 90 % en algún montaje relevante | 3.8 | aviso/crítica | un clic: asistente de limpieza |
| sys.degraded | `is-system-running` = degraded / unidades fallidas | 3.9 | aviso | enlace a Servicios filtrado por fallidas |
| sys.uid0 | Otra cuenta con UID 0 | `/etc/passwd` | crítica | guiado |
| sys.selinux | SELinux desactivado o permisivo (RHEL/Fedora) | `["getenforce"]` | info | guiado |
| sys.journal_volatile | El journal no es persistente (se pierde al reiniciar) | falta `/var/log/journal` | info | un clic: persistente (SHOULD) |
| noust.web_not_unit | La consola no es una unidad de systemd (no vuelve tras reiniciar) | `is-enabled noust-web` | aviso | un clic: `noust web enable` |
| noust.units_not_enabled | Unidades de aplicaciones sin `enable` | `is-enabled` | aviso | un clic |
| noust.fleet_key_root | La clave de la central vive en root (ítem 45) | claves | info | guiado |

Cada `one_click` es **el mismo método del manager** que usa su pestaña (regla 3); el botón del
Resumen y el de la pestaña no son dos implementaciones. `noust health` y `GET /api/system/health`
incorporan el **recuento** (críticas/avisos) y sus razones para que el Overview y los avisos
existentes las vean, pero la sonda cara nunca corre en `/api/system/health`: lee `ServerFacts`.

---

## 4. Arquitectura de información

### 4.1 Principios

1. **Sobre el pliegue (1440×900, ~700 px útiles; 390×844 móvil), cada pestaña dice el estado y da
   la acción principal.** Lo demás va detrás de una tarjeta que abre un panel lateral (`Drawer`), un
   diálogo o una vista secundaria. Las listas largas (paquetes, reglas, claves) muestran 8 filas y
   "Ver todas".
2. **Pestañas con URL** (`LinkTabs` como Ajustes; en móvil la tira se desplaza y deja visible la
   activa con `useTabStrip`): se pueden marcar, compartir y abrir en otra pestaña. Las vistas
   secundarias (Seguridad y Almacenamiento) usan `SegmentedControl` con `?view=` en la URL.
3. **Nada de diez secciones apiladas.** El Resumen es una pantalla; cada pestaña, lo mismo salvo
   Registros (el visor de registros es una pantalla completa que se desplaza por dentro).
4. **Color solo para estado** (D8): verde en marcha, ámbar en curso/aviso, rojo fallo, gris parado, más
   el violeta interactivo; todo estado con forma y texto (`StatusPill`, `StatusGlyph`).
5. **Los errores del sistema no se parafrasean**: la salida literal de apt, ufw o sshd va en mono
   (`SystemOutput`) con el arreglo sugerido encima, como el resto de la consola.
6. **Cada acción muestra su equivalente en terminal** (`CommandHint`) y la que exige modo sudo lo
   dice antes de pulsar, no después.
7. **Vacío y degradado explican, no fallan**: sin gestor soportado, sin cortafuegos, en un
   contenedor o en un sistema transaccional, la pestaña dice qué hay y qué no se puede hacer aquí.
8. **Por nodo, sin cambios en las pantallas**: `/n/<nodo>/server/...`; el encabezado lleva una pastilla
   con el nombre del servidor seleccionado (para no confundir dónde estás) y las acciones
   destructivas repiten el nombre del host en la confirmación.

### 4.2 Mapa

```
/server                     Resumen       (estado, "Necesita atención", 4 medidores, salud, acciones rápidas)
/server/updates             Actualizaciones
/server/security            Seguridad     ?view=checks|ssh|firewall|ports|bans   (defecto: checks)
/server/storage             Almacenamiento ?view=usage|analyze                   (defecto: usage)
/server/services            Servicios     (unidades del sistema y de Noust; fallidas primero)
/server/logs                Registros     ?unit=&priority=&since=&q=
/server/system              Sistema       (hora, hostname, reinicio y energía, SO, procesos, red, monitor)
```

Cómo se reparte lo que hoy hay: Salud → Resumen; tarjetas Host/Kernel/CPU/Memoria/Versión →
Resumen (medidores) y Sistema; tabla de discos → Almacenamiento (filtrada y con barras);
Red → Sistema (contraída); Procesos → Sistema (10 filas y "Ver todos" en un `Drawer`, con
agrupar por unidad); Monitor de recursos → Sistema (sección "Monitor de Noust", contraída con su
estado en la cabecera).

**Dónde viven los servicios (decisión D1).** Recomendado: `/server/services` con un interruptor
"Noust / Todo el sistema" (el mismo que ya existe: `noust_only`), y `/services` redirige ahí y
desaparece del menú lateral (un elemento menos, y el sitio natural para "gestionar servicios de la
VPS"). Alternativa conservadora: dejar `/services` como está y añadir `/server/services` solo
para el sistema. La segunda duplica una tabla; la primera obliga a actualizar enlaces, atajos y E2E.

Menú lateral: el elemento **Servidor** lleva el mismo contador rojo que Aplicaciones cuando hay
comprobaciones críticas o un reinicio pendiente de más de 7 días, y la barra superior (`MachineStrip`)
una pastilla "Reinicio necesario".

### 4.3 Qué se ve en cada pestaña

**Resumen** (objetivo: una pantalla a 1440×900; en 390 las tarjetas se apilan en el orden de la
lista, con "Necesita atención" primero):

```
 Servidor   [vps-2 ▾]  Ubuntu 24.04.5 LTS · kernel 6.8.0-45 · activo hace 12 d 3 h     [ Reiniciar… ▾ ]
 ┌ Necesita atención (3) ────────────────────────────────────────────────────────────────────────┐
 │ ✕ Crítico   4 actualizaciones de seguridad pendientes (una de kernel)      [Instalar seguridad]│
 │ ▲ Aviso     Hace falta reiniciar desde hace 9 días (linux-image, kmod)     [Programar…]        │
 │ ▲ Aviso     El puerto 5435 (Docker: arenna_postgres) está abierto al mundo  [Ver puerto]       │
 └── 2 más ▸ ────────────────────────────────────────────────────────────────────────────────────┘
 ┌ CPU ──────┐ ┌ Memoria ──────┐ ┌ Disco ───────┐ ┌ Actualizaciones ─┐
 │ 12 %      │ │ 61 % · swap 0 │ │ / 71 % · 18 GB│ │ 23 · 4 seguridad │
 │ carga 0,4 │ │ 2,4 / 4 GiB   │ │ peor: /var 88%│ │ auto: activadas  │
 └───────────┘ └───────────────┘ └──────────────┘ └──────────────────┘
 ┌ Salud ─────────────────────────────┐ ┌ Últimos eventos del servidor ──────────────────────────┐
 │ ● Nginx en marcha   ● Apps 14/17   │ │ 21:43 Servidor reiniciado (2 min 10 s)                  │
 │ ▲ Certificados: 2 caducan pronto   │ │ ayer   Actualizaciones aplicadas por yago (12 paquetes)│
 │ ● Memoria   ● Sistema: en marcha   │ │ ayer   sshd: contraseñas desactivadas (confirmado)      │
 └────────────────────────────────────┘ └─────────────────────────────────────────────────────────┘
```

- "Necesita atención" muestra las 3 más graves con su acción; el resto en un desplegable. Es la
  misma lista que alimenta la de "Necesita atención" del Overview (ítem 41).
- "Últimos eventos" sale del registro de auditoría filtrado por `server.*` (ya existe el
  `AuditLogger`); no hay tabla nueva.
- Acciones rápidas en el menú "Reiniciar…": reiniciar ahora, programar, apagar (con doble
  confirmación); si hay un reinicio programado, la cabecera lo dice con "Cancelar".

**Actualizaciones**:

```
 23 actualizaciones · 4 de seguridad · comprobado hace 3 h            [Comprobar ahora]
 ▲ Hace falta reiniciar (kernel, kmod) desde hace 9 días                [Programar reinicio]
 Automáticas: ● Solo seguridad · diarias · sin reinicio automático      [Cambiar]
 [Instalar seguridad (4)]  [Instalar todo (23)]                          ⓘ noust server updates apply --security
 ┌ Paquete ────────── Instalado ──── Nuevo ───── Tipo ─────────────────────────────────────┐
 │ linux-image-6.8.0  6.8.0-45       6.8.0-47     ✕ Seguridad · kernel · USN-1234           │
 │ openssl            3.0.13-...     3.0.13-...   ✕ Seguridad                                │
 │ ... 8 filas ... Ver las 23 ▸                                                              │
 └───────────────────────────────────────────────────────────────────────────────────────────┘
 Servicios que usan librerías viejas (5): nginx, cron, php8.3-fpm…   [Reiniciar los seguros]
```

- Al pulsar instalar: diálogo con el resumen (paquetes que se instalarán, los que **se quitarían**
  si los hay, los de impacto: Docker, nginx, `noust`), el modo sudo si falta y "esto reiniciará la
  consola si actualiza Noust". Luego el job con su registro (`LogViewer`) y los botones "Cerrar" y
  "Seguir en segundo plano"; sobrevive a cerrar la ventana (unidad transitoria).
- "Servicios con librerías viejas" solo aparece cuando hay; los que la lista de denegación
  prohíbe tienen candado y motivo.

**Seguridad**: vista por defecto `checks`; el resto, segmentos.

```
 Seguridad                                    Comprobaciones ▏SSH ▏Cortafuegos ▏Puertos ▏Bloqueos
 ┌ SSH ─────────────────┐ ┌ Cortafuegos ─────────┐ ┌ Fuerza bruta ─────┐ ┌ Exposición ────────────┐
 │ ▲ Contraseñas: sí    │ │ ● ufw activo · 4 reg.│ │ ● fail2ban · sshd │ │ ✕ 1 puerto fuera de la │
 │ root: prohibit-pass. │ │ política: deny       │ │ 3 IP bloqueadas   │ │ línea base (Docker)    │
 └──────────────────────┘ └──────────────────────┘ └───────────────────┘ └────────────────────────┘
 Endurecimiento: 3 críticas · 4 avisos · 22 correctas                          [Ver las 29]
 ✕ Contraseñas SSH activas ... evidencia: `passwordauthentication yes`   [Desactivar…]  (una prueba de clave primero)
 ▲ Sin actualizaciones automáticas ...                                    [Activar]
 ▲ ... (5 filas; el resto en "Ver las 29")
```

- Cada tarjeta abre su vista (`?view=`) con la tabla completa: reglas y "añadir regla" (solo
  con la guarda), claves por cuenta con huella, tipo y "Última vez usada" (la evidencia dinámica),
  puertos que escuchan con su veredicto, IP bloqueadas con "Desbloquear".
- Los arreglos "un clic" abren un diálogo con **antes → después efectivo**, la prueba (por ejemplo
  "Se ha visto un acceso con la clave `SHA256:...` de `root` el 27/09 desde 1.2.3.4") y, cuando aplica,
  el contador de "confirmar en 120 s".
- Desde la central, las acciones prohibidas al token de flota (5.3) muestran el candado con
  "Hazlo en el servidor: `noust server ssh harden`" y el `CommandHint`.

**Almacenamiento**:

```
 Almacenamiento                                          Uso ▏Analizar
 ┌ Montajes ──────────────────────────────────────────────────────────────────────────────────┐
 │ /        ███████░░░ 71 % · 18 GB libres · inodos 4 %        ext4  /dev/vda1                │
 │ /var/lib/docker ██████████ 88 % · 4 GB libres ▲                                             │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
 ┌ Se puede liberar ~ 9,4 GB ─────────────────────────────────────────────────────────────────┐
 │ Journal 1,2 GB [Reducir a 200 MB]   Caché de apt 640 MB [Vaciar]                            │
 │ Docker: caché de builds 6,1 GB [Limpiar]   Imágenes sin uso 1,4 GB [Revisar…]               │
 │ Releases viejas de 3 apps 0,9 GB [Revisar…]                                                 │
 └─────────────────────────────────────────────────────────────────────────────────────────────┘
 ┌ Swap ──────────────────────────────────────────────────────────────────────────────────────┐
 │ Sin swap · 2 GiB de RAM ▲ los builds pueden morir por memoria   [Crear 2 GiB de swap]      │
 └─────────────────────────────────────────────────────────────────────────────────────────────┘
```

"Analizar" (`du` en un job) abre el desglose por ruta (SHOULD).

**Servicios**: `SegmentedControl` Noust / Todo el sistema, chips de filtro (Fallidos 2 · En marcha
· Parados · Todos), búsqueda, tabla con acciones por fila (reiniciar, parar, habilitar) y el menú
"Ver registros" que abre Registros con la unidad. Las unidades de la lista de denegación llevan
candado. (Cockpit: pestañas Objetivos, Sockets, Temporizadores y Rutas; SHOULD: solo
Temporizadores, por las tareas de Noust.)

**Registros**: selector de unidad (fallidas primero), prioridad (`err` por defecto en "Solo
errores"), rango (`Última hora|24 h|Desde el arranque|Rango`), búsqueda y "Seguir en directo";
visor a pantalla completa. Botón "Ver el arranque anterior" y "Kernel".

**Sistema**: cuatro tarjetas (Hora y NTP, Nombre del host, Reinicio y apagado, Sistema operativo) y,
contraídas, Procesos (10), Red y Monitor de Noust. "Reinicio y apagado" enseña el estado
programado, las comprobaciones previas en vivo (semáforo por cada una: "volverá `noust-web`",
"`fstab` correcto", "sin jobs en marcha") y los botones.

### 4.4 Cómo se ve cada cosa por nodo y desde la central

- Todo `GET`/`POST` de `/api/server/...` pasa por `node_proxy` sin trabajo nuevo. La elevación
  sale del mapa OpenAPI del nodo (`x-noust-requires-elevation`): que un endpoint dependa de
  `require_elevated` basta para que la central pregunte al operador antes de reenviar.
- **Vista de flota (ítem 33)**: `GET /api/server/summary` devuelve en una sola llamada barata
  `{hostname, os, kernel, uptime_s, updates:{pending, security, reboot_required, auto}, hardening:{critical, warn}, disk:{worst_pct, worst_mount}, failed_units, power:{scheduled}, capabilities}`
  (de `ServerFacts`, sin sondas caras). La página Flota la lee de cada nodo y pinta una tabla
  "Servidores × actualizaciones/reinicio/seguridad" con "Actualizar seguridad en los seleccionados"
  (un job por nodo). Es lo que permite "actualizar todos los servidores" sin una pantalla nueva.
- **Estados propios de la flota**: nodo "reiniciando (esperado)" tras un reinicio pedido; un nodo
  con una actualización en curso muestra el job (los `job` del nodo llegan por su `/events`).
- **Ajustes del propio servidor** (ítem 37): lo que hoy vive en `/settings` y es "de este servidor"
  no entra aquí; esta página es solo la máquina.

---

## 5. API y CLI

### 5.1 Endpoints de `/api/server` (router nuevo, `web/api/server.py`)

Leyenda: **scope** = el que exige el endpoint (`read`; toda mutación es `admin` por
`required_scope`; los `GET` que enumeran superficie de ataque se marcan `admin` y llevan
`ensure_scope`). **Sudo** = `require_elevated`. **Job** = `202 JobAcceptedResponse`. **Central** =
qué puede hacer un token de flota (5.3): ok, refuse, o opt-in.

| Método y ruta | Scope | Sudo | Job | Central |
|---|---|---|---|---|
| `GET /api/server/summary` | read | no | no | ok |
| `GET /api/server/capabilities` | read | no | no | ok |
| `GET /api/server/updates` | read | no | no | ok |
| `POST /api/server/updates/refresh` | admin | no | sí (`os_refresh`) | ok |
| `POST /api/server/updates/apply` `{scope: security\|all, allow_removals?}` | admin | **sí** | sí (`os_update`, unidad transitoria) | ok |
| `POST /api/server/updates/repair` | admin | **sí** | sí | ok |
| `GET /api/server/updates/auto` | read | no | no | ok |
| `PUT /api/server/updates/auto` `{enabled, security_only, reboot?}` | admin | **sí** | no | ok |
| `POST /api/server/updates/restart-services` `{units[]}` | admin | **sí** | sí (corto) | ok |
| `GET /api/server/power` | read | no | no | ok |
| `POST /api/server/power/reboot` `{in_minutes\|at}` | admin | **sí** | no (acción programada) | ok |
| `POST /api/server/power/shutdown` `{confirm_hostname}` | admin | **sí** | no | **refuse** |
| `DELETE /api/server/power/scheduled` | admin | no | no | ok |
| `GET /api/server/ports` | admin (GET) | no | no | ok |
| `GET /api/server/firewall` | admin (GET) | no | no | ok |
| `POST /api/server/firewall/rules`, `DELETE /api/server/firewall/rules/{id}`, `POST /api/server/firewall/{enable,disable}` | admin | **sí** | no (segundos) + hombre muerto | **opt-in** |
| `POST /api/server/firewall/confirm` | admin | no | no | opt-in |
| `GET /api/server/ssh` | admin (GET) | no | no | ok |
| `POST /api/server/ssh/keys`, `DELETE /api/server/ssh/keys/{fingerprint}` | admin | **sí** | no | **refuse** |
| `PUT /api/server/ssh/config` | admin | **sí** | no | **refuse** |
| `POST /api/server/ssh/{confirm,revert}` | admin | no | no | refuse |
| `GET /api/server/fail2ban` | admin (GET) | no | no | ok |
| `POST /api/server/fail2ban/install` | admin | **sí** | sí (`install`) | ok |
| `POST /api/server/fail2ban/unban` | admin | no | no | ok |
| `POST /api/server/fail2ban/{ban,ignore}` | admin | **sí** | no | opt-in |
| `GET /api/server/storage` | read | no | no | ok |
| `POST /api/server/storage/analyze`, `GET /api/server/storage/analyze/latest` | admin | no | sí (`disk_scan`) | ok |
| `POST /api/server/storage/cleanup` `{action, dry_run?}` | admin | **sí** (salvo `dry_run`) | sí (`cleanup`) | ok |
| `POST /api/server/swap`, `DELETE /api/server/swap`, `PUT /api/server/swap/swappiness` | admin | **sí** | sí (`swap`) salvo `swappiness` | ok |
| `GET /api/server/time` · `PUT /api/server/time` `{timezone?, ntp?}` | read · admin | no · **sí** | no | ok |
| `GET /api/server/identity` · `PUT /api/server/identity/hostname` | read · admin | no · **sí** | no | ok |
| `GET /api/server/units?scope=system\|noust&state=` | read | no | no | ok |
| `POST /api/server/units/{name}/{start,stop,restart,reload,enable,disable}` | admin | **sí** para `stop`/`disable`/`restart` de la lista de "infraestructura"; el resto sin sudo | no | ok |
| `GET /api/server/logs?unit=&priority=&since=&until=&lines=&q=&boot=&cursor=` | admin (GET) | no | no | ok |
| `GET /api/server/hardening` | admin (GET) | no | no | ok |
| `POST /api/server/hardening/{id}/fix` | admin | **sí** | según la acción | según la acción |
| `PUT/DELETE /api/server/hardening/{id}/accepted` `{reason, until}` | admin | **sí** | no | opt-in |

`/api/system/*` no cambia (esquema de OpenAPI y `schema.gen.ts` intactos salvo `?group=unit` en
procesos); se añade `inodes`, `fstype` y `readonly` a los discos y se filtran los montajes de
ruido. Los modelos nuevos pasan por `web/pydantic_compat.py` (pydantic 1.10 en Ubuntu 24.04).
`JobType` gana `OS_REFRESH`, `OS_UPDATE`, `CLEANUP`, `SWAP`, `DISK_SCAN`, `INSTALL`. Eventos:
los `job` existentes; el `notice` de "servidor reiniciado" y notificaciones nuevas del
notificador (`updates_pending`, `reboot_required`, `disk_low`, `server_rebooted`).

Los endpoints de acciones que cambian el SSH o el cortafuegos devuelven
`pending_confirmation{expires_at}` y el resultado de la verificación (`sshd -T` antes/después).

### 5.2 La capacidad "unidades del sistema" (regla 4)

`ServiceManager._require_managed` no se relaja. Se añade `SystemUnits` (en `managers/host/units.py`)
que:

- **Lista** todas las unidades (ya existe `_list_units(["*"])` y `describe_units`) y las marca
  `managed`, `infra` (nginx, apache2/httpd, php*-fpm, mysql/mariadb, postgresql, redis, docker,
  containerd, fail2ban, ssh/sshd, cron/crond, chrony/chronyd, rsyslog, cloud-init...) o `protected`.
- **Actúa** solo sobre `infra` y sobre las de Noust por su propia vía, con verbos `restart`,
  `reload`, `start`, `stop`, `enable`, `disable`. Denegación permanente: `noust-web` y
  `noust-monitor` (van por `noust web`/`noust monitor`), `dbus*`, `systemd-*`, `networking`,
  `NetworkManager`, `getty@*`, `user@*`, `init.scope`, los temporizadores de cron y copias.
- Consulta el estado con `systemctl show` (ya en la lista de solo lectura del runner) y pide modo
  sudo para lo destructivo, como `services.py`.

### 5.3 Qué puede hacer una central (decisión D2, la más importante de seguridad)

`fleet_refusal` (`web/auth.py`) dice que una central gestiona "aplicaciones, servicios, bases de
datos y copias" con la autoridad de su operador, y **no** las credenciales del nodo, ni quién
alcanza su consola, ni el enrolado, para que una central comprometida no pueda "dejar fuera al
operador, ampliar la exposición del nodo o acuñarse una credencial que sobreviva a la revocación".
CLAUDE.md lo resume: **la central nunca tiene shell**. La gestión de la VPS cruza exactamente esa
línea en cuatro sitios:

| Acción | Riesgo si la central está comprometida |
|---|---|
| Añadir una clave SSH | shell root permanente; sobrevive a revocar el token de flota |
| Cambiar sshd (contraseñas, root, `Match`) | dejar fuera al operador o abrir el acceso |
| Cambiar el cortafuegos | dejarlo fuera o exponer bases de datos |
| Apagar (`poweroff`) | nodo caído hasta la consola del proveedor |

Propuesta: **rechazar por defecto a un token de flota** estas rutas (se añaden a
`fleet_refusal`, sitio único, con su frase), **permitir** todo lo demás (actualizaciones,
reinicio, limpieza, swap, hora, hostname, unidades de infraestructura, registros, lecturas de
seguridad) y ofrecer un **opt-in por nodo** que solo su operador puede activar
(`fleet.central_may_manage_access`; la sección `fleet` ya está en `FLEET_PROTECTED_CONFIG_SECTIONS`,
así que una central no puede cambiarlo). Es la "lista de permisos por nodo" del ítem 45. Desde la
central esas acciones enseñan el candado y el comando para el nodo. **Choca con la letra del ítem
33** ("todo lo que se pueda hacer en un servidor, desde la central"): hay que decidirlo con el
dueño (D2).

### 5.4 CLI: `noust server ...` (`cli/commands/server.py`, una línea en `COMMAND_MODULES`)

Todo con `--json`, `--dry-run` heredado (el `DryRunRunner` ya lo cubre), `-y/--yes` en lo que
confirma, y la misma implementación que el API (un manager, dos clientes).

| Comando | Equivale a |
|---|---|
| `noust server status` | `GET /summary` |
| `noust server updates list \| refresh \| apply [--security] [--allow-removals] [--detach] \| repair \| auto [status\|enable\|disable] [--security-only]` | `/updates*`; `apply` en primer plano imprime la salida del gestor y `--detach` usa la unidad transitoria; `noust server updates run --job ID` es la orden interna de esa unidad |
| `noust server restart-services [UNIT...]` | `/updates/restart-services` |
| `noust server reboot [--in 5m \| --at 04:00] [--now] [--if-idle]`, `reboot status \| cancel`, `shutdown [--in ...]` | `/power*` |
| `noust server firewall status \| ports \| allow PORT [--from IP] [--proto tcp] \| deny ... \| delete ID \| enable \| disable \| confirm \| revert` | `/firewall*`, `/ports` |
| `noust server ssh status \| keys list \| keys add [--user U] [--file F \| -] \| keys remove FINGERPRINT \| harden [--no-password] [--no-root] [--defaults] \| confirm \| revert` | `/ssh*` |
| `noust server fail2ban status \| install \| unban IP [--jail J] \| ban IP \| ignore IP` | `/fail2ban*` |
| `noust server disk usage \| analyze \| clean {journal\|pkg-cache\|autoremove\|docker-build-cache\|docker-images\|releases} [--dry-run]` | `/storage*` |
| `noust server swap status \| create --size 2G \| remove \| swappiness N` | `/swap*` |
| `noust server time status \| timezone ZONE \| ntp on\|off` | `/time` |
| `noust server hostname [set NAME]` | `/identity` |
| `noust server units [--all] [--failed]`, `unit start\|stop\|restart\|enable\|disable NAME` | `/units*` |
| `noust server logs UNIT [-f] [-n N] [--since T] [-p PRIO] [-k] [-b]` | `/logs` |
| `noust server hardening [check] \| fix ID \| accept ID --reason ... [--until ...]` | `/hardening*` |
| `noust server processes [--by-unit] [--sort cpu\|memory]` | `/api/system/processes` |

`noust health` se mantiene y añade el recuento de endurecimiento; `--json` con el mismo esquema
compatible hacia atrás (campos nuevos, ninguno quitado).

---

## 6. Arquitectura del código, pruebas y arnés

- `core/packages.py`: la tabla de `setup.py` (`PackageManager`, `PACKAGE_MANAGERS`,
  `detect_package_manager`) y una función `os_family()` por `/etc/os-release`. `setup.py`,
  `web.py`, `dependencies.py` y `database/base.py` la usan (quedan de ganancia cuatro llamadas a
  `apt-get` reescritas a mano).
- `managers/host/`: `facts.py` (caché), `updates.py`, `power.py`, `firewall.py`, `sockets.py`,
  `ssh.py`, `fail2ban.py`, `storage.py`, `swap.py`, `clock.py`, `identity.py`, `journal.py`,
  `units.py`, `hardening.py`. Cada uno recibe `runner` y `fs` de los seams; ninguno importa
  `subprocess` (lo comprueba `tests/test_architecture.py`).
- Chokepoints (regla 4): `SshdConfig.apply` es el único escritor del drop-in; `Firewall.apply` el
  único que toca reglas; `SystemUnits` es la lista blanca/negra; `Cleanup` es un registro cerrado de
  acciones (**ningún endpoint acepta una ruta**); `Swap` solo quita lo que puso Noust.
- Hombre muerto: una unidad transitoria que ejecuta `noust server <x> revert`; su estado
  pendiente vive en la base de datos (`server_pending`), para que un reinicio del panel no lo
  pierda y el CLI pueda confirmarlo o revertirlo.
- **Sin `except Exception` mudo** (regla 2): cada sonda captura sus excepciones concretas
  (`OSError`, `CommandError`, `ValueError`) y devuelve `status="unknown"` con el motivo, que se
  enseña; una sonda que falla nunca deja el Resumen vacío ni se convierte en un aviso cosmético.
- **Pruebas**: `FakeRunner` con guiones de salida **capturada de verdad** guardada en
  `tests/fixtures/server/{apt,dnf4,dnf5,zypper,ufw,firewalld,sshd-T,fail2ban,needrestart,
  swapon,timedatectl,hostnamectl,docker-df,ss}/...` (una por distro y versión). Casos obligados: el
  formato `Inst` con varios orígenes; `dnf check-update` con línea partida; `zypper` XML con
  parches y `restart="true"`; `needrestart -b` con `KSTA` 0-3; `ufw` con reglas v4 y v6; `sshd -T`
  con `Match` y con `50-cloud-init.conf`; el hombre muerto (reversión al no confirmar); la guarda
  anti-bloqueo (rechaza `enable` sin regla SSH; rechaza desactivar contraseñas sin evidencia;
  rechaza `PermitRootLogin no` con la clave de la central en root); el job de actualización que
  sobrevive a un reinicio de `noust-web`; y `is_read_only` para cada argv nuevo de lectura.
- **Arnés `tests/integration/run.py`** (contenedores con systemd): ampliar a `debian:12`,
  `ubuntu:24.04`, `fedora` reciente, `almalinux:9` y `opensuse/leap:15.6` para `updates list`
  (con repos reales o un repo local con paquetes de prueba marcados como seguridad),
  `ssh harden` con un `sshd` real, `fail2ban install` y `logs`. **No se pueden probar en un
  contenedor** reinicio, swap, ufw ni fstab: necesitan una máquina virtual (qemu con systemd); se
  marcan como el nivel de pruebas manuales antes de cada release.
- Rendimiento: ninguna sonda de más de 100 ms en la ruta de una petición; todo lo demás de
  `ServerFacts`. El hilo de refresco cada 15 min reutiliza el patrón de `metrics_collector.py`.
- Documentación y traducciones: catálogos `es`/`en` nuevos (`server.updates.*`, ...), con la regla
  de frases enteras; las salidas del sistema, sin traducir.

---

## 7. Fases, prioridades, riesgos y decisiones

### 7.1 MUST / SHOULD / COULD

| | Qué | Por qué |
|---|---|---|
| **MUST** | Pestañas y Resumen con "Necesita atención" | El ítem 32 y la petición del dueño sobre páginas verticales |
| **MUST** | Actualizaciones (lista con seguridad, aplicar como job en unidad transitoria, reinicio necesario, servicios con librerías viejas, automáticas nativas) | Ítems 29, 32 y 46; sin la unidad transitoria un `noust` en la lista mata el job |
| **MUST** | Reinicio programado con pre-vuelo y evento de vuelta; apagado con confirmación | Ítem 29; el reinicio es la mitad de "actualizar" |
| **MUST** | Cortafuegos ufw+firewalld, sockets reales, aviso de Docker, guarda anti-bloqueo | Ítems 29, 32 y 46; hallazgo real del ítem 8 |
| **MUST** | SSH: config efectiva, claves, endurecimiento con prueba, confirmación y reversión; fail2ban | Ítem 46, con las comprobaciones del "no te deja fuera" |
| **MUST** | Disco (montajes limpios, inodos), limpieza (journal, cachés, Docker con cuidado, releases), swap | Ítem 29; los builds que mueren por memoria |
| **MUST** | Hora/zona/NTP, hostname, SO/kernel/fin de vida, unidades fallidas, registros de cualquier unidad, servicios del sistema | Ítem 29 |
| **MUST** | `noust server ...` con paridad y modo sudo/`--dry-run`/auditoría en todo | Reglas del repositorio |
| **MUST** | `GET /api/server/summary` y "reiniciando (esperado)" en la central | Ítems 32 (por nodo) y 33 |
| **MUST** | Rechazo de las rutas de acceso a un token de flota con opt-in por nodo | Decisión D2 |
| SHOULD | Análisis de espacio con `du` (job), temporizadores y sockets de systemd, journal persistente, instalar `needrestart`, ventanas de reinicio "si está libre", retención de logs de Noust, ESM/Ubuntu Pro (`pro security-status --format json`) | Valor claro, sin bloqueo |
| SHOULD | Alinear el monitor de recursos (que sus acciones pidan modo sudo) | Hueco encontrado en 1.1 |
| COULD | Comprobación de alcance desde la central (una conexión TCP desde la central a los puertos públicos del nodo) | Diferenciador único, opt-in |
| COULD | Podman, zram, ampliar el sistema de ficheros guiado, cambio de puerto SSH guiado | |
| **NO** | Matar procesos (D5), actualizar de versión de SO, gestionar usuarios del sistema (el ENS de ítem 30 va por cuentas de Noust), redimensionar particiones, instalar paquetes arbitrarios, alpine/arch | Riesgo alto o fuera de alcance |

### 7.2 Riesgos

| Nº | Riesgo | Mitigación |
|---|---|---|
| R1 | Actualizar `noust` en su propio job (postinst reinicia `noust-web`; el árbol de Python cambia bajo el proceso) | Unidad transitoria, `import` previo, reconciliación de jobs al arrancar, prueba en el arnés con el repo de OBS |
| R2 | Bloquear al operador (SSH, cortafuegos) | Guarda + prueba de clave + hombre muerto + verificar con `sshd -T` |
| R3 | dnf5 y zypper: formatos que no pude verificar (JSON de dnf5, `needs-rebooting`, `-C` de sshd) | Fixtures reales y prueba del arnés antes de fijar el parser |
| R4 | Una sonda que tarda (apt, needrestart, docker) frena el Resumen | `ServerFacts` en caché y refresco por hilo |
| R5 | `--dry-run` ejecuta de verdad una acción: `is_read_only` acepta el comando si *cualquier* argumento coincide (`ufw allow 22 comment status`, `docker system prune` si se añadiera `system`) | Endurecer `is_read_only` para mirar el subcomando, añadir solo lo estrictamente de lectura, validar argumentos, un test por cada argv |
| R6 | Un token de flota cambia acceso (5.3) | Rechazo por defecto |
| R7 | La limpieza de Docker borra `wasm-previous` o volúmenes | Nunca `-a` ni `--volumes`; borrado individual; prueba con imagen etiquetada |
| R8 | Distros no soportadas (Alpine, Arch, transaccionales, contenedores) | `capabilities` y `UnsupportedBackend` con mensaje |
| R9 | `is-system-running`/`timedatectl show`/`hostnamectl --json` ausentes en systemd viejos (RHEL 8, Ubuntu 22.04) | Respaldos por fichero o `status`, probados con fixtures |
| R10 | El hombre muerto lo dispara un reinicio del panel o una espera larga | Estado en base de datos y unidad transitoria independiente del panel |

### 7.3 Decisiones que necesitan al dueño

- **D1 Servicios**: mover `/services` bajo `/server/services` (recomendado) o mantener las dos.
- **D2 Central**: rechazar por defecto las acciones de acceso (recomendado, opt-in por nodo) o
  permitir todo desde la central (ítem 33 literal).
- **D3 Sistemas transaccionales de openSUSE**: solo estado (recomendado) o soportarlos.
- **D4 EPEL para fail2ban en RHEL/Alma/Rocky**: pedir confirmación aparte (recomendado) o no
  ofrecer la instalación en EL.
- **D5 `dist-upgrade` con eliminaciones**: permitido tras enseñar la lista (recomendado) o nunca.
- **D6 "Aceptar riesgo"**: tabla propia con motivo y caducidad (recomendado para el ENS) o
  descartar comprobaciones sin registro.
- **D7 Reinicio automático tras actualizar**: apagado y sin interruptor en 3.1 (recomendado).
- **D8 Tabla de fin de vida**: incluida en el paquete (recomendado; sin llamadas de red) o consulta a
  endoflife.date (rompe la regla de "sin red salvo el comprobador de versión").

---

## 8. Fuentes

Competencia: [Forge: Servidores](https://laravel.com/forge/docs/servers/the-basics.md),
[Forge: Seguridad](https://laravel.com/forge/docs/servers/security.md),
[Forge: Red y cortafuegos](https://laravel.com/forge/docs/resources/network.md),
[Forge: Monitorización](https://laravel.com/forge/docs/servers/monitoring.md),
[Forge: Base de conocimiento de servidores](https://laravel.com/forge/docs/knowledge-base/servers.md),
[Forge: CVE-2026-31431](https://laravel.com/forge/docs/knowledge-base/cve-2026-31431.md),
[Ploi: documentación del servidor](https://ploi.io/documentation/server),
[RunCloud: Seguridad](https://runcloud.io/docs/server/security/),
[RunCloud: SSH](https://runcloud.io/docs/ssh-service-hardening-on-runcloud),
[CloudPanel: Seguridad](https://cloudpanel.io/docs/v2/admin-area/security),
[ServerPilot: seguridad](https://serverpilot.io/docs/security/),
[Cockpit: aplicaciones](https://cockpit-project.org/applications.html),
[Cockpit: actualizaciones](https://github.com/cockpit-project/cockpit/wiki/Feature:-System-Updates-for-dnf%2C-yum%2C-apt-hosts),
[Cockpit: Software updates (blog)](https://www.ctrl.blog/entry/cockpit-packagekit/),
[Coolify: parcheo del servidor](https://coolify.io/docs/core/infrastructure/servers/server-patching),
[Dokploy: servidor](https://docs.dokploy.com/en/docs/core/server/overview),
[Plesk: Fail2Ban](https://docs.plesk.com/en-US/obsidian/administrator-guide/server-administration/plesk-for-linux-protection-against-brute-force-attacks-fail2ban.73381/),
[cPanel Security Center](https://docs.cpanel.net/whm/security-center/cphulk-brute-force-protection/),
[HestiaCP](https://fornex.com/help/hestia-control-panel/),
[aaPanel: seguridad](https://docs.digitalocean.com/products/marketplace/catalog/aapanel/).

Técnicas: [apt-get(8)](https://manpages.debian.org/testing/apt/apt-get.8.en.html),
[apt(8)](https://manpages.debian.org/testing/apt/apt.8.en.html),
[needrestart(1)](https://manpages.debian.org/testing/needrestart/needrestart.1.en.html),
[needrestart README.batch](https://doc.duckcorp.org/cgi-bin/dwww/usr/share/doc/needrestart/README.batch.md),
[needrestart en Ubuntu 22.04](https://bugs.launchpad.net/ubuntu/+source/needrestart/+bug/2004203),
[dnf needs-restarting](https://dnf-plugins-core.readthedocs.io/en/latest/needs_restarting.html),
[dnf5 needs-restarting](https://dnf5.readthedocs.io/en/latest/dnf5_plugins/needs_restarting.8.html),
[dnf5 check-upgrade](https://dnf5.readthedocs.io/en/latest/commands/check-upgrade.8.html),
[dnf-automatic](https://dnf.readthedocs.io/en/latest/automatic.html),
[dnf5-automatic](https://dnf5.readthedocs.io/en/latest/dnf5_plugins/automatic.8.html),
[zypper(8)](https://manpages.opensuse.org/Tumbleweed/zypper/zypper.8.en.html),
[salida XML de zypper](https://en.opensuse.org/openSUSE:Standards_Zypper_Xml),
[libzypp: variables de entorno](https://doc.opensuse.org/projects/libzypp/HEAD/zypp-envars.html),
[os-update(8)](https://manpages.opensuse.org/Tumbleweed/os-update/os-update.8.en.html),
[unattended-upgrades en Debian](https://wiki.debian.org/UnattendedUpgrades),
[reboot-required en Debian/Ubuntu](https://linux-audit.com/check-required-reboot-debian-ubuntu-others/),
[ufw(8)](https://manpages.debian.org/testing/ufw/ufw.8.en.html),
[firewall-cmd(1)](https://firewalld.org/documentation/man-pages/firewall-cmd.html),
[Docker: filtrado de paquetes y cortafuegos](https://docs.docker.com/engine/network/packet-filtering-firewalls/),
[Docker: publicación de puertos](https://docs.docker.com/engine/network/port-publishing/),
[ufw-docker](https://github.com/chaifeng/ufw-docker),
[docker system df](https://docs.docker.com/reference/cli/docker/system/df/),
[docker image prune](https://docs.docker.com/reference/cli/docker/image/prune/),
[docker system prune](https://docs.docker.com/reference/cli/docker/system/prune/),
[sshd_config(5)](https://manpages.debian.org/testing/openssh-server/sshd_config.5.en.html),
[sshd(8)](https://manpages.debian.org/testing/openssh-server/sshd.8.en.html),
[Mozilla: OpenSSH](https://infosec.mozilla.org/guidelines/openssh),
[el primer valor gana en `sshd_config.d`](https://support.binarylane.com.au/support/solutions/articles/11000135607-why-your-ssh-hardening-changes-aren-t-working-on-binarylane),
[ssh.socket en Ubuntu 24.04](https://dev.to/sai_surapaneni/understanding-ssh-socket-based-activation-in-ubuntu-2404-28m),
[CIS Ubuntu 22.04](https://ubuntu.com/aws/docs/aws-how-to/instances/cis-hardening/),
[fail2ban-client(1)](https://manpages.debian.org/testing/fail2ban/fail2ban-client.1.en.html),
[fail2ban en Debian 12](https://github.com/fail2ban/fail2ban/issues/3645),
[timedatectl(1)](https://manpages.debian.org/testing/systemd/timedatectl.1.en.html),
[hostnamectl(1)](https://manpages.debian.org/testing/systemd/hostnamectl.1.en.html),
[shutdown(8)](https://manpages.debian.org/testing/systemd-sysv/shutdown.8.en.html),
[journalctl(1)](https://manpages.debian.org/testing/systemd/journalctl.1.en.html),
[swapon(8)](https://manpages.debian.org/testing/mount/swapon.8.en.html),
[swapfile en btrfs](https://btrfs.readthedocs.io/en/latest/Swapfile.html),
[ss(8)](https://manpages.debian.org/testing/iproute2/ss.8.en.html),
[fin de vida](https://endoflife.date/).
