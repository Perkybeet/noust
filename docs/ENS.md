# Noust y el Esquema Nacional de Seguridad (categoría MEDIA)

## English summary

This document is for organisations subject to Spain's **Esquema Nacional de Seguridad**
(ENS, Real Decreto 311/2022), category **MEDIUM**, that use Noust to run their servers. It is
written in Spanish because its readers are Spanish auditors and security officers.

- **Noust is a component, not a certified system.** An ENS certificate is issued to the
  organisation's information system; Noust is not in the CCN's CPSTIC catalogue. What Noust
  does is enforce what a component can enforce and produce the evidence an auditor asks for.
- **`security.profile: ens-medium`** fixes, in one place every area reads
  (`noust.core.ens.profile`), the values category MEDIUM expects: 15-minute idle lock and
  8-hour sessions, lockout after 5 failures, 14-character passwords, 90-day API tokens, a
  365-day audit retention, four-eyes approvals, a `--reason` for every CLI change, the master
  token restricted to account recovery, backups encrypted before they leave, an operator TLS
  certificate for the console. Turn it on with `noust config set security.profile ens-medium`.
- **`noust ens check`** compares the server with the profile, check by check, each mapped to its
  RD 311/2022 measure, and exits 0/1/2 like a monitoring check. **`noust ens report`** writes the
  evidence bundle (JSON and Markdown, hashed). The console shows both under Settings > Security >
  Compliance (`GET /api/ens/check`, `GET /api/ens/report`).
- Evidence commands: `noust audit verify|review|export`, `noust ens access-review`,
  `noust ens inventory`, `noust backup verify`, `noust central backup`, `noust incident freeze`.
- What stays the organisation's (policy, roles and their appointment, risk analysis, personnel,
  facilities, providers, the SIEM, the incident process) is listed in section 7.

---

## 0. Alcance y límites

Este documento responde, medida a medida del Anexo II del RD 311/2022, a tres preguntas del
auditor: **qué hace Noust, qué tiene que hacer la organización y con qué evidencia se demuestra**.

- **Noust es un componente de administración con capacidad de root** sobre los servidores que
  gestiona: despliega código (desplegar es ejecutar código), escribe unidades de systemd y
  configuración de nginx o Apache, gestiona certificados, bases de datos y copias. Quien controla
  Noust controla esos servidores. El sistema de información que se certifica es el de la
  organización; Noust es una pieza dentro de él.
- **Noust no figura en el CPSTIC** ni está certificado (op.pl.5). La organización debe
  justificarlo en su análisis de riesgos como componente de administración y no como producto de
  la arquitectura de seguridad; si el auditor lo cataloga como tal, la Declaración de
  Aplicabilidad recoge la medida compensatoria (art. 28.3): este documento, el SBOM de cada
  versión, el código abierto (AGPL-3.0), las pruebas publicadas en CI y `SECURITY.md`.
- **Los números no los fija el RD**: los fija la política de la organización (op.acc.6.4,
  mp.eq.2.1 "tiempo prudencial", op.exp.8.r3.1). Los valores del perfil son valores por defecto
  razonables; la organización puede endurecerlos, nunca relajarlos con el perfil activo.
- La numeración es la del **RD 311/2022**; la concordancia con la del RD 3/2010 (y la CCN-STIC 804
  de 2017) está en el Anexo A. Las referencias a guías CCN-STIC marcadas **[verificar]** deben
  contrastarse con la edición vigente antes de citarse en un informe (ver sección 8).

## 1. Responsabilidad compartida

| Ámbito | Noust | Organización | Proveedor de la VPS / CPD |
|---|---|---|---|
| Marco organizativo (org.1-4) | Roles técnicos mapeables a RSEG, RSIS y supervisión; aviso de uso con aceptación registrada | Política, normativa, procedimientos, designación de responsables (art. 13), autorización de sistemas y enlaces | - |
| Planificación (op.pl) | Arquitectura de flota, SBOM, este documento | Análisis de riesgos, arquitectura propia, adquisición, capacidad | - |
| Control de acceso (op.acc) | Cuentas, un rol por cuenta, MFA, bloqueo, sesiones, tokens con dueño y caducidad, cuatro ojos, revisión de accesos | Lista de personas autorizadas, altas y bajas, política de contraseñas y de acceso remoto, custodia del token maestro | - |
| Explotación (op.exp) | Inventario de aplicaciones, perfil y deriva, parches pendientes, cambios con referencia, auditoría con cadena y envío, incidentes con evidencias | Procedimientos de parcheo y cambio, antimalware/EDR, SIEM, gestión de incidentes y notificación | Parches del hipervisor |
| Servicios externos (op.ext, op.nub) | Enlace central-nodo autorizado en el nodo, restringido y registrado | Acuerdos de nivel de servicio, proveedores con certificado ENS | Certificación ENS del servicio (op.nub.1) |
| Continuidad (op.cont) | Copias verificadas, copia cifrada de la central | Análisis de impacto, RTO/RPO, sitio alternativo | Infraestructura |
| Monitorización (op.mon) | `noust ens check`, comprobaciones de bastionado, indicadores, eventos con id estable | IDS/IPS, correlación, informe anual (art. 32) | - |
| Instalaciones, personal, equipos (mp.if, mp.per, mp.eq.1/3/4) | - | Todo | CPD del proveedor |
| Comunicaciones (mp.com) | TLS 1.2+ con suites AEAD, certificado del operador, lista de acceso, túneles SSH con algoritmos fijados | Perímetro, VLAN/VPN de gestión, segmentación | Red |
| Soportes e información (mp.si, mp.info) | Copias cifradas y autenticadas, clasificación por aplicación, minimización | Custodia, transporte, borrado seguro, calificación | Borrado de discos |
| Software y servicios (mp.sw, mp.s) | Compilaciones sin privilegios, previews, HealthGate, plantillas nginx endurecidas, CSP estricta | Desarrollo seguro de sus aplicaciones, pruebas de penetración | - |

## 2. El perfil `ens-medium`

```bash
noust config set security.profile ens-medium   # requiere el rol security en la consola
noust ens profile                               # qué fija el perfil y cuál está activo
```

El perfil no es un interruptor de funciones: es un conjunto de valores y de rechazos definido en
un único módulo (`src/noust/core/ens/profile.py`) que leen el inicio de sesión, la CLI, las
aprobaciones, la auditoría, las copias y `noust ens check`. Un nombre mal escrito se interpreta
como `ens-medium` (falla hacia lo estricto). Cambiarlo queda en la auditoría.

