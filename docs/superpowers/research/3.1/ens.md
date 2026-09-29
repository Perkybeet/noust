# ENS categoría MEDIA y Noust 3.1: revisión de cumplimiento del Anexo II del RD 311/2022

Fecha: 2026-09-29. Rama: `dev/3.1`. Estado: investigación (solo lectura del repositorio; ningún cambio de código).
Origen: backlog 30 y 30b del dueño ("ENS categoría MEDIA", "todos los huecos que Noust pueda cerrar en código").
Destino: insumo del diseño de la 3.1 y, más adelante, de `docs/ENS.md` (guía de bastionado para el auditor).

---

## 0. Resumen ejecutivo

**Veredicto.** Noust 3.0.0 tiene una base técnica sólida en lo que el ENS llama seguridad del canal y de la
consola (puntos únicos de control: `CommandRunner`, `require_auth`, `ensure_elevated`; CSP estricta; secretos 0600;
sellado; llaves de flota restringidas), pero **no puede sostener una auditoría de categoría MEDIA** por siete
carencias, todas de identidad, trazabilidad y criptografía (G01 a G07 en la sección 4). Todo lo demás es o bien
responsabilidad del operador (organizativo, físico, red, proveedores, personal) o bien refuerzo cerrable en código
sin arquitectura nueva.

**Hallazgos que el encargo no traía** (verificados en el código; detalle y archivos en la sección 2.2):

1. El **token maestro enviado como `Authorization: Bearer`** es `admin` sin segundo factor y sin modo sudo. Con el 2FA
   activado, el 2FA solo protege el inicio de sesión del navegador (H1).
2. Los registros del logger **`noust.audit`** (sentencias SQL de la consola, borrado de bases de datos, cron, copias,
   destinos, integraciones) **no tienen ningún handler** en todo `src/noust`: por lectura del código no llegan a
   ningún sitio. `docs/security.md` afirma lo contrario (H2; confirmar con una prueba antes de reescribir la doc).
3. La **retención real de la auditoría es de unos 20 MiB** (4 ficheros de 5 MiB); la rotación borra el más antiguo y
   no se puede configurar desde `config.yaml` (H3).
4. El **actor de un evento no es una persona**: `master`, `token:<nombre>` o 12 hex de una sesión (H4).
5. El certificado autofirmado es **RSA-2048, CN sin SAN, 10 años**; el CCN clasifica RSA por debajo de 3000 bits como
   "legacy" con fechas de caducidad 2025/2026 [verificar contra CCN-STIC 221/807] (H5).
6. **Inactividad de sesión de 12 h** (mp.eq.2 pide un "tiempo prudencial"), y la "inactividad" real es una caducidad
   renovada al 50 %, no un temporizador de actividad (H6).
7. La pantalla previa al login **revela hostname, versión y si hay 2FA**; el login distingue el factor que falla y
   cuenta intentos restantes (op.acc.5.7 / op.acc.6.7 piden lo contrario) (H7).
8. El bloqueo es **por IP y se levanta solo a los 15 min**; op.acc.6.8 pide bloquear la cuenta "requiriendo una
   intervención específica para reactivarla" (H8).
9. **`admin` equivale a root** (servicios, cron y ganchos de copia crudos; compilaciones como root, backlog 44), y un
   admin puede debilitar la propia configuración de seguridad (`PUT /api/config/web`): la separación de funciones
   no es alcanzable solo con roles (H9, H10).
10. **Numeración**: el encargo usa la de RD 3/2010 (`op.acc.1-7`, `op.exp.1-11`, `mp.info.1-9`). En el RD 311/2022
    son `op.acc.1-6`, `op.exp.1-10`, `mp.info.1-6` (más `op.nub.1`). Concordancia en el anexo A.

**Recomendación de arquitectura** (sección 4.2): cinco roles (`viewer`, `operator`, `admin`, `security`, `auditor`),
**una cuenta = un rol** (lo pide literalmente op.acc.1.2), MFA obligatoria por usuario, alta por invitación de un
solo uso, token maestro relegado a *break-glass*, aprobación de cuatro ojos para las acciones críticas y las
equivalentes a root, y una auditoría v2 con identidad, catálogo cerrado de eventos, cadena HMAC, retención por
tiempo y envío a journald y syslog RFC 5424 con cola en disco. Un perfil `security.profile: ens-medium` fija los
valores y `noust ens check` produce la evidencia para el auditor.

**Corte mínimo para una 3.1 certificable**: G01 a G07 más `docs/ENS.md`. G08 a G14 son los refuerzos que MEDIA
exige o que un auditor pedirá; G15 en adelante, mejora.

---

## 1. Alcance, fuentes, método y limitaciones

### 1.1 Fuentes usadas

