# Bases de datos en Noust 3.1: competidores, estado actual y análisis de brechas

Feedback 38 del dueño: "Rehacer por completo la gestión de bases de datos. Hoy es muy pobre y la
competencia lo hace mil veces mejor y tiene muchas más cosas. Investigar primero a fondo a la
competencia y escribir un análisis de brechas antes de diseñar."

Fecha: 2026-09-29. Rama `dev/3.1`. Documento de investigación previo al diseño: no cambia código.

---

## 0. Resumen ejecutivo

1. **Lo que falta no es una función, es el modelo.** Los competidores tratan la base de datos como un
   recurso de primera clase ligado a una aplicación: se crea con un clic, su cadena de conexión llega
   sola al entorno de la app, tiene copias programadas, un explorador de datos y métricas. En Noust
   la base de datos es "un servidor de motores con una tabla de bases y usuarios": la app no sabe
   que tiene una base (solo recetas y monorepo la provisionan), la consola no muestra datos, y las
   copias de una base son una acción manual o un efecto secundario del backup de la app.
2. **Hay defectos de corrección, no solo carencias** (sección 3.7). Los graves: la API y la CLI
   registran bases de datos de forma distinta (rompe el backup de la app si se borra una base desde
   la consola), `restore --drop` destruye la base antes de restaurar sin copia previa, los dumps y
   restores son peticiones HTTP síncronas de hasta 3600 s contra un proxy de central que corta a 300 s,
   las contraseñas generadas no se codifican en las URL de conexión, Redis con `requirepass` deja de
   funcionar, y MongoDB se instala sin autenticación (sus "usuarios" son decorativos).
3. **Ventaja real de Noust que hay que conservar y ampliar:** el modo solo lectura lo impone el
   servidor (rol `wasm_ro_<bd>` en PostgreSQL, cuenta de solo `SELECT` en MySQL), no una lista de
   palabras. Ningún competidor consultado documenta algo equivalente. El explorador de datos y la
   consola nueva deben apoyarse en esa misma garantía.
4. **Competidores más relevantes para Noust**: Laravel Forge (instala motores nativos en un VPS, como
   Noust: elige versión, crea usuarios de solo lectura, sincroniza bases creadas fuera, backups a S3) y
   Coolify/Dokploy (autohospedados, provisión con un clic, backups programados a S3, restauración desde
   la UI). En experiencia de datos, Railway/Supabase/Neon marcan el listón (explorador, editor SQL con
   historial, métricas).
5. **Alcance recomendado 3.1 (MUST):** M1 modelo único (servicio de bases de datos, store como verdad,
   corrección de motores); M2 provisión y vinculación por app con inyección de entorno y rotación de
   contraseña; M3 página de base de datos con pestañas, Conectar (túnel SSH) y detección de
   exposición; M4 explorador de datos de solo lectura; M5 consola SQL v2; M6 copias programadas con
   retención, verificación, offsite, restauración segura y jobs; M7 usuarios y acceso con perfiles;
   M8 métricas, actividad y salud. SHOULD y LATER en la sección 6.

---

## 1. Método y límites

- Documentación pública de cada producto, consultada el 2026-09-29 (enlaces en línea). No se probó
  ningún producto en vivo ni se vieron capturas: las "pantallas" se describen según la documentación.
- Varias páginas se resumieron con una herramienta de extracción; donde una página no cubría un
  punto se dice "no documentado", que no equivale a "no existe".
- Páginas que no se pudieron leer: la página de backups de Forge (404 en su ruta nueva; se usó
  el resumen del buscador), documentación detallada de Ploi (la ruta directa devolvió 404), RunCloud
  (solo la página de bases de datos y la de phpMyAdmin) y la de RedisInsight (página vacía; sus
  funciones se citan de conocimiento general y están marcadas).
- Del código de Noust se leyó todo `src/noust/managers/database/`, `web/api/databases.py`,
  `cli/commands/db.py`, `deployers/helpers/databases.py`, la parte de bases de datos de
  `managers/backup_manager.py`, `panel/src/features/databases/` y lo que toca la central
  (`web/api/node_proxy.py`). Los números de línea son de la rama `dev/3.1` en esta fecha.
- Nada de lo marcado "por verificar" se ha ejecutado contra un servidor real.

---

## 2. Competidores

### 2.1 Comparativa A: modelo, motores, aprovisionamiento, usuarios, acceso externo