| Valor | Estándar | `ens-medium` | Medidas |
|---|---|---|---|
| Inactividad de sesión (`auth.session.idle_minutes`) | 30 min | 15 min como máximo | mp.eq.2 |
| Duración absoluta de sesión (`auth.session.absolute_hours`) | 12 h | 8 h como máximo | mp.eq.2 |
| Modo sudo, «Confirma que eres tú» (`auth.sudo.*`) | abierto 15 min tras la última acción, 120 min como máximo, un factor | abierto 10 min tras la última acción, 30 min como máximo, contraseña y código (como máximo) | op.acc.6 |
| Bloqueo de cuenta (`auth.lockout.*`) | 5 fallos / 15 min | 5 fallos / 15 min como mínimo | op.acc.6.8 |
| Longitud mínima de contraseña (`auth.password.min_length`) | 12 | 14 como mínimo | op.acc.6.r1 |
| Caducidad de tokens de API (`auth.tokens.max_days`) | opcional | obligatoria, 90 días como máximo | op.acc.1.3, op.acc.4 |
| Token maestro con cuentas creadas | *break-glass*: toda la consola, auditado | solo recuperar el acceso de una persona | op.acc.6.r8 |
| Retención de la auditoría (`audit.retention_days`) | 90 días de suelo | 365 días como mínimo | op.exp.8.r3 |
| Destino de auditoría fuera de la máquina | opcional | esperado (`ens check` lo exige) | op.mon.3.1, op.exp.8.r4 |
| Aprobación de cuatro ojos (`approval.enabled`) | desactivada | activa | op.acc.3, op.exp.5.4 |
| `--reason` en cada orden de la CLI que cambia algo | opcional | obligatorio (sin él, código 2) | op.exp.5.1 |
| Cifrado de copias que salen del servidor (`backup.encryption`) | opcional | obligatorio: se rechaza subir a un destino sin cifrar | mp.si.2 |
| Verificación de copias (`backup.verify_days`) | 7 días | 7 días como máximo, no desactivable | mp.info.6.r1 |
| Certificado de la consola | cualquiera | del operador (el autofirmado es un fallo en `ens check`) | mp.com.2.r1, mp.com.3.r2 |
| Lista de acceso a la consola (`web.ip_whitelist`, `NOUST_ALLOW_IP`) | opcional | explícita si escucha fuera de loopback | mp.com.1, op.acc.4.5 |

Ajustes de seguridad relacionados que no dependen del perfil:

- `security.allowed_sources`: orígenes de código permitidos (hosts, organizaciones o repositorios,
  y `local` para directorios del servidor). Vacío permite todo. Se comprueba donde se clasifica
  cualquier fuente antes de descargarla, así que afecta a despliegues, actualizaciones, previews y
  webhooks; un rechazo queda como `apps.source.denied`. Lo cambia el rol `security`.
- `auth.notice.text`: aviso de derechos y obligaciones que cada persona acepta tras entrar
  (op.acc.6.2, op.acc.6.9, mp.per.2.r1); cambiar el texto obliga a aceptarlo de nuevo.
- `audit.syslog`, `audit.journald`, `audit.enterprise_id`: destinos de la auditoría (RFC 5424
  por UDP, TCP o TLS con certificado de cliente; journald).

## 3. Matriz de cumplimiento

Columnas: **medida** | requisito (con los refuerzos que aplican a MEDIA) | cómo lo cumple Noust |
responsabilidad del operador | hueco residual. "N.A." indica que no aplica a MEDIA o a este tipo de
componente.

### 3.1 Marco organizativo

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| org.1 Política de seguridad | Política aprobada; roles y su designación (org.1.3) | Roles `security`, `admin`, `operator`, `auditor`, `viewer` que se corresponden con RSEG, administradores y operadores del RSIS, y supervisión | Aprobar la política (art. 12) y designar RINF, RSERV, RSEG, RSIS; RSEG independiente del RSIS (art. 13.3) | Ninguno en el componente |
| org.2 Normativa | Uso correcto e indebido; responsabilidades | Aviso de uso configurable (`auth.notice.text`) con aceptación registrada por persona y versión (`auth.notice.accept`) | Redactar la normativa y el texto del aviso | Ninguno |
| org.3 Procedimientos | Procedimientos de operación | Este documento, sección 5 y Anexo B (plantillas) | Adaptarlos, aprobarlos y aplicarlos | - |
| org.4 Autorización | Autorizar aplicaciones en producción (org.4.3) y enlaces (org.4.4) | El enlace central-nodo lo autoriza root en el nodo (`noust fleet authorize`); alta y baja de nodos sujetas a cuatro ojos en el perfil | Aprobación formal de cada nodo, aplicación y proveedor | La creación de una aplicación no requiere un segundo actor (solo las acciones equivalentes a root) |

### 3.2 Planificación (op.pl)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| op.pl.1 Análisis de riesgos (+R1) | Análisis semiformal | Modelo de amenazas en `docs/security.md`; escenarios en la sección 5.1 | MAGERIT/PILAR, revisión anual | Operador |
| op.pl.2 Arquitectura (+R1) | Documentar instalaciones, líneas de defensa, identificación y autenticación | Arquitectura de referencia (5.1); la central solo abre conexiones salientes; la consola de los nodos solo en loopback | Diagrama y sistema de gestión propios | - |
| op.pl.3 Adquisición | Proceso formal | SBOM CycloneDX por versión, `pip-audit` y `npm audit` en CI, Dependabot, `SECURITY.md` | Registrar la incorporación de Noust | Sin firma propia de artefactos (sigstore) todavía |
| op.pl.4 Capacidad (D MEDIO, +R1) | Previsión y monitorización | Métricas con retención horaria de 400 días, `noust health` | Estudio de capacidad | Sin previsión automática |
| op.pl.5 Componentes certificados | CPSTIC o certificados | **No certificado** | Justificación o medida compensatoria en la DdA (art. 28.3) | **No cerrable en código** |