| Fuente | Cómo se leyó | Uso |
|---|---|---|
| RD 311/2022 (BOE-A-2022-7191), texto consolidado. ELI: <https://www.boe.es/eli/es/rd/2022/05/03/311/con> | Descargado íntegro de la API de datos abiertos del BOE (`https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-2022-7191/texto`, fecha de actualización 2022-05-04, sin modificaciones posteriores) y leídos todos los artículos, los anexos I a III y el Anexo II completo | Requisitos, refuerzos y aplicabilidad por categoría: **fuente normativa de toda la matriz** |
| CCN-STIC 804 "ENS. Guía de implantación", junio 2017 (<https://www.aec.es/wp-media/uploads/DPD-00268.SEG-GUI-006-804_medidas_de_implantacion_del_ens.pdf>) | PDF leído (100 pp.) | Criterios de implantación que no han cambiado (contraseñas, bloqueo de sesión, registros). **Está redactada sobre el RD 3/2010** |
| RFC 5424 (<https://www.rfc-editor.org/rfc/rfc5424.html>) | Leídas las secciones 6.2.3, 6.3.2 y 7 y las tablas 1 y 2 | Formato de syslog: facilidad 13 "log audit", SD-ID `nombre@<PEN>`, `timeQuality`, `origin` |
| CCN-STIC 807 (mayo 2022) y CCN-STIC 221 | **Solo por fuentes secundarias** (<https://inza.blog/2025/09/01/robustez-de-las-claves-asimetricas-recomendadas-por-el-ccn/>, resultados de búsqueda, <https://www.ccn.cni.es/index.php/en/menu-news-ccn-en/970-nueva-guia-ccn-stic-221-sobre-mecanismos-criptograficos-autorizados-por-el-ccn>) | Estado de RSA-2048 y curvas recomendadas. Marcado **[verificar]** cada vez que se usa |
| ITS y guías del CCN citadas por <https://angelortegacastro.com/ens-instrucciones-tecnicas-seguridad-ccn-stic/> | Página leída | Lista de ITS (conformidad, auditoría, incidentes, informe de estado) y de guías 801 a 809 |

### 1.2 Limitaciones (importantes)

- `ccn-cert.cni.es` devuelve 403 / desafío JavaScript a cualquier descarga automatizada. **No se han podido leer las
  ediciones vigentes de CCN-STIC 804, 807, 808 ni 221**, ni los perfiles de cumplimiento. Lo dicho sobre criptografía
  autorizada es de segunda mano y debe verificarse antes de afirmarlo en `docs/ENS.md`. No he encontrado una edición
  de la 804 posterior al RD 311/2022; la de 2017 se usa solo para criterios de implantación.
- Los criterios de "número" (contraseñas, minutos de inactividad, días de retención) **no los fija el RD**: los fija la
  política de la organización (op.acc.6.4, mp.eq.2.1 "tiempo prudencial", op.exp.8.r3.1). Los valores propuestos aquí
  son defaults razonables, no exigencias del ENS. Cada uno está marcado como **decisión del operador**.
- Todo el análisis de código es estático (lectura). El único comportamiento inferido sin ejecutarlo es el de uvicorn
  ante `log_level="warning"` y su cadena de cifrados por defecto; ambos se marcan **[confirmar]**.

### 1.3 Asunciones

1. La organización es proveedora privada de servicios al sector público (art. 2.3) o sector público, con sistema de
   **categoría MEDIA**. La disposición transitoria única dio 24 meses desde el 5-may-2022: **desde el 5-may-2024 toda
   auditoría es contra el RD 311/2022**, no contra el RD 3/2010.
2. Para el plano de gestión de todas las VPS se asume **nivel MEDIO en C, I, T, A y D** (caso conservador: quien
   administra el plano de gestión tiene capacidad de root sobre la flota). Si el responsable de la información valorase
   alguna dimensión en BAJO, las medidas por dimensión (op.acc.1, op.exp.8, mp.eq.2, mp.si.2...) bajarían de nivel.
3. Noust es un **componente** del sistema de la empresa, no el sistema entero. La central vive en infraestructura de la
   empresa (no en un NAS doméstico: mp.if.1 a mp.if.7 se aplican al equipo que guarda todas las claves).
4. El operador designa RINF, RSERV, RSEG y RSIS (art. 13); el RSEG no depende jerárquicamente del RSIS (art. 13.3).

### 1.4 Cómo leer la matriz (sección 3)

Estados en la columna "Hueco": **Cumple** (con la evidencia en el código), **Parcial**, **Hueco** (falta en el
producto), **Operador** (fuera del alcance del producto), **N.A.** (no aplica a categoría MEDIA o a este tipo de
componente). La corrección apunta a un identificador `Gnn` de la sección 4. Prioridad: **P0** bloquea MEDIA, **P1**
refuerzo exigido o que el auditor pedirá, **P2** mejora, **P3** 3.2 o posterior.

Aplicabilidad tomada de la tabla del apartado 2.4 del Anexo II y de los bloques "Aplicación de la medida". A
categoría MEDIA: los refuerzos que aplican van en la columna "requisito"; los que solo aplican a ALTA se citan solo si
Noust ya los cubre.

---

## 2. Qué es hoy Noust 3.0.0 (hechos verificados en el código)

### 2.1 Inventario de controles reales

| Área | Hecho verificado | Dónde |
|---|---|---|
| Credencial humana | **Un único token maestro** por consola (256 bits, hash SHA-256 salado con la clave de firma). Sin usuarios, sin contraseñas | `web/auth.py:1947` (`generate_master_token`), `:2007` (`_hash_token`) |
| Tokens API | `read`/`deploy`/`admin` (+`fleet`); nombre único; hash salado; caducidad **opcional**; sin propietario; sin restricción de origen | `web/auth.py:180`, `:984` (tabla), `:2489` (`_issue_api_token`) |
| Sesiones | Cookie `wasm_session` HttpOnly, SameSite=Strict, Secure con TLS. Inactividad 12 h, absoluta 24 h, id rotado al pasar el 50 % de la vida, ligada a IP, CSRF ligado a sesión. **`last_seen` = `issued_at` de la última rotación**, no la última petición | `web/auth.py:288`, `:302`, `:2751-2868`, `web/api/auth.py:272` |
| Segundo factor | TOTP RFC 6238 (HMAC-SHA1, 6 dígitos, 30 s, ±1 paso), **uno solo para toda la consola**, secreto en claro en `web-totp` (0600), 8 códigos de respaldo (32 bits) con hash salado, un código no se reutiliza por finalidad | `core/totp.py`, `web/auth.py:2137-2426` |
| Bloqueo | 5 fallos por **IP** → 15 min, se levanta solo. Común a login, elevación, WS, bearer, cookie | `web/auth.py:76-77`, `:852-960`, `:3452` (`record_auth_failure`) |
| Limitación de tasa | 120 pet/min sin credencial por IP; 1200 pet/min con credencial válida por credencial; cuerpo 1 MiB (5 MiB hooks); 8 WS por credencial | `web/auth.py:82-157`, `web/server.py:1213-1276` |
| Modo sudo | 10 min; TOTP o token maestro; sesiones, no tokens. **Exentos: `master` y `api_token`** | `web/auth.py:316`, `web/api/deps.py:587-674` |
| Autorización | `required_scope(method, path)`: lectura=`read`; update/rollback/activate/rebuild=`deploy`; cualquier otra mutación=`admin`. Sesión = siempre `admin` | `web/auth.py:3892-3922`, `:2800` |
| Auditoría | JSON Lines en `/etc/noust/web-audit.log` (0600), campos `ts, action, result, actor, ip, resource, detail`. **Solo mutaciones `/api` + eventos de autenticación**; sin lecturas sensibles; sin cadena ni firma. 5 MiB × 4 ficheros | `web/auth.py:1626-1844`, `web/server.py:1543-1565` |
| Auditoría CLI | **Ninguna**, salvo `noust fleet authorize/deauthorize` y `noust node add/remove`, que escriben como `cli:<getpass.getuser()>` | `cli/commands/env.py:283` (lo declara), `fleet/audit.py` |
| TLS de la consola | Obligatorio fuera de loopback; `--tls-cert/--tls-key` o `--self-signed`. uvicorn recibe solo certificado y clave: **versión mínima y cifrados no fijados** [confirmar: uvicorn usa `ssl_ciphers="TLSv1"` por defecto] | `web/server.py:1815-1860`, `:1963-1975` |
| Autofirmado | `openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -subj /CN=<nombre>`; **sin SAN** | `managers/cert_manager.py:1075-1153`, `:96` |
| TLS de sitios | Plantillas nginx: TLS 1.2/1.3, ECDHE + AES-GCM. **Sin HSTS, sin `server_tokens off`**; `X-XSS-Protection` obsoleto | `templates/nginx/*.conf.j2` |
| Cabeceras de la consola | CSP estricta con Trusted Types, `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, COOP/CORP, HSTS solo con TLS; `/health` estático; OpenAPI cerrado | `web/server.py:146-162`, `:1567-1589`, `:985` |
| Secretos en reposo | Ficheros 0600 en directorios 0700 (config, store, tokens, secretos). **Sellado opcional** de `secrets/` (scrypt N=2^15 r=8 p=1 + AES-256-CBC + HMAC-SHA256, encrypt-then-MAC, passphrase ≥ 12) | `core/secrets.py`, `core/sealing.py:80-107` |
| Copias | 0600/0700; SHA-256 en sidecar junto al archivo (sin MAC); `verify(deep)` extrae con el mismo extractor que restaura; destinos rclone con `crypt` **opcional** | `managers/backup_manager.py:702`, `:2930-3150`, `managers/backup_destinations.py:19` |
| Flota | Clave Ed25519 por nodo (solo Ed25519), instalada restringida (`permitopen` a la consola, `permitlisten` al puerto 1, `command=/usr/bin/false`), `StrictHostKeyChecking=yes` con host key fijada, token de flota solo por loopback sin cabeceras de proxy, actor/scope/elevación por cabeceras, 2FA obligatorio antes del primer nodo. **Sin rotación de claves, sin algoritmos SSH fijados, login como root** | `fleet/keys.py`, `fleet/models.py:56`, `fleet/tunnels.py:140-192`, `web/auth.py:3627-3730`, `fleet/policy.py` |
| Actualizaciones | Comprobación en cada ejecución CLI (caché 5 min) contra el repositorio de paquetes y `api.github.com`; `updates.check=false` la apaga. No se autoactualiza | `core/update_checker.py` |
| Cadena de suministro | Publicación PyPI por *trusted publishing*, repos apt/rpm firmados por OBS, imagen GHCR multi-arch. **Sin SBOM, sin firma de artefactos propia, sin `pip-audit`/`npm audit`/Dependabot, imagen por etiqueta y no por digest** | `.github/workflows/`, `packaging/container/compose.yaml` |
| Monitorización | `noust monitor` (nombres de procesos, umbrales, unidades, certificados, discos: solo informa), `noust health`, métricas | `docs/MONITOR.md`, `managers/health.py` |

### 2.2 Hallazgos

| Id | Sev. | Hallazgo | Evidencia | Corrección |
|---|---|---|---|---|
| H1 | Alta | El token maestro por `Authorization: Bearer` es `admin`, no pasa por TOTP ni por sudo. Con 2FA activado, solo el navegador lo cumple | `web/auth.py:4244-4250` (`check_credential`), `web/api/deps.py:587`, `docs/security.md` "Sudo mode" | G02 |
| H2 | Media (confirmar) | Los `audit_log.info(...)` del logger `noust.audit` no llegan a ningún handler: no hay `basicConfig`/`addHandler` en `src/noust`, uvicorn solo configura sus loggers y con `log_level="warning"` tampoco emite el access log (nivel INFO) que su comentario dice que deja rastro | `web/api/databases.py:70`, `backup_schedules.py:58`, `cron.py`, `integrations.py`, `backup_destinations.py:56`, `deployers/app_export.py:81`, `web/server.py:1845-1847`; `docs/security.md` dice `wasm.audit` (nombre viejo) | G03 |
| H3 | Alta | Retención efectiva ≈ 20 MiB. La rotación `_rotate` borra el más antiguo; eventos anónimos (IP no permitida, credencial errónea, límite de tasa) inundan; no configurable por `config.yaml` (`web.*` solo lleva `token_expiration_hours`, lockout y tasas) | `web/auth.py:306-307`, `:1738-1756`; `core/config.py:212-214`; `cli/commands/web.py:366-368` | G03 |
| H4 | Alta | El actor no es una persona: `master`, `token:<nombre>` o 12 hex de sesión. Todos los operadores comparten el maestro. No cumple art. 24.3 ni op.exp.8.1 | `web/auth.py:3551` (`actor_label`) | G01 |
| H5 | Media | Autofirmado RSA-2048 sin SAN a 3650 días; consola sin política TLS propia | `managers/cert_manager.py:1119-1132`; `web/server.py:1841` | G07 |
| H6 | Media | Inactividad 12 h; la renovación se dispara al 50 % de la vida, luego la inactividad efectiva es de 6 a 12 h | `web/auth.py:288`, `:2861` | G06 |
| H7 | Media | `GET /api/auth/session` anónimo devuelve `hostname`, `version`, `totp_enabled`. Login: `invalid_token` / `totp_required` / `invalid_totp` + "N attempts remaining" | `web/api/auth.py:433-438`, `:329-369` | G10 |
| H8 | Media | Bloqueo por IP con desbloqueo temporal; no hay cuentas que bloquear | `web/auth.py:852-960` | G01 |
| H9 | Alta (arquitectura) | `admin` ≡ root: `POST /api/services` (unidad cruda), `PUT /api/services/{n}/config`, `POST/PUT /api/cron`, `POST/PUT /api/backup-schedules`, `PUT /api/sites/{d}/config`, SQL en modo escritura, despliegue de ruta local, y todo despliegue compila código del repositorio como root (backlog 44). La separación de funciones por API es política, no frontera | `docs/security.md` "Sudo mode" y "Untrusted repositories"; backlog 44 | G08, G14 |
| H10 | Media | Un `admin` puede cambiar `web.*` (lockout, whitelist, expiración, límites), emitir tokens y desactivar 2FA: no se separa operar de gobernar la seguridad | `PUT /api/config/web` [ELEV], `POST /api/auth/tokens` [ELEV], `/api/auth/2fa/disable` [ELEV] | G01 |
| H11 | Media | Tokens sin propietario y sin caducidad obligatoria; sin restricción de red; los `admin` no se someten a sudo | `web/auth.py:2427-2543` | G02 |
| H12 | Media | Las previews heredan el `.env` de producción (secretos incluidos) y hablan con las bases de datos del padre; se compilan como root | `managers/previews.py` (docstring) | G18 |
| H13 | Media | Copias: cifrado no obligatorio; sidecar con SHA-256 sin MAC; clave `crypt` de rclone guardada en la misma máquina | `managers/backup_manager.py:702`, `managers/backup_destinations.py:19` | G12 |
| H14 | Baja | `noust health` no mira hora, parches, firewall, sshd ni fail2ban/auditd | `managers/health.py` | G09, G13 |
| H15 | Baja | Flota: solo Ed25519, sin rotación, sin algoritmos SSH fijados, `authorized_keys` de root | `fleet/models.py:56`, `fleet/tunnels.py:140-192`; backlog 45 | G11 |
| H16 | Baja | Cadena de suministro sin SBOM ni firma propia ni escaneo de dependencias | `.github/` | G16 |
| H17 | Baja | Documentación desalineada: `wasm.audit`; `docs/CENTRAL.md` presenta el autofirmado como camino por defecto de una central | `docs/security.md`, `docs/CENTRAL.md` | G07 |

### 2.3 Lo que ya juega a favor (para no rehacerlo)

- **Puntos únicos de control** que se pueden reutilizar tal cual: `required_scope`/`require_auth` (autorización),
  `ensure_elevated`/`x-noust-requires-elevation` (sudo, también en la flota), `SecurityMiddleware` (auditoría de
  mutaciones), `record_auth_failure` (todo fallo de credencial), `ReleaseManager` + `HealthGate` (cambio con prueba de
  aceptación y vuelta atrás), `CommandRunner`/`is_read_only` (todo proceso), `core/fs.py` (todo fichero), `core/redact.py`.
- **Cripto ya en el árbol** sin dependencias nuevas: `hashlib.scrypt`, `openssl` por el runner, `core/sealing.py`.
- **Flota**: el nodo decide y la central avala (`X-Noust-Elevated`), la clave de la central no da shell,
  host key fijada: es la base de un modelo de interconexión autorizada (op.ext.4).
- **Backups**: verificación profunda con el extractor real, destinos remotos con cifrado opcional, retención remota.

---

## 3. Matriz de cumplimiento (entregable a)

Formato: **medida** | requisito resumido (con los refuerzos que aplican a MEDIA) | cómo lo cumple Noust hoy | responsabilidad del operador | hueco | corrección propuesta | prioridad.
`Op.` = operador. Las referencias `Gnn` son los huecos de la sección 4.

### 3.1 Marco organizativo (solo donde la herramienta lo soporta)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| org.1 Política de seguridad (aplica) | Documento con misión, marco legal, **roles y su designación (org.1.3)**, comités, estructura documental | No la produce. Aporta el modelo de amenazas en `docs/security.md` | Aprobar la política (art. 12) y designar RINF, RSERV, RSEG, RSIS; RSEG independiente del RSIS (art. 13.3) | **Parcial**: los roles técnicos de Noust (3 scopes) no se corresponden con los roles ENS | Roles `security`, `admin`, `auditor` mapeables a RSEG, RSIS y supervisión (G01); tabla de correspondencia en `docs/ENS.md` | P0 |
| org.2 Normativa de seguridad (aplica) | Uso correcto e indebido de equipos y servicios; responsabilidad y medidas disciplinarias | No la produce | Redactar y aprobar las normas de uso (art. 15.2) | **Hueco**: no hay aviso de derechos y obligaciones ni aceptación registrada (op.acc.6.1, .2, .9; mp.per.2.r1) | Aviso versionado y configurable con aceptación auditada (G06) | P0 |
| org.3 Procedimientos (aplica) | Cómo hacer las tareas, quién, cómo reportar anomalías, tratamiento de la información | `docs/*` documenta operación técnica | Procedimientos propios (alta/baja de cuentas, revisión de logs, restauración, rotación) | **Hueco**: falta la guía de bastionado y procedimientos tipo | `docs/ENS.md` con procedimientos plantilla (sección 5) | P1 |
| org.4 Proceso de autorización (aplica) | Autorización formal de utilización de instalaciones, entrada de equipos y **aplicaciones en producción (org.4.3)**, **enlaces con otros sistemas (org.4.4)**, servicios de terceros | El enlace central-nodo se autoriza **en el nodo**, como root (`noust fleet authorize`); crear una aplicación exige `admin` | Aprobar formalmente cada nodo, cada aplicación en producción y cada proveedor | **Parcial**: la entrada de una aplicación en producción no tiene aprobación de un segundo actor | Aprobaciones de cuatro ojos para `apps.create` en nodos marcados `production` (G08) | P1 |

### 3.2 Marco operacional: planificación (op.pl)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| op.pl.1 (+R1) Análisis de riesgos | Análisis **semiformal**: valorar activos, amenazas, salvaguardas y riesgo residual | Modelo de amenazas en `docs/security.md`; amenazas específicas: comprometer la central compromete la flota; despliegue = ejecución de código | MAGERIT/PILAR o equivalente, revisión anual (Anexo III 1.1.d) | **Operador**. Noust puede aportar escenarios de riesgo prefabricados | Sección "riesgos de Noust" en `docs/ENS.md` (escenarios: robo del token/clave de la central, repositorio hostil, insider admin, pérdida de la passphrase) | P2 |
| op.pl.2 (+R1) Arquitectura de seguridad | Documentar instalaciones, sistema, líneas de defensa, **sistema de identificación y autenticación (op.pl.2.4)**; R1 sistema de gestión | Diseño de flota: la central solo marca hacia fuera; consola de nodo solo en loopback; allow-list | Diagrama y sistema de gestión (SGSI) propios | **Parcial**: op.pl.2.4 depende de G01; falta arquitectura de referencia escrita | Arquitectura de referencia en `docs/ENS.md` (central en zona de gestión, VPN, SIEM, NTP, copias fuera de sitio) | P1 |
| op.pl.3 Adquisición de componentes | Proceso formal de adquisición conforme al análisis de riesgos | AGPL-3.0, código abierto, CI con mypy/ruff-bandit, contacto de vulnerabilidades | Registrar la incorporación de Noust conforme a su proceso | **Parcial**: sin SBOM ni `SECURITY.md` | SBOM CycloneDX por release, `SECURITY.md` (G16) | P2 |
| op.pl.4 (+R1) Capacidad (D MEDIO) | Estudio previo; **R1** previsión mantenida y herramientas de monitorización | `web/metrics_collector.py`, `noust health` (disco, memoria), umbrales de `noust monitor` | Estudio de capacidad y previsión | **Parcial**: sin tendencia/previsión | Informe de tendencia en `ens report` (G09); resto P3 | P3 |
| op.pl.5 Componentes certificados (aplica) | **Usar el CPSTIC** para los productos de la arquitectura de seguridad; si no hay, productos certificados (art. 19) | Noust **no figura en el CPSTIC** ni está certificado | Justificar en el análisis de riesgos que Noust es un componente de administración y no un producto de seguridad de la arquitectura; si el auditor lo cataloga como tal, medida compensatoria documentada (art. 28.3) en la Declaración de Aplicabilidad | **Hueco no cerrable en código** | Compensar con SBOM, firmas y pruebas publicadas (G16); redacción de la justificación en `docs/ENS.md` (backlog 30 ya lo apunta) | P2 |

### 3.3 Marco operacional: control de acceso (op.acc)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| op.acc.1 (+R1) Identificación (T, A MEDIO) | Identificador **singular** por entidad, usuario o proceso; **un identificador por cada perfil (.1.2)**; cuentas con identificador único, inhabilitadas al cesar, retenidas por el periodo de retención (.1.4); R1 singularizar a la persona y **lista actualizada de usuarios autorizados** | Solo credenciales: token maestro compartido, sesión anónima (12 hex), token con nombre sin propietario | Mantener la lista de personas autorizadas (r1.3) y decidir altas y bajas | **Hueco**: no hay identidad de persona; art. 24.3 y op.exp.8.1 imposibles | **G01** (cuentas, una cuenta = un rol, retención) y **G02** (tokens con propietario) | P0 |
| op.acc.2 Requisitos de acceso (aplica) | Recursos protegidos frente a quien no tenga derechos (.1); derechos por decisión del responsable (.2); controlar acceso a SO y ficheros de configuración (.3) | Todo `/api` y `/ws` pasa por `require_auth`; scope por método; ficheros 0600/0700; unidades de Noust protegidas en `ServiceManager`; rutas locales solo con maestro o sudo | Fijar quién decide los derechos de cada recurso | **Parcial**: derechos por rol grueso, sin alcance por aplicación o nodo | Permisos por rol + `nodes` y `resources` (G01, G02) | P1 |
| op.acc.3 Segregación de funciones (aplica) | **Concurrencia de dos o más personas para tareas críticas**; desarrollo y operación separados; quien autoriza distinto de quien controla (.3.1, .3.2) | El sudo es reautenticación de la **misma** persona; un `admin` hace todo, incluida la configuración de seguridad y los tokens (H10) | Repartir cuentas entre personas | **Hueco** | Roles con incompatibilidades estructurales (G01) y aprobación de cuatro ojos (G08) | P0 |
| op.acc.4 Gestión de derechos (aplica) | Todo prohibido salvo autorización (.4.1); **mínimo privilegio (.4.2)**; necesidad de conocer (.4.3); solo quien tiene competencia concede o anula, **revisión periódica (.4.4)**; **política de acceso remoto con autorización expresa (.4.5)** | Denegación por defecto por método (`required_scope`); consola en loopback por defecto, allow-list, TLS obligatorio fuera de loopback | Política de acceso remoto escrita; revisión de permisos periódica | **Parcial**: 3 niveles gruesos; sin revisión de accesos; central con allow-list por defecto = todos los rangos privados | Roles finos, `users review` con atestación, allow-list explícita en el perfil ENS (G01, G09) | P0 |
| op.acc.5 Autenticación, usuarios **externos** (+R2 o R3 o R4, +R5) | Como op.acc.6 para quien no es de la organización | La consola es para personal propio o contratado; **N.A.** salvo que se dé acceso a clientes | Si se da acceso a clientes, aplican los mismos mecanismos (G01) más R2 y R5 | **N.A.** | Mismo mecanismo de G01 | — |
| op.acc.6 (+[R1 o R2 o R3 o R4], +R5, +R8, +R9) Autenticación, usuarios de la **organización** (C, I, T, A MEDIO) | .1/.2 conocer y aceptar política y obligaciones antes de activar; .3 credencial bajo control exclusivo del usuario; .4 cambio periódico; .5 inhabilitar ante compromiso; .6 inhabilitar al cesar; **.7 información mínima previa y no informar del motivo del rechazo**; **.8 intentos limitados, bloqueo con intervención específica para reactivar**; **.9 informar de derechos y obligaciones tras el acceso**. R2 contraseña + OTP; **R5 registrar accesos con éxito y fallidos e informar del último acceso**; **R8 doble factor desde o a través de zonas no controladas**; **R9 acceso remoto: ITS de interconexión, autorizado, cifrado, deshabilitado si no se usa, con registros** | R5 (registro de éxito y fallo): **Cumple** (`auth.login`, `auth.credential`). R2: TOTP existe pero es global y omisible por token maestro Bearer (H1). R9: cifrado obligatorio, allow-list, loopback por defecto, registro: **Cumple parcial**. .7/.8: H7, H8 | Política de contraseñas y de acceso remoto (org.2); si el acceso es por Internet, doble factor obligatorio (R8) | **Hueco**: .1, .2, .3, .4, .6, .9 (sin cuentas); .7 y .8 incumplidos; R5 sin "último acceso"; R8 incumplido por el maestro Bearer y por tokens | **G01** (cuentas, invitación, contraseñas, MFA por usuario, bloqueo por cuenta), **G02** (break-glass), **G06** (aviso posterior y último acceso), **G10** (login sin fugas) | P0 |

### 3.4 Marco operacional: explotación (op.exp)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| op.exp.1 Inventario (aplica) | Inventario actualizado con naturaleza y **responsable** (.1.1); R4 lista de componentes software (opcional) | `apps`, `sites`, `services`, `nodes` en el store; página Fleet con versión y "visto por última vez" | Mantener el inventario global y el responsable de cada activo | **Parcial**: sin responsable ni criticidad; sin exportación; sin SBOM | Campos `owner`, `criticality`, `classification` por aplicación y nodo, exportación JSON/CSV, SBOM (G15) | P2 |
| op.exp.2 Configuración de seguridad (aplica) | Retirar cuentas y contraseñas estándar (.2.1); mínima funcionalidad (.2.2); seguridad por defecto (.2.3): reducir seguridad exige actos conscientes | Token aleatorio de 256 bits; consola en loopback; TLS obligatorio fuera de loopback; `--insecure-http` explícito; rol `hub` sin despliegue local; unidad con `NoNewPrivileges`; imagen no root, solo lectura, sin capabilities | Bastionado del SO de cada nodo (guías CCN-STIC por SO), cuentas estándar del SO | **Parcial**: compilaciones como root; unidad de consola sin `ProtectSystem` (documentado); `authorized_keys` de root en nodos | G14 (compilaciones sin privilegios), G11 (cuenta de túnel), perfil ENS que impide `--insecure-http` (G09) | P1 |
| op.exp.3 (+R1) Gestión de la configuración (aplica) | Mantener funcionalidad mínima y privilegio mínimo; **la configuración de seguridad solo la edita personal autorizado (.3.6)**; R1: configuraciones autorizadas y mantenidas, **verificación periódica (r1.2)**, lista de servicios autorizados (r1.3) | Config en capas 0600; escritura por API con sudo; unidades propias marcadas y protegidas | Definir la línea base autorizada y quién la cambia | **Hueco**: un `admin` cambia `web.*`; sin comprobación de deriva | Permiso `security.config` solo para `security` (G01); `noust ens check` compara la configuración efectiva con la línea base del perfil (G09) | P1 |
| op.exp.4 (+R1) Mantenimiento y actualizaciones (aplica) | Seguir anuncios del fabricante (.4.1); **procedimiento para analizar y priorizar parches (.4.2)**; solo personal autorizado (.4.3); R1 pruebas previas en entorno controlado | Comprobador de versión (`core/update_checker.py`); despliegue con `HealthGate` y vuelta atrás (que además cubre R2, exigido solo a ALTA); previews | Procedimiento de parcheo del SO y de Noust; entorno de pruebas (una central de preproducción) | **Parcial**: Noust no informa de parches pendientes ni de reinicio necesario en los nodos que gestiona; sin canal de avisos de seguridad | G13 (parches pendientes, reboot-required), G16 (`SECURITY.md`, avisos), procedimiento en `docs/ENS.md` | P1 |
| op.exp.5 Gestión de cambios (aplica) | Cambios registrados **con número de referencia (.5.1)**, información suficiente (.5.2), preproducción (.5.3), **riesgo ALTO aprobado por el RSEG (.5.4)**, pruebas de aceptación (.5.5) | Cada despliegue queda con id, commit, disparador (`deployments.triggered_by`), actor del job y release; `HealthGate` como prueba de aceptación; vuelta atrás en segundos | Definir qué es cambio de riesgo ALTO y quién aprueba | **Hueco**: sin referencia de cambio ni motivo; sin aprobación previa | Campo `reason`/referencia en mutaciones y CLI (G18); aprobaciones (G08) | P1 |
| op.exp.6 (+R1, +R2) Código dañino (aplica) | Prevención y reacción; **software antimalware en servidores (.6.2)**; R1 escaneo periódico; R2 analizar funciones críticas al arrancar | `noust monitor` señala nombres conocidos de mineros y malware (solo informa; no es antimalware); el resto es del SO | Antimalware/EDR en servidores, integridad de arranque (R2) | **Parcial**: el código de terceros se compila como root (H9); Noust no comprueba que haya agente | G14; check informativo de agente de detección en el nodo (G13) | P1 |
| op.exp.7 (+R1, +R2) Gestión de incidentes (aplica) | Proceso integral (.7.1); RGPD (.7.2); R1 notificación al CCN-CERT; **R2 medidas urgentes: detener servicios, aislar el sistema, recoger evidencias, proteger registros** | `diagnose`, notificaciones de `monitor`, auditoría; el nodo puede revocar a la central (`noust fleet deauthorize`) | Procedimiento de incidentes; notificación (art. 33: CCN-CERT, INCIBE-CERT para privados) | **Hueco**: no hay "congelar evidencias" ni bloqueo de emergencia | `noust incident freeze` y bloqueo de emergencia (G17) | P2 |
| op.exp.8 (+R1, +R2, +R3, +R4) Registro de la actividad (T MEDIO) | **.8.1 registro con identificador de usuario, fecha y hora, sobre qué información, tipo de evento y resultado**; .8.2 registros activados en servidores; **R1** revisión periódica informal; **R2** referencia de tiempo, sincronización con autenticación e integridad; **R3** eventos auditados y tiempo de retención documentados; **R4** solo personal autorizado accede o borra registros y copias | Existe registro JSON de mutaciones y autenticación, con IP, resultado y recurso. **No** hay identidad de persona (H4), ni lecturas sensibles, ni CLI (`cli/commands/env.py:283` lo declara), ni catálogo, ni retención (H3), ni integridad, ni envío externo, ni control de reloj | Activar auditd/journald en los servidores (.8.2); NTP/NTS autenticado (R2); definir eventos y retención; revisar (R1) | **Hueco** en .8.1, R1 (falta evidencia), R2, R3, R4 | **G03** (auditoría v2: identidad, catálogo, cadena, retención, lecturas, revisión), **G04** (envío), **G05** (CLI y libro de acciones), G09 (reloj) | P0 |
| op.exp.9 Registro de gestión de incidentes (aplica) | Registrar reportes, actuaciones de emergencia, modificaciones y **evidencias con valor jurídico (.9.2)** | La auditoría es evidencia parcial | Herramienta de incidentes (p. ej. LUCIA del CCN) | **Parcial** | Paquete de evidencias con manifiesto SHA-256 (G17) | P2 |
| op.exp.10 (+R1) Protección de claves criptográficas (aplica) | Ciclo de vida completo (.10.1); generación aislada de la explotación (.10.2); archivo aislado (.10.3); **R1 algoritmos y parámetros autorizados por el CCN** | Claves Ed25519 por nodo (0600, sellado opcional), clave de firma web, TLS, sellado scrypt + AES-256-CBC + HMAC-SHA256 con passphrase que no se escribe; rotación del token y de la clave de firma (`--regenerate`, que invalida todo) | Generar y archivar claves fuera de explotación (.10.2, .10.3) | **Parcial**: RSA-2048 autofirmado, sin rotación de claves de nodo, TOTP con HMAC-SHA1, `crypt` de rclone: estado CCN **[verificar]** | G07 (TLS/SSH/autofirmado), G11 (rotación de claves de nodo), G12 (cifrado de copias con primitivas ya usadas) | P0 |

### 3.5 Recursos externos, servicios en la nube y continuidad (op.ext, op.nub, op.cont)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| op.ext.1 Contratación y SLA (aplica) | ANS contractual antes de usar recursos externos | N.A. al producto | ANS con el proveedor de VPS y de alojamiento de la central | **Operador** | — | — |
| op.ext.2 Gestión diaria (aplica) | Sistema rutinario para medir el cumplimiento del ANS; coordinación de mantenimiento | Página Fleet (alcanzable, versión, visto por última vez) aporta señal de disponibilidad de nodos | Medir y reclamar | **Operador** (Noust aporta la señal) | Exportar disponibilidad por nodo en `ens report` (G09) | P3 |
| op.ext.3 Cadena de suministro | **N.A. a MEDIA** (solo ALTA); R3 lista de componentes opcional | — | — | N.A. | SBOM ya cubre R3 (G16) | — |
| op.ext.4 Interconexión (aplica) | **Autorización previa de todo intercambio (.4.1)**; documentar interfaz, requisitos y naturaleza de la información (.4.2) | El enlace central-nodo se autoriza en el nodo; clave restringida a reenviar el puerto de la consola; token de flota solo por loopback; host key fijada; cambio de host key corta el túnel | Aprobar y documentar cada nodo (org.4.4) | **Parcial**: sin registro exportable de interconexiones | Registro de interconexiones (nodo, clave, huella, token, restricciones, fecha) exportable (G11) | P1 |
| op.nub.1 (+R1) Servicios en la nube | Servicios de terceros conformes con el ENS; **R1 certificados por el Organismo de Certificación** | N.A. al producto | Las VPS y cualquier servicio cloud deben ser de un proveedor con certificado ENS de categoría igual o superior (CPSTIC servicios cloud) | **Operador** | Lista de comprobación en `docs/ENS.md` | — |
| op.cont.1 Análisis de impacto (D MEDIO) | Determinar requisitos de disponibilidad y elementos críticos | Si la central cae, los nodos siguen sirviendo (solo se pierde la gestión); restauración desde `/data` | Análisis de impacto, RTO/RPO de la central | **Operador**; Noust aporta la restauración | `noust central backup` consistente y cifrado + procedimiento (G12) | P1 |
| op.cont.2 a op.cont.4 | **N.A. a MEDIA** (solo dimensión D en nivel ALTO) | — | — | N.A. | — | — |

### 3.6 Monitorización (op.mon)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| op.mon.1 (+R1) Detección de intrusión | Herramientas de detección o prevención (.1.1); **R1 basadas en reglas** | `noust monitor` no es un IDS; la consola bloquea fuerza bruta por IP | IDS/IPS del servidor y de la red (Wazuh, OSSEC, CrowdSec, fail2ban, auditd, Suricata) | **Parcial** | Eventos con id estable para reglas del operador (G03, G04); check de presencia de agente y de fail2ban/auditd en el nodo (G13) | P1 |
| op.mon.2 (+R1, +R2) Sistema de métricas | Recopilar datos para conocer el grado de implantación y el informe anual; **R1** eficacia de la gestión de incidentes; **R2** eficiencia | `noust health`, métricas de máquina y de aplicación | Informe anual del estado de la seguridad (art. 32) | **Hueco**: sin indicadores de seguridad | `noust ens report`: MFA, tiempo de revocación, revisiones, parches, copias verificadas, bloqueos, uso de break-glass (G09) | P1 |
| op.mon.3 (+R1, +R2) Vigilancia | **.3.1 sistema automático de recolección de eventos**; **R1 correlación**; **R2 determinar la superficie de exposición y deficiencias de configuración** | El registro es solo local | SIEM del operador (correlación, alertas) | **Hueco**: no hay recolección automática hacia fuera; sin comprobación de exposición | **G04** (journald + syslog RFC 5424), **G09** (`ens check`: puertos, sshd, firewall, TLS) | P0 |

### 3.7 Medidas de protección (mp)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco | Corrección | P |
|---|---|---|---|---|---|---|
| mp.if.1 a mp.if.7 Instalaciones (aplican; mp.if.4 con R1, mp.if.6 D MEDIO) | Áreas separadas y con control de acceso, identificación de personas, acondicionamiento, energía (+SAI en R1), incendios, inundaciones, registro de entrada y salida de equipos | N.A. al producto | **Operador**: la central y sus copias en CPD o sala controlada, no en un NAS doméstico | **Operador** | Guía "dónde vive la central" en `docs/ENS.md` y en `docs/CENTRAL.md` (backlog 30) | P2 |
| mp.per.1 a mp.per.4 Personal (mp.per.2 con R1) | Caracterización de puestos, deberes, concienciación, formación; **R1 confirmación expresa de que conocen las instrucciones** | N.A. al producto | Operador | **Operador**; Noust aporta la aceptación registrada del aviso de uso (mp.per.2.r1.1) | G06 | P0 (por G06) |
| mp.eq.1 Puesto despejado (+R1) | Puesto despejado y material guardado | N.A. | Operador | Operador | — | — |
| mp.eq.2 Bloqueo de puesto de trabajo (A MEDIO) | **Bloqueo por inactividad tras un tiempo prudencial, con nueva autenticación** | Sesión de 12 h sin actividad, 24 h absolutas (H6) | Fijar el tiempo (decisión del operador) | **Hueco** | G06: inactividad servidor-side configurable (default 15 min en el perfil), absoluta 8 h, reautenticación | P0 |
| mp.eq.3 Dispositivos portátiles | Inventario, procedimiento de pérdida, sin claves de acceso remoto innecesarias (.3.4) | Noust no guarda claves de administradores | Portátiles de los administradores | **Operador** | — | — |
| mp.eq.4 Otros dispositivos (C MEDIO, +R1) | Configuración segura de dispositivos conectados | N.A. | Operador | Operador | — | — |
| mp.com.1 Perímetro seguro (aplica) | Protección perimetral; **todo flujo autorizado previamente** | Consola en loopback por defecto; `--allow-ip`; TLS obligatorio; la clave de la central solo reenvía un puerto | Cortafuegos, VLAN de gestión, DMZ | **Parcial**: la central admite por defecto todos los rangos privados RFC 1918 | Perfil ENS exige allow-list explícita (G09) | P1 |
| mp.com.2 (+R1) Confidencialidad (C MEDIO) | VPN cifradas fuera del dominio de seguridad (.2.1); **R1 algoritmos y parámetros autorizados por el CCN** | Túneles SSH cifrados entre central y nodo; TLS obligatorio fuera de loopback | VPN corporativa si central y nodos cruzan redes fuera del dominio | **Parcial**: algoritmos SSH y TLS de la consola no fijados; RSA-2048 autofirmado | **G07**; SSH con algoritmos fijados (G11) | P0 |
| mp.com.3 (+R1, +R2) Integridad y autenticidad (I, A MEDIO) | **Autenticar el otro extremo antes de intercambiar (.3.1)**; prevenir ataques activos (.3.2); R1 VPN; **R2 algoritmos autorizados** | Host key de cada nodo fijada, `StrictHostKeyChecking=yes`, token de flota, TLS, CSRF, sesión ligada a IP, revalidación de WebSockets | VPN (R1) | **Parcial**: R2 como en mp.com.2; el cambio de host key debe quedar como evento de auditoría | G07, G11 | P0 |
| mp.com.4 (+R1 o R2 o R3) Separación de flujos (aplica) | Segmentar el tráfico por necesidad | Diseño de flota: solo tráfico saliente de la central; consola del nodo solo en loopback | VLAN/VPN de gestión separada | **Operador** | Documentar segmentación recomendada | P2 |
| mp.si.1 Marcado de soportes (C MEDIO) | Metadatos con el nivel de seguridad | N.A. (opcional: clasificación por aplicación) | Operador | Operador | G15 | P3 |
| mp.si.2 Criptografía de soportes (C, I MEDIO) | **Mecanismos que garanticen confidencialidad e integridad** de soportes que salen del área controlada; algoritmos CCN (.2.2) | Secretos sellables; copias locales 0600; remotas con `crypt` de rclone opcional | Cifrado de disco y de los soportes de copia | **Parcial**: cifrado de copias no obligatorio; integridad del sidecar sin MAC | **G12** (cifrado obligatorio en el perfil con AES-256 + HMAC-SHA256, MAC del sidecar) | P1 |
| mp.si.3 Custodia (aplica) | Control de acceso físico o lógico a soportes | 0600/0700; sellado | Custodia física | **Operador** | — | — |
| mp.si.4 Transporte (aplica) | Registro de entrada y salida; cifrado; claves según op.exp.10 | Subida con rclone por TLS/SSH | Procedimiento de transporte | **Operador** | — | — |
| mp.si.5 (+R1) Borrado y destrucción (C MEDIO) | Borrado seguro antes de reutilizar o liberar | `noust node remove --revoke` retira credenciales; la retención borra copias | Borrado seguro o destrucción de la clave de cifrado de disco al retirar una VPS | **Operador** | Procedimiento de baja en `docs/ENS.md` | P3 |
| mp.sw.1 (+R1, +R2, +R3, +R4) Desarrollo | **Desarrollo separado de producción, sin datos reales de producción en pruebas (r4)** | Para las aplicaciones del operador: **las previews heredan el `.env` de producción** (H12). Para Noust como software: CI con ruff (reglas bandit `S`), mypy bloqueante, ~7590 pruebas, E2E con CSP y axe, reglas de arquitectura | Metodología de desarrollo seguro propia | **Hueco** en previews; **Parcial** en evidencias del fabricante | G18 (previews sin secretos de producción en el perfil), G16 (evidencias y SBOM) | P1 |
| mp.sw.2 (+R1) Aceptación y puesta en servicio | Comprobar funcionamiento y seguridad antes de producción; **R1 pruebas en entorno aislado** | `HealthGate` (prueba automática y vuelta atrás), previews | Nodo de preproducción; pruebas de Noust antes de actualizar la central | **Parcial**: las previews no son un entorno aislado | G18; documentar central de preproducción | P1 |
| mp.info.1 Datos personales (aplica) | Requisitos RGPD fijados por el responsable | Noust guardará usuarios, correos e IP | Base jurídica, DPD, plazos | **Operador**; Noust minimiza | Retención configurable y datos mínimos (G19) | P2 |
| mp.info.2 Calificación (C MEDIO) | Responsable y nivel de cada información | N.A. | Operador | Operador | G15 (clasificación opcional) | P3 |
| mp.info.3, mp.info.4, mp.info.5 Firma electrónica, sellos de tiempo, limpieza de documentos | mp.info.3 con R1-R3 (I, A MEDIO); **mp.info.4 N.A. a MEDIA**; mp.info.5 aplica | Noust no produce documentos firmados | — | **N.A.** | — | — |
| mp.info.6 (+R1) Copias de seguridad (D MEDIO) | Copias con periodicidad y retención; controles de acceso (.6.2.d); **R1 pruebas de recuperación regulares** | Copias programadas, `verify` profundo, retención local y remota, 0600 | Normativa de copias; destino en otro lugar (R2 es de ALTO, pero conviene) | **Parcial**: la prueba de restauración no se programa ni deja evidencia; la copia de la central es manual (`tar`) | G12: verificación programada con evidencia en auditoría, `noust central backup` | P1 |
| mp.s.1 Correo | Protección del correo | Notificaciones SMTP con `use_tls`/`use_ssl` opcionales | Relé corporativo | **Operador** | Exigir TLS en SMTP en el perfil (G09) | P3 |
| mp.s.2 (+R1 o R2) Servicios y aplicaciones web (aplica) | Impedir acceso sin autenticación, manipulación de URL y cookies, inyección, escalada, XSS; **R1 auditorías de caja negra o R2 de caja blanca** | Consola: CSP estricta con Trusted Types, CSRF, cookies HttpOnly/SameSite=Strict, límites de cuerpo y tasa, E2E que falla ante violaciones CSP y axe | **Pruebas de penetración** de la consola y de las aplicaciones (R1), con periodicidad del procedimiento de auditoría | **Parcial**: sin evidencia DAST/pentest publicada; sitios sin HSTS ni `server_tokens off` | DAST (ZAP baseline) en CI (G16), plantillas nginx endurecidas (G20) | P2 |
| mp.s.3 Navegación web | Protección de la navegación de usuarios internos | N.A. | Operador | N.A. | — | — |
| mp.s.4 Denegación de servicio (D MEDIO) | Capacidad con holgura (.4.1); tecnologías preventivas (.4.2) | Límites de tasa (120/1200 por minuto), cuerpo, WebSockets; `advanced.conf.j2` admite `limit_req` | Protección del proveedor/WAF/CDN de las aplicaciones; la consola no se expone a Internet | **Parcial** | Documentar; no exponer la central | P3 |

---

## 4. Huecos que Noust debe cerrar en código en la 3.1 (entregable b)

### 4.1 Lista priorizada

Tamaño orientativo: **S** hasta 2 días, **M** 3 a 6, **L** más de una semana. `dep.` = depende de.

| Id | Hueco | Medidas que cierra | P | Tam. | dep. |
|---|---|---|---|---|---|
| **G01** | Cuentas de usuario con roles, separación de funciones, MFA obligatoria por usuario, política de contraseñas, bloqueo por cuenta, alta por invitación, arranque del primer usuario, revisión de accesos, `noust user` | op.acc.1, .2, .3, .4, .6 (.1-.9, R2, R5); org.1.3; op.exp.3.6; art. 24.3 | P0 | L | permisos (4.2.3) |
| **G02** | Token maestro reducido a *break-glass*; tokens API con propietario, caducidad obligatoria, permisos acotados, CIDR y sin sudo implícito | op.acc.6 R8, op.acc.1.3, op.acc.4 | P0 | M | G01 |
| **G03** | Auditoría v2: identidad, catálogo cerrado de eventos, un único punto de registro, lecturas sensibles, cadena HMAC + `noust audit verify`, retención por tiempo, anti-inundación, fallo visible, revisión con atestación; recoger `noust.audit` | op.exp.8 (.1, R1, R3, R4), op.exp.9, art. 24 | P0 | L | G01 (identidad) |
| **G04** | Envío a journald y a syslog RFC 5424 (UNIX, UDP, TCP con recuento de octetos, TLS mutuo), con cola en disco y puntos de control | op.mon.3 (.3.1, R1), op.exp.8 R4, op.exp.7 | P0 | M | G03 |
| **G05** | Auditoría de la CLI (identidad del SO, argumentos saneados, intención y resultado) y libro de acciones del anfitrión en el runner y en `fs` | op.exp.8.1, .8.2, op.exp.5 | P0 | M | G03 |
| **G06** | Sesión: inactividad servidor-side (default 15 min en el perfil), absoluta 8 h, aviso de derechos y obligaciones con aceptación, último acceso, sudo con reautenticación fuerte | mp.eq.2, op.acc.6.2/.9/R5, mp.per.2.r1, org.2 | P0 | M | G01 |
| **G07** | TLS y criptografía autorizada: certificado del operador como camino por defecto documentado, autofirmado ECDSA con SAN, TLS 1.2+ y suites AEAD fijadas en la consola, aviso y caducidad, SSH de flota con algoritmos fijados, guía "la central vive en la empresa" | mp.com.2 R1, mp.com.3 R2, op.exp.10 R1, mp.com.1 | P0 | M | — |
| **G08** | Aprobación de cuatro ojos para acciones críticas y equivalentes a root | op.acc.3, op.exp.5.4, org.4 | P1 | L | G01, G03 |
| **G09** | Perfil `security.profile: ens-medium`, `noust ens check` y `ens report` (evidencia, deriva, exposición, indicadores) | op.exp.2, op.exp.3 R1, op.mon.2, op.mon.3 R2, op.exp.8 R2 | P1 | M | G01, G03 |
| **G10** | Login sin fugas: errores uniformes, sin `version` ni `totp_enabled` previos, sin "intentos restantes" | op.acc.5.7 / op.acc.6.7 | P1 | S | G01 |
| **G11** | Flota: propagar usuario y rol a los nodos, permisos por nodo, cuenta de túnel sin root, rotación de claves, tipo de clave y algoritmos SSH, registro de interconexiones, id de correlación | op.acc.4.2, op.exp.10, op.ext.4, mp.com.2/.3 | P1 | L | G01 |
| **G12** | Copias: cifrado obligatorio en el perfil, MAC del sidecar, verificación programada con evidencia, `noust central backup` | mp.si.2, mp.info.6 (.6.2, R1), op.cont.1 | P1 | M | G07 |
| **G13** | Estado del servidor y de los nodos: parches y reinicio pendientes, hora, sshd efectivo, firewall vs puertos, fail2ban/auditd/agente (backlog 32, 46) | op.exp.4, op.exp.3 R1, op.mon.1, op.mon.3 R2 | P1 | M | — |
| **G14** | Compilaciones e instalaciones sin privilegios y en sandbox (backlog 44) | art. 20, op.exp.6, op.exp.2.2, mp.sw.2 | P1 | L | — |
| G15 | Inventario ENS: responsable, criticidad, clasificación, exportación y SBOM | op.exp.1 (+R4), mp.info.2, mp.si.1 | P2 | S/M | — |
| G16 | Cadena de suministro de Noust: SBOM, firmas, `pip-audit`, `npm audit`, Dependabot, imagen por digest, DAST en CI, `SECURITY.md` | op.pl.3, op.exp.4, mp.sw.1, mp.s.2 R1 | P2 | M | — |
| G17 | `noust incident freeze` (paquete de evidencias con SHA-256) y bloqueo de emergencia | op.exp.7 R2, op.exp.9 | P2 | M | G03 |
| G18 | Origen del código: lista de orígenes permitidos, despliegue de SHA fijado o commits firmados; previews sin secretos de producción; campo `reason` | op.exp.5.1/.5.2, mp.sw.1.r4, mp.sw.2.r1 | P2 | M | — |
| G19 | Retención configurable del resto de registros (jobs, despliegues, observaciones, sesiones) y minimización de datos personales | op.exp.8.r3, mp.info.1 | P2 | S | G03 |
| G20 | Plantillas nginx/apache: HSTS opcional por defecto, `server_tokens off`, sin `X-XSS-Protection` | mp.s.2 | P2 | S | — |
| G21 | Passkeys/WebAuthn (backlog 48) como factor R2/R3 preferente | op.acc.6 R2 | P3 | L | G01 |
| G22 | Tokens con alcance por recurso; sellado ampliado (config, store); cifrado de los secretos TOTP | op.acc.4.3, op.exp.10 | P3 | M | G01 |

Orden de implementación sugerido (dependencias): **A** permisos + esquema de usuarios + auditoría v2 (G03 base) en paralelo;
**B** flujos de autenticación, sesión, break-glass, login (G01, G02, G06, G10) y CLI `noust user`; **C** envío (G04),
CLI/libro (G05), TLS (G07); **D** cuatro ojos (G08), perfil y `ens check` (G09), flota (G11), copias (G12), estado del
servidor (G13); **E** el resto y `docs/ENS.md`.

---

### 4.2 G01: cuentas, roles y separación de funciones

#### 4.2.1 Principios

1. **Una cuenta = un rol.** Lo pide el RD literalmente: "cuando el usuario tenga diferentes roles frente al sistema ...
   recibirá identificadores singulares para cada perfil" (op.acc.1.2). Una persona con dos funciones tiene dos cuentas
   (`maria` y `maria.adm`), y el sistema **rechaza** (o avisa, según `auth.sod`) que dos cuentas de la misma persona
   (`person_ref`, por defecto el correo) reúnan funciones incompatibles.
2. **Guardas en el punto único** (regla 4). `AccountManager` impone contraseña, bloqueo, incompatibilidades y
   retención; `required_permission()` en `require_auth` impone permisos. Ningún endpoint decide por su cuenta.
3. **La web es cliente del manager** (regla 3). Login, API y CLI llaman a `AccountManager`; no hay una segunda
   implementación de "crear usuario".
4. **Sin dependencias nuevas**: `hashlib.scrypt` (ya usado en `core/sealing.py`), `core/totp.py`, sqlite del store.
5. **Compatibilidad**: sin usuarios creados, Noust 3.1 se comporta como la 3.0 (modo compat con token maestro). El
   perfil `ens-medium` no permite el modo compat.

Ubicación propuesta: `core/accounts.py` (manager), `core/permissions.py` (datos puros: roles y tabla ruta a permiso),
`web/api/users.py` y `web/api/approvals.py` (routers finos), `cli/commands/user.py`. Persistencia en el **store**
(`core/store.py`, migración v12), que ya tiene migraciones versionadas, WAL y modo 0600. Las sesiones siguen en
`web-sessions.db` y guardan `user_id`; el rol se **lee en cada petición** del store (caché de 5 s), de modo que un
cambio de rol o una baja surten efecto de inmediato.

#### 4.2.2 Roles

| Capacidad | `viewer` | `operator` | `admin` | `security` | `auditor` |
|---|:-:|:-:|:-:|:-:|:-:|
| Ver inventario, estado, métricas, registros de aplicaciones y despliegues (secretos redactados) | sí | sí | sí | sí (solo lectura) | metadatos |
| Operar: arrancar, parar, reiniciar aplicaciones; `cron run`; unidades gestionadas; renovar certificados; crear y verificar copias | no | sí | sí | no | no |
| Desplegar: update, rollback, activar release, rebuild | no | sí | sí | no | no |
| Crear y configurar aplicaciones, sitios, dominios, bases de datos, destinos de copia; borrar (con sudo) | no | no | sí | no | no |
| Revelar `.env`, exportar con secretos, mostrar la clave de un destino cifrado | no | no | sí (sudo; `show-key` con aprobación) | no | no |
| Operaciones **equivalentes a root** (unidad cruda, cron, ganchos de copia, configuración cruda de sitio, SQL de escritura, ruta local) | no | no | sí (sudo + aprobación en el perfil) | no | no |
| Configuración de seguridad (`web.*`, `auth.*`, `audit.*`, `central.*`, `fleet.*`) | no | no | no | sí (sudo) | lectura |
| Cuentas, roles, MFA, desbloqueo, revocar sesiones y tokens ajenos, decidir aprobaciones | no | no | no | sí | lista en lectura |
| Auditoría: leer, exportar, verificar la cadena, registrar revisión | solo lo propio | solo lo propio | solo lo propio | sí | sí |
| Flota: ver | sí | sí | sí | sí | sí |
| Flota: añadir o quitar nodo | no | no | sí (sudo + aprobación) | no | no |
| Desbloquear la central sellada | no | no | no | sí (custodia de la passphrase; configurable `central.unlock_roles`) | no |
| `ens check` y `ens report` | no | no | sí | sí | sí |

Motivos: `admin` **no** gobierna la seguridad (H10) ni lee toda la auditoría (op.exp.8.r4); `security` **no** despliega ni
cambia infraestructura (op.acc.3.2: quien autoriza distinto de quien usa); `auditor` es exclusivo y de solo lectura
(op.acc.3.r1.2 es refuerzo de ALTA y op.acc.3.r2.1, opcional; cuestan poco). Incompatibilidades entre cuentas de una misma
persona: `admin`/`operator` con `security`, y `auditor` con cualquier otro. Excepción documentada
(`user.sod_exception` con motivo y caducidad, art. 13.3 "medidas compensatorias") visible en `ens report`.

Correspondencia orientativa con el ENS: `security` ↔ RSEG (y delegados), `admin` ↔ administradores del RSIS,
`operator` ↔ operadores del RSIS, `auditor` ↔ supervisión/auditoría interna, `viewer` ↔ consulta.

Aprobaciones (G08): por defecto solo `security` decide; `approval.approvers: [security, admin]` permite que un `admin` distinto del
solicitante apruebe acciones de infraestructura, nunca cambios de rol ni de configuración de seguridad.

#### 4.2.3 Permisos y rutas

`core/permissions.py` define permisos estables y una tabla declarativa `(método, patrón de ruta) -> permiso`. Sustituye a
`required_scope()` y a los `require_scope`/`ensure_scope` sueltos (9 llamadas en `web/api` fuera del punto único). Se exporta a OpenAPI como
`x-noust-permission` junto a `x-noust-requires-elevation` (y `x-noust-requires-approval`, G08), para que la central y la
consola no dupliquen la tabla. **Prueba de arquitectura**: toda operación de `panel/openapi.json` (hoy 224) tiene permiso.

Permisos: `inventory.read`, `apps.operate`, `apps.deploy`, `backups.run`, `apps.write`, `infra.write`, `infra.destroy`,
`infra.root`, `secrets.reveal`, `security.config`, `users.manage`, `audit.read`, `audit.manage`, `fleet.manage`,
`central.unlock`, `approvals.decide`, `compliance.read`, `self.manage`.

| Grupo de rutas (existentes salvo indicación) | Permiso | Rol mínimo | Sudo hoy | Aprobación (perfil ens) |
|---|---|---|:-:|:-:|
| `GET` de `/api/apps*` (salvo `env` sin redactar y `export`), `/api/sites*`, `/api/certs*`, `/api/cron*`, `/api/services*` (salvo journal de unidades de Noust), `/api/databases/*`, `/api/backups*`, `/api/backup-*`, `/api/deployments*`, `/api/jobs*`, `/api/metrics*`, `/api/system/*` (sin líneas de órdenes), `/api/monitor/*`, `/api/nodes`, `/api/config*`, `/api/recipes*`, `/api/domains/dns` | `inventory.read` | `viewer` | no | no |
| Líneas de órdenes de procesos, journal de `noust-web` y `noust-monitor`, `GET /api/apps/{d}/env` sin redactar, `GET /api/apps/{d}/export`, `POST /api/backup-destinations/{n}/show-key`, `POST /api/databases/connection-string` | `secrets.reveal` | `admin` | sí (env, show-key) | `show-key` |
| `POST /api/apps/{d}/{start,stop,restart}`, `/api/cron/{n}/{run,enable,disable}`, `/api/services/{n}/{start,stop,restart,enable,disable}`, `/api/databases/engines/{e}/{start,stop,restart}`, `/api/certs/renew-all`, `/api/certs/{d}/renew`, `/api/monitor/scan`, `.../acknowledge`, `/api/jobs/{id}/cancel`, `/api/sites/reload`, `/api/sites/{d}/config/test`, pruebas de destino y de canal | `apps.operate` | `operator` | no | no |
| `POST /api/jobs/update`, `/api/jobs/rollback`, `.../releases/{r}/activate`, `.../deployments/{id}/{rebuild,rollback}` | `apps.deploy` | `operator` | no | no |
| `POST /api/backups`, `/api/jobs/backup`, `/api/backups/{id}/verify`, `POST /api/databases/backups` | `backups.run` | `operator` | no | no |
| `POST /api/apps`, `/api/apps/inspect`, `/api/jobs/cert`, `POST /api/certs/{d}`, `POST /api/sites`, `POST .../domains`, `PATCH .../{health,limits,releases/retention}`, `PUT .../{previews/settings,zero-downtime,env/marks}`, `POST/DELETE .../webhook-secret`, `POST .../migrate`, `POST /api/databases/{databases,users,users/grant,users/revoke}`, `.../engines/{e}/install`, `/api/monitor/{enable,disable,install,uninstall,start,stop,test-email}`, `/api/integrations/github*` | `apps.write` / `infra.write` | `admin` | varios | `apps.create` en nodos `production` |
| Los `DELETE` de aplicaciones, sitios, certificados, dominios, bases, usuarios de base, servicios, cron, copias, planes, destinos, previews; `POST /api/jobs/delete`, `/api/backups/{id}/restore`, `.../push`, `/api/databases/backups/restore`, `.../engines/{e}/uninstall`, `/api/certs/{d}/revoke`; `PUT /api/apps/{d}/env`; `PUT` de `/api/config/{webserver,ssl,backup,smtp,apps-directory,notifications/telegram}`; `POST /api/apps/import` | `infra.destroy` / `infra.write` | `admin` | sí | borrado y restauración de aplicaciones marcadas `critical` |
| `POST /api/services`, `PUT /api/services/{n}/config`, `POST/PUT /api/cron`, `POST/PUT /api/backup-schedules`, `PUT /api/sites/{d}/config`, `POST /api/databases/query` con `mode=write`, despliegue desde ruta local | `infra.root` | `admin` | sí | **sí** |
| `PUT /api/config/web`; `PUT/PATCH /api/config` en las secciones `web`, `auth`, `audit`, `central`, `fleet` | `security.config` | `security` | sí | no |
| **Nuevas**: `/api/users*`, `/api/approvals*`, `POST /api/auth/sessions/{revoke-all,revoke-others}`, `DELETE /api/auth/sessions/{sid}` y `DELETE /api/auth/tokens/{id}` de otros, `POST /api/audit/reviews`, `/api/audit/sinks` | `users.manage` / `audit.manage` | `security` | sí | no |
| `GET /api/audit`, `GET /api/audit/verify`, exportación | `audit.read` | `auditor` o `security` (`admin` y demás: solo eventos propios) | no | no |
| `POST /api/nodes`, `DELETE /api/nodes/{n}` | `fleet.manage` | `admin` | sí | **sí** |
| `POST /api/central/unlock` | `central.unlock` | `security` | sí | no |
| Nueva: `GET /api/compliance/ens` | `compliance.read` | `auditor`, `security`, `admin` | no | no |
| Sesión, MFA, contraseña y tokens **propios** | `self.manage` | todos | según endpoint | no |

Rutas proxificadas (`/api/nodes/{n}/api/...`): se evalúan por lo que sigue al nodo, más las **concesiones por nodo** del
usuario (4.2.7). El nodo vuelve a aplicar su propia tabla (como hoy con el scope).

#### 4.2.4 Datos (migración v12 del store)

```sql
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,        -- 3-64, [a-z0-9._-]
  kind TEXT NOT NULL DEFAULT 'human' CHECK (kind IN ('human','service')),
  display_name TEXT NOT NULL DEFAULT '',
  email TEXT,
  person_ref TEXT,                                     -- por defecto el correo; base de la comprobación de incompatibilidades
  role TEXT NOT NULL CHECK (role IN ('viewer','operator','admin','security','auditor')),
  state TEXT NOT NULL CHECK (state IN ('invited','active','locked','disabled')),
  password_hash TEXT,                                  -- scrypt$n$r$p$salt$hash (16 B de sal; pimienta HMAC con /etc/noust/auth-pepper)
  password_changed_at REAL, password_history TEXT NOT NULL DEFAULT '[]',
  must_change_password INTEGER NOT NULL DEFAULT 0,
  mfa_secret TEXT, mfa_confirmed_at REAL, mfa_last_steps TEXT NOT NULL DEFAULT '{}',
  backup_codes TEXT NOT NULL DEFAULT '[]',             -- hashes con sal propia por código (no la clave de firma), con marca de uso
  failed_count INTEGER NOT NULL DEFAULT 0, locked_at REAL, locked_reason TEXT,
  last_login_at REAL, last_login_ip TEXT, last_failed_at REAL, last_failed_ip TEXT,
  notice_version TEXT, notice_accepted_at REAL,
  nodes TEXT NOT NULL DEFAULT '["*"]',                 -- concesiones por nodo: nombres o patrones
  owner_id INTEGER REFERENCES users(id),               -- solo para kind='service': persona responsable
  created_by INTEGER, created_at REAL NOT NULL,
  disabled_at REAL, disabled_by INTEGER, disabled_reason TEXT
);
CREATE TABLE invitations (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  code_hash TEXT NOT NULL UNIQUE, expires_at REAL NOT NULL, used_at REAL, created_by INTEGER NOT NULL);
CREATE TABLE approvals (...);   -- G08
```
`web-sessions.db`: `sessions.user_id`, `sessions.last_activity`; `api_tokens.owner_id`, `permissions` (JSON),
`allowed_cidrs` (JSON), `last_used_ip`, `allow_elevated`.

Las cuentas **no se borran**: `disable` las inhabilita al instante y se conservan durante el periodo de retención de la
auditoría (op.acc.1.4.c); pasado ese periodo, `noust user purge` las anonimiza (`deleted-<id>`), sin romper la cadena de
auditoría (que guarda `user_id` y nombre en el evento).

#### 4.2.5 Flujos

**Primer usuario (arranque).**
1. `noust web enable` (sin cambios) imprime el token maestro, que pasa a ser **credencial de arranque**.
2. Con la tabla `users` vacía, la consola ofrece "Crear la primera cuenta de seguridad" y el endpoint
   `POST /api/setup/first-user {token, username, email, password, totp_code}` (solo válido mientras no exista ningún
   usuario). Crea una cuenta `security`, obliga a enrolar el TOTP en el mismo paso, y registra `user.create` con
   `bootstrap=true`. CLI equivalente: `noust user bootstrap` (root; contraseña con `--stdin`/`--prompt`).
3. `security` invita al primer `admin` y, si procede, al `auditor`. Hasta que existan al menos un `security`, un `admin` y
   MFA en todas las cuentas, `ens check` sale en rojo.
4. Desde ese momento el token maestro deja de servir para iniciar sesión o como Bearer salvo *break-glass* (G02).

**Invitación** (op.acc.6.2, .3: la credencial está "bajo el control exclusivo del usuario y se activa cuando está bajo su
control efectivo"). `noust user invite NOMBRE --role R [--email E] [--nodes ...]` (o la consola) crea la cuenta en estado
`invited` e imprime **una vez** un código `noust_inv_...` (24 h, un solo uso, almacenado con hash). El usuario abre
`/invite`, fija su contraseña (política), escanea el TOTP y confirma un código, ve y acepta el aviso de uso (op.acc.6.1,
.2), recibe los 8 códigos de respaldo, y solo entonces la cuenta pasa a `active`. Nadie recibe contraseñas por correo
ni por chat.

**Inicio de sesión.** `POST /api/auth/login {username, password, totp_code}`: un solo paso, tres campos siempre
visibles, **error uniforme** `401 invalid_credentials` sea cual sea el motivo, y `scrypt` de relleno con usuarios
inexistentes para no filtrar existencia por tiempo (op.acc.6.7). El campo `totp_code` es obligatorio para toda cuenta
`human` (`auth.mfa: required`; solo `viewer` puede relajarse en el perfil estándar). Respuesta: sesión, `csrf`, `last_login`
(fecha, IP, resultado), `failed_since` y, si procede, `notice_pending` (G06). El camino heredado `{token}` sigue en modo
compat.

**Contraseñas** (op.acc.6.r1.2 exige "complejidad mínima y robustez frente a ataques de adivinación"; los números son
**decisión del operador**): `auth.password.min_length` 14, lista de contraseñas comunes empaquetada (unas 10 000, texto
plano), no puede contener el nombre de usuario ni el nombre de la máquina, historial de 5, `max_age_days` 365 en el
perfil (op.acc.6.4 exige una periodicidad que marque la política; NIST SP 800-63B desaconseja rotaciones periódicas sin
sospecha de compromiso, luego el valor cero es una decisión documentada si el operador lo prefiere:
<https://pages.nist.gov/800-63-3/sp800-63b.html>), sin reglas de composición. Hash `scrypt` N=2^15, r=8, p=1 (los mismos
parámetros que el sellado) con sal de 16 bytes y **pimienta** HMAC-SHA256 con una clave propia (`/etc/noust/auth-pepper`, 0600), de modo
que una copia del store sin ella no basta. La pimienta **no** es la clave de firma web: `noust web token --regenerate` rota esa
clave y no debe invalidar las contraseñas de nadie (hoy invalida sesiones, tokens API y códigos de respaldo TOTP). Cambio y restablecimiento: el restablecimiento genera una invitación
nueva (`must_change_password`), nunca una contraseña conocida por quien restablece.

**Bloqueo por cuenta** (op.acc.6.8: "número de intentos permitidos será limitado, bloqueando la oportunidad de acceso ...
y requiriendo una intervención específica para reactivar la cuenta, que se describirá en la documentación"). Umbral
`auth.lockout.threshold` = 5 fallos consecutivos de contraseña o TOTP para un usuario **existente**, desde cualquier IP;
`state=locked`, `locked_reason=too_many_failures`; **reactivación manual** por `security` (`noust user unlock` o consola),
auditada y notificada (`account_locked`). Se mantiene el bloqueo por IP actual como capa previa (frena la enumeración y
protege la cuenta de bloqueos ajenos: primero se agota el presupuesto de la IP). Un atacante que conozca nombres puede
bloquear cuentas desde una IP permitida: mitigación por allow-list de red, alerta inmediata y la cuenta *break-glass*
con bloqueo temporal. `auth.lockout.mode = timed` (con duración) queda disponible para el perfil estándar.

**Sudo (modo elevado)**: `POST /api/auth/elevate {password, totp_code}` (contraseña **y** TOTP; código no reutilizable por
finalidad, como hoy). Ventana `auth.elevation_minutes` (10 hoy; 5 en el perfil). Un usuario sin MFA no puede elevar.

**Baja**: `noust user disable` (o consola) revoca de inmediato sesiones, tickets de WebSocket, flujos (la revalidación de
30 s de los WS ya lo cubre), tokens propios y aprobaciones pendientes; registra `user.disable` con motivo.

**Revisión de accesos** (op.acc.4.4 "los permisos de acceso se revisarán de forma periódica"): `noust user review` lista
cuentas, rol, último acceso, MFA, tokens, concesiones y excepciones de incompatibilidad; `POST /api/users/review/attest`
(rol `security`) escribe `access.review` con el hash de la lista revisada; `ens check` avisa a los 90 días (decisión del
operador). Cuentas inactivas: `auth.inactive_disable_days` (0 = apagado; en el perfil 90).

#### 4.2.6 Tokens y cuentas de servicio

Un token API **pertenece a un usuario** (`owner_id`) y lo actúa con los permisos del propietario **intersecados** con
los del token: `effective = token.permissions ∩ owner.permissions(hoy)`. Al desactivar al propietario, sus tokens dejan
de valer. En el perfil: caducidad obligatoria (máximo `auth.token.max_days`, 90), `allowed_cidrs` opcional, y **sin
elevación implícita**: un token no puede ejecutar acciones con `x-noust-requires-elevation` salvo `allow_elevated`
concedido por `security`, y nunca `infra.root` ni `users.manage`. Presets: `ci-deploy` (solo `apps.deploy`),
`read-only`. Cuentas de proceso (`kind=service`, sin contraseña ni login interactivo) siempre con `owner_id`, para que
"cada entidad, usuario o proceso" tenga identificador singular y persona responsable (op.acc.1.3). Los tokens
anteriores a 3.1 (sin propietario) siguen funcionando en el perfil estándar; en el perfil, `noust token adopt ID
--owner USUARIO` los asigna o quedan rechazados a partir de `auth.legacy_tokens_until`. El token de flota conserva su
naturaleza (`fleet`, sin caducidad, solo por loopback).

#### 4.2.7 Flota

- Las cuentas y los roles **viven en la central** (y en cada nodo, solo la cuenta de emergencia y los usuarios locales).
- La central propaga `X-Noust-Actor: <username>` y `X-Noust-Actor-Role: <rol>` (además de `X-Noust-Actor-Scope` para
  nodos antiguos: `viewer`→`read`, `operator`→`deploy`, `admin` y `security`→`admin`) y un `X-Noust-Request-Id` que aparece
  en la auditoría de ambos lados (correlación en el SIEM). El **nodo aplica su propia tabla** de permisos al rol
  recibido; un rol desconocido cae a `viewer`. Es el mismo principio de hoy con la elevación: el nodo decide, la
  central avala.
- **Concesiones por nodo** (`users.nodes`): la central comprueba antes de reenviar que el usuario puede actuar sobre ese
  nodo (mínimo privilegio, necesidad de conocer: op.acc.4.2 y .4.3).
- La auditoría del nodo registra `actor=<usuario> via fleet-<central>`, con `role` y `request id`.

#### 4.2.8 CLI

`noust user list | show | invite | bootstrap | disable | enable | unlock | set-role | reset-mfa | reset-password |
review | purge | sod-exception`, con `--json`. La CLI corre como root: **es el canal de emergencia** y no se somete a
RBAC (root ya puede todo en la máquina). Por eso (G05) cada orden mutadora queda auditada con la identidad del SO, y el
perfil puede exigir `--reason`. `sudo noust ...` desde cuentas nominales es la forma prevista (queda `SUDO_USER`).

#### 4.2.9 Migración y pruebas

- 3.0 a 3.1: migración v12 crea las tablas vacías; nada cambia hasta `noust user bootstrap` o el asistente de la consola.
  El TOTP global actual pasa a ser el de la cuenta *break-glass*.
- Pruebas de arquitectura (en `tests/test_architecture.py`): cada operación OpenAPI tiene permiso; cada `action` de
  auditoría está en el catálogo (G03); cada comando Click está clasificado lectura o escritura (G05, contra
  `tests/contracts/cli_surface.json`); ningún `except Exception` nuevo.
- Pruebas de propiedades: ningún rol tiene un permiso que otro rol incompatible necesite para el mismo ciclo (matriz de
  incompatibilidades); un token nunca supera a su propietario; login uniforme (cuerpo y tiempo) para usuario
  inexistente, contraseña errónea y TOTP erróneo.
- E2E (Playwright): invitación, primer usuario, bloqueo y desbloqueo, aviso, caducidad por inactividad, sudo, axe en
  todas las pantallas nuevas en ambos temas.

---

### 4.3 G02: token maestro a *break-glass* y tokens con dueño

**El problema (H1).** `check_credential` acepta el token maestro como Bearer y `ELEVATION_EXEMPT_TYPES` lo exime de sudo:
quien lo tenga es `admin` sin segundo factor, aunque el 2FA esté activo. Con usuarios y MFA por usuario, el maestro sería la
puerta trasera que cualquier auditor encuentra primero (op.acc.6.r8: "doble factor ... desde o a través de zonas no
controladas").

```yaml
auth:
  master_token: full | break_glass | disabled   # full mientras no haya usuarios; break_glass en el perfil ens-medium
```

En `break_glass`:
- `POST /api/auth/login {token, totp_code, reason}` (el TOTP global actual pasa a ser el de esta vía; `reason` obligatorio,
  10 a 200 caracteres). Sesión con un rol virtual `break_glass` = permisos de `admin` y de `security` **salvo**
  `approvals.decide` y `audit.manage` (no puede aprobarse a sí mismo ni tocar la auditoría), **30 min, sin renovación**.
- `Authorization: Bearer <maestro>` responde `401 master_bearer_disabled` con la pista de qué hacer. Único uso restante:
  `GET /health`.
- Todo lo que hace queda con severidad `crit` y marca `bg=1`; el login dispara `auth.break_glass` y una notificación
  inmediata por todos los canales (`break_glass_used`); `ens check` queda en rojo hasta rotar el token
  (`noust web token --new`).
- Bloqueo **temporal** (15 min) y no manual: es la vía de recuperación y no puede depender de otra persona.
- En `disabled`, no hay inicio de sesión con el maestro; la recuperación es por la CLI de root (`noust user unlock`,
  `reset-password`, `bootstrap --force`).

Tokens de API: ver 4.2.6. El cambio en el punto único es pequeño: `check_credential` deja de devolver `master_payload` fuera
de la ruta de login y `ensure_elevated` deja de eximir a `api_token` cuando el token no lleva `allow_elevated`.

### 4.4 G03: auditoría v2

**Objetivo**: cumplir op.exp.8 (.8.1, R1, R3, R4), op.exp.9 y el art. 24 ("identificar en cada momento a la persona que actúa";
"retener la información estrictamente necesaria para monitorizar, analizar, investigar y documentar actividades indebidas o no autorizadas").

#### 4.4.1 Un único punto de registro y un catálogo cerrado

- Hoy hay unas 45 llamadas `audit.record(...)` en 14 ficheros (17 solo en `web/api/auth.py`) **además** del `api.<método>`
  genérico del middleware (duplicados y huecos). Nuevo contrato: los endpoints solo aportan contexto
  (`audit_context(request).set(target=..., reason=..., changes=[...])`) y `SecurityMiddleware` escribe **exactamente un
  evento por petición** relevante. Los eventos que no son peticiones (login, bloqueo, arranque) usan `audit.event(...)`.
- `core/audit_events.py` es el **catálogo**: `EVENTS[nombre] = EventSpec(categoría, severidad, medidas ENS, descripción)`.
  `AuditLogger.event()` rechaza acciones no registradas (en pruebas falla; en producción registra `audit.unknown_action`).
  Prueba de arquitectura: un AST recorre `src/noust` y falla si una llamada usa una acción fuera del catálogo. El catálogo
  genera la tabla de eventos de `docs/ENS.md` (`noust audit events --markdown`): op.exp.8.r3.1 pide indicar "los eventos de
  seguridad que serán auditados".
- Los `audit_log.info(...)` del logger `noust.audit` (H2) pasan a `audit.event(...)` con sus campos (`db.query`, `db.drop`,
  `cron.create`, `backup.schedule`, `integration.*`). La sentencia SQL se registra como longitud + SHA-256 + primeros 200
  caracteres saneados, no completa (puede llevar secretos).

Catálogo mínimo (nombre, severidad syslog, medida):

| Familia | Eventos | Sev. | Medida |
|---|---|:-:|---|
| Acceso | `auth.login.success`, `auth.login.failure`, `auth.logout`, `auth.lockout`, `auth.unlock`, `auth.mfa.enroll/confirm/disable/reset`, `auth.password.change/reset`, `auth.elevate`, `auth.notice.accept`, `auth.session.revoke`, `auth.session.timeout`, `auth.token.create/revoke/denied`, `auth.break_glass` | 6 (éxito), 4 (fallo), 2 (`lockout`, `break_glass`) | op.acc.6.r5, op.exp.8 |
| Cuentas | `user.create/invite/activate/disable/enable/role_change/unlock/purge`, `user.sod_exception`, `access.review`, `approval.request/approve/reject/use` | 5 | op.acc.1, .3, .4 |
| Configuración | `config.change` (clave; valores solo si no son secretos, si no, huella), `security.profile.change`, `security.profile.exception`, `audit.sink.change`, `central.seal/unseal/unlock/lock` | 5 | op.exp.3 |
| Cambios | `apps.create/update/rollback/delete/env.reveal/env.write/export.secrets`, `services.*`, `sites.*`, `certs.*`, `cron.*`, `backups.*`, `db.*`, `fleet.node.*`, `fleet.tunnel.up/down/hostkey_changed` | 5 | op.exp.5 |
| Lecturas sensibles | `audit.read`, `users.list`, `tokens.list`, `sessions.list`, `processes.cmdline`, `logs.noust.read`, `db.query.read`, `nodes.key`, `compliance.read` | 6 | op.exp.8.1 |
| Denegaciones | `http.denied.ip/rate/size/csrf/scope/elevation/approval` | 4 | op.acc.4.1 |
| Sistema | `system.start` (versión, perfil, reloj), `system.stop`, `audit.checkpoint`, `audit.gap`, `audit.degraded`, `audit.review`, `audit.verify`, `audit.clock_unsynced`, `cli.command`, `host.exec` | 6 (crit 2 en `degraded`, `gap`) | op.exp.8.r2, .r4 |

#### 4.4.2 Esquema del evento (v2)

```json
{"v":2,"id":"0198f1c2-7b1e-7a55-9d1e-3c2f1b7e5a10","seq":18422,"ts":"2026-09-29T21:14:07.123Z",
 "host":"central1.corp.example","ver":"3.1.0","action":"apps.delete","out":"success","sev":5,
 "actor":{"type":"user","id":17,"name":"maria.adm","role":"admin","via":"fleet-central1","sess":"a1b2c3d4"},
 "src":{"ip":"10.20.0.15","chan":"web","req":"7f3c19aa"},
 "target":{"type":"app","id":"shop.example.com","node":"vps-web-3"},
 "elev":true,"appr":"a-231","reason":"retirada tras migración","detail":{"changes":["status"]},
 "prev":"9f2c…","mac":"e1a7…"}
```

Cubre op.exp.8.1 campo a campo: **identificador del usuario** (`actor`), **fecha y hora** (`ts`, UTC con ms), **sobre qué
información** (`target`), **tipo de evento** (`action`), **resultado** (`out`). `actor.type` ∈ `user`, `service`,
`break_glass`, `fleet`, `cli`, `system`, `anonymous`. Compatibilidad: el lector actual (`GET /api/audit`) sigue leyendo v1 y
v2; el fichero conserva el nombre y el modo 0600.

#### 4.4.3 Integridad (op.exp.8.r4) y honestidad sobre sus límites

- Cadena: `mac = HMAC-SHA256(k_audit, prev || json_canónico(evento_sin_mac))`; `seq` monotónico a través de las rotaciones.
  Clave en `/etc/noust/audit-key` (0600), distinta de la de firma web. Escrituras serializadas con `fcntl.flock` sobre
  `audit.lock`, de modo que el servidor y los procesos CLI comparten la cadena.
- `noust audit verify [--desde ...]` (y `GET /api/audit/verify`) recorre ficheros y rotaciones y informa de cadena rota,
  huecos de `seq` y truncamientos. `audit.checkpoint {seq, mac}` cada 5 min a todos los sinks.
- **Límite que debe constar en `docs/ENS.md`**: root en la misma máquina puede recalcular una cadena con la clave local. La
  garantía real es la copia **fuera de la máquina** (G04) y los puntos de control almacenados en el SIEM, donde una
  reescritura local se ve como discontinuidad. Y `admin` es root-equivalente (H9): por eso G08 y G04 van juntos.

#### 4.4.4 Retención (op.exp.8.r3) y protección frente a inundación (H3)

```yaml
audit:
  enabled: true
  retention_days: 365        # decisión del operador; suelo de 90; nada se purga antes ni sin haber sido enviado
  max_total_mb: 2048         # avisa al 80 % (audit.degraded); NUNCA borra dentro de la retención
  rotate: daily              # y a 64 MiB; ficheros audit-AAAAMMDD.ndjson[.gz]
  chain: true
  on_failure: continue       # o deny_mutations: rechazar mutaciones si no se puede registrar
```

- Retención **por tiempo**, no por tamaño. La purga solo borra ficheros con más de `retention_days` **y** ya entregados a
  todos los sinks con acuse.
- Inundación anónima: los eventos `anonymous` con igual `(action, ip, resource)` en 60 s se **coalescen** en uno con
  `count`. Por encima de `max_total_mb`, se descartan solo eventos `anonymous` de severidad informativa y se anota
  `dropped` en el siguiente evento: los eventos de seguridad y de personas identificadas nunca se descartan.
- Fallo de escritura: `audit.degraded` (crit) a journald, notificación `audit_failure`, `noust health` en rojo. Con
  `on_failure: deny_mutations`, las mutaciones devuelven `503 audit_unavailable` (decisión del operador; MEDIA no lo exige).

#### 4.4.5 Revisión periódica (op.exp.8.r1)

`noust audit digest` y un resumen semanal por los canales de notificación (fallos de acceso, bloqueos, uso de *break-glass*,
cambios de rol, configuración, aprobaciones, huecos). `POST /api/audit/reviews {periodo, notas}` (roles `auditor`/`security`)
escribe `audit.review`: es la **evidencia de que la revisión ocurrió**. `ens check` avisa si pasan más de 7 días (valor
del operador).

### 4.5 G04: envío a journald y a syslog RFC 5424

**Modelo**: el fichero local encadenado es la fuente de verdad (es un WAL). Solo journald se escribe en línea; los sinks de
red los sirve **un remitente** que lee el fichero y reenvía (el mismo para servidor y CLI), con cursor persistente:
**al menos una vez**, con `id` y `seq` para deduplicar. El remitente vive en `noust-web` (hilo) y como
`noust audit ship --once` para cron. Nunca bloquea la ruta de la petición.

```yaml
audit:
  sinks:
    - {type: journald}                            # protocolo nativo: SYSLOG_IDENTIFIER=noust-audit, campos NOUST_*
    - type: syslog                                # RFC 5424
      transport: tls                              # unix | udp | tcp | tls
      address: siem.corp.example:6514
      ca: /etc/noust/siem-ca.pem
      client_cert: /etc/noust/siem-client.pem     # TLS mutuo (RFC 5425)
      client_key: /etc/noust/siem-client.key
      facility: 13                                # "log audit" (RFC 5424, tabla 1)
      enterprise_id: 32473                        # PEN; ver decisiones abiertas
    - {type: stdout}                              # contenedor (central hub): NDJSON al log del contenedor
```

**Mensaje RFC 5424** (ejemplo; facilidad 13 x 8 + severidad 5 = PRI 109):

```
<109>1 2026-09-29T21:14:07.123Z central1.corp.example noust-audit 4123 apps.delete [noust@32473 seq="18422" id="0198f1c2-7b1e-7a55-9d1e-3c2f1b7e5a10" actor="maria.adm" atype="user" role="admin" node="vps-web-3" ip="10.20.0.15" chan="web" out="success" res="app:shop.example.com" elev="1" appr="a-231" req="7f3c19aa" sess="a1b2c3d4"][timeQuality tzKnown="1" isSynced="1"][origin enterpriseId="32473" software="noust" swVersion="3.1.0"] maria.adm deleted app shop.example.com
```

- `MSGID` = la acción (máx. 32 caracteres ASCII imprimibles; el catálogo lo impone); `APP-NAME` = `noust-audit`; `PROCID` = pid; marca de tiempo con `T` y
  `Z` en mayúsculas y milisegundos (RFC 5424 6.2.3).
- SD-ID propio `noust@<PEN>`: los nombres sin arroba están reservados a IETF (RFC 5424 6.3.2). `32473` es el número
  reservado para documentación (RFC 5424 lo usa en su ejemplo, `ourSDID@32473`; RFC 5612): **Noust debe registrar su PEN
  gratuito en IANA (<https://pen.iana.org/>)** o exigir el de la organización en `enterprise_id`.
- `timeQuality.isSynced` sale de la comprobación de reloj (G09); si el reloj no está sincronizado, además se emite
  `audit.clock_unsynced` (op.exp.8.r2).
- Escapado obligatorio en valores de SD-PARAM (`"`, `\`, `]`) y saneado de caracteres de control en MSG y `detail`
  (inyección de registros, CWE-117).
- Severidad → PRI: 2 crítico (104+2=106: `lockout`, `break_glass`, `audit.degraded`), 4 aviso (108: fallos y denegaciones), 5
  nota (109: mutaciones), 6 informativo (110: lecturas y accesos correctos).
- Transportes: UNIX (`/dev/log`), UDP, TCP con **recuento de octetos** (RFC 6587) y TLS (RFC 5425, puerto 6514) con
  validación obligatoria de certificado, TLS 1.2 como mínimo y la misma política de cifrados que la consola (G07).
- **journald** (nativo, sin dependencias): datagrama a `/run/systemd/journal/socket` con campos `MESSAGE`, `PRIORITY`,
  `SYSLOG_IDENTIFIER=noust-audit`, `NOUST_SEQ`, `NOUST_ACTION`, `NOUST_ACTOR`, `NOUST_ROLE`, `NOUST_OUTCOME`,
  `NOUST_SRC_IP`, `NOUST_TARGET`, `NOUST_NODE`. Se reenvía con `rsyslog imjournal`, `systemd-journal-upload`, Wazuh o Filebeat
  y se lee con `journalctl -t noust-audit -o json`.
- Cola: si el sink falla, el cursor no avanza; a partir de N minutos de retraso, `audit.sink.degraded` y notificación.

### 4.6 G05: auditoría de la CLI y libro de acciones del anfitrión

#### 4.6.1 Órdenes de la CLI

Hoy solo `fleet` y `node` auditan (`fleet/audit.py`); `cli/commands/env.py:283` deja escrito que auditar las mutaciones de
la CLI es una decisión para la CLI en su conjunto, no de un comando. Diseño en el **límite de la CLI** (`cli/app.py:main`, ya el único punto que
traduce errores, regla 2):

1. `cli/audit_policy.py` clasifica cada comando como `read` o `write`. Por defecto **todo lo no clasificado se audita**
   (falla cerrado). Prueba: cada comando del árbol Click, contra `tests/contracts/cli_surface.json`, tiene clasificación.
2. Para órdenes `write` se escribe primero un evento de **intención** (`cli.command`, fase `start`) y al final el de
   **resultado** (`exit_code`, duración): un proceso terminado a la fuerza deja rastro.
3. **Identidad**: `/proc/self/loginuid` (auditd conserva el usuario original a través de `sudo`/`su`; 4294967295 = sin
   asignar), si no `SUDO_USER`, si no `getpass.getuser()`. Se registran también `via_sudo`, `tty` y la IP de `SSH_CONNECTION`.
   Desde una unidad systemd (cron, copias, `INVOCATION_ID`): `actor=system:<unidad>`. `docker exec` en la central de
   contenedor: `cli:container` y el operador debe complementarlo con el registro del anfitrión (Docker).
4. **Argumentos saneados** con `core/redact.py` (regla 3): valores de claves secretas de `config set`, opciones marcadas
   como secretas y todo lo dado por `--stdin`/`--prompt` se enmascaran; nunca se registra un valor secreto.
5. Un ensayo (`--dry-run`) **no escribe** en el registro: `AuditLogger` ya manda lo que habría escrito al log del proceso
   (`web/auth.py:1668`, `:1720-1722`), y un ensayo no puede crear ficheros. Solo las ejecuciones reales dejan evento.
6. Perfil `ens-medium`: `security.cli_policy: reason` exige `--reason "texto"` (referencia de cambio, op.exp.5.1) en órdenes
   `write`; sin ella, exit 2.
7. Como la CLI es root, **no está sujeta a RBAC**: es el canal de emergencia. Su control es el del SO (cuentas nominales,
   `sudo` con registro de E/S, claves SSH por persona), responsabilidad del operador (op.acc.4.5).

#### 4.6.2 Libro de acciones del anfitrión (`host.exec`)

El `CommandRunner` es el **único** punto por el que Noust ejecuta procesos y `core/fs.py` el único por el que cambia ficheros
(reglas 1 y 3). Ahí se puede registrar, con la identidad ambiental (un `contextvar` fijado por el middleware, por el
límite de la CLI y capturado al encolar un job), **qué hizo realmente Noust como root** en respuesta a cada petición:
`systemctl restart shop-example-com`, `nginx -s reload`, `certbot ...`, escrituras en `/etc/nginx/...`. Es la evidencia
que permite detectar un "diputado confundido" (la API dijo A, el runner hizo B) y aporta el "sobre qué información" de
op.exp.8.1 para acciones de sistema.

- Se registran las ejecuciones **no de solo lectura** (`core/runner.py:is_read_only` ya distingue) y las escrituras de `fs`
  bajo rutas del sistema, con argv saneado (`core/redact.Scrubber`), sin entorno.
- `audit.host_activity: off | mutations | all` (perfil: `mutations`). Un despliegue son ~60 a 150 eventos; tope por
  petición (500) con un evento resumen. Severidad informativa, en un flujo aparte que el operador puede no enviar al SIEM.

### 4.7 G06: sesión, aviso de derechos y último acceso

| Parámetro | Estándar (hoy) | `ens-medium` | Comentario |
|---|---|---|---|
| `auth.session.idle_lock_minutes` | 720 | **15** | mp.eq.2.1 "tiempo prudencial" (decisión del operador). **Bloqueo servidor-side**: la sesión pasa a `locked` y `401 session_locked` hasta `POST /api/auth/unlock {password, totp}` |
| `auth.session.idle_cancel_minutes` | 720 | **60** | mp.eq.2.r1 (ALTO, pero barato): cancela la sesión |
| `auth.session.absolute_hours` | 24 | **8** | tope de una jornada |
| `auth.elevation_minutes` | 10 | **5** | modo sudo con contraseña y TOTP |
| `auth.session.max_per_user` | sin límite | 3 | sesiones concurrentes por usuario |

- **Temporizador real de inactividad**: columna `sessions.last_activity` actualizada como máximo cada 30 s; la verificación
  compara `ahora - last_activity`. Hoy la "inactividad" es la caducidad renovada al 50 % (`web/auth.py:2861`).
- La consola dibuja un bloqueo (superposición) a los `idle_lock_minutes - 1` con reautenticación sin perder el estado.
- **Aviso posterior al acceso** (op.acc.6.9: "informará al usuario de sus derechos u obligaciones inmediatamente después de
  obtener el acceso"; op.acc.6.2 y mp.per.2.r1.1 piden reconocimiento expreso): texto configurable
  (`auth.notice.text`, ES y EN), versión = SHA-256 del texto; si la versión aceptada difiere, la respuesta del login
  incluye `notice_pending` y **el punto único** responde `403 notice_required` a todo salvo `/api/auth/*` hasta
  `POST /api/auth/notice/accept`. Queda `auth.notice.accept` con versión y hora.
- **Último acceso** (op.acc.6.r5.2): el login devuelve `last_login {fecha, IP, resultado}` y `failed_since`; la consola lo
  muestra tras entrar.
- **Antes** del login no hay aviso ni advertencias: op.acc.6.7 pide "la información mínima imprescindible" y la CCN-STIC 804
  (2017, apartado sobre acceso local) desaconseja los textos de advertencia previos porque atraen a no autorizados. Solo el
  formulario y una **etiqueta de servidor definida por el operador** (`web.login_label`) para no teclear credenciales
  en la máquina equivocada.

### 4.8 G07: TLS, criptografía autorizada y "la central vive en la empresa"

**TLS de la consola.** uvicorn recibe hoy solo certificado y clave (`web/server.py:1841-1860`); versión mínima y cifrados
quedan a lo que decida OpenSSL y uvicorn [confirmar: uvicorn usa por defecto `ssl_ciphers="TLSv1"`].

- `web/tls.py`: contexto con `minimum_version=TLSv1_2`, sin compresión, `OP_CIPHER_SERVER_PREFERENCE`, suites ECDHE con
  AES-GCM (y las de TLS 1.3), curvas P-256/P-384. uvicorn no acepta un contexto: se sobrescribe `Config.load()` en la
  subclase que ya existe (`_serve`). **Una sola definición** de protocolos y suites (`core/tls_policy.py`) alimenta el
  contexto de la consola, el cliente TLS del sink de syslog y las plantillas nginx/apache (regla 3): hoy la lista está
  copiada en ocho plantillas (seis de nginx y dos de Apache).
- Prueba: un handshake de prueba contra la consola con TLS 1.0/1.1 y con una suite CBC/RSA debe fallar.

**Certificado del operador como camino por defecto documentado.**
- `docs/CENTRAL.md` y el `compose.yaml` presentan primero `NOUST_TLS_CERT`/`NOUST_TLS_KEY` (o `--tls-cert/--tls-key`) y el
  autofirmado como excepción.
- `noust tls check` y `noust tls import --cert C --key K [--chain F]`: valida que la clave corresponde, que la **SAN** cubre
  el nombre, que no ha caducado, que algoritmo y tamaño son aceptables (ECDSA P-256/P-384 o RSA de 3072 bits o más [verificar
  CCN-STIC 221]) y que la cadena está completa; avisa a 30 días de la caducidad (evento `cert_expiring` también para
  el certificado de la consola, hoy solo cubre los de certbot) y muestra huella y vencimiento en `noust central status`.
- Recarga sin corte no es posible con uvicorn: `noust tls import` reinicia `noust-web` (`systemctl restart`) y así puede usarse como gancho
  de renovación de la PKI de la organización o de certbot.
- **Autofirmado (solo perfil estándar)**: ECDSA P-384 con **SAN** (nombre y IP), validez de 825 días o menos, en lugar de
  RSA-2048 sin SAN a 3650 días (`managers/cert_manager.py:1119-1132`). El perfil `ens-medium` lo rechaza salvo
  `--allow-self-signed` (queda `security.profile.exception`).
- Recomendación de red: la consola de la central, en una VLAN/VPN de gestión con `NOUST_ALLOW_IP` **explícita**. Hoy la
  central admite por defecto todos los rangos privados RFC 1918 (`central/setup.py:39-46`): demasiado ancho para MEDIA.

**SSH de la flota.** `fleet.ssh.*` (por defecto los de OpenSSH; en el perfil, lista fija de `KexAlgorithms`, `Ciphers`, `MACs`,
`HostKeyAlgorithms`, `PubkeyAcceptedAlgorithms` a las opciones autorizadas). Tipo de clave por nodo: Ed25519 por defecto
(`fleet/models.py:56` solo acepta este); `fleet.key_type: ecdsa-p384 | rsa-3072` para el caso de que el CCN no
autorice Ed25519 [verificar en CCN-STIC 221/807]. Rotación: `noust node rekey NODO` (genera par nuevo, instala la clave nueva
en el nodo con el mismo `authorize`, retira la vieja, audita `fleet.node.rekey`).

**Hash de token, contraseñas y TOTP.** Contraseñas: scrypt (4.2.5). Tokens de 256 bits: SHA-256 salado es adecuado. TOTP:
HMAC-SHA1 es el estándar de las apps; opción `auth.mfa.totp_algorithm: sha256` (RFC 6238 lo permite; algunas apps lo
ignoran); el estado CCN de HMAC-SHA1 en OTP **debe verificarse** [verificar].

**Certificados de los sitios (certbot).** Noust lanza `certbot certonly` sin `--key-type` (`managers/cert_manager.py:969`): el algoritmo
depende de la versión instalada. `ssl.key_type` (`ecdsa` por defecto) y `ssl.elliptic_curve` (`secp384r1`) o `ssl.rsa_key_size` (3072 o más) se
pasan a certbot, para que `mp.com.2.r1` no dependa de un valor por defecto ajeno.

**Guía "la central vive en la empresa" (backlog 30).** La central guarda las llaves de toda la flota y, con ellas, un atacante
despliega código (deploy = ejecución de código). Por eso mp.if.1 a mp.if.7 (áreas separadas y con control de acceso,
energía, incendios, inundaciones, registro de entradas y salidas), op.cont.1 (impacto), mp.info.6 (copias) y op.exp.8
(sincronización de reloj y envío de logs) **se aplican al equipo que la aloja**: un NAS doméstico incumple mp.if.1, .2, .3, .4,
.5, .6 y .7. Requisitos mínimos de emplazamiento en `docs/ENS.md`: CPD o sala controlada de la empresa, SAI, reloj NTP,
segmento de gestión, copia cifrada fuera de sitio, sellado con passphrase custodiada por `security` (y no en el equipo).

### 4.9 G08: aprobación de cuatro ojos y operaciones equivalentes a root

**Por qué.** op.acc.3: "se exija la concurrencia de dos o más personas para realizar tareas críticas, anulando la
posibilidad de que un solo individuo autorizado pueda abusar de sus derechos". El sudo es la misma persona repitiéndose. Y
`admin` es root-equivalente (H9): la separación debe apoyarse en un segundo actor.

**Acciones sujetas** (`security.approvals`, por defecto en el perfil): los permisos `infra.root` (unidad, cron, hooks de
copia, configuración cruda de sitio, SQL de escritura, ruta local), `fleet.manage` (alta y baja de nodos),
`secrets.reveal` en `show-key`, `apps.create` en nodos `production`, borrado y restauración de aplicaciones marcadas
`critical`, y cambios de rol a `admin` o `security`. Lista editable por `security`.

**Mecanismo (guarda en el punto único, como `ensure_elevated`).** `ensure_approved(request, session)`:
1. Primera petición del solicitante → `403 approval_required` con `approval_id`, quién puede aprobar y la caducidad. Se crea
   la fila `approvals {solicitante, método, ruta, sha256(cuerpo), nodo, motivo, estado, caduca}`. La consola muestra
   "pendiente de aprobación" y hace sondeo.
2. Un **usuario distinto** con `approvals.decide` (o `admin` distinto, según `approval.approvers`) llama a
   `POST /api/approvals/{id}/approve` con sudo (TOTP). No puede aprobar quien comparte `person_ref` con el solicitante.
3. El solicitante repite la petición con `X-Noust-Approval: {id}`: el guardián comprueba que la aprobación es de **ese**
   usuario, método, ruta y hash de cuerpo, no está caducada (TTL 30 min) y no se usó; **un solo uso**.
4. Auditoría: `approval.request`, `approval.approve|reject`, `approval.use`, y `appr` en el evento de la acción.

**Flota.** Igual que la elevación: el nodo declara `x-noust-requires-approval` en OpenAPI; la central gestiona las
aprobaciones (donde viven las cuentas) y avala con `X-Noust-Approved-By` y `X-Noust-Approval`; el nodo **rechaza** la
llamada si el aval falta, de modo que una central vieja o comprometida no salta el control del nodo.

**Casos límite.** Con un único `security`, los cambios de rol no tienen segundo aprobador: se permite solo con
`user.sod_exception` visible en `ens report` ("un único responsable de seguridad", art. 13.3). Los tokens nunca aprueban.

### 4.10 G09: perfil `ens-medium`, `noust ens check` y `ens report`

`security.profile: standard | ens-medium`. El perfil no es magia: es **un conjunto de valores por defecto y de rechazos**,
definido en un solo módulo (`core/profiles.py`, regla 3), que se puede ver (`noust config show security`) y comprobar. Cambiar
de perfil exige `security.config`, sudo, aprobación y deja `security.profile.change`.

| Clave | estándar | `ens-medium` |
|---|---|---|
| `auth.master_token` | `full` | `break_glass` |
| `auth.mfa` | `required` salvo `viewer` | `required` para todos |
| `auth.lockout` | por IP, temporal | por cuenta, manual (+ IP) |
| `auth.session.*` | 720 min / 24 h | 15 min bloqueo, 60 cancelación, 8 h |
| `auth.password.min_length`, `max_age_days` | 12, 0 | 14, 365 |
| `auth.token.max_days`, `legacy_tokens_until` | sin límite | 90, fecha |
| `audit.sinks` | fichero | fichero + (journald o syslog) obligatorio |
| `audit.host_activity` | `off` | `mutations` |
| `web.tls` | obligatorio fuera de loopback | siempre; certificado del operador; autofirmado rechazado |
| `web.allow_ip` | opcional (central: privadas) | explícita |
| `--insecure-http` | permitido con la marca | rechazado |
| `backup.encryption` | opcional | obligatorio en destinos remotos |
| `fleet.ssh.*` | por defecto de OpenSSH | lista fija |
| `security.approvals` | apagado | activo |
| `security.cli_policy` | `audit` | `reason` |

`noust ens check [--json|--md] [--node N]` (también `GET /api/compliance/ens`, y agregado por nodo en la central) evalúa, sin
cambiar nada y con la misma infraestructura de `noust health`:

| Id | Comprobación | Medida |
|---|---|---|
| ENS-ACC-01 | Existen ≥1 `security` y ≥1 `admin`; ninguna persona con funciones incompatibles; excepciones listadas | op.acc.3 |
| ENS-ACC-02 | MFA confirmada en el 100 % de cuentas humanas | op.acc.6.r2, r8 |
| ENS-ACC-03 | Token maestro en `break_glass` y sin uso desde la última rotación | op.acc.6.r8 |
| ENS-ACC-04 | Tokens con propietario y caducidad ≤ máximo; ninguno heredado sin adoptar | op.acc.1.3 |
| ENS-ACC-05 | Última revisión de accesos ≤ 90 días | op.acc.4.4 |
| ENS-SES-01 | Bloqueo por inactividad ≤ política | mp.eq.2 |
| ENS-LOG-01 | Auditoría activa, cadena íntegra (`audit verify`), sin huecos | op.exp.8 |
| ENS-LOG-02 | Al menos un sink externo configurado y sin retraso > 5 min | op.mon.3.1, op.exp.8.r4 |
| ENS-LOG-03 | Retención ≥ política; espacio por debajo del 80 % | op.exp.8.r3 |
| ENS-LOG-04 | Reloj sincronizado (`timedatectl show -p NTPSynchronized`, o chrony) | op.exp.8.r2 |
| ENS-LOG-05 | Última revisión de la auditoría ≤ 7 días | op.exp.8.r1 |
| ENS-CRY-01 | Certificado del operador, algoritmo y SAN correctos, caducidad > 30 días | mp.com.2.r1, mp.com.3.r2 |
| ENS-CRY-02 | Sin `--insecure-http`; TLS ≥ 1.2 con suites AEAD (handshake propio) | mp.com.2 |
| ENS-CRY-03 | Central sellada (si es hub); permisos 0600/0700 de secretos | op.exp.10 |
| ENS-NET-01 | Allow-list explícita; consola no en `0.0.0.0` sin ella | mp.com.1, op.acc.4.5 |
| ENS-NET-02 | (por nodo) `sshd`: `PermitRootLogin`, `PasswordAuthentication`, cuenta de túnel | op.acc.6.r9, op.exp.2 |
| ENS-NET-03 | (por nodo) cortafuegos activo; puertos en escucha frente a los autorizados; sin BD publicada | mp.com.1, op.mon.3.r2 |
| ENS-BAK-01 | Copia ≤ 24 h, verificada ≤ 7 días, cifrada, destino en otro lugar | mp.info.6, mp.si.2 |
| ENS-UPD-01 | Noust al día; (por nodo) parches de seguridad pendientes y reinicio necesario | op.exp.4 |
| ENS-MON-01 | `noust monitor` activo; presencia de agente de detección, fail2ban, auditd en el nodo | op.mon.1 |
| ENS-INV-01 | Toda aplicación con `owner` y `criticality` | op.exp.1.1 |
| ENS-CHG-01 | Aprobaciones activas para las acciones sujetas | op.exp.5.4, op.acc.3 |

Cada fila devuelve `estado` (`ok`, `aviso`, `fallo`, `n/a`), `evidencia` (valores y rutas, sin secretos),
`medida` y `remediación`. `noust ens report` añade los **indicadores** de op.mon.2 (MFA %, tiempo medio de revocación,
revisiones, parches, copias verificadas, bloqueos, uso de *break-glass*, disponibilidad por nodo) y una huella SHA-256 del
informe, para adjuntar a la Declaración de Aplicabilidad y al informe anual (art. 32).

### 4.11 G10 a G20: criterios de aceptación

| Id | Cambio | Criterio de aceptación |
|---|---|---|
| G10 | Login sin fugas | `GET /api/auth/session` anónimo solo devuelve `authenticated=false` y `login_label`; login uniforme en cuerpo y tiempo; sin "intentos restantes"; prueba de igualdad de respuesta entre los tres fallos |
| G11 | Flota | Nodo audita `actor` y `role` de la central; concesiones por nodo denegadas antes de reenviar; `noust fleet authorize --tunnel-user noust-tunnel` crea cuenta sin shell (backlog 45); `noust node rekey`; `noust fleet register export` (nodo, huella de host, clave, token, restricciones, fecha); `X-Noust-Request-Id` en ambos registros |
| G12 | Copias | Con `backup.encryption: required` no se sube nada sin cifrar; cifrado AES-256-CBC + HMAC-SHA256 reutilizando `core/sealing.py` generalizado a flujos (no hay AEAD en `openssl enc`); sidecar con MAC; `backup.verify_schedule` semanal con evento `backup.verify` y su resultado; `noust central backup` (copia consistente de `/data` con la API de copia de SQLite, cifrada, con manifiesto) |
| G13 | Estado del servidor | `noust health` y la ficha de nodo muestran parches de seguridad pendientes (apt, dnf o zypper por el runner), `reboot-required`, hora, `sshd -T` efectivo, cortafuegos y puertos en escucha, fail2ban/auditd/agente; sin acciones destructivas (backlog 32 y 46) |
| G14 | Compilaciones | Instalar y compilar como el usuario de la aplicación en `systemd-run` con sandbox (backlog 44); escenario del arnés: un `postinstall` que escribe en `/root` o lee `/etc/noust` falla |
| G15 | Inventario | `owner`, `criticality`, `classification` en `apps` y `nodes`; `noust inventory export --format json,csv`; SBOM CycloneDX de Noust y, opcional, por aplicación |
| G16 | Cadena de suministro | `SECURITY.md`; SBOM por release; `pip-audit` y `npm audit` bloqueantes en CI; `.github/dependabot.yml`; imagen por digest en `compose.yaml`; firma de artefactos (sigstore/cosign) y atestaciones PEP 740; ZAP baseline en CI |
| G17 | Incidentes | `noust incident freeze` copia auditoría, sesiones, tokens (sin hashes), configuración saneada y extracto de journal a un paquete 0600 con manifiesto SHA-256, cierra túneles y revoca sesiones (opcional); evento `incident.freeze` |
| G18 | Origen del código | `apps.allowed_sources` (patrones de host y organización) validado en el punto único de fuentes; opción de desplegar solo SHA fijado o etiqueta firmada; previews con `.env` propio en el perfil; campo `reason` en `apps create/update/delete` y en `POST /api/jobs/*` |
| G19 | Retención | `retention.jobs_days`, `deploy_logs_days`, `observations_days`, `sessions_days` y purga con evento; base de datos de usuarios con datos mínimos; documento de datos personales |
| G20 | Plantillas | HSTS por sitio (opt-in en estándar, activo con TLS en el perfil), `server_tokens off`, retirada de `X-XSS-Protection`; prueba de plantilla |

### 4.12 Decisiones abiertas para el dueño

1. **PEN de IANA** para `noust@<PEN>` (gratuito, unos días): sin él, `enterprise_id` debe ponerlo el operador.
2. **Números por defecto** del perfil (15 min de bloqueo, 60 de cancelación, 8 h, 5 fallos, contraseña de 14, 365 días,
   90 de tokens, 365 de retención): propuestos, no impuestos por el RD; los fija la política de la organización.
3. **Alcance de `operator`**: incluye reinicios y renovación de certificados (hoy `admin`). Reduce privilegio de quien
   despliega a diario; el coste es un cambio de comportamiento de los tokens `deploy`.
4. **`--reason` obligatorio en la CLI** en el perfil (referencia de cambio): fricción alta pero es la evidencia de op.exp.5.1.
5. **Central hub en contenedor**: no hay journald; el sink `stdout` + syslog TLS directo son la vía. Confirmar que el
   operador enruta el log del contenedor.
6. **Verificar contra los textos oficiales del CCN** (no accesibles desde este entorno): estado de Ed25519, de
   HMAC-SHA1 en OTP, de RSA-2048/3072 y de las suites TLS en CCN-STIC 807/221; la CCN-STIC 804 y la 808 vigentes.
7. **MFA**: TOTP ya (R2). Passkeys (backlog 48) como factor preferente en 3.2; si el auditor prefiriese factor no
   suplantable, subirlo.
8. **Excepción de un único responsable de seguridad**: aceptar la excepción documentada del art. 13.3 en instalaciones
   pequeñas, o exigir dos cuentas `security`.
9. **`DELETE /api/jobs/cleanup`** borra historial de actividad y hoy lo puede cualquier `admin`: moverlo a `security` o
   quitarlo (op.exp.8.r4).

---

## 5. Esquema de la guía de bastionado que pedirá el auditor (`docs/ENS.md`, entregable c)

Objetivo: que un auditor de categoría MEDIA (Anexo III, 2.2; art. 31 cada dos años y art. 38 para certificar) encuentre, por
medida, **qué hace Noust, qué debe hacer la organización y con qué evidencia se demuestra**. Va en español (con versión
en inglés después), sin promesas de certificación: se certifica el sistema de la organización, no un componente.

1. **Alcance y límites.** Qué es Noust dentro del sistema (componente de administración con capacidad de root); qué no
   garantiza; no figura en el CPSTIC y cómo se justifica (op.pl.5, art. 28.3); aviso: los umbrales numéricos los fija la
   política de la organización.
2. **Responsabilidad compartida.** Tabla Noust / organización / proveedor de VPS (derivada de la sección 3 de este
   documento), incluidos op.ext.1, op.ext.2, op.nub.1.
3. **Arquitectura de referencia** (op.pl.2). Diagrama de zonas: central en zona de gestión de la empresa, nodos, SIEM, NTP,
   destino de copias fuera de sitio, VPN. **Tabla de flujos autorizados** (origen, destino, puerto, protocolo, autenticación,
   cifrado, quién lo aprobó): mp.com.1.2 y op.ext.4.2.
4. **Emplazamiento de la central** (mp.if.1 a .7, op.cont.1): CPD o sala controlada, SAI, sin NAS doméstico; requisitos y por
   qué.
5. **Puesta en marcha con el perfil `ens-medium`** (día 0): instalación desde repositorio firmado; certificado del operador;
   allow-list explícita; `security.profile`; primer usuario y `security`/`admin`/`auditor`; MFA de todos; sellado y
   custodia de la passphrase; sinks; NTP; copias cifradas; `noust ens check` en verde; captura del informe como línea base.
6. **Identidad y acceso** (op.acc.*). Roles y correspondencia con RSEG/RSIS; alta y baja (procedimiento y plantilla);
   revisión trimestral con atestación; contraseñas y MFA; **procedimiento de reactivación de cuenta** (op.acc.6.8 exige que se
   describa en la documentación); *break-glass* (custodia del token en sobre cerrado o caja fuerte, uso, rotación posterior);
   cuentas de servicio y tokens; excepciones de incompatibilidad y su compensación.
7. **Registro de la actividad** (op.exp.8). Catálogo de eventos (generado del código), formato v2, sinks, retención (política de
   la organización), integridad y `noust audit verify`, revisión semanal (procedimiento y evidencia), sincronización de reloj,
   ejemplos de integración (rsyslog, Wazuh, Elastic, Splunk) y **casos de uso de detección para el SIEM**: fuerza bruta,
   bloqueo de cuenta, uso de *break-glass*, cambio de rol o de configuración de seguridad, alta de nodo, borrado masivo,
   `host.exec` fuera de horario, `audit.gap`/`audit.degraded`, desconexión de sink.
8. **Criptografía y claves** (op.exp.10, mp.com.2/.3, mp.si.2). Inventario de algoritmos y su estado frente al CCN (anexo B),
   ciclo de vida (generación, custodia, rotación, archivo, destrucción) de cada clave de Noust, calendario de rotación,
   pérdida de la passphrase (no hay recuperación).
9. **Cambios y despliegues** (op.exp.5, mp.sw.2). Repositorios protegidos (rama principal, revisión obligatoria, commits
   firmados), previews aisladas, aprobación, `HealthGate` como prueba de aceptación, vuelta atrás, referencia de cambio
   (`--reason`), **actualización de Noust** (canal, verificación de paquete y firma, prueba en una central de preproducción,
   ventana, marcha atrás) y parches de los nodos (op.exp.4).
10. **Copias y continuidad** (mp.info.6, op.cont.1). Qué copiar (`/data` de la central, destinos), cifrado, fuera de sitio,
    pruebas de restauración (frecuencia y evidencia), RTO/RPO de la central, recuperación de la central desde cero.
11. **Monitorización y respuesta a incidentes** (op.mon.*, op.exp.7, op.exp.9). Qué vigila `noust monitor` y qué no; IDS/EDR,
    fail2ban y auditd que aporta la organización; procedimiento de incidente; `incident freeze`; notificación (art. 33:
    CCN-CERT o INCIBE-CERT); registro de la gestión de incidentes.
12. **Datos personales** (mp.info.1). Inventario de lo que guarda Noust (usuarios, correo, IP en auditoría), base jurídica,
    retención y minimización.
13. **Evidencias para la auditoría.** Tabla medida a evidencia a comando/endpoint a periodicidad (`ens report`, `audit verify`,
    `user review`, `health`, `config show`, `token list`, `backup verify`, `node list`). Lista de documentos que debe tener la
    organización: política (art. 12), normativa, procedimientos, análisis de riesgos, Declaración de Aplicabilidad
    (art. 28.2), inventario, plan de auditorías, informe de auditoría bienal (art. 31), certificado de conformidad (art. 38).
14. **Calendario operativo.** Diario (alertas), semanal (revisión de la auditoría, parches), mensual (rotación de tokens
    caducados, prueba de restauración), trimestral (revisión de accesos), anual (análisis de riesgos, categorización, DdA,
    ejercicio de *break-glass*), bienal (auditoría).
15. **Anexos.** Plantilla de Declaración de Aplicabilidad con una fila por medida y estado; procedimientos plantilla (alta y
    baja, revisión de accesos, reactivación de cuenta, uso de *break-glass*, restauración de la central, rotación de claves,
    respuesta a incidente); cuestionario "lo que preguntará el auditor".

Guías del CCN a citar (verificar la edición vigente de cada una antes de publicar): CCN-STIC 801 (responsables y funciones),
802 (auditoría), 803 (valoración y categorización), 804 (medidas de implantación), 807 (criptología de empleo, mayo 2022) y
221 (mecanismos criptográficos autorizados), 808 (verificación del cumplimiento), 809 (declaración y certificación de
conformidad), 817 (gestión de ciberincidentes), 823 (entornos cloud) y, para perfiles de cumplimiento, 890 (requisitos
esenciales de seguridad), 892 (NIS2), 887 (AWS) y 881 (universidades): ninguno es específico de una flota de VPS, pero el de
NIS2 puede aplicar si la organización es entidad esencial o importante. Fuente de la lista:
<https://angelortegacastro.com/ens-instrucciones-tecnicas-seguridad-ccn-stic/> y resultados de búsqueda del CCN (no se pudieron abrir las guías: ver 1.2).

---

## Anexo A. Concordancia de numeración (RD 3/2010 y CCN-STIC 804 de 2017 frente al RD 311/2022)

El encargo y la CCN-STIC 804 que pude leer usan la numeración del RD 3/2010 (`op.acc.1-7`, `op.exp.1-11`, `mp.info.1-9`, con
`op.ext.9`, `mp.if.9`, `mp.per.9`, `mp.eq.9`, `mp.com.9` y `mp.s.9` "medios alternativos" y `mp.s.8` "denegación de
servicio", según el índice de la 804). El RD 311/2022 las reordena; **el auditor auditará contra el nuevo**.

| RD 3/2010 (y CCN-STIC 804 de 2017) | RD 311/2022 (BOE-A-2022-7191) |
|---|---|
| `op.acc.5` Mecanismo de autenticación | `op.acc.5` (usuarios externos) y `op.acc.6` (usuarios de la organización) |
| `op.acc.6` Acceso local | Absorbido: `op.acc.5.7`/`.6.7` (información mínima), `.8` (intentos), `.9` (derechos y obligaciones), `R5` (registro y último acceso) |
| `op.acc.7` Acceso remoto | `op.acc.4.5` (política de acceso remoto), `op.acc.6.r8` (doble factor desde zonas no controladas), `op.acc.6.r9` |
| `op.exp.8` Registro de la actividad de los usuarios | `op.exp.8` (con R1 revisión) |
| `op.exp.10` Protección de los registros de actividad | `op.exp.8.r2` (reloj), `.r3` (retención), `.r4` (control de acceso) |
| `op.exp.11` Protección de las claves criptográficas | `op.exp.10` |
| `op.ext.9` Medios alternativos (y homólogas `.9`) | `op.cont.4` Medios alternativos |
| `mp.info.4` Firma electrónica | `mp.info.3` |
| `mp.info.5` Sellos de tiempo | `mp.info.4` (N.A. a MEDIA) |
| `mp.info.6` Limpieza de documentos | `mp.info.5` |
| `mp.info.9` Copias de seguridad | `mp.info.6` |
| `mp.s.8` Protección frente a la denegación de servicio | `mp.s.4` |
| (no existía) | `op.nub.1` Protección de servicios en la nube; `op.mon.3` Vigilancia; `op.pl.5` R2; `mp.com.4` reformulado |

Las correspondencias `op.acc.*`, `op.exp.8/10/11`, `op.ext.9`, `mp.info.4/5/6/9` y `mp.s.8` se han contrastado con el índice de la
CCN-STIC 804 (2017) y con el Anexo II vigente. No he contrastado dónde cae el antiguo `mp.info.3` (cifrado de la
información); según el articulado nuevo, el cifrado está en `mp.si.2` (soportes) y en `mp.com.2` (comunicaciones).

## Anexo B. Inventario criptográfico de Noust hoy y estado frente al CCN

Estado CCN: "ok" = mecanismo estándar que las guías del CCN aceptan sin controversia (SHA-256, HMAC-SHA256, AES-256, ECDHE con AES-GCM);
**[verificar]** = no he podido leer el texto vigente de CCN-STIC 807/221 (ver 1.2).

| Uso | Mecanismo y parámetros | Dónde | Estado CCN | Acción |
|---|---|---|---|---|
| TLS de la consola | El de OpenSSL/uvicorn por defecto; versión mínima y suites **no fijadas** | `web/server.py:1841` | [confirmar] | G07: TLS ≥ 1.2, ECDHE + AEAD fijados |
| Certificado autofirmado | RSA-2048, SHA-256, 3650 días, sin SAN | `managers/cert_manager.py:1119` | RSA < 3000 bits "legacy" hasta 2025/2026 [verificar] | G07: ECDSA P-384 con SAN |
| Certificados de sitios (certbot) | El tipo de clave por defecto de la versión de certbot instalada; Noust no pasa `--key-type` | `managers/cert_manager.py:969` | [verificar] | G07: `ssl.key_type`/`ssl.elliptic_curve` hacia certbot |
| Nginx y Apache | TLS 1.2/1.3; ECDHE-(ECDSA/RSA)-AES128/256-GCM; sin HSTS | `templates/nginx`, `templates/apache` | ok | G07 (una definición), G20 |
| Túnel SSH de flota | Ed25519 por nodo; `ssh` con sus algoritmos por defecto | `fleet/keys.py`, `fleet/tunnels.py` | Ed25519 [verificar] | G07, G11 |
| Sellado de secretos | scrypt (N=2^15, r=8, p=1, sal 32 B) + AES-256-CBC (openssl, PBKDF2 1 iteración sobre clave de 256 bits) + HMAC-SHA256 encrypt-then-MAC | `core/sealing.py` | AES-256, SHA-256 ok; scrypt y CBC [verificar] | Mantener; reutilizar en G12 |
| Hash de token maestro, API y de flota | SHA-256 salado con la clave de firma (tokens de 256 bits) | `web/auth.py:2007` | ok | — |
| Hash de contraseñas (G01) | scrypt N=2^15, r=8, p=1, sal 16 B, pimienta HMAC-SHA256 | nuevo | [verificar] | Si el CCN no lo cita, PBKDF2-HMAC-SHA256 ≥ 600 000 iteraciones como alternativa |
| TOTP | HMAC-SHA1, 6 dígitos, 30 s (RFC 6238) | `core/totp.py` | [verificar] | Opción SHA-256 |
| Códigos de respaldo | 32 bits, SHA-256 salado | `web/auth.py:2193` | — | — |
| Webhooks de forja | HMAC-SHA256 | `web/api/hooks.py` | ok | — |
| Copias remotas cifradas | rclone `crypt` (XSalsa20 + Poly1305, contraseña y sal con scrypt) | `managers/backup_destinations.py` | [verificar] | G12: AES-256 + HMAC-SHA256 con primitivas del sellado |
| Integridad de copias | SHA-256 en un sidecar sin MAC | `managers/backup_manager.py:702` | ok (sin autenticación) | G12: MAC |
| Cadena de auditoría (G03) | HMAC-SHA256 con clave propia | nuevo | ok | — |
| Sink syslog TLS (G04) | TLS ≥ 1.2, validación de certificado, TLS mutuo | nuevo | ver TLS | Misma política que la consola |

## Anexo C. Referencias

- RD 311/2022, de 3 de mayo (BOE-A-2022-7191): <https://www.boe.es/eli/es/rd/2022/05/03/311/con>. Texto consolidado obtenido de
  <https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-2022-7191/texto> (cabecera `Accept: application/xml`).
  Artículos citados: 2.3, 11, 12, 13.3, 15, 16, 17, 19, 20, 21, 22, 23, 24, 25, 26, 28, 30, 31, 32, 33, 38; Anexo I (categorías), Anexo
  II (medidas), Anexo III (auditoría); disposición transitoria única.
- CCN-STIC 804, junio 2017: <https://www.aec.es/wp-media/uploads/DPD-00268.SEG-GUI-006-804_medidas_de_implantacion_del_ens.pdf>
  (apartados sobre op.acc.1 a .7, op.exp.8, .10, .11 y mp.eq.2 del RD 3/2010).
- RFC 5424 (syslog): <https://www.rfc-editor.org/rfc/rfc5424.html>; RFC 5425 (TLS para syslog):
  <https://www.rfc-editor.org/rfc/rfc5425>; RFC 6587 (syslog sobre TCP): <https://www.rfc-editor.org/rfc/rfc6587>; RFC 5612
  (número de empresa reservado para documentación); registro de PEN: <https://pen.iana.org/>.
- RFC 6238 (TOTP): <https://www.rfc-editor.org/rfc/rfc6238>. NIST SP 800-63B: <https://pages.nist.gov/800-63-3/sp800-63b.html>.
- CCN-STIC 807 (mayo 2022) y 221: <https://inza.blog/2025/09/01/robustez-de-las-claves-asimetricas-recomendadas-por-el-ccn/>,
  <https://www.ccn.cni.es/index.php/en/menu-news-ccn-en/970-nueva-guia-ccn-stic-221-sobre-mecanismos-criptograficos-autorizados-por-el-ccn>
  (segunda mano; no se han podido abrir los originales).
- ITS y guías: <https://angelortegacastro.com/ens-instrucciones-tecnicas-seguridad-ccn-stic/>.
- Repositorio (todo en `/home/yago/Documents/GitHub/wasm`): `CLAUDE.md`, `docs/security.md`, `docs/CENTRAL.md`, `docs/console.md`, `docs/MONITOR.md`,
  `docs/releases.md`, `panel/openapi.json`, y el código citado en cada fila.
- Memoria del proyecto: backlog 30, 30b, 32, 33, 44, 45, 46, 47 y 48 (`wasm-feedback-backlog.md`).

## Anexo D. Cómo reproducir las comprobaciones más delicadas

```bash
# Texto oficial (todo el RD, Anexo II incluido)
curl -H 'Accept: application/xml' \
  https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/BOE-A-2022-7191/texto > boe.xml

# H1: el token maestro entra como Bearer sin segundo factor ni sudo
grep -n 'master_token_generation\|ELEVATION_EXEMPT_TYPES' src/noust/web/auth.py src/noust/web/api/deps.py

# H2: nadie configura logging, así que noust.audit no llega a ningún sitio (confirmar con uvicorn instalado)
grep -rnE 'basicConfig|addHandler|dictConfig|log_config' src/noust        # sin resultados
grep -rn 'getLogger("noust.audit")' src/noust

# H3: retención del registro
grep -n 'AUDIT_MAX_BYTES\|AUDIT_BACKUPS' src/noust/web/auth.py

# Cuántos sitios registran auditoría hoy
grep -rn 'audit\.record(' src/noust --include=*.py | wc -l
```

Comprobación pendiente para H2 (no reproducible aquí por falta de `uvicorn`): arrancar `noust web start` con `NOUST_WEB_STATE_DIR`
temporal, ejecutar una consulta SQL desde la consola y comprobar si `journalctl -u noust-web` o el fichero de log del proceso
contiene una línea `query engine=...`.