| Producto | Modelo | Motores y versiones | Aprovisionar e inyectar en la app | Usuarios y roles | Acceso externo |
|---|---|---|---|---|---|
| [Coolify](https://coolify.io/docs/databases/) | Contenedores Docker en tus servidores | PostgreSQL, MySQL, MariaDB, MongoDB, Redis, Dragonfly, KeyDB, ClickHouse. PG 18/17/16 + PostGIS, pgvector y Supabase-PG ([PG](https://coolify.io/docs/databases/postgresql)); `mysql:8` por defecto ([MySQL](https://coolify.io/docs/databases/mysql)); Redis 7.2 ([Redis](https://coolify.io/docs/databases/redis)) | Credenciales autogeneradas y URL interna con el nombre del contenedor en la red compartida; la URL se pega en la app (no documentan enlace automático) | Solo las credenciales del recurso (usuario/contraseña/BD) | "Make it publicly available" (proxy TCP con URL pública) o mapeo de puerto; SSL `allow`..`verify-full` con CA propia |
| [Dokploy](https://docs.dokploy.com/docs/core/databases) | Contenedores (Swarm) | PostgreSQL, MySQL, MariaDB, MongoDB, Redis; imagen Docker personalizable | URL de conexión interna y "External Credentials" ([conexión](https://docs.dokploy.com/docs/core/databases/connection)); variables de entorno por BD | Las del recurso | Puerto externo elegido por el operador con URL generada; recomienda VPN o lista de IP |
| [CapRover](https://caprover.com/docs/one-click-apps.html) | Contenedores, apps de un clic | MongoDB, MySQL, Redis, PostgreSQL, más phpMyAdmin y mongo-express como apps | Otras apps llegan por nombre de servicio (`srv-captain--nombre`) | Ninguno propio | Mapeo explícito de puerto; sin pantallas de gestión ni backups documentados |
| [Railway](https://docs.railway.com/guides/postgresql) | Plataforma gestionada (contenedores + volúmenes) | PostgreSQL, MySQL, MongoDB, Redis; plantillas con PostGIS, TimescaleDB, pgvector; upgrade mayor y HA | Variables `PGHOST`...`PGDATABASE` y `DATABASE_URL` en el servicio; privado por defecto | Las del recurso | "Public Access" crea un proxy TCP y `DATABASE_PUBLIC_URL` (coste de egress) |
| [Render](https://render.com/docs/postgresql) | Plataforma gestionada | PostgreSQL 13 a 18 (11/12 heredadas); Key Value = Valkey 8 ([doc](https://render.com/docs/key-value)) | URL interna (misma región) y externa; enlace automático a variables de servicios | Rotación de credenciales sin caída | Lista de IP en CIDR (por defecto `0.0.0.0/0`), TLS obligatorio fuera ([conexión](https://render.com/docs/postgresql-creating-connecting)) |
| [Laravel Forge](https://forge.laravel.com/docs/resources/databases) | Motores **nativos** en el VPS (como Noust) | MySQL 8.0/8.4/9.x, MariaDB 10.11/11.4, PostgreSQL 13 a 18; el motor se instala al crear el servidor o después; sin upgrades automáticos | BD y usuario `forge` por defecto; para apuntar una app a otra BD se edita el entorno del sitio (no documentan escritura automática) | Crear usuarios eligiendo BD y marcándolos **solo lectura**; reset de contraseñas root/forge desde la UI; "Sync Databases" adopta bases creadas fuera | Conexión solo por SSH con clave privada (ej. TablePlus por SSH), con URL de conexión generada y botón "abrir en el cliente" |
| Forge, bases gestionadas ([doc](https://laravel.com/forge/docs/resources/managed-databases)) | Clústeres gestionados (DigitalOcean) | MySQL 8.4, PostgreSQL 17/18 | Credenciales primaria/standby/réplicas en pestañas | Usuarios con BD elegidas | "Public access" con confirmación; por defecto solo la red privada |
| [Ploi](https://ploi.io/documentation/database) | Panel sobre VPS | MySQL, MariaDB, PostgreSQL | Crear BD y usuario en el panel; **clonar BD** para staging | Crear usuarios | Configuración de acceso remoto para MySQL/MariaDB y PostgreSQL |
| [RunCloud](https://runcloud.io/docs/server/database) | Panel sobre VPS | Solo MariaDB | Crear usuario, luego BD, y enlazarla a la web app | "Assign & Revoke User" por BD | phpMyAdmin de un clic ([doc](https://runcloud.io/docs/installing-phpmyadmin)) |
| [CloudPanel](https://www.cloudpanel.io/docs/v2/frontend-area/databases) | Panel sobre VPS | MySQL/MariaDB (no especifica más) | Añadir BD + usuario + contraseña | Añadir usuarios con permisos por BD; credenciales maestras por `clpctl` | IP whitelist en el firewall para el puerto 3306 |
| [Supabase](https://supabase.com/docs/guides/database/overview) | BaaS sobre PostgreSQL | PostgreSQL con extensiones (pgvector, PostGIS, pg_cron) | Cadena en el diálogo "Connect": directa, pooler de sesión y de transacción ([conexión](https://supabase.com/docs/guides/database/connecting-to-postgres)) | Roles propios y por defecto (`anon`, `authenticated`...) ([roles](https://supabase.com/docs/guides/database/postgres/roles)); cambio de contraseña sin caída | Restricciones de red: lista CIDR para Postgres y pooler ([doc](https://supabase.com/docs/guides/platform/network-restrictions)); `sslmode=verify-full` |
| [Neon](https://neon.com/docs/manage/roles) | Postgres serverless | PostgreSQL | Cadena por rama/rol | Roles desde consola/CLI/API, reset de contraseña, rol dueño por defecto | Pooling PgBouncer; allowlist (no cubierto en lo leído) |

### 2.2 Comparativa B: datos, consola, copias, métricas, upgrades

| Producto | Explorador de datos | Consola SQL | Copias programadas, offsite, PITR | Métricas | Upgrade mayor |
|---|---|---|---|---|---|
| Coolify | No documentado | No documentado | PG/MySQL/MariaDB/Mongo/ClickHouse: cron o alias, retención por número, días y GB, S3, "disable local backup", página de ejecuciones con estado/tamaño/S3 ([backups](https://coolify.io/docs/databases/backups)); Redis-family **sin** backups; sin PITR | CPU y memoria del contenedor, health checks | No; recomienda probar en una copia |
| Dokploy | No documentado | No documentado | Backups a destinos S3, cron, botón **Test**, restauración desde el bucket a una BD destino ([backups](https://docs.dokploy.com/docs/core/databases/backups), [restore](https://docs.dokploy.com/docs/core/databases/restore)); solo garantiza restaurar los que él generó | Memoria, CPU, disco, red (solo con la página abierta) | No |
| CapRover | Vía phpMyAdmin/mongo-express | Vía esas apps | No | No | No |
| Railway | **Data tab**: tablas, entradas editables, crear tabla/columnas; claves Redis; colecciones y documentos Mongo; **sin SQL crudo** ([database view](https://docs.railway.com/databases/database-view)) | No (recomienda pgAdmin/DBeaver) | Backups de volumen diario (6 d), semanal (27 d), mensual (89 d) con restore "staged" a un volumen nuevo ([doc](https://docs.railway.com/reference/backups)); guía con tres capas: volumen, **PITR con pgBackRest** (~4 semanas) y dumps lógicos ([guía](https://docs.railway.com/guides/postgres-backups-restores)) | Observabilidad externa recomendada (Prometheus/Grafana) | Upgrade mayor soportado |
| Render | Herramientas admin externas | Externas (psql) | PITR (Hobby 3 d, Pro 7 d; restaura a una **instancia nueva**), exports lógicos 7 días descargables `.dir.tar.gz` ([backups](https://render.com/docs/postgresql-backups)) | Métricas en el dashboard | In-place a PG 18 con **prueba previa en un clon**, caída de hasta 1 h ([doc](https://render.com/docs/postgresql-upgrading)) |
| Laravel Forge | Ninguno (cliente externo por SSH) | Ninguno | Backups por hora/día/semana/cron, S3, DO Spaces, Scaleway, OVH y S3 compatible, retención por número, aviso por correo al fallar, restaurar los recientes ([doc](https://forge.laravel.com/docs/resources/database-backups)); gestionadas: diario + PITR 7 d a un clúster nuevo | Gestionadas: CPU/memoria/disco 1/7/30 días | Manual |
| Ploi | phpMyAdmin de un clic | phpMyAdmin | Backups programados a S3, Google Drive, Dropbox, DO Spaces, S3 propio, SFTP, FTP, rclone; restauración de un clic ([feature](https://ploi.io/features/database-backups)). **Sin subida/descarga de dumps en el panel "por seguridad"**: recomienda TablePlus/SSH ([roadmap](https://roadmap.ploi.io/projects/1-server-level-requests/items/399-import-export-database-tools)) | No | No |
| RunCloud | phpMyAdmin | phpMyAdmin | Backup de la BD junto con la web app si está enlazada | No | No |
| CloudPanel | phpMyAdmin ("Manage") | phpMyAdmin | Dump nocturno 03:15, retención 7 días, en el directorio del usuario del sitio; import/export por `clpctl db:export/db:import` (gzip por extensión) | No | No |
| Supabase | **Table Editor** visual con pestañas, edición de filas, import CSV | **SQL Editor**: snippets en carpetas, favoritos, compartidos, export CSV, asistente IA | Diario (Pro 7 d, Team 14, Ent 30), **PITR** como add-on (WAL-G, RPO 2 min, 7/14/28 d) ([backups](https://supabase.com/docs/guides/platform/backups)); restaurar causa caída | Informes: memoria, CPU, IOPS, conexiones, tamaño, pooler; **Query Performance** ([reports](https://supabase.com/docs/guides/telemetry/reports)) | `pg_upgrade` con pre-chequeos (réplicas, tipos `reg*`, slots lógicos, extensiones), `pg_basebackup` previo y vuelta automática si falla ([doc](https://supabase.com/docs/guides/platform/upgrading)) |
| Neon | **Tables** (Drizzle Studio): editar celdas, filtros guardados como Views, borrado masivo, export JSON/CSV/SQL, crear roles y políticas RLS ([doc](https://neon.com/docs/guides/tables)) | SQL Editor: consultas guardadas, historial (9 KB), **Explain/Analyze**, export CSV/JSON/XLSX ([doc](https://neon.com/docs/get-started/query-with-neon-sql-editor)) | Ramas copy-on-write y **instant restore** a timestamp o LSN con rama de respaldo automática y "Time Travel Assist" (consultas de solo lectura al pasado) ([doc](https://neon.com/docs/introduction/branch-restore)) | Conexiones (activas/inactivas/máx.), tamaño de BD, deadlocks, filas, tasa de acierto de caché, historial 1/3/14 d ([doc](https://neon.com/docs/introduction/monitoring-page)) | Gestionado |

### 2.3 Herramientas de referencia (lo que el operador compara)

| Herramienta | Qué ofrece que el operador espera |
|---|---|
| [phpMyAdmin](https://www.phpmyadmin.net/) | Explorar/editar filas, SQL con marcadores, query-by-example, importar CSV/SQL, exportar en muchos formatos, usuarios y privilegios, estado del servidor, varios servidores |
| [Adminer](https://www.adminer.org/en/) | Un solo archivo, varios motores, edición con búsqueda y filtros, export SQL/CSV, privilegios, **lista de procesos con kill**, claves foráneas, esquema |
| [pgAdmin](https://www.pgadmin.org/features/) | Dashboard de monitorización (sesiones, transacciones, tuplas, E/S de bloques), Query Tool con plan gráfico, backup/restore, Grant Wizard, ERD, vacuum/analyze, agente de tareas |
| [CloudBeaver](https://dbeaver.com/docs/cloudbeaver/) (la idea web de DBeaver) | Editor SQL con autocompletado, editor de datos, exportación, ERD, historial de consultas, RBAC con conexiones compartidas por equipo, auditoría, sesiones |
| RedisInsight (funciones de conocimiento general, no verificadas en esta pasada) | Navegador de claves con vista por tipo, CLI/Workbench, slowlog, análisis de memoria |
| [mongo-express](https://github.com/mongo-express/mongo-express) | Ver/crear/borrar bases, colecciones y documentos, GridFS, BSON, lista blanca de bases |

### 2.4 Redis y Mongo: qué hacen los demás

- **Redis**: Coolify lo ofrece (y Dragonfly/KeyDB) pero **sin backups programados ni restore desde la UI**,
  con credenciales generadas y TLS en otro puerto ([Redis](https://coolify.io/docs/databases/redis));
  Railway muestra las claves en su Data tab; Render Key Value expone modo de persistencia
  (journal+snapshot, snapshot, off), política `maxmemory`, allowlist y métricas de memoria/CPU/conexiones,
  y ya es **Valkey 8**. Fedora sustituyó Redis por Valkey desde la 41
  ([cambio](https://fedoraproject.org/wiki/Changes/Replace_Redis_With_Valkey)); Debian 13 lleva
  `redis-server` 8.0.x ([paquete](https://aptpeek.dev/debian/trixie/main/armhf/redis-server/5:8.0.2-3+deb13u1))
  y Valkey ([guía](https://computingforgeeks.com/install-valkey-debian/)).
- **MongoDB**: Coolify hace `mongodump` programado y restaura con `mongorestore`; Railway muestra
  colecciones y documentos editables; mongo-express es el navegador clásico. MongoDB no está en los
  repositorios de las distros: según su página de instalación
  ([Linux](https://www.mongodb.com/docs/manual/administration/install-community-linux/.md)) hay
  repositorio propio para Ubuntu 22.04/24.04, Debian 13, RHEL y SLES, y la versión actual es 9.0
  (leído hoy; por verificar).
- **PITR de MongoDB** exige oplog, es decir conjunto de réplicas (`mongodump --oplog` con
  `mongorestore --oplogReplay`, [mongodump](https://www.mongodb.com/docs/manual/reference/program/mongodump/)):
  no aplica a una instancia standalone.

### 2.5 Patrones que ganan (lo que hay que copiar y lo que no)

1. **La BD nace ligada a la app** y la variable de entorno se escribe sola (Railway, Render, Coolify
   con URL interna, Forge con su `forge` por defecto). Es la brecha más visible.
2. **Copias con retención y estado visible** (Coolify: número/días/GB, historial de ejecuciones; Forge:
   aviso por correo; Dokploy: botón Test). Y **restaurar nunca pisa lo que hay**: Render, Neon, Supabase,
   Forge managed y Railway restauran a una instancia/volumen nuevo o dejan una rama de respaldo.
3. **"Una copia que no se ha restaurado no es una copia"**: Coolify lo dice explícitamente y sugiere
   restaurar en una BD desechable; Railway lo enseña como "drill".
4. **Explorador de datos separado de la consola SQL** (Railway solo explorador; Neon y Supabase ambos).
   Las dos cosas responden a usos distintos: mirar una tabla vs. escribir consultas.
5. **Historial + consultas guardadas + export** en el editor (Supabase, Neon, phpMyAdmin con marcadores).
6. **Métricas de base de datos, no del contenedor**: conexiones, tamaño, tasa de acierto de caché,
   consultas lentas (Neon, Supabase, pgAdmin). Coolify y Dokploy solo dan CPU y memoria.
7. **Acceso externo por defecto cerrado**, con túnel SSH (Forge), lista de IP (Render, Supabase,
   CloudPanel) o proxy/puerto opt-in con aviso (Coolify, Dokploy, Railway).
8. **Solo lectura como concepto de usuario** (Forge: usuarios de solo lectura) y credenciales
   distintas por servicio (Supabase lo recomienda para auditar).
9. **Lo que los paneles nativos no hacen** (Forge, Ploi, RunCloud, CloudPanel): explorador, consola,
   métricas. Se remiten a phpMyAdmin o a un cliente externo. Ahí Noust puede diferenciarse sin
   instalar nada más en el servidor.
10. **Lo que no conviene copiar**: subir dumps por el navegador (Ploi lo rechaza por seguridad; en Noust
    además el cuerpo de la API está limitado a 1 MiB, `web/auth.py:145`), alojar phpMyAdmin/Adminer en
    el propio servidor (superficie de ataque PHP fuera del CSP y de la política de "un solo cliente
    JSON") y prometer HA o réplicas sin poder operarlas.

---

## 3. Noust hoy

### 3.1 Motores y operaciones (por motor)

Cuatro managers registrados en `DatabaseRegistry` (`managers/database/registry.py`), unas 4.700 líneas
de motores sobre `BaseDatabaseManager` (`base.py`, 1.524 líneas) más el lexer de psql
(`psql_script.py`, 551). Todo va por `CommandRunner`; contraseñas por stdin/env/archivo 0600.

| Operación | PostgreSQL | MySQL/MariaDB | Redis | MongoDB |
|---|---|---|---|---|
| Instalar | `apt-get install postgresql postgresql-contrib` | `mariadb-server`, si no `mysql-server` | `redis-server` | Repositorio upstream, serie **7.0 fija** (`mongodb.py:74`), línea apt con `ubuntu jammy` **fija** (`mongodb.py:139`) |
| Versión | La del distro (14 en Ubuntu 22.04, 15 en Debian 12, 16 en Ubuntu 24.04, 17 en Debian 13); sin elección | La del distro | La del distro | 7.0 |
| Crear/borrar BD | Sí (`OWNER`, `ENCODING`, `TEMPLATE`); drop con `--force` termina sesiones | Sí (charset/collation) | No crea; "borrar" = `FLUSHDB` de un slot | Crea colección `_wasm_init` y la borra |
| Listar/info | Tamaño, dueño, codificación; nº de tablas **solo del esquema `public`** (`postgres.py:645`) | Tamaño, nº de tablas, charset | Slots con claves, memoria del servidor | Tamaño, colecciones |
| Usuarios | `CREATE ROLE` con flags `superuser/createdb/createrole`; listado con atributos y BD conectables | `CREATE USER user@host`; listado con `mysql.db` | ACL `SETUSER` con reglas validadas | `createUser` con roles |
| Privilegios | GRANT/REVOKE con lista blanca: BD (`CONNECT/CREATE/TEMP`) + `ALL TABLES IN SCHEMA public` (`postgres.py:882-923`) | GRANT/REVOKE a nivel BD con lista blanca | Reglas ACL | `grantRolesToUser` |
| Cambiar contraseña | **No existe en ningún motor** | No | `set_password` (`requirepass`) **sin llamadores** | No |
| Backup | `pg_dump` plain (por defecto), custom o tar; por esquema; streaming a fichero 0600 | `mysqldump --single-transaction --routines --triggers` (sin `--events`) | `BGSAVE` + `cat dump.rdb`, o AOF (`backup_aof`, alcanzable solo desde el archivo de la app) | `mongodump` + `tar` |
| Restore | Plain (revisado por `psql_script.check_plain_dump`) o custom; `--drop` = borrar, recrear, cargar | Por stdin en `--binary-mode`; `--drop` igual | Parar servicio, copiar RDB, arrancar | `mongorestore` |
| Consola lectura | Rol `wasm_ro_<bd>` por TCP + `BEGIN READ ONLY` + `default_transaction_read_only` (`postgres.py:1269-1362`) | Cuenta `wasm_ro_<bd>` con solo `SELECT` en esa BD + `START TRANSACTION READ ONLY` (`mysql.py:382-428`, `1075-1126`) | **No hay** | **No hay** |
| Consola escritura | Superusuario por socket peer (`runuser`), sentencia en argv `-c` | Cuenta administrativa de `noust db config`, sentencia por stdin | `redis-cli` con `query.split()` (`redis.py:827`) | JS arbitrario en `mongosh` |
| Cadena de conexión | `postgresql://user:pass@host:<puerto real>/db` | `mysql://...:3306` (puerto fijo) | `redis://...:6379` (fijo) | `mongodb://...:27017` (fijo) |

### 3.2 API (`/api/databases/...`, `web/api/databases.py`, 1.360 líneas)

| Endpoint | Notas |
|---|---|
| `GET /engines`, `GET /engines/{e}/status`, `/privileges`, `/logs` | Estado, versión, puerto; logs de journal |
| `POST /engines/{e}/install` (job 202), `POST /engines/{e}/uninstall` (job, elevado), `POST .../start|stop|restart` | Instalación y desinstalación como job (`web/jobs.py:1625`) |
| `GET/POST /databases`, `GET/DELETE /databases/{e}/{name}` | `DELETE` elevado; **no registra ni borra nada en el store** |
| `POST /users`, `POST /users/grant`, `POST /users/revoke`, `GET /users/{e}`, `DELETE /users/{e}/{u}` | La contraseña se devuelve una vez al crear |
| `GET /backups`, `POST /backups`, `POST /backups/restore` | **Síncronos**; restore elevado; sin borrar, verificar, programar ni empujar |
| `POST /query` | Una sentencia; `mode=read|write`; write elevado; salida en columnas/filas **como strings** (tope 1.000 filas) |
| `POST /connection-string` | Formatea lo que el cliente teclea; no lee nada del servidor |

Auditoría (`audit_log.info`): solo drop, create_user, grant, revoke, drop_user, restore y query
(`databases.py:911, 958, 999, 1037, 1110, 1240, 1292`); **`create_database` y `create_backup` no se auditan**.

### 3.3 CLI (`noust db ...`, `cli/commands/db.py`, 1.877 líneas)

`install`, `uninstall`, `status`, `start`, `stop`, `restart`, `engines`, `create`, `drop`, `list`,
`info`, `user-create`, `user-delete`, `user-list`, `grant`, `revoke`, `backup`, `restore`, `backups`,
`query [--write]`, `connect` (cliente interactivo por `execvp`), `connection-string`, `config`
(credenciales administrativas). **Sin**: cambio de contraseña, borrado de dumps, programación,
verificación, vinculación con apps. La CLI **sí** registra y borra las filas del store
(`db.py:503-515`, `562-565`) y marca `tracked`/`linked_app` en `list`: la API no.

### 3.4 Consola (React, `panel/src/features/databases/`, 2.414 líneas)

- `/databases` (`DatabasesPage`): tira de 4 motores (estado, versión, puerto, instalar/arrancar/parar/
  reiniciar), tabla de bases (filtro por motor, "Nueva base"), tabla de usuarios por motor (crear,
  grant, revoke, borrar).
- `/databases/$engine/$name` (`DatabasePage`): Overview (motor, tablas/claves, tamaño, dueño,
  codificación), **consola SQL** (textarea, Read/Write, Ctrl+Enter, `ResultGrid` con columnas/filas),
  copias de esa BD (crear, restaurar con nombre tecleado y "drop first"), formulario de cadena de
  conexión (usuario, contraseña, host) y acción de borrar.
- No hay: explorador de tablas ni de filas, métricas, actividad, vínculo con la app (las páginas de
  app no mencionan bases de datos), paso de BD en el asistente de nueva app (solo recetas), pestañas,
  exportación de resultados, historial ni consultas guardadas, exposición/túnel.
- El diálogo de crear usuario no permite elegir BD ni perfil (`CreateUserDialog.tsx:58` envía solo
  usuario/contraseña/host), aunque la API acepta `database`: el usuario queda sin acceso hasta un
  segundo paso "grant".

### 3.5 Copias de bases de datos

- Dumps sueltos en `/var/backups/noust/databases/<motor>-<bd>-<timestamp>.<ext>[.gz]` (0600, directorio
  0750). Sin retención, sin borrado (ni CLI ni API), sin programación, sin verificación, sin offsite.
- Una BD **solo entra en una copia programada si está vinculada a una app** (`store.list_databases(app_id=)`,
  `backup_manager.py:2576`) y se pide `include_databases`. Las bases creadas desde la consola no se
  registran (B1) y por tanto **nunca** salen en un backup de app.
- El backup de app sí tiene retención, verificación, push offsite por rclone (`backup_destinations.py`,
  2.2) y notificación `backup_failed`; nada de eso llega a los dumps de BD porque `push()` exige un
  `BackupMetadata` de archivo de app.

### 3.6 Provisión por app

`deployers/helpers/databases.py::provision_database` crea BD + usuario idempotente, con comprobación de
propiedad (nunca reutiliza lo de otra app), contraseña de 32 caracteres alfanuméricos en `SecretStore`
y `DatabaseCredentials.url` con percent-encoding. Solo lo llaman recetas (`recipes/deploy.py:240`) y
el despliegue monorepo (`deployers/monorepo.py`). Un despliegue normal de una app no tiene forma de
pedir una base de datos ni de enlazar una existente; `DATABASE_URL` no se escribe salvo en esos dos
caminos.

### 3.7 Defectos y limitaciones observados

Severidad: C = pérdida de datos o mentira del producto; A = fallo funcional frecuente; M = molestia.

| # | Sev. | Hallazgo | Dónde |
|---|---|---|---|
| B1 | C | **Dos implementaciones de "crear/borrar BD"** (regla 3). La API no toca el store; la CLI sí. Consecuencias: bases hechas en la consola no salen en backups de app; borrar desde la consola una BD provisionada para una app deja la fila del store, y `_dump_databases` itera esas filas y trata `DatabaseNotFoundError` como fatal, así que **el siguiente backup de la app falla** | `web/api/databases.py:819-850, 885-919` vs `cli/commands/db.py:503-515, 562-565`; `backup_manager.py:2576-2609` |
| B2 | C | `restore(drop_existing=True)` borra la BD antes de restaurar, **sin copia previa**; si el dump está truncado o falla, la BD queda vacía. Mismo patrón en MySQL y Mongo | `postgres.py:1154-1157`, `mysql.py:1016-1019`, `mongodb.py:711` |
| B3 | C | Dump y restore son **peticiones HTTP síncronas** con timeout de 3600 s (`TRANSFER_TIMEOUT`); el proxy de la central corta la lectura a 300 s (`node_proxy.py:128`), así que vía central un dump grande "falla" mientras sigue corriendo en el nodo. Las copias de app sí son jobs | `databases.py:1170-1249` |
| B4 | A | Las cadenas de conexión de los managers **no codifican** usuario/contraseña, y `generate_password` incluye `!@#$%^&*` (`base.py:550`): `postgresql://u:pa@ss#w@host/db` no se parsea. MySQL/Redis/Mongo usan el puerto por defecto aunque el servidor escuche en otro. Existe una segunda implementación correcta (`DatabaseCredentials.url`, regla 3) | `postgres.py:1692`, `mysql.py:1198`, `redis.py:838-864`, `mongodb.py:776-795`; `deployers/helpers/databases.py:54-95` |
| B5 | A | **PostgreSQL 15+ (Debian 12, Ubuntu 24.04): probable fallo de migraciones** de apps provisionadas. La BD se crea sin dueño (`create_database(name)`, propiedad de `postgres`), el usuario recibe `ALL` en la BD y `ALL TABLES IN SCHEMA public`; desde PG 15 `PUBLIC` ya no tiene `CREATE` en `public` y solo el dueño de la BD lo tiene ([notas de PG 15](https://www.postgresql.org/docs/release/15.0/)). No se conceden secuencias ni `DEFAULT PRIVILEGES`. La única receta con PostgreSQL es Umami. **Por verificar en un servidor real**; el harness de integración no lo cubre | `deployers/helpers/databases.py:550`, `postgres.py:915-921` |
| B6 | A | **Redis con `requirepass`**: `self._password` nunca se carga (solo `set_password` lo fija, y nadie lo llama); toda operación falla con `NOAUTH`. Consola por `query.split()` rompe `SET k "a b"`. Restore de RDB **no surte efecto si `appendonly yes`**: Redis reconstruye desde el AOF cuando ambos existen ([persistencia](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/)); Noust igualmente registra "Restored". La página de una BD Redis no muestra sus backups porque el fichero se etiqueta `dump`, no el slot | `redis.py:98-116, 736-800, 827, 660` |
| B7 | A | **MongoDB se instala sin `security.authorization`**: los usuarios y roles que crea Noust no protegen nada y la consola ejecuta JS sin credenciales. Repositorio fijado a `ubuntu jammy` incluso en Debian; serie 7.0 fija | `mongodb.py:74, 139` (sin `_post_install`) |
| B8 | A | El instalador de motores es **solo apt** (`base.py:699-716`) aunque Noust se empaqueta para Fedora y openSUSE (y `noust setup` ya abstrae apt/dnf/zypper, `cli/commands/setup.py:139-177`); en RPM PostgreSQL además necesita `initdb`. `mysql-server` como alternativa no existe en Debian. Nombres de unidad fijos (`redis-server`, `mysql`) | `base.py`, `redis.py:81`, `mysql.py:228` |
| B9 | A | **El filtro de palabras sigue en la API** (`READ_STATEMENT_KEYWORDS`, `_check_read_only`) y el docstring del módulo aún lo presenta como el mecanismo. La garantía real es el rol, así que el filtro solo produce falsos rechazos (`(SELECT ...) UNION`, un comentario inicial, `SET`, `CALL` de solo lectura), y el `;` dentro de un literal también se rechaza | `databases.py:1-32, 76, 459-489`; `base.py:425` |
| B10 | A | Consola: los resultados son strings (NULL y cadena vacía llegan igual en PG; en MySQL `-B` imprime `NULL` literal), el tope de 1.000 filas se aplica **después** de que el cliente imprimiera todo (memoria y tiempo hasta los 120 s), sin paginación, sin export, sin historial | `base.py:349-383`, `postgres.py:1466-1516` |
| B11 | M | Usuarios: **no hay cambio de contraseña**; el listado muestra cuentas internas (`wasm_ro_*`, `postgres`, `root`, `mariadb.sys`, ...) borrables; al borrar una BD quedan el rol `wasm_ro_<bd>` (es de clúster) y su fichero de contraseña | `postgres.py:773-781`, `mysql.py:837-844` |
| B12 | M | La provisión del rol de lectura **se repite en cada consulta**: PostgreSQL recorre todos los esquemas concediendo `SELECT` cada vez (`postgres.py:1638-1652`); MySQL crea la cuenta, rota su contraseña y hace `FLUSH PRIVILEGES` en cada llamada (`mysql.py:411-428`). Aceptable para una consola, caro para un explorador que hace varias llamadas por pantalla | ver referencias |
| B13 | M | Auditoría incompleta (`create_database`, `create_backup`); `except Exception` en la CLI (`db.py:624, 757, 1082`) que tragan errores salvo con `--verbose` (regla 2: el límite de error debe registrar) | ver referencias |
| B14 | M | Métodos de manager **inalcanzables** desde la CLI y la API: `set_password`, `get_memory_stats`, `flush_all` (Redis), `backup_schema`, `backup_all_schemas`, `list_schemas` y el formato `custom`/`tar` (PG); la CLI no tiene `--schema` ni `--format`, y el error de `backup_manager.py:1086` recomienda `noust db backup --schema`, una opción que **no existe** | `redis.py:890-935`, `postgres.py:1004-1108`, `backup_manager.py:1086` |
| B15 | M | La página de una BD no sabe a qué app pertenece: `DatabaseInfoResponse` no lleva `app` ni `tracked`; `mysqldump` sin `--events`; el listado de PG cuenta tablas solo de `public` | `databases.py:118-141` |

Fortalezas a conservar: el rol/cuenta de solo lectura impuesto por el servidor (con `docs/security.md`
documentándolo), contraseñas fuera de argv, nombres validados en un solo sitio, dumps por streaming a
fichero 0600 sin shell, `check_plain_dump` contra metacomandos de psql, rechazo de `\!` y `system`,
`hand_over_file`, aislamiento por `HOME` privado en MySQL, y la provisión idempotente con comprobación
de propiedad.

---

## 4. Análisis de brechas

Severidad: **Crítica** = riesgo de datos o promesa rota; **Alta** = todos los competidores serios lo
tienen o el operador lo echa de menos cada día; **Media** = diferencia notable; **Baja** = nicho.

| # | Capacidad | Competidores que la tienen | Noust hoy | Severidad |
|---|---|---|---|---|
| 1 | BD por app con un clic e inyección de `DATABASE_URL` en su entorno | Railway, Render, Coolify (URL interna), Dokploy, Forge (BD por defecto), Supabase | Solo recetas y monorepo; el asistente de nueva app y la página de la app no tienen BD | **Crítica** |
| 2 | Vincular/desvincular una BD existente; la app muestra sus BD; la BD muestra su app | Railway, Render, Coolify, Dokploy, RunCloud (enlazar para backup) | No existe; la API no expone `app_id` | Alta |
| 3 | Cambio/rotación de contraseña (con actualización de la app) | Render, Supabase, Forge, Neon | Inexistente | Alta |
| 4 | Usuarios con permisos por BD y perfiles (incl. solo lectura) | Forge, CloudPanel, RunCloud, Neon, Supabase | Grant/revoke con lista blanca; alta de usuario sin BD/perfil; PG solo `public`, sin secuencias ni defaults | Alta |
| 5 | Adoptar bases creadas fuera ("Sync") y proteger cuentas internas | Forge (Sync, nombres reservados) | Aparecen sin marca; B1 | Media |
| 6 | **Explorador de datos** (esquemas, tablas, estructura, filas, paginación, orden, filtros) | Railway, Supabase, Neon, phpMyAdmin, Adminer, pgAdmin, CloudBeaver; Ploi/RunCloud/CloudPanel vía phpMyAdmin | Ninguno | **Crítica** |
| 7 | Edición de filas / columnas | Railway, Supabase, Neon, phpMyAdmin, Adminer, CloudBeaver | Solo SQL en modo escritura | Media |
| 8 | Consola SQL: historial, consultas guardadas, export, EXPLAIN, resultados tipados | Supabase, Neon, pgAdmin, CloudBeaver, phpMyAdmin | Textarea, una sentencia, strings, sin nada de lo demás | Alta |
| 9 | Solo lectura impuesto por el servidor | Ninguno lo documenta (Forge: usuarios solo lectura) | **Sí** (PG/MySQL); Redis/Mongo no | Fortaleza |
| 10 | Importar un dump (subir o desde origen remoto) | Coolify (subida o S3 + comando editable), Dokploy (S3), Supabase (CSV/pg_dump), CloudPanel (CLI), phpMyAdmin | Solo restaurar dumps que ya están en el directorio; cuerpo de API 1 MiB | Alta |
| 11 | Descargar/exportar dump o tabla | Render, Neon (JSON/CSV/SQL), phpMyAdmin, Adminer | No hay descarga | Media |
| 12 | Copias programadas con retención | Coolify, Dokploy, Forge, Ploi, CloudPanel, Railway, Render, Supabase | Solo manuales o como parte del backup de app; sin retención ni borrado | **Crítica** |
| 13 | Copias fuera del servidor | Coolify (S3), Dokploy (S3), Forge (S3), Ploi (S3, Drive, Dropbox, SFTP, rclone) | Existe para archivos de app (rclone); no para dumps | Alta |
| 14 | Verificación / prueba de restauración | Coolify (restaurar en BD desechable), Dokploy (Test), Railway (drill) | Ninguna | Alta |
| 15 | Restauración segura: a BD nueva, con copia previa | Render, Neon, Supabase, Forge managed, Railway (staged), Ploi (clonar) | `--drop` destruye antes; sin restore a nueva; B2 | **Crítica** |
| 16 | Dump/restore como jobs con progreso | Todas las UIs | Síncronos; B3 | Alta |
| 17 | PITR | Supabase, Render, Neon, Railway (pgBackRest), Forge managed | Ninguno | Media (los paneles nativos y de contenedores no lo dan; es diferenciador) |
| 18 | Métricas: tamaño, tablas, conexiones, tasa de acierto de caché | Neon, Supabase, Render, pgAdmin, Adminer | Tamaño/tablas en el listado; memoria de Redis en el estado | Alta |
| 19 | Actividad, consultas lentas, bloqueos, cancelar una consulta | Supabase (Query Performance), Neon, pgAdmin, phpMyAdmin/Adminer (procesos) | Ninguno | Media |
| 20 | Histórico de métricas y alertas de BD | Neon (1 a 14 d), Supabase, Render | `MetricsStore` existe para máquina y apps, no para BD; sin alertas | Media |
| 21 | Acceso externo: túnel SSH explicado, cliente de escritorio | Forge (SSH + URL + botón), CloudPanel, Dokploy (VPN/IP) | Sin guía; el formulario de cadena no conoce el host real | Alta |
| 22 | Puerto público, lista de IP, TLS | Coolify, Dokploy, Railway, Render, Supabase, CloudPanel | Sin gestión; sin detección de puertos expuestos (feedback 8) | Media |
| 23 | Elegir versión y ver fin de soporte | Forge (PG 13-18, MariaDB, MySQL), Render (13-18), Coolify (16-18) | La del distro; Mongo 7.0 fija; sin EOL | Media |
| 24 | Upgrade mayor con prechequeos y vuelta atrás | Render (clon de prueba), Supabase (pre-checks + basebackup), Railway | Ninguno | Media |
| 25 | Extensiones (PostGIS, pgvector, pg_cron) | Coolify, Railway, Supabase, Render, Neon | Ninguno | Media |
| 26 | Clonar / duplicar una BD | Coolify (clone), Ploi (clone), Neon (rama) | Ninguno (PostgreSQL lo permite con `CREATE DATABASE ... TEMPLATE`) | Media |
| 27 | Pooling (PgBouncer) | Supabase, Render, Railway, Neon | Ninguno | Baja |
| 28 | Redis: explorador, persistencia, ACL, backups fiables | Railway (claves), Render KV (persistencia/maxmemory/allowlist), RedisInsight | Consola cruda; B6 | Alta |
| 29 | MongoDB: colecciones/documentos, roles reales, backups | Railway, mongo-express, Coolify | JS crudo, sin auth; B7 | Alta |
| 30 | Auditoría y avisos de fallo de backup de BD | Forge (correo), Coolify (ejecuciones), CloudBeaver (audit) | Auditoría parcial; el aviso `backup_failed` no cubre dumps | Media |
| 31 | Postura: detectar BD escuchando fuera de loopback | (ninguno nativo) | No (feedback 8: Docker publicaba 5435 en `0.0.0.0`) | Media, diferenciador barato |
| 32 | Vista de bases de toda la flota | (ninguno) | Cada nodo por separado | Baja (feedback 33) |
| 33 | HA, réplicas de lectura | Render, Supabase, Railway, Forge managed | No | Fuera de alcance |

---

## 5. Principios de diseño (las cuatro reglas aplicadas)

- **Regla 1, procesos solo por `CommandRunner`.** Nada de drivers (`psycopg`, `PyMySQL`) aunque existan
  en las cuatro distros: abrirían un segundo camino para credenciales y no pueden usar la
  autenticación peer que hoy usa PostgreSQL. Todo sigue siendo `psql`, `mysql`, `redis-cli`,
  `mongosh` y las herramientas de dump. Herramientas nuevas (`pg_restore --list`, `gzip -t`, `tar -tzf`,
  `redis-check-rdb`, más adelante `pgbackrest`) con argv, timeout y, si llevan secretos, por env o stdin.
- **Regla 2, sin `except Exception` mudos.** Errores `Database*Error` con `details`; el límite de
  error de la API y de la CLI registra. Corregir los tres de `cli/commands/db.py`.
- **Regla 3, una sola implementación.** Nace `DatabaseService` (sección M1) y la CLI, la API, el
  asistente de apps, las recetas y el backup de apps son sus clientes. Se reutilizan, sin duplicar:
  `BackupDestinationManager` (rclone), `validate_calendar` y las plantillas de timers de
  `BackupScheduler`, `MetricsStore`/`MetricsCollector`, `JobManager`, `SecretStore`, el gestor de
  entorno y `helpers/layout.py` (`env_file_for`), `core/redact.Scrubber`. `DatabaseCredentials.url`
  pasa a ser el único constructor de URL.
- **Regla 4, la guarda en el punto de estrangulamiento.** Solo lectura = rol/cuenta/ACL del servidor,
  nunca palabras clave. Los nombres de esquema, tabla y columna del explorador **no se aceptan de la
  petición tal cual**: se validan contra el catálogo que devuelve la propia sesión de solo lectura y
  luego se citan con `quote_identifier`; los valores van como literales escapados por el manager;
  los operadores son un enum. La propiedad y la protección de cuentas internas (`wasm_ro_*`,
  usuarios provisionados) viven en `DatabaseService`, no en los endpoints, igual que la propiedad de
  unidades en `ServiceManager`.
- **Empaquetado.** Sin dependencias Python nuevas. Herramientas del sistema opcionales (pgBackRest en
  LATER) se declaran como `RCLONE_DEPENDENCY` en `core/dependencies.py`. En el panel, sin librerías que
  inyecten `<style>` (CSP): un editor SQL tipo CodeMirror es un riesgo a verificar antes de decidirlo.
- **Nada implícito sobre datos existentes.** Igual que "una app in place nunca se convierte
  implícitamente": migrar propietarios de bases ya provisionadas (producción tiene unas 17 apps de
  clientes en un solo servidor) es una acción explícita, con vista previa y reversible.
- **Copia de seguridad antes de destruir**, en todo lo que sobrescriba (restore, drop con datos).

---

## 6. Alcance propuesto para 3.1

### 6.1 MUST

Tamaños relativos: S, M, L, XL. **Núcleo indivisible** (si hay que recortar): M1, M2, M6, M7, M3 y
M4 (solo SQL). M5 y M8 pueden entregarse en una versión reducida (indicada) sin dejar el producto
incoherente.

#### M1. Modelo único de base de datos y corrección de motores (M)

Backend (`managers/database/service.py`, nuevo):

- `DatabaseService`: única cabecera de "qué es una base de datos". Métodos:
  `list(engine=None) -> list[DatabaseView]` (verdad del motor unida a la del store: `tracked`,
  `app_domain`, `owner`, `size`, `tables`, `engine_version`, `last_backup`), `create(engine, name,
  owner=None, options)` (operación de motor + fila del store + auditoría), `drop(engine, name, force,
  keep_backup=True)` (dump de seguridad opcional, fila del store, desvinculación del entorno, rol
  `wasm_ro_<bd>` y su fichero de contraseña; **se niega** si la BD está vinculada a una app y no se
  pide desvincular), `adopt(engine)` (como el "Sync Databases" de Forge: registra las no rastreadas),
  `provision_for_app(...)` (hoy `provision_database`, movido aquí y reexportado).
- `DatabaseCredentials.url` se convierte en el único constructor de URL (percent-encoding, puerto
  real de `server_port()`, `sslmode` si aplica); `get_connection_string` de los managers delega en él
  (arregla B4).
- **Descriptor de capacidades** en cada manager (`CAPABILITIES`: `sql`, `tables`, `keys`, `documents`,
  `read_only`, `users`, `dump`, `metrics`, `pitr`) que la API entrega en `GET /engines`; el panel
  pinta pestañas según capacidades, sin `if (engine === "redis")` repartidos (hoy `DatabasePage.tsx`).
- Arreglos de corrección incluidos aquí: B1 (API y CLI llaman al servicio), B2 (ver M6), B4, B6
  (cargar la contraseña de Redis de `databases.credentials.redis` y de `SecretStore`; rechazar con
  mensaje claro el restore de RDB si `appendonly yes` en lugar de fingir éxito; etiquetar el fichero
  por slot o mostrar los backups de Redis a nivel de motor), B9 (borrar el filtro de palabras y
  arreglar el docstring), B11 (limpiar rol y fichero al borrar), B13 (auditar todo desde el servicio),
  B14 (exponer o borrar lo inalcanzable). Motores: nombres de unidad y binarios por distro, instalación
  con el gestor de `noust setup` (apt/dnf/zypper) o **rechazo explícito** en distros no soportadas,
  repositorio de Mongo según la distro (no `jammy` fijo), y "Redis/Valkey" como una sola familia con
  detección de qué hay instalado.
- Store: migraciones versionadas para las tablas que traen los demás bloques (`db_console_history`,
  `db_saved_queries`, `db_backup_policies`); `databases` ya tiene `app_id`, `username`, `host`, `port`.
- Prueba de paridad: una prueba que crea y borra por CLI y por API y compara el estado del store (la
  clase de defecto de B1).

API: los endpoints existentes se conservan (compatibilidad) pero delegan; `DatabaseInfoResponse` gana
`tracked`, `app`, `engine_version`, `last_backup`. Pantallas: ninguna nueva (la tabla de `/databases`
gana columnas App, Versión y Última copia).

#### M2. Provisión y vinculación por app con inyección de entorno (L)

Backend:

- `DatabaseService.link(domain, engine, name, env_var="DATABASE_URL", extra_vars=False, restart=True)`:
  lee las credenciales de `SecretStore` (`databases/<motor>/<usuario>`), escribe la variable en el
  entorno de la app **con `helpers/layout.py::env_file_for(app)`** (nunca uniendo `app_path` con
  `.env`), la marca como secreta y reinicia con `HealthGate` (el mismo camino que "límites aplicados
  con reinicio"); si el gate falla, restaura el valor anterior. Variables: `DATABASE_URL`
  (PG/MySQL/Mongo) o `REDIS_URL`, y opcionalmente `DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`
  (Laravel, Rails). `unlink(...)` quita la variable; `drop=True` (elevado) además borra la BD.
- `DatabaseService.rotate_password(engine, user, propagate=True)`: genera, aplica en el motor
  (PostgreSQL con verificador SCRAM como ya hace el rol de lectura, para que ni el log del servidor con
  `log_statement=ddl` vea la contraseña), actualiza `SecretStore` y, por cada app vinculada, reescribe
  la variable y reinicia con `HealthGate`; si falla, revierte contraseña y variable. Devuelve la
  contraseña una sola vez. Aviso honesto: hay una ventana de reinicio (a diferencia de la rotación sin
  caída de Render, que usa credenciales dobles).
- `provision_for_app` corrige B5: crea el **usuario primero y la BD con `OWNER` = ese usuario** (así las
  migraciones funcionan en PG 15+); la migración de bases ya provisionadas con dueño `postgres` es una
  acción explícita (`noust db fix-owner`), nunca automática.
- Asistente de nueva app: `database: {engine, mode: none|new|existing, name?}` para **cualquier tipo
  de app**, no solo recetas.

Endpoints:

```
GET    /api/apps/{domain}/databases
POST   /api/apps/{domain}/databases                      crear + vincular   -> 202 job
POST   /api/apps/{domain}/databases/link                 {engine, database, env_var, extra_vars}
DELETE /api/apps/{domain}/databases/{engine}/{name}      desvincular; ?drop=true (elevado)
POST   /api/databases/users/{engine}/{username}/password rotar (elevado)  {propagate}
```

CLI: `noust db link|unlink`, `noust db user-password`, `noust app create --database postgresql`.

Consola:

- Pestaña **Databases** en la app (`/apps/$domain/databases`). Sobre el pliegue: una tarjeta por BD
  vinculada con motor + versión, tamaño, conexiones, variable inyectada (`DATABASE_URL`), URL enmascarada
  con "Mostrar" (elevado, auditado) y las acciones Rotar contraseña, Desvincular y Abrir en Databases.
  Si no hay ninguna: estado vacío con "Nueva base de datos" y "Vincular una existente".
- Paso "Base de datos" en el asistente de nueva app (Ninguna / Nueva PostgreSQL / Nueva MariaDB /
  Nueva Redis / Existente), con resumen de las variables que se escribirán antes de desplegar.

Central: todo son endpoints `/api/apps/...` ya proxiados; los destructivos (`drop`, mostrar, rotar) usan
`require_elevated` para que el esquema lleve `x-noust-requires-elevation` y la central pida la
confirmación antes de reenviar.

#### M3. Página de base de datos con pestañas, Conectar y exposición (M)

Consola. Ruta `/databases/$engine/$name/$tab` (TanStack Router anidada). Sobre el pliegue: cabecera con
nombre, motor + versión, chip de la app vinculada, indicador de salud, y una tira de KPI (Tamaño, Tablas,
Conexiones x/y, Acierto de caché, Última copia con estado). Pestañas: **Resumen | Datos | SQL | Usuarios |
Copias | Conectar**, según `CAPABILITIES` (Redis: Claves en lugar de Datos; Mongo: sin SQL).

`/databases` (índice) se reordena: tabla de bases primero (Nombre, Motor·versión, App, Tamaño,
Conexiones, Última copia, Salud) con "Nueva base de datos" (con opción "para la app...") y "Sincronizar"
(adoptar no rastreadas); los motores pasan a una tira compacta de chips (estado, versión, puerto) con
menú de instalar/arrancar/parar; los usuarios pasan a la pestaña Usuarios de cada BD y a
`/databases/engines/$engine` (Resumen del motor, Usuarios del servidor, Logs, Ajustes en SHOULD).

Pestaña **Conectar** (la brecha 21):

1. "Desde tu app": la variable y la URL enmascarada (M2).
2. "Desde tu ordenador": comando `ssh -L <puerto local>:127.0.0.1:<puerto del motor> usuario@<servidor>`
   (el propio manual de PostgreSQL insiste en usar `localhost` como destino del túnel, no el nombre del
   servidor, porque el motor solo escucha en loopback por defecto,
   [túneles SSH](https://www.postgresql.org/docs/current/ssh-tunnels.html)) y la cadena resultante
   `127.0.0.1:<puerto local>`; fragmentos para `psql`, `mysql`, `mongosh`, `redis-cli` y URL
   JDBC/DBeaver. Dirección del servidor: la que la central conoce del nodo (`NodeRecord`) o un ajuste
   `server.public_address` o la IPv4 pública detectada, editable en el campo.
3. "Exposición": dónde escucha el motor (`listen_addresses`, `bind-address`, `bind`), si algún puerto
   de BD está en `0.0.0.0` (`ss -ltn` por el runner) con aviso rojo y texto de cómo cerrarlo, y el mismo
   dato en `noust health`.

Backend: `DatabaseService.connection_info(engine, name, user)` (URL correcta, snippets, escucha) y
`engine.listen_status()`. Endpoints: `GET /api/databases/databases/{engine}/{name}/connect`,
`GET /api/databases/engines/{engine}/exposure`. La contraseña solo sale con "Mostrar" (elevado) para
usuarios provisionados por Noust; para el resto, "Rotar para ver una nueva".

Central: la clave de la central está restringida a reenviar el puerto de la consola del nodo
(`permitopen`), así que **no se ofrece "túnel a la BD a través de la central"**; el operador usa su propia
clave SSH contra el nodo y la pestaña muestra la dirección del nodo, no la de la central.

#### M4. Explorador de datos de solo lectura (L)

Backend (`managers/database/browser.py`, mixin común; SQL específico de motor en cada manager):

- `list_schemas(db)`, `list_relations(db, schema, kind)`, `describe_relation(db, schema, name)`
  (columnas con tipo/nulabilidad/defecto/PK/FK, índices, restricciones, tamaño, filas estimadas),
  `read_rows(db, schema, name, order, filters, limit, offset)`, `export_rows(...)`.
- Resultados **tipados** generados por el servidor: PostgreSQL con `json_agg` sobre una subconsulta con
  `LIMIT/OFFSET`, MySQL/MariaDB con `JSON_ARRAYAGG(JSON_OBJECT(...))` (columnas conocidas por el
  catálogo; MariaDB ≥ 10.5, por verificar), de modo que NULL, números, booleanos y JSON llegan como
  tales. Binarios como hex/base64 con límite. Filas estimadas con `pg_class.reltuples` o
  `TABLE_ROWS`, sin `COUNT(*)` en tablas grandes (con "contar exacto" opcional y timeout).
- Todo corre con el **rol/cuenta de solo lectura**; `statement_timeout` (PG, por `PGOPTIONS` en env, no
  argv) o `max_execution_time` (MySQL) y `max_statement_time` (MariaDB) por sesión. Tamaño de página
  máximo 1.000.
- Prerrequisito: **no re-provisionar el rol de lectura en cada llamada** (B12). En PostgreSQL, una
  consulta barata de huella del catálogo (nº de relaciones y OID máximo por BD) y el script de `GRANT`
  solo cuando cambia, más `ALTER DEFAULT PRIVILEGES FOR ROLE <dueño> ... GRANT SELECT` para lo que cree
  el dueño. **No usar `pg_read_all_data`** ([roles predefinidos](https://www.postgresql.org/docs/current/predefined-roles.html))
  para el rol por BD: la pertenencia a un rol es del clúster y `PUBLIC` tiene `CONNECT` en las demás
  bases por defecto, así que el rol de la base A leería la base B. (Solo sería aceptable si Noust
  revocara `CONNECT` a `PUBLIC` en todas las bases, lo que no puede garantizar en bases ajenas; para las
  bases que Noust cree a partir de 3.1 sí se hará `REVOKE CONNECT ... FROM PUBLIC` como endurecimiento.)
  En MySQL, una contraseña persistente en `SecretStore` en lugar de rotarla y hacer `FLUSH PRIVILEGES`
  por llamada.
- Redis: `scan_keys(slot, match, cursor, count, type)`, `key_info` (tipo, TTL, tamaño) y vista previa
  acotada por tipo (string, hash, list, set, zset), con un **usuario ACL de solo lectura**
  (`wasm_ro_redis`: `+@read +info -@dangerous ~*`) para que también ahí lo imponga el servidor.

Endpoints (todos GET salvo export):

```
GET  /api/databases/databases/{engine}/{name}/schemas
GET  /api/databases/databases/{engine}/{name}/relations?schema=&q=&kind=
GET  /api/databases/databases/{engine}/{name}/relations/{schema}/{relation}
GET  /api/databases/databases/{engine}/{name}/relations/{schema}/{relation}/rows
       ?limit=&offset=&order=col:asc&filter=col:op:valor  (ops: eq neq lt lte gt gte like ilike null notnull in)
POST /api/databases/databases/{engine}/{name}/relations/{schema}/{relation}/export  -> 202 job, CSV/JSON
GET  /api/databases/databases/{engine}/{name}/keys?match=&cursor=&count=&type=       (Redis)
GET  /api/databases/databases/{engine}/{name}/key?key=                                (Redis, vista previa)
```

Consola, pestaña **Datos**: sobre el pliegue, a la izquierda un árbol esquema → tablas/vistas con
buscador (filas estimadas y tamaño en gris); a la derecha la cuadrícula con cabeceras (icono de tipo,
insignia PK/FK), paginación en servidor (50/100/500), orden por columna, barra de filtros como chips
(columna, operador, valor), sub-pestaña **Estructura** (columnas, índices, restricciones) y botón
Exportar. NULL se dibuja como valor distinto de la cadena vacía. Copiar celda. Componente de cuadrícula
sin librerías que inyecten estilos (virtualización con CSSOM). Pestaña **Claves** para Redis:
buscador por patrón, tabla tipo/TTL/tamaño y panel de valor.

Central: son GET JSON normales; el proxy ya los reenvía. Las exportaciones grandes son jobs y la
descarga se hace en streaming (ver M6).

#### M5. Consola SQL v2 (M; versión reducida posible)

Backend:

- `execute_query_structured` devuelve `columns: [{name, type}]` y valores JSON con NULL real cuando el
  cliente lo permite. Spike necesario: `psql -P null=<centinela>` con `--csv` (por verificar) y, en
  MySQL, distinguir `NULL` de la cadena `'NULL'` (el modo `-B` no lo permite; se documenta como
  aproximado en la consola y exacto en el explorador).
- Tope de filas **durante** la lectura (no después) y `statement_timeout`/`max_execution_time`
  seleccionable (5 s, 30 s, 120 s).
- `POST /api/databases/explain {engine, database, query, analyze}`: `EXPLAIN` en lectura (con el rol de
  solo lectura es seguro incluso `EXPLAIN ANALYZE`, porque el servidor rechaza escribir); `ANALYZE` de
  sentencias que escriben solo en modo escritura.
- Historial (últimas 200 por operador y BD) y consultas guardadas en el store, con el texto pasado por
  `Scrubber` antes de guardar: `GET/POST/DELETE /api/databases/console/history`,
  `GET/POST/PUT/DELETE /api/databases/console/saved` (`engine`, `database`).
- `POST /api/databases/query/export` (mismo cuerpo que `/query`) -> job -> descarga CSV/JSON.
- Redis: tokenizador con comillas (en vez de `query.split()`), modo lectura con el usuario ACL de M4;
  MongoDB sigue solo en escritura hasta que exista autenticación (SHOULD).
- Se elimina el filtro de palabras (B9). Se mantiene "una sentencia" hasta poder dividir con el lexer de
  `psql_script.py`, que ya sabe dónde acaba una sentencia y dónde hay un metacomando (SHOULD).

Consola, pestaña **SQL**: editor mono con números de línea (textarea; ver riesgo del editor en la
sección 9), selector Lectura/Escritura (escritura pide la confirmación de sudo mode y queda auditada),
Ejecutar (Ctrl+Enter), Explicar / Explicar y analizar, selector de tiempo máximo, panel lateral
Historial y Guardadas, y resultados tipados (NULL en cursiva, números a la derecha, JSON plegado) con
etiqueta "truncado", tiempo y filas, y Exportar CSV/JSON desde el servidor (no de la tabla truncada).
Reducida si hace falta: historial + guardadas + export + timeout; sin explain.

#### M6. Copias: políticas programadas, retención, verificación, offsite y restauración segura (XL)

Backend:

- `DatabaseBackupPolicy` en el store: `engine`, `database`, `schedule` (validada por
  `validate_calendar`, la misma definición que las copias de app), `retention_count`,
  `retention_days`, `destinations` (nombres de destinos rclone, con su retención remota), `format`,
  `enabled`, `last_run`, `last_status`. Cubre **todas** las bases, estén o no vinculadas a una app.
- Programación: timers `noust-backup-db-<motor>-<bd>` (el prefijo `noust-backup-` ya existe) que ejecutan
  `noust db backup-run <motor> <bd>`, reutilizando el renderizado Jinja de `BackupScheduler` (se extrae
  la parte genérica; regla 3). Retención: se extrae de `BackupManager` una función común
  `apply_retention(dir, patrón, cuenta, días)`.
- Offsite: `BackupDestinationManager.push_file(local, destino, carpeta="databases/<motor>/<bd>/")` y
  `remote_list`/`download` para dumps, generalizando `push()` (que hoy exige `BackupMetadata`), con la
  misma verificación tras subir, el mismo cifrado `crypt` y la misma regla de "una carpeta por
  servidor".
- Formato: PostgreSQL pasa a `pg_dump -Fc` por defecto (comprimido, restauración selectiva y
  `pg_restore --list` para verificar; lo usa Coolify). Los `.sql` antiguos se siguen restaurando por la
  ruta actual con `check_plain_dump`; el formato se detecta por la firma `PGDMP`.
- `verify_backup(path)`: PG `pg_restore --list`; MySQL cola del fichero (`-- Dump completed`) + `gzip -t`;
  Mongo `tar -tzf`; Redis `redis-check-rdb`. Y `verify_by_restore(policy)` (SHOULD): restaurar en una BD
  desechable `noust_verify_<aleatorio>` y borrarla, como recomienda Coolify.
- Restauración segura (en el manager, no en el endpoint): `restore(..., safety_backup=True)` toma un dump
  previo y lo conserva; si la restauración falla, **se restaura el dump previo** y el error lleva ambos
  resultados literales. `restore_as_new(backup, new_name)` (restaurar a una BD con otro nombre, sin tocar
  la original: el patrón de Render, Neon y Ploi).
- Todo como **job** (202) con progreso, y notificación `backup_failed` con `kind=database` por el
  notificador existente (`backup_scheduler.py:695`).
- El backup de app (`_dump_databases`, `_restore_databases`) llama al mismo servicio.

Endpoints:

```
GET/PUT/DELETE /api/databases/backup-policies/{engine}/{database}
GET  /api/databases/backups?engine=&database=      (añade kind, verified_at, destinos)
POST /api/databases/backups                        -> 202 job
POST /api/databases/backups/{name}/verify          -> 202 job
POST /api/databases/backups/{name}/push            {destination} -> 202 job
GET  /api/databases/backups/{name}/download        elevado, streaming
DELETE /api/databases/backups/{name}               elevado
POST /api/databases/backups/restore                -> 202 job {mode: replace|new, new_name, safety_backup=true}
```

CLI: `noust db backup-schedule set|show|remove`, `noust db backup-run`, `noust db backup-delete`,
`noust db backup-verify`, `noust db backup-push`, `noust db restore --as-new NOMBRE`.

Consola, pestaña **Copias**: sobre el pliegue, tarjeta de política (programación, conservar N /
N días, destinos, "Copia ahora", estado de la última) y debajo la tabla (fecha, tamaño, formato,
verificada, dónde está: local/destinos) con acciones Restaurar (sustituir / como nueva), Verificar,
Descargar, Enviar a destino y Borrar. El diálogo de restauración muestra "Se hará antes una copia de
seguridad" activado por defecto, exige teclear el nombre y sigue el job en vivo con la salida literal de
psql/mysql. En `/databases`, aviso "N bases sin política de copias" y "Última copia hace...". Las
importaciones (SHOULD) reutilizan el mismo diálogo con origen distinto.

Central: los jobs y su seguimiento por WebSocket/SSE ya se reenvían por nodo; la descarga es
streaming (el proxy reenvía respuestas por trozos, `node_proxy.py`) y el timeout de lectura de 300 s
es por lectura, no por transferencia; los destinos y su cifrado son del nodo (cada nodo guarda sus
propias claves).

#### M7. Usuarios y acceso con perfiles (M)

Backend (`managers/database/access.py`):

- Perfiles por motor y BD: **Propietario**, **Lectura/escritura**, **Solo lectura**, **Personalizado**.
  PostgreSQL: propietario = `OWNER` de la BD; lectura/escritura = `CONNECT, TEMP`, `USAGE` en esquemas,
  DML en tablas, `USAGE, SELECT, UPDATE` en secuencias y `ALTER DEFAULT PRIVILEGES` para tablas futuras;
  solo lectura = `CONNECT` + `USAGE` + `SELECT` en lo existente + `ALTER DEFAULT PRIVILEGES` del dueño
  (no `pg_read_all_data`, por la razón dada en M4). MySQL/MariaDB: `ALL`, `SELECT, INSERT, UPDATE,
  DELETE`, `SELECT`. Redis: presets ACL. Los privilegios siguen saliendo de la lista blanca del manager.
- `set_password(user, password=None, host)` (PG con verificador SCRAM), `list_access(db)` (usuarios con
  perfil efectivo calculado con `has_table_privilege`/`SHOW GRANTS`), `apply_profile(user, db, profile)`.
- Cuentas internas (`wasm_ro_*`, `postgres`, `root`, `mysql.*`, `mariadb.sys`) marcadas "gestionada por
  Noust/sistema", ocultas por defecto y **no alterables ni borrables por la API** (la guarda vive en
  `DatabaseService`); un usuario vinculado a una app no se borra sin desvincular.

Endpoints:

```
GET /api/databases/databases/{engine}/{name}/access
PUT /api/databases/databases/{engine}/{name}/access/{user}     {profile, privileges?}
POST /api/databases/users   (existente) + {profile, link_app?}
```

Consola, pestaña **Usuarios** (por BD): tabla (usuario, perfil, host permitido en MySQL, app vinculada,
antigüedad de la contraseña) con Rotar contraseña, Cambiar acceso y Borrar; "Nuevo usuario" con perfil
(Propietario / Lectura-escritura / Solo lectura), host, contraseña generada que se muestra una vez con
copiar y "añadir a la app...". Se retira la tabla global de usuarios de `/databases` (pasa al motor).

#### M8. Métricas, actividad y salud (L; versión reducida posible)

Backend (`managers/database/metrics.py`):

- `engine_metrics()` y `database_metrics(db)` con una sola consulta/llamada por motor:
  - PostgreSQL: `pg_stat_database` (transacciones, `blks_hit/blks_read` para la tasa de acierto,
    deadlocks), `pg_database_size`, `pg_stat_activity` (conexiones activas/inactivas frente a
    `max_connections`), tablas más grandes con `pg_total_relation_size`.
  - MySQL/MariaDB: `SHOW GLOBAL STATUS` (`Threads_connected`, `Slow_queries`, ratio de
    `Innodb_buffer_pool_reads` sobre `_read_requests`), tamaño por `information_schema`.
  - Redis: `INFO` (memoria, clientes, `keyspace_hits/misses`, `evicted_keys`, ops/s), `SLOWLOG`.
  - Mongo: `serverStatus()` y `db.stats()`.
- `activity(db)` (PG `pg_stat_activity` de esa BD, MySQL `PROCESSLIST`, Redis `CLIENT LIST`) y
  `cancel(pid)` (elevado, `pg_cancel_backend`; el rol `pg_signal_backend` solo cancela sesiones no
  superusuario).
- `slow_queries(db, n)`: PostgreSQL con `pg_stat_statements` si está cargada; si no, la pestaña dice
  cómo activarla (requiere `shared_preload_libraries` y **reinicio**,
  [doc](https://www.postgresql.org/docs/current/pgstatstatements.html); la activación guiada es SHOULD);
  MySQL con `performance_schema.events_statements_summary_by_digest`
  ([doc](https://dev.mysql.com/doc/refman/8.0/en/performance-schema-statement-digests.html)); Redis
  `SLOWLOG GET`.
- Histórico: **el `MetricsStore` que ya existe** (`monitor/timeseries.py`, tramos de 1 h crudo, 1 día
  a minutos, 30 días a horas) con series `db.<motor>.<bd>.size|connections|cache_hit|tps`,
  muestreadas por una subtarea lenta del `MetricsCollector` (cada 60 s; el colector actual va a una
  cadencia corta, `metrics_collector.py:124-232`), solo en nodos con rol `server` y motores en marcha.
  Los gráficos reutilizan `Chart.tsx` y `GET /api/metrics/{serie}?window=` (acepta cualquier nombre de
  serie, como ya hace con las de apps).
- Salud: comprobaciones nuevas en `collect_health_report` y en `diagnose` (motor caído, disco del
  directorio de datos, conexiones por encima del 80 %, puerto escuchando fuera de loopback, última copia
  más vieja que su política) y eventos de notificación (`db_down`, `db_backup_stale`) por el notificador
  existente. Para `diagnose`: sonda "¿la app llega a su BD?" con las credenciales vinculadas (SHOULD).

Endpoints:

```
GET  /api/databases/engines/{engine}/metrics
GET  /api/databases/databases/{engine}/{name}/metrics
GET  /api/databases/databases/{engine}/{name}/activity
POST /api/databases/databases/{engine}/{name}/activity/{pid}/cancel   elevado
GET  /api/databases/databases/{engine}/{name}/slow-queries
```

Consola: en **Resumen**, la tira de KPI y cuatro gráficos (tamaño, conexiones, acierto de caché,
transacciones/s) con la misma lectura por teclado y tabla alternativa que ya exigen los gráficos; una
sub-vista **Actividad** (sesiones, consultas largas, bloqueos, botón Cancelar) y **Lentas**. Reducida: KPI
+ comprobaciones de salud, sin histórico ni actividad.

### 6.2 SHOULD

| # | Elemento | Por qué | Tamaño |
|---|---|---|---|
| S1 | Edición de filas por clave primaria (insertar/actualizar/borrar) en el explorador, elevada, auditada, en una sola sentencia atómica que exige exactamente una fila afectada (PG con bloque `DO`; MySQL comprobando `ROW_COUNT()`); tablas sin PK, solo lectura | Railway, Supabase, Neon y phpMyAdmin lo tienen; es el punto de mayor riesgo del explorador | L |
| S2 | Elegir la versión mayor al instalar: PostgreSQL vía PGDG (`/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh`, versiones 13 a 18 en paralelo con paquetes `postgresql-NN`, [wiki apt](https://wiki.postgresql.org/wiki/Apt)), MariaDB LTS, MySQL 8.4 LTS, Valkey/Redis; mostrar fin de soporte | Forge, Render y Coolify lo ofrecen; añade una fuente de paquetes de terceros (Noust ya confía en la clave de MongoDB) | L |
| S3 | Extensiones PostgreSQL: listar `pg_available_extensions`, activar (`CREATE EXTENSION`), instalar paquetes (`postgresql-NN-postgis-3`, `pgvector`), y activar `pg_stat_statements` con aviso de reinicio | Coolify, Railway, Supabase, Render, Neon | M |
| S4 | Clonar una BD (PG: `CREATE DATABASE ... TEMPLATE`, que exige que nadie más esté conectado; MySQL: pipe de dump) y restaurar "como nueva" desde una copia; base de las bases de previews | Coolify, Ploi, Neon | M |
| S5 | Importar un dump: origen = fichero ya en el servidor, destino rclone, o **subida por trozos** por una ruta propia con su tope y escritura a fichero 0600 (el cuerpo de la API es de 1 MiB; Ploi la rechaza por seguridad); ejecutada como job con la ruta de restauración segura de M6 | Coolify, Supabase, CloudPanel | L |
| S6 | MongoDB con autenticación real: activarla en instalaciones nuevas, asistente guiado para las existentes (romperá apps que conecten sin credenciales), usuario admin, rol `read` para consola de solo lectura, explorador de colecciones/documentos | Hoy los usuarios son decorativos (B7) | L |
| S7 | Exposición gestionada: dirección de escucha, lista de IP y TLS con certificado del motor; depende de un gestor de firewall (feedback 29/32, aún inexistente en el código) | Coolify, Dokploy, Render, Supabase, CloudPanel | L |
| S8 | Redis: persistencia (RDB/AOF/ninguna), `maxmemory` y política, presets ACL, restore correcto con AOF, `MEMORY USAGE` por clave | Render Key Value, RedisInsight | M |
| S9 | Consola: varias sentencias por ejecución (dividir con el lexer de `psql_script.py`), cancelar una consulta en curso, ejecuciones asíncronas | Supabase, Neon, pgAdmin | M |
| S10 | Verificar restaurando en una BD desechable, programable | Coolify, Dokploy | M |
| S11 | Vista de flota: bases y estado de copias de todos los nodos desde la central (fan-out de solo lectura por el proxy) | Feedback 33 | M |
| S12 | Sonda "la app llega a su BD" en `diagnose`, enlazada a la pestaña Databases de la app | Diferenciador | S |

### 6.3 LATER (3.2 o posterior)

- **PITR** de PostgreSQL con WAL archivado y pgBackRest (`archive_command = 'pgbackrest --stanza=X
  archive-push %p'`, restauración `--type=time`, repos locales/S3/SFTP y cifrado del lado cliente,
  [guía](https://pgbackrest.org/user-guide.html)); dependencia opcional como rclone. Paquetes:
  Debian y Ubuntu (universe/main), PGDG/EPEL en RPM y OBS en openSUSE (Leap 15.5, versión 2.44 vista en
  OBS; **hay que verificar Fedora y openSUSE actuales**). MariaDB/MySQL PITR con binlog +
  `mariadb-backup`/`mysqlbinlog` por posición
  ([doc MariaDB](https://mariadb.com/docs/server/server-usage/backup-and-restore/mariadb-backup/point-in-time-recovery-pitr-mariadb-backup)).
  El modelo de política de M6 ya deja sitio (`type: logical|pitr`).
- **Upgrade mayor de PostgreSQL** con `pg_upgradecluster` (Debian/Ubuntu: mantiene el clúster viejo en
  otro puerto, método `dump` o `upgrade`, [manpage](https://manpages.debian.org/jessie/postgresql-common/pg_upgradecluster.1.en.html))
  con pre-chequeos, prueba en clon, `HealthGate` de las apps vinculadas y clúster viejo parado N días:
  encaja con la promesa de "reversión instantánea" del deploy. Comprobaciones al estilo Supabase
  (extensiones no soportadas, tipos `reg*`, slots).
- PgBouncer por BD (Supabase, Render, Railway), réplicas de lectura y HA (Render, Forge managed),
  ClickHouse y otros motores (Coolify), ERD y diff de esquema (pgAdmin, CloudBeaver), RLS y políticas
  (Neon, Supabase), lista de hosts permitidos gestionando `pg_hba.conf`, exports de tablas enormes
  directos a un destino, bases de previews con clon por PR (`previews.py`), rotación programada de
  credenciales.

### 6.4 Fuera de alcance

Alojar phpMyAdmin/Adminer/pgAdmin dentro del servidor, drivers de base de datos en el proceso de
Noust, subida de dumps por un formulario de un solo cuerpo, "túnel a la BD a través de la central",
HA/réplicas gestionadas.

---

## 7. Funcionamiento por nodo desde una central

Todas las páginas ya funcionan por nodo porque cada llamada del panel se reenvía al API del nodo por su
túnel (`node_proxy.py`). Para que lo nuevo herede eso sin código de central:

1. **Solo endpoints JSON/REST normales** bajo `/api/databases/...` y `/api/apps/{domain}/databases`;
   nada de URLs absolutas ni de "el host de la página es el host de la BD".
2. **Destructivos con `require_elevated`** para que el esquema OpenAPI lleve
   `x-noust-requires-elevation`; la central pide la confirmación a su operador y el nodo la exige igual.
   Lista: drop de BD, restore, borrar dump, descargar dump, mostrar/rotar contraseña, cancelar consulta,
   consola en escritura, editar filas.
3. **Operaciones largas = jobs** (dump, restore, verificación, push, exportación, instalación, rotación
   con reinicio de apps): el proxy corta lecturas a 300 s (`HTTP_TIMEOUT`, `node_proxy.py:128`) y los
   jobs se siguen por los sockets ya reenviados.
4. **Descargas por streaming** (`StreamingResponse`): el proxy reenvía cuerpos y respuestas por trozos
   (`node_proxy.py:779`); un dump no debe cargarse entero en memoria en ninguno de los dos extremos.
5. **Metadatos de conexión del nodo**: la pestaña Conectar usa la dirección que la central tiene del
   nodo (o el ajuste del nodo), nunca `window.location`.
6. **Rol `hub`**: un hub no tiene bases (`require_server_role("Databases")` en el `runner`); la
   navegación de Databases no se ofrece en un hub y los endpoints devuelven el mensaje del rol.
7. **Muestreo de métricas** solo en nodos `server`; el gráfico de un nodo lee el `MetricsStore` de ese
   nodo por `/api/nodes/{nodo}/api/metrics/{serie}`. El flujo `/events` del nodo ya se reenvía.
8. **Secretos**: las contraseñas de BD, los destinos y sus claves `crypt` son del nodo y nunca pasan
   por la central más que en el instante de una respuesta elevada "mostrar una vez".
9. **Vista de flota** (S11) como fan-out de lecturas por el proxy; no hay un segundo API en la central.
10. **Esquema y cliente**: `panel/openapi.json` -> `schema.gen.ts` (`npm run gen:api`); `npm run build` y
    commit de `src/noust/web/static`. Textos en inglés y español en los catálogos tipados de
    `panel/src/i18n`; cada sección con su `CommandHint` (paridad CLI) y axe a cero en los dos temas.

---

## 8. Orden de implementación, dependencias y pruebas

Paquetes de trabajo (los de la misma fila pueden ir en paralelo):

| Fase | Contenido | Depende de |
|---|---|---|
| 0 | M1 (servicio, descriptor de capacidades, paridad CLI/API, arreglos B1, B4, B6, B9, B11, B13, B14); rol de lectura sin re-provisión por llamada (huella del catálogo); jobs para dump/restore | Nada |
| 1 | M6 (copias) · M2 + M7 (vincular, rotar, perfiles, arreglo B5) | Fase 0 |
| 2 | M3 (carcasa con pestañas, Conectar, exposición) · M8 (métricas y salud) | Fase 0; M3 lee M6 y M2 |
| 3 | M4 (explorador) · M5 (consola v2) | Fase 0 (rol de lectura sin re-provisión, tipado) |
| 4 | SHOULD por orden de valor: S4, S1, S5, S3, S6... | Fases previas |

Pruebas:

- Unitarias con el runner falso (el conftest hace fallar cualquier ejecución real): una prueba por
  bloque de SQL por motor y una de "el identificador viene del catálogo, no de la petición".
- **Integración real** (`tests/integration/run.py`, contenedor con systemd): PostgreSQL (15 y 16) y
  MariaDB reales para B5 (dueño y migraciones), aislamiento entre bases del rol de lectura, JSON tipado, verificación y
  restauración segura, `pg_restore --list`. Redis con AOF y sin él. Hoy el harness no toca PostgreSQL.
- E2E de Playwright (`panel/e2e/databases.spec.ts`) ampliado: cada pestaña en los dos temas, axe, CSP,
  y el sandbox de `scripts/console_server.py` con respuestas del runner falso para las consultas nuevas
  (`tests/panel_factory.seed_console_state`).
- Documentación: `docs/console.md`, `docs/security.md` (sección Databases: perfiles, ACL de Redis,
  descarga de dumps), `docs/api.md`, CHANGELOG de 3.1.

---

## 9. Riesgos y decisiones abiertas para el dueño

1. **Dueño de las bases provisionadas (M2/B5).** Cambiar a "la app es dueña" arregla PG 15+, pero las
   bases ya provisionadas (producción incluida) siguen con dueño `postgres`. Propuesta: no tocar nada
   automáticamente; `noust db fix-owner` explícito con vista previa. ¿De acuerdo?
2. **Formato de dump por defecto** (`-Fc`): mejora verificación y restauración, pero los dumps existentes
   son `.sql`. Se mantienen ambos; la extensión y la firma distinguen.
3. **Edición de filas en 3.1 o después (S1).** Es donde un panel puede hacer daño; propuesta: SHOULD, tras
   M4, con elevación y una fila exacta.
4. **Repositorios de terceros para elegir versión (S2).** PGDG, MariaDB y MySQL amplían la superficie de
   confianza (claves y fuentes apt/dnf). Noust ya lo hace con MongoDB. Decidir si 3.1 se limita a "la
   versión del distro + aviso de EOL".
5. **Editor SQL.** Un editor tipo CodeMirror es una dependencia npm (solo se usa en la compilación) pero
   su CSS dinámico podría chocar con `style-src 'self'`; hay que probarlo bajo el CSP real antes de
   adoptarlo. Alternativa: textarea con números de línea y resaltado propio.
6. **MongoDB con autenticación (S6).** Activarla en instalaciones existentes rompe a las apps que
   conectan sin credenciales; requiere migración guiada por app. ¿Se pospone a 3.2 dejando la advertencia
   "los usuarios no protegen nada" en la consola?
7. **Redis o Valkey.** Fedora ya solo trae Valkey y Debian 13 ambos; el nombre del servicio cambia
   (`redis-server`, `redis`, `valkey`). Propuesta: familia "Redis/Valkey" con detección, sin obligar a
   migrar.
8. **Coste de procesos por petición.** Cada llamada lanza `psql`/`mysql` (sin pool, porque no hay
   drivers). Medir con el explorador real; si molesta, agrupar varias consultas por llamada y cachear
   metadatos de catálogo por sesión.
9. **Nombres.** Se conservan `wasm_ro_` (roles y cuentas) y `wasm.branch` etc. según CLAUDE.md; el
   usuario ACL de Redis nuevo sigue el mismo prefijo (`wasm_ro_redis`).
10. **Ventana de rotación de contraseña.** Sin credenciales dobles hay un corte breve al reiniciar la
    app; se documenta y se protege con el `HealthGate` (vuelta atrás si la app no arranca).
11. **Alcance frente a los puntos ENS del mismo 3.1** (feedback 30): la fase 0 más M6, M2 y M7 son el
    mínimo que corrige riesgo de datos; el resto puede repartirse en 3.1.x.

---

## Anexo A. Consultas de referencia por motor (para el diseño, no para copiar tal cual)

| Necesidad | PostgreSQL | MySQL/MariaDB |
|---|---|---|
| Tamaño de BD | `pg_database_size(datname)` | `SUM(DATA_LENGTH + INDEX_LENGTH)` en `information_schema.TABLES` |
| Tablas y tamaños | `pg_class` + `pg_total_relation_size`; filas estimadas `reltuples` | `information_schema.TABLES` (`TABLE_ROWS` es estimación en InnoDB) |
| Columnas/índices/FK | `information_schema.columns`, `pg_index`, `pg_constraint` | `information_schema.COLUMNS`, `STATISTICS`, `KEY_COLUMN_USAGE` |
| Conexiones | `pg_stat_activity` frente a `max_connections` | `SHOW GLOBAL STATUS LIKE 'Threads_connected'` frente a `max_connections` |
| Acierto de caché | `blks_hit / (blks_hit + blks_read)` de `pg_stat_database` | `1 - Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests` |
| Consultas lentas | `pg_stat_statements` (`total_exec_time`, `calls`, `mean_exec_time`) | `events_statements_summary_by_digest` |
| Cancelar | `pg_cancel_backend(pid)` | `KILL QUERY id` |
| Solo lectura por servidor | Rol `wasm_ro_<bd>` con `SELECT` por BD (no `pg_read_all_data`, que es del clúster), `default_transaction_read_only`, `BEGIN READ ONLY` | Cuenta `SELECT`-only por BD + `START TRANSACTION READ ONLY` |
| Verificación de dump | `pg_restore --list` (custom) | Última línea `-- Dump completed` + `gzip -t` |
| Clonar | `CREATE DATABASE nueva TEMPLATE origen` (sin sesiones en el origen) | `mysqldump | mysql` |

Redis: `INFO` (memoria, `keyspace_hits/misses`, `evicted_keys`, `connected_clients`), `SLOWLOG GET`,
`SCAN` con `MATCH`/`COUNT`, `TYPE`, `TTL`, `MEMORY USAGE`; usuario ACL de solo lectura con
`+@read -@dangerous`. MongoDB: `db.serverStatus()`, `db.stats()`, `db.currentOp()`.

## Anexo B. Versiones y paquetes por distro (a fecha de hoy, por verificar al implementar)

| Distro | PostgreSQL por defecto | MariaDB | Redis/Valkey | MongoDB |
|---|---|---|---|---|
| Debian 12 | 15 | 10.11 | `redis-server` 7.0 | Repositorio propio (la página actual no lista Debian 12) |
| Debian 13 | 17 | (11.x) | Redis 8.0.x y Valkey | Repositorio propio, `debian trixie` |
| Ubuntu 22.04 | 14 | 10.6 | `redis-server` 6.0 | Repositorio propio, `jammy` |
| Ubuntu 24.04 | 16 | 10.11 | `redis-server` 7.0.15; Valkey 7.2 en universe | Repositorio propio, `noble` |
| Fedora 41+ | Los de Fedora (requiere `initdb`) | Los de Fedora | **Valkey** (con `valkey-compat`) | Repositorio propio (RHEL) |
| openSUSE | `postgresqlNN-server` (varias en paralelo) | Los de openSUSE | Los de openSUSE | Repositorio propio (SLES) |

Fuentes de las versiones de Debian: [wiki PostgreSQL apt](https://wiki.postgresql.org/wiki/Apt);
PGDG cubre Debian 12/13 y Ubuntu 22.04/24.04/26.04 con PostgreSQL 13 a 18. Las cifras de MariaDB y
Redis de Ubuntu/Debian vienen de conocimiento del paquete, no de esta ronda de lectura: confirmar en
`packages.debian.org` y `packages.ubuntu.com` antes de fijar versiones mínimas en el código.

## Anexo C. Fuentes consultadas (2026-09-29)

Coolify: [databases](https://coolify.io/docs/databases/), [backups](https://coolify.io/docs/databases/backups),
[restore](https://coolify.io/docs/databases/restore), [PostgreSQL](https://coolify.io/docs/databases/postgresql),
[MySQL](https://coolify.io/docs/databases/mysql), [Redis](https://coolify.io/docs/databases/redis).
Dokploy: [databases](https://docs.dokploy.com/docs/core/databases), [backups](https://docs.dokploy.com/docs/core/databases/backups),
[restore](https://docs.dokploy.com/docs/core/databases/restore), [connection](https://docs.dokploy.com/docs/core/databases/connection).
CapRover: [one-click apps](https://caprover.com/docs/one-click-apps.html).
Railway: [PostgreSQL](https://docs.railway.com/guides/postgresql), [database view](https://docs.railway.com/databases/database-view),
[backups](https://docs.railway.com/reference/backups), [Postgres backups](https://docs.railway.com/guides/postgres-backups-restores).
Render: [PostgreSQL](https://render.com/docs/postgresql), [backups](https://render.com/docs/postgresql-backups),
[upgrading](https://render.com/docs/postgresql-upgrading), [connecting](https://render.com/docs/postgresql-creating-connecting),
[Key Value](https://render.com/docs/key-value).
Laravel Forge: [databases](https://forge.laravel.com/docs/resources/databases),
[managed databases](https://laravel.com/forge/docs/resources/managed-databases),
[database backups](https://forge.laravel.com/docs/resources/database-backups),
[anuncio](https://blog.laravel.com/forge-database-backups-now-supported).
Ploi: [database](https://ploi.io/documentation/database), [backups](https://ploi.io/features/database-backups),
[roadmap import/export](https://roadmap.ploi.io/projects/1-server-level-requests/items/399-import-export-database-tools).
RunCloud: [database](https://runcloud.io/docs/server/database), [phpMyAdmin](https://runcloud.io/docs/installing-phpmyadmin).
CloudPanel: [databases](https://www.cloudpanel.io/docs/v2/frontend-area/databases).
Supabase: [overview](https://supabase.com/docs/guides/database/overview), [backups](https://supabase.com/docs/guides/platform/backups),
[upgrading](https://supabase.com/docs/guides/platform/upgrading), [connecting](https://supabase.com/docs/guides/database/connecting-to-postgres),
[network restrictions](https://supabase.com/docs/guides/platform/network-restrictions),
[roles](https://supabase.com/docs/guides/database/postgres/roles), [reports](https://supabase.com/docs/guides/telemetry/reports).
Neon: [monitoring](https://neon.com/docs/introduction/monitoring-page), [branching](https://neon.com/docs/guides/branching-intro),
[Tables](https://neon.com/docs/guides/tables), [instant restore](https://neon.com/docs/introduction/branch-restore),
[roles](https://neon.com/docs/manage/roles), [SQL Editor](https://neon.com/docs/get-started/query-with-neon-sql-editor).
Herramientas: [phpMyAdmin](https://www.phpmyadmin.net/), [Adminer](https://www.adminer.org/en/),
[pgAdmin](https://www.pgadmin.org/features/), [CloudBeaver](https://dbeaver.com/docs/cloudbeaver/),
[mongo-express](https://github.com/mongo-express/mongo-express).
Técnicas: [PostgreSQL 15 release notes](https://www.postgresql.org/docs/release/15.0/),
[roles predefinidos](https://www.postgresql.org/docs/current/predefined-roles.html),
[pg_stat_statements](https://www.postgresql.org/docs/current/pgstatstatements.html),
[túneles SSH](https://www.postgresql.org/docs/current/ssh-tunnels.html),
[pgBackRest](https://pgbackrest.org/user-guide.html), [pg_upgradecluster](https://manpages.debian.org/jessie/postgresql-common/pg_upgradecluster.1.en.html),
[persistencia de Redis](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/),
[MariaDB PITR](https://mariadb.com/docs/server/server-usage/backup-and-restore/mariadb-backup/point-in-time-recovery-pitr-mariadb-backup),
[Fedora: Redis por Valkey](https://fedoraproject.org/wiki/Changes/Replace_Redis_With_Valkey),
[instalación de MongoDB](https://www.mongodb.com/docs/manual/administration/install-community-linux/.md).