### 3.3 Control de acceso (op.acc)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| op.acc.1 Identificación (+R1) | Identificador singular por persona y por perfil; lista de usuarios autorizados; bajas | Cuentas con un rol cada una (`noust user`); incompatibilidades por persona (`person_ref`); tokens con dueño; bajas que revocan sesiones y tokens; la lista en `noust ens access-review list` | Mantener la lista de personas autorizadas y decidir altas y bajas | - |
| op.acc.2 Requisitos de acceso | Recursos protegidos; derechos por decisión del responsable | Todo `/api` y `/ws` pasa por un punto único con un permiso declarado por ruta (`x-noust-permission`); una prueba falla si una ruta no lo declara | Decidir quién recibe qué rol | - |
| op.acc.3 Segregación de funciones | Concurrencia de dos personas en tareas críticas; quien autoriza distinto de quien usa | Roles incompatibles por persona (`admin`/`operator` con `security`, `auditor` con cualquiera); aprobación de cuatro ojos para lo equivalente a root, las escrituras en bases de datos, nodos, cambios de rol, altas de cuentas e invitaciones de recuperación (`noust approval`) | Repartir cuentas entre personas; excepción documentada si solo hay un responsable (`noust user exception add`, visible en el informe) | En instalaciones de una persona, la excepción es la medida compensatoria |
| op.acc.4 Gestión de derechos | Mínimo privilegio, necesidad de conocer, revisión periódica (.4.4), acceso remoto autorizado (.4.5) | Permisos por rol; techo por nodo para la central (`noust fleet access`); revisión con atestación (`noust ens access-review attest`, evento `access.review`, ENS-ACC-05 a 90 días) | Política de acceso remoto; hacer la revisión | - |
| op.acc.5 Autenticación (externos) | Como op.acc.6 | Mismos mecanismos | Si hay usuarios externos, R2 y R5 | N.A. salvo acceso de clientes |
| op.acc.6 Autenticación (organización) (+R1..R5, R8, R9) | Aceptación de obligaciones (.1/.2); credencial bajo control exclusivo (.3); mínima información previa (.7); bloqueo con intervención (.8); aviso tras el acceso (.9); doble factor (R2, R8); registro de éxito y fallo y último acceso (R5); acceso remoto (R9) | Alta por invitación de un solo uso; contraseña con política; MFA obligatoria (TOTP o passkey); secretos TOTP cifrados en reposo; login con errores uniformes y sin versión ni hostname; bloqueo por cuenta que exige `noust user unlock`; último acceso y fallos desde entonces tras entrar; aviso con aceptación; TLS y lista de acceso | Política de contraseñas; decidir el procedimiento de reactivación (5.4) | - |

### 3.4 Explotación (op.exp)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| op.exp.1 Inventario (+R4) | Inventario con naturaleza y responsable | Aplicaciones, sitios, servicios, bases de datos y nodos en el store; responsable, criticidad y clasificación por aplicación (`noust ens inventory`, exportable a JSON y CSV); SBOM de Noust | Inventario global y responsable de cada activo | Sin campos de inventario por nodo |
| op.exp.2 Configuración de seguridad | Sin cuentas por defecto; mínima funcionalidad; seguridad por defecto | Token aleatorio de 256 bits; consola en loopback; TLS obligatorio fuera de loopback; compilaciones sin privilegios (`noust app sandbox`); cuenta de túnel sin privilegios en los nodos; comprobaciones de bastionado (`noust server security checks`) | Bastionado del SO según la guía CCN-STIC de cada distribución | Aplicaciones anteriores a 3.1 compilan como root hasta activar el sandbox (ENS-BLD-01 lo señala) |
| op.exp.3 Gestión de la configuración (+R1) | Solo personal autorizado edita la configuración de seguridad (.3.6); verificación periódica | Las secciones `web`, `auth`, `security`, `audit`, `central`, `fleet`, `approval` solo las cambia `security`; `noust ens check` compara con la línea base del perfil (ENS-PRF-01) | Definir y aprobar la línea base | - |
| op.exp.4 Mantenimiento (+R1) | Analizar y priorizar parches; solo personal autorizado | Actualizaciones pendientes, de seguridad marcadas, reinicio necesario, servicios con librerías viejas y fin de vida del SO (`noust server`, ENS-UPD-01) | Procedimiento de parcheo; central de preproducción | - |
| op.exp.5 Gestión de cambios | Referencia de cambio (.5.1); aprobación de cambios de riesgo alto (.5.4); pruebas de aceptación | `--reason` obligatorio en la CLI; aprobación de cuatro ojos; cada despliegue con id, commit, disparador y actor; HealthGate y vuelta atrás; orígenes de código permitidos | Qué es un cambio de riesgo alto y quién lo aprueba | - |
| op.exp.6 Código dañino (+R1, R2) | Prevención; antimalware en servidores | Compilaciones sin privilegios en sandbox; `noust monitor` señala procesos sospechosos (solo informa) | Antimalware/EDR en los servidores | Noust no es un antimalware |
| op.exp.7 Gestión de incidentes (+R1, R2) | Proceso; medidas urgentes: aislar, recoger evidencias, proteger registros | `noust incident freeze`: paquete de evidencias con manifiesto SHA-256 cuyo hash queda en la auditoría enviada fuera, y bloqueo de la consola (solo el token maestro entra); `noust fleet deauthorize` revoca una central | Procedimiento de incidentes, notificación (art. 33: CCN-CERT; INCIBE-CERT para el sector privado) | - |
| op.exp.8 Registro de actividad (+R1..R4) | Usuario, fecha, objeto, tipo y resultado (.8.1); revisión (R1); reloj e integridad (R2); eventos y retención documentados (R3); acceso restringido (R4) | Catálogo cerrado de eventos (`noust audit events --markdown`); actor con persona y rol; cadena HMAC y `noust audit verify`; envío a journald y syslog RFC 5424; retención por tiempo; CLI auditada con identidad del SO; libro de acciones del anfitrión; revisión con atestación (`noust audit review`); reloj en las comprobaciones | Activar el envío a su SIEM; NTP; revisar semanalmente | Root en la máquina puede recalcular la cadena local: la garantía es la copia fuera de la máquina |
| op.exp.9 Registro de incidentes | Evidencias con valor jurídico | Paquete de `incident freeze` con manifiesto y hash en la auditoría | Herramienta de gestión de incidentes (p. ej. LUCIA) | - |
| op.exp.10 Protección de claves (+R1) | Ciclo de vida; algoritmos autorizados | Anexo de claves (5.6): claves en ficheros 0600 o selladas; rotación de claves de nodo (`noust node rekey`); TOTP cifrado; copias con MAC | Generación y custodia de la passphrase de sellado y de la del respaldo de la central; rotación | Estado CCN de Ed25519, scrypt y AES-CBC **[verificar]** |

### 3.5 Servicios externos, nube y continuidad

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| op.ext.1, op.ext.2 | Acuerdos y gestión diaria | Señal de disponibilidad por nodo en la vista de flota | ANS y su seguimiento | Operador |
| op.ext.4 Interconexión | Autorización previa y documentación | Autorizado en el nodo por root; clave restringida a reenviar un puerto (`restrict,port-forwarding,permitopen`); host key fijada; cambio de host key corta el túnel y se audita; techo por nodo | Aprobar y documentar cada nodo | Sin exportación del registro de interconexiones como documento |
| op.nub.1 | Servicios en la nube certificados | N.A. al producto | Proveedores de VPS con certificado ENS de categoría igual o superior | Operador |
| op.cont.1 Análisis de impacto | Requisitos de disponibilidad | Si la central cae, los nodos siguen sirviendo; `noust central backup` cifrado y verificable | Análisis de impacto, RTO/RPO | Operador |

### 3.6 Monitorización (op.mon)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| op.mon.1 Detección de intrusión (+R1) | Herramientas basadas en reglas | fail2ban (instalación y estado desde Noust), eventos de auditoría con id estable para reglas del SIEM | IDS/IPS (Wazuh, CrowdSec, Suricata...) | Noust no es un IDS |
| op.mon.2 Métricas | Indicadores | Indicadores de `noust ens report`: MFA, revisiones, *break-glass*, bloqueos, copias verificadas, parches, bastionado, flota, inventario | Informe anual (art. 32) | - |
| op.mon.3 Vigilancia (+R1, R2) | Recolección automática, correlación, superficie de exposición | Envío continuo de la auditoría; `noust ens check` y las comprobaciones de bastionado (puertos en escucha frente al cortafuegos, sshd efectivo, Docker que publica puertos) | SIEM y correlación | - |

### 3.7 Medidas de protección (mp)

| Medida | Requisito | Cómo lo cumple Noust | Operador | Hueco residual |
|---|---|---|---|---|
| mp.if.1-7 Instalaciones | Áreas controladas, energía, incendios | N.A. | La central y sus copias en CPD o sala controlada (5.2) | Operador |
| mp.per.1-4 Personal | Deberes, formación, R1 confirmación expresa | Aceptación registrada del aviso de uso | Operador | Operador |
| mp.eq.2 Bloqueo de puesto (A MEDIO) | Bloqueo por inactividad con reautenticación | Inactividad en el servidor, 15 min en el perfil; 8 h absolutas | Fijar el tiempo | - |
| mp.com.1 Perímetro | Todo flujo autorizado | Consola en loopback por defecto; lista de acceso; `allowed_hosts`; tabla de flujos (5.1) | Cortafuegos perimetral, VLAN de gestión | - |
| mp.com.2 Confidencialidad (+R1) | Cifrado; algoritmos autorizados | TLS 1.2+ con ECDHE y AES-GCM fijados en la consola; SSH de la flota con intercambio `sntrup761x25519`/`curve25519` y cifrados AEAD fijados; autofirmado ECDSA P-256 con SAN, y certificado del operador esperado en el perfil | VPN si central y nodos cruzan redes ajenas | Estado CCN de las suites **[verificar]** |
| mp.com.3 Integridad (+R1, R2) | Autenticar el extremo | Host key fijada por nodo; TLS; CSRF; sesión ligada a IP | VPN (R1) | - |
| mp.com.4 Separación de flujos | Segmentación | Solo tráfico saliente de la central; consola de nodo en loopback | Red de gestión separada | Operador |
| mp.si.2 Criptografía de soportes | Confidencialidad e integridad de lo que sale | Copias: subida solo a destinos cifrados en el perfil; metadatos con HMAC-SHA256 que cubre el SHA-256 del archivo; copia de la central con scrypt + AES-256-CBC + HMAC-SHA256 | Cifrado de disco; custodia de claves y passphrases | - |
| mp.si.3-5 Custodia, transporte, borrado | - | Ficheros 0600/0700 | Custodia física, transporte, borrado seguro | Operador |
| mp.sw.1 Desarrollo (+R1..R4) | Separar desarrollo y producción | Orígenes permitidos; previews en sandbox, compiladas sin los secretos de producción (red estricta opcional por aplicación); SBOM y CI con análisis estático | Desarrollo seguro de sus aplicaciones; sin datos reales en pruebas | Las previews heredan el `.env` de la aplicación salvo que el operador defina el suyo |
| mp.sw.2 Aceptación (+R1) | Pruebas antes de producción | HealthGate y vuelta atrás; previews | Nodo o central de preproducción | - |
| mp.info.1 Datos personales | RGPD | Datos mínimos (nombre de usuario, correo opcional, IP en auditoría); retención configurable (`retention.*`) | Base jurídica y plazos | - |
| mp.info.2 Calificación | Nivel de cada información | Clasificación por aplicación en el inventario | Calificar | - |
| mp.info.6 Copias (+R1) | Copias y pruebas de recuperación regulares | Copias programadas; verificación profunda programada con evidencia (`backups.verify`); `noust central backup` | Copia fuera de sitio; pruebas de restauración completas | La verificación extrae el archivo pero no restaura la base de datos en un motor de prueba |
| mp.s.2 Servicios web (+R1 o R2) | Protección frente a ataques web; auditorías | CSP estricta con Trusted Types en la consola; plantillas nginx con `server_tokens off` y HSTS opcional; E2E con axe y CSP | Pruebas de penetración | Sin DAST publicado en CI |
| mp.s.4 Denegación de servicio (D MEDIO) | Capacidad y prevención | Límites de tasa y de cuerpo en la consola | Protección del proveedor/WAF; no exponer la central | Operador |

## 4. Evidencias: qué pedir y cómo obtenerlo

### 4.1 `noust ens check`

```bash
noust ens check            # los fallos y avisos, con evidencia y qué hacer; código 0, 1 o 2
noust ens check --all      # también lo que pasa
noust ens check --json     # para un sistema de monitorización
noust ens report           # paquete de evidencias (JSON, Markdown y SHA256SUMS)
```

| Id | Comprobación | Medidas |
|---|---|---|
| ENS-PRF-01 | Perfil `ens-medium` activo; valores en vigor | op.exp.2, op.exp.3.r1 |
| ENS-ACC-01 | Existe `security` y `admin`; nadie con roles incompatibles sin excepción | op.acc.3, org.1.3 |
| ENS-ACC-02 | Segundo factor en todas las cuentas | op.acc.6.r2, op.acc.6.r8 |
| ENS-ACC-03 | Token maestro restringido y sin uso en 30 días | op.acc.6.r8 |
| ENS-ACC-04 | Tokens de API con dueño y caducidad de 90 días como máximo | op.acc.1.3, op.acc.4 |
| ENS-ACC-05 | Revisión de accesos en los últimos 90 días | op.acc.4.4 |
| ENS-ACC-06 | Cuentas sin uso en 90 días; cuentas bloqueadas | op.acc.1.4, op.acc.6.6 |
| ENS-SES-01 | Inactividad y duración de sesión | mp.eq.2 |
| ENS-LOG-01 | Cadena de auditoría íntegra; escrituras funcionando | op.exp.8, op.exp.8.r4 |
| ENS-LOG-02 | Auditoría enviada fuera de la máquina, sin retraso | op.mon.3.1, op.exp.8.r4 |
| ENS-LOG-03 | Retención de 365 días y espacio | op.exp.8.r3 |
| ENS-LOG-04 | Reloj sincronizado | op.exp.8.r2 |
| ENS-LOG-05 | Revisión de la auditoría en los últimos 7 días | op.exp.8.r1 |
| ENS-CRY-01 | Certificado de la consola del operador, no autofirmado, más de 30 días de vigencia | mp.com.2.r1, mp.com.3.r2 |
| ENS-CRY-02 | Consola sin texto claro fuera de loopback | mp.com.2, mp.com.3 |
| ENS-CRY-03 | Secretos TOTP cifrados; secretos de una central *hub* sellados | op.exp.10 |
| ENS-NET-01 | Lista de acceso explícita | mp.com.1, op.acc.4.5 |
| ENS-NET-02 | SSH bastionado | op.acc.6.r9, op.exp.2 |
| ENS-NET-03 | Cortafuegos activo, nada interno expuesto | mp.com.1, op.mon.3.r2 |
| ENS-BAK-01 | Copia en 26 h, verificada en 7 días, enviada a otro lugar | mp.info.6, mp.info.6.r1 |
| ENS-BAK-02 | Destinos cifrados y cifrado exigido | mp.si.2 |
| ENS-UPD-01 | Parches de seguridad, reinicio, fin de vida del SO | op.exp.4 |
| ENS-MON-01 | fail2ban vigilando SSH; journal persistente | op.mon.1 |
| ENS-HRD-01 | Resto de comprobaciones de bastionado y riesgos aceptados con su fecha | op.exp.2, op.exp.3.r1 |
| ENS-INV-01 | Toda aplicación con responsable y criticidad | op.exp.1, mp.info.2 |
| ENS-CHG-01 | Cuatro ojos y `--reason` activos | op.exp.5, op.exp.5.4, op.acc.3 |
| ENS-SUP-01 | Orígenes de código permitidos definidos | mp.sw.1, op.exp.5 |
| ENS-BLD-01 | Compilaciones en sandbox | op.exp.6, op.exp.2.2 |
| ENS-FLT-01 | Túneles sin root; techo de la central por nodo | op.acc.4.2, op.ext.4 |
| ENS-INC-01 | Ningún bloqueo de incidente olvidado | op.exp.7 |

Un área que no se puede leer (por ejemplo, sin permisos de root) aparece como aviso con el error
literal: nunca como aprobado.

### 4.2 Tabla de evidencias

| Medida | Evidencia | Orden o endpoint | Periodicidad |
|---|---|---|---|
| op.acc.1, op.acc.4.4 | Lista de cuentas, roles, MFA y tokens; atestación con su SHA-256 | `noust ens access-review list`, `... attest`; `GET/POST /api/ens/access-review` | Trimestral |
| op.acc.3 | Excepciones de segregación con motivo y caducidad | `noust user exception list` | Al crearlas y en cada revisión |
| op.acc.6 | Inicios de sesión, fallos, bloqueos, *break-glass* | `noust audit list --category access` | Semanal (revisión) |
| op.exp.1 | Inventario de aplicaciones | `noust ens inventory list --format csv` | Mensual |
| op.exp.3, op.exp.2 | Comparación con la línea base | `noust ens check`, `noust ens profile` | Semanal y tras cada cambio |
| op.exp.4 | Parches pendientes y reinicio | `noust server security checks`, `ens check` (ENS-UPD-01) | Semanal |
| op.exp.5 | Cambios con referencia, aprobaciones | `noust audit list --action cli.command`, `noust approval list` | Continua |
| op.exp.8 | Cadena verificada, envío, revisión | `noust audit verify`, `noust audit status`, `noust audit review` | Semanal |
| op.exp.9 | Paquete de incidente y su hash en la auditoría | `noust incident freeze`, `sha256sum -c MANIFEST.sha256` | Por incidente |
| op.mon.2 | Indicadores | `noust ens report` | Mensual y anual |
| mp.info.6 | Verificaciones de copias | `noust backup verify`, `noust audit list --action backups.verify` | Semanal (automática) |
| op.cont.1, mp.info.6 | Copia cifrada de la central y su verificación | `noust central backup`, `noust central backup --verify <fichero>` | Diaria o semanal; verificación mensual |
| op.pl.3 | SBOM de la versión instalada | Adjunto de la release en GitHub (`noust-<versión>.cdx.json`) | Por versión |

## 5. Guía de bastionado

### 5.1 Arquitectura de referencia

```
             zona de gestión de la organización (VLAN/VPN)
  ┌─────────────────────────────────────────────────────────────┐
  │  personas (navegador) ──TLS──> CENTRAL (consola 8443)        │
  │                                  │   │    │                  │
  │           SIEM <──syslog TLS─────┘   │    └── NTP autenticado │
  │   destino de copias <──rclone crypt──┘                        │
  └──────────────────────────────────│──────────────────────────┘
                                     │ SSH saliente (túnel, cuenta noust-tunnel)
                ┌────────────────────┼────────────────────┐
             NODO 1               NODO 2               NODO n
       (consola en loopback)  (consola en loopback)  (consola en loopback)
```

Flujos autorizados (mp.com.1.2, op.ext.4.2):

| Origen | Destino | Puerto | Protocolo | Autenticación | Cifrado |
|---|---|---|---|---|---|
| Personas de la red de gestión | Consola de la central | 8443/tcp | HTTPS | Cuenta + MFA (o passkey) | TLS 1.2+ AEAD |
| Central | Cada nodo | 22/tcp | SSH (reenvío de un puerto) | Clave Ed25519 por nodo, restringida, host key fijada | SSH con algoritmos fijados |
| Central y nodos | SIEM | 6514/tcp | syslog RFC 5425 | Certificado del SIEM (y de cliente) | TLS 1.2+ |
| Nodos | Destino de copias | según backend | rclone | Credenciales del destino | `crypt` de rclone + TLS/SSH |
| Nodos | Repositorios de código permitidos | 443/tcp | HTTPS/SSH | Credencial de despliegue | TLS/SSH |
| Todos | NTP | 123/udp o 4460/tcp (NTS) | NTP/NTS | NTS si es posible | - |

Riesgos propios de Noust para el análisis de riesgos (op.pl.1): robo del acceso a la central
(controla la flota); un repositorio hostil (desplegar es ejecutar código: por eso el sandbox y los
orígenes permitidos); un administrador malicioso (por eso los cuatro ojos y el envío de la
auditoría fuera); pérdida de la passphrase de sellado o del respaldo de la central (no hay
recuperación).

### 5.2 La central de una empresa vive en la infraestructura de la empresa

La central guarda las claves de toda la flota y, con ellas, un atacante despliega código en todos
los servidores. Por eso las medidas de instalaciones (mp.if.1 a mp.if.7), continuidad
(op.cont.1), copias (mp.info.6) y reloj (op.exp.8.r2) **se aplican al equipo que la aloja**. Un
NAS doméstico o el portátil de un administrador incumplen mp.if.1 a mp.if.7. Requisitos mínimos:

- CPD o sala de la organización con control de acceso y registro de entradas, o un proveedor con
  certificado ENS de categoría MEDIA o superior (op.nub.1).
- SAI, reloj sincronizado con NTP (NTS si está disponible) y red de gestión separada.
- Certificado TLS de la PKI de la organización (`NOUST_TLS_CERT`, `NOUST_TLS_KEY`), no el
  autofirmado; `NOUST_ALLOW_IP` limitado a la red de gestión.
- Secretos sellados (`noust central seal`) con la passphrase custodiada por el responsable de
  seguridad, fuera del equipo.
- Copia cifrada diaria o semanal (`noust central backup`) guardada fuera de sitio, con su
  passphrase custodiada aparte, y verificada mensualmente (`--verify`).
- Una central de preproducción para probar cada actualización de Noust antes de la de producción.
- El perfil `ens-medium` activo **en la propia central** cuando gestiona nodos que lo tienen. El
  modo sudo se confirma en la central: esta pregunta a su operador y responde de ello al nodo
  (`X-Noust-Elevated`), y el nodo lo acepta sin aplicar a esa llamada su propia política de modo
  sudo, tampoco la del perfil (contraseña y código, 10 min tras la última acción, 30 como máximo).
  La política que rige lo que se hace desde la central es, por tanto, la de la central.

### 5.3 Puesta en marcha (día 0)

1. Instalar desde el repositorio firmado de OBS o PyPI; anotar la versión y su SBOM.
2. `noust web enable --tls-cert <fullchain> --tls-key <clave> --allow-ip <red de gestión>` (en una
   central: `NOUST_TLS_CERT`, `NOUST_TLS_KEY`, `NOUST_ALLOW_IP`).
3. Crear la primera cuenta `security` y la primera `admin` (`noust user create`, o invitaciones
   con `noust user invite`); cada persona enrola su segundo factor al entrar.
4. `noust config set security.profile ens-medium`.
5. `noust config set auth.notice.text '<aviso de derechos y obligaciones>'`.
6. Configurar el envío de la auditoría: `audit.syslog` hacia el SIEM (TLS) y `audit.enterprise_id`
   con el PEN de la organización.
7. `noust config set security.allowed_sources '[github.com/<organización>]'`.
8. Destinos de copia cifrados (`noust backup destination add ... --encrypt`) y calendarios con
   destino (`noust backup schedule create <dominio> --schedule daily --destination <nombre>`);
   guardar las claves con `show-key` fuera del servidor.
9. Sandbox en cada aplicación (`noust app sandbox test`, `enable`) y bastionado
   (`noust server security checks`: corregir o aceptar el riesgo con motivo y fecha).
10. En una central: `noust central seal`; nodos con la cuenta de túnel y el techo adecuado.
11. Inventario: `noust ens inventory set <dominio> --owner ... --criticality ... --classification ...`.
12. `noust ens check` en verde (o con avisos justificados) y `noust ens report` como línea base.

### 5.4 Identidad y acceso

**Roles y correspondencia con el ENS** (una cuenta, un rol; op.acc.1.2):

| Rol | Puede | Correspondencia orientativa |
|---|---|---|
| `security` | Configuración de seguridad, cuentas, auditoría, aprobaciones, cumplimiento | RSEG y delegados |
| `admin` | Crear, configurar, borrar, operaciones equivalentes a root (con aprobación en el perfil), cumplimiento | Administradores del RSIS |
| `operator` | Arrancar, parar, reiniciar, desplegar, renovar certificados, hacer copias | Operadores del RSIS |
| `auditor` | Leer todo, la auditoría completa y el cumplimiento; nada más | Supervisión y auditoría interna |
| `viewer` | Leer sin secretos | Consulta |

Incompatibles para una misma persona: `admin`/`operator` con `security`; `auditor` con cualquier
otro. Con un único responsable de seguridad (instalaciones pequeñas), la excepción se documenta con
motivo y caducidad (`noust user exception add`) y aparece en el informe (art. 13.3).

**Alta**: invitación de un solo uso con caducidad (`noust user invite`); la persona fija su
contraseña, enrola el segundo factor y acepta el aviso. Nadie recibe contraseñas por correo.

**Baja**: `noust user disable <usuario> --reason ...` revoca de inmediato sesiones y tokens; la cuenta
se conserva durante la retención de la auditoría (op.acc.1.4).

**Revisión trimestral** (op.acc.4.4): `noust ens access-review list`, comprobar cada cuenta contra
la lista de personas autorizadas, corregir y `noust ens access-review attest --notes '...'`.

**Reactivación de una cuenta bloqueada** (op.acc.6.8 exige describirla): tras 5 fallos
consecutivos la cuenta queda bloqueada. El responsable de seguridad (1) comprueba en la auditoría
el origen de los fallos (`noust audit list --action auth.login --result failure`), (2) contacta con
la persona por un canal distinto, (3) si hay sospecha de compromiso, restablece el segundo factor
(`noust user reset-mfa`) y abre un incidente, y (4) desbloquea con `noust user unlock <usuario>
--reason "<ticket>"`, que queda auditado.

**Token maestro (*break-glass*)**: con cuentas creadas y el perfil activo solo sirve para recuperar
el acceso de una persona. Se guarda en sobre cerrado o caja fuerte bajo custodia del responsable de
seguridad; cada uso genera `auth.break_glass` (crítico) y una notificación; tras usarlo se rota
(`noust web token --regenerate`) y se documenta el motivo. `noust ens check` (ENS-ACC-03) avisa de
cualquier uso en 30 días.

**Tokens de API**: siempre con dueño y caducidad (`noust token create <nombre> --owner <cuenta>
--expires-hours 2160`); nunca con más permisos que su dueño.

### 5.5 Registro de la actividad

- **Catálogo** de eventos auditados (op.exp.8.r3.1): `noust audit events --markdown` genera la
  tabla completa con categoría y severidad syslog.
- **Formato**: JSON por línea con actor (persona, rol, canal), fecha UTC, objeto, acción, resultado
  e id de correlación; cadena HMAC-SHA256 con clave propia (`audit-key`, 0600).
- **Integridad**: `noust audit verify` comprueba la cadena. Límite: root en la máquina puede
  recalcularla con la clave local; la garantía es el envío en tiempo real a un SIEM, donde una
  reescritura local se ve como discontinuidad frente a los `audit.checkpoint` recibidos.
- **Retención**: 365 días como mínimo en el perfil; nada se purga sin haberse enviado.
- **Revisión semanal** (op.exp.8.r1): `noust audit review --from ... --to ... --notes '...'` deja
  el evento `audit.review` con la verificación de la cadena.
- **Reloj** (op.exp.8.r2): ENS-LOG-04; los mensajes syslog llevan `timeQuality`.
- **Casos de uso para el SIEM**: fuerza bruta (`auth.login` fallidos por IP), `auth.lockout`,
  `auth.break_glass`, `user.role_change`, `config.change` en secciones de seguridad,
  `security.profile.change`, `fleet.node.add`, `fleet.tunnel.hostkey_changed`, borrados masivos
  (`apps.delete`, `db.drop`), `host.exec` fuera de horario, `audit.gap`, `audit.degraded`,
  `audit.sink.degraded`, `apps.source.denied`, `incident.freeze`, `incident.lockdown`.

### 5.6 Criptografía y claves

| Uso | Mecanismo | Dónde vive la clave | Rotación | Estado CCN |
|---|---|---|---|---|
| TLS de la consola | TLS 1.2+, ECDHE con AES-GCM; certificado del operador (autofirmado: ECDSA P-256 con SAN) | Fichero del certificado, 0600 | Con la PKI de la organización | [verificar] suites y curvas |
| Túneles de la flota | SSH Ed25519, KEX `sntrup761x25519`/`curve25519`, cifrados AEAD | `secrets/fleet/nodes/<nodo>/`, 0600 o sellado | `noust node rekey` | Ed25519 [verificar] |
| Sellado de secretos (central) | scrypt (N=2^15, r=8, p=1) + AES-256-CBC (openssl) + HMAC-SHA256 | Passphrase: en ningún sitio | `noust central unseal` y `seal` con otra | AES-256, SHA-256 ok; scrypt y CBC [verificar] |
| Copia de la central | La misma construcción, sobre el fichero completo | Passphrase: en ningún sitio | Una por copia si se desea | Como el sellado |
| Metadatos de copias | HMAC-SHA256 que cubre el SHA-256 del archivo | `secrets/backups/sidecar-mac-key` | Borrar la clave genera otra; las copias anteriores quedan como "otra clave" | ok |
| Copias remotas | rclone `crypt` (XSalsa20-Poly1305, clave derivada con scrypt) | `secrets/backup-destinations/<nombre>` | Nuevo destino | [verificar] |
| Secretos TOTP en el store | HMAC-SHA256 como flujo de clave (contador) + HMAC-SHA256 (cifrar y luego autenticar), ligado a la cuenta | `totp.key` junto al store, 0600 (no en el store) | Restablecer el segundo factor | HMAC-SHA256 ok; construcción propia [verificar] |
| TOTP | HMAC-SHA1, 6 dígitos, 30 s (RFC 6238) | En el store, cifrado | `noust user reset-mfa` | HMAC-SHA1 en OTP [verificar]; passkeys como alternativa |
| Contraseñas | scrypt con sal | Store | Cambio por la persona | [verificar] |
| Tokens (maestro, API, flota) | 256 bits aleatorios; SHA-256 salado | `web-sessions.db`, `web-token` | `noust web token --regenerate`, caducidad | ok |
| Cadena de auditoría | HMAC-SHA256 | `audit-key`, 0600 | Nueva cadena documentada | ok |

La pérdida de la passphrase de sellado o de la del respaldo de la central **no tiene
recuperación**: custodia por el responsable de seguridad en dos lugares distintos.

### 5.7 Cambios y despliegues

- Repositorios protegidos (rama principal protegida, revisión obligatoria, commits firmados) en la
  forja de la organización; `security.allowed_sources` limitado a ellos.
- Cada orden de la CLI que cambia algo lleva `--reason "<referencia de cambio>"`; los despliegues
  desde la consola quedan con su actor, commit y disparador. Las que ejecutan las unidades, los
  temporizadores y los scripts de los paquetes (`web start`, `monitor run`, `central run`,
  `backup run-schedule`, `preview sweep`, `config upgrade`, `monitor install`...) no lo necesitan:
  quedan auditadas igual, con `entry_point` en el detalle y, bajo systemd, el actor `system`.
- Acciones equivalentes a root, altas y bajas de nodos y cambios de rol: aprobación de otra persona
  (`noust approval list`, `approve`).
- HealthGate como prueba de aceptación automática y vuelta atrás en segundos; previews para probar
  antes de producción.
- **Actualizar Noust**: desde el repositorio firmado; primero en la central de preproducción;
  revisar el changelog y `UPGRADING-*.md`; ventana de cambio; `noust ens check` después.

### 5.8 Copias y continuidad

- Copias de aplicación diarias con destino cifrado fuera del servidor; la verificación profunda
  semanal es automática y queda registrada (`backups.verify`); `noust backup verify` bajo demanda.
- Prueba de restauración completa (con la base de datos) al menos trimestral en un nodo de prueba,
  documentada.
- Central: `noust central backup` (fichero cifrado con manifiesto SHA-256); `--verify` mensual;
  restauración: `noust central backup --decrypt <fichero> --to <archivo.tar.gz>`, extraer sobre un
  directorio de datos vacío y arrancar la central. Definir RTO y RPO en el análisis de impacto.

### 5.9 Monitorización e incidentes

- `noust monitor` informa (no mata procesos); las comprobaciones de bastionado cubren SSH,
  cortafuegos, fail2ban, parches, reloj y fin de vida del SO; la organización aporta IDS/EDR y
  auditd.
- **Procedimiento ante un incidente**: (1) `noust incident freeze --reason "<id del incidente>"`
  (paquete de evidencias y bloqueo de la consola; `--revoke-sessions` si hay sesiones sospechosas);
  (2) copiar el paquete fuera del servidor y anotar el SHA-256 del manifiesto, que ya consta en la
  auditoría enviada; (3) si una central está comprometida, revocarla en cada nodo
  (`noust fleet deauthorize`); (4) notificar según el art. 33 (CCN-CERT, o INCIBE-CERT en el sector
  privado); (5) al cerrar, `noust incident unfreeze --reason "..."` y rotar credenciales.

### 5.10 Datos personales (mp.info.1)

Noust guarda nombre de usuario, nombre visible y referencia de persona (habitualmente el correo) de
cada cuenta, y direcciones IP y acciones en la auditoría y las sesiones. Retención configurable
(`audit.retention_days`, `retention.sessions_days`, `retention.jobs_days`,
`retention.deployments_days`); las cuentas dadas de baja se conservan durante la retención de la
auditoría. La base jurídica y los plazos son de la organización.

### 5.11 Calendario operativo

| Periodicidad | Tarea |
|---|---|
| Diaria | Alertas del SIEM y de Noust; copias (automáticas) |
| Semanal | `noust audit review`; `noust ens check`; parches pendientes |
| Mensual | `noust ens report`; tokens caducados; `noust central backup --verify` |
| Trimestral | `noust ens access-review attest`; prueba de restauración completa |
| Anual | Análisis de riesgos, categorización, Declaración de Aplicabilidad, ejercicio de *break-glass*, rotación de claves de nodo |
| Bienal | Auditoría de conformidad (art. 31) |

## 6. Qué entregar al auditor

- `noust ens report` (JSON y Markdown con su `SHA256SUMS`), y su hash en la auditoría.
- La salida de `noust audit verify` y la comparación de su cabeza con la del SIEM.
- Las atestaciones de revisión de accesos y de auditoría (`noust audit list --action access.review`,
  `--action audit.review`).
- El inventario (`noust ens inventory list --format csv`).
- El SBOM de la versión instalada.
- Los documentos de la organización: política (art. 12), normativa, procedimientos, análisis de
  riesgos, Declaración de Aplicabilidad (art. 28.2), plan de auditorías, informe de auditoría bienal
  (art. 31) y, en su caso, certificado de conformidad (art. 38).

## 7. Lo que sigue siendo de la organización

- Aprobar la política de seguridad y designar RINF, RSERV, RSEG y RSIS (org.1, art. 13).
- Normativa de uso, procedimientos y su aplicación (org.2, org.3); formación y concienciación
  (mp.per).
- Análisis de riesgos, arquitectura, capacidad y adquisición (op.pl); justificar el uso de un
  componente no incluido en el CPSTIC (op.pl.5).
- Lista de personas autorizadas, altas, bajas y revisión (op.acc.1, op.acc.4.4); custodia del token
  maestro y de las passphrases.
- Bastionado del sistema operativo según la guía CCN-STIC aplicable, antimalware/EDR, IDS/IPS,
  auditd (op.exp.2, op.exp.6, op.mon.1).
- SIEM, correlación y revisión (op.mon.3, op.exp.8.r1); NTP autenticado.
- Gestión y notificación de incidentes (op.exp.7, art. 33).
- Instalaciones, equipos, soportes y proveedores (mp.if, mp.eq, mp.si.3-5, op.ext, op.nub).
- Pruebas de penetración de la consola y de las aplicaciones (mp.s.2.r1).
- Desarrollo seguro de sus aplicaciones y datos de prueba no reales (mp.sw.1).

## 8. Huecos residuales y verificaciones pendientes

- **CPSTIC** (op.pl.5): no cerrable en código.
- **Algoritmos**: el estado frente a CCN-STIC 807 y 221 de Ed25519, HMAC-SHA1 en OTP, scrypt,
  AES-CBC, rclone `crypt` y la construcción del cifrado de TOTP debe **verificarse** en las ediciones
  vigentes (no pudieron leerse al redactar este documento). Alternativas disponibles: passkeys en
  lugar de TOTP; certificados ECDSA P-384 o RSA de 3072 bits del operador.
- **PEN de IANA**: `audit.enterprise_id` usa por defecto 32473 (reservado para documentación); la
  organización debe poner el suyo.
- Sin firma propia de artefactos (sigstore/cosign) ni DAST en CI; sin campos de inventario por nodo;
  la verificación programada de copias no restaura la base de datos en un motor de prueba.
- La cadena de auditoría local la puede recalcular root: el envío a un SIEM es la garantía.

Guías del CCN a citar (verificar la edición vigente): CCN-STIC 801, 802, 803, 804, 807, 808, 809,
817, 823 y 221; perfiles de cumplimiento 890 y 892.

## Anexo A. Concordancia de numeración (RD 3/2010 frente al RD 311/2022)

| RD 3/2010 (y CCN-STIC 804 de 2017) | RD 311/2022 |
|---|---|
| `op.acc.5` Mecanismo de autenticación | `op.acc.5` (usuarios externos) y `op.acc.6` (usuarios de la organización) |
| `op.acc.6` Acceso local | `op.acc.5.7`/`.6.7`, `.8`, `.9` y R5 |
| `op.acc.7` Acceso remoto | `op.acc.4.5`, `op.acc.6.r8`, `op.acc.6.r9` |
| `op.exp.10` Protección de los registros | `op.exp.8.r2`, `.r3`, `.r4` |
| `op.exp.11` Protección de claves | `op.exp.10` |
| `mp.info.9` Copias de seguridad | `mp.info.6` |
| `mp.info.4` Firma electrónica | `mp.info.3` |
| `mp.info.5` Sellos de tiempo | `mp.info.4` (N.A. a MEDIA) |
| `mp.s.8` Denegación de servicio | `mp.s.4` |
| (no existía) | `op.nub.1`, `op.mon.3` |

## Anexo B. Procedimientos plantilla

**Alta de una persona.** Solicitud aprobada por el responsable del servicio → el responsable de
seguridad invita (`noust user invite <usuario> --role <rol>`) → la persona activa la cuenta
(contraseña, segundo factor, aviso) → se anota en la lista de personas autorizadas.

**Baja.** Comunicación de RR. HH. o del responsable → `noust user disable <usuario> --reason
"<ticket>"` el mismo día → revisión de tokens de esa cuenta → anotación en la lista.

**Reactivación de cuenta bloqueada.** Ver 5.4.

**Uso del token maestro.** Dos personas presentes; apertura del sobre; motivo en el ticket; uso;
rotación inmediata (`noust web token --regenerate`); nuevo sobre; revisión del evento
`auth.break_glass`.

**Restauración de la central.** Ver 5.8; prueba anual documentada.

**Rotación de claves de nodo.** `noust node rekey <nodo>` y el código que imprime, ejecutado en el
nodo; comprobación con `noust node test <nodo>`.

**Incidente.** Ver 5.9.

## Anexo C. Lo que preguntará el auditor

- ¿Quién puede entrar en la consola y con qué rol? → `noust ens access-review list`.
- ¿Cuándo se revisaron los accesos por última vez y quién? → evento `access.review`.
- ¿Los registros son íntegros y salen de la máquina? → `noust audit verify`, `noust audit status`, SIEM.
- ¿Cuánto tiempo se guardan? → `audit.retention_days` (365).
- ¿Hay doble factor para todos? → ENS-ACC-02.
- ¿Cómo se autoriza un cambio y cómo se referencia? → `--reason`, aprobaciones, auditoría.
- ¿Se prueban las copias? → `backups.verify` semanales, prueba de restauración trimestral.
- ¿Dónde está la central y quién tiene acceso físico? → sección 5.2, documentos de la organización.
- ¿Qué hace el token maestro y dónde está? → sección 5.4.
- ¿Qué versión de Noust se usa y qué contiene? → `noust --version`, SBOM de la release.
