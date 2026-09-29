# La central en 3.1: gestión de flota de verdad

Fecha: 2026-09-29 · Rama `dev/3.1` · Investigación de solo lectura (sin cambios en el código).
Ítems del dueño que cubre: 31, 33, 34, 35, 36, 37, 37b, 45.

Convención de citas: `ruta:línea` es el estado del árbol en `dev/3.1` (commit `3a19e40`). Todo lo que
sigue a "Competencia" cita URL; lo que no pude verificar en la documentación oficial lo digo.

---

## 0. Resumen

1. **Hoy la central es un proxy de un servidor a la vez.** Cada llamada de la consola va a un nodo por su
   túnel SSH; lo único agregado es la página Fleet, y ese resumen está implementado **dos veces**
   (Python en `fleet/status.py` para el CLI, TypeScript en `useFleet.ts` para la consola, 5 consultas HTTP
   por nodo y ciclo desde el navegador). No hay endpoint `/api/fleet`, ni lista de apps/certificados/backups
   de toda la flota, ni acciones masivas, ni estado "obsoleto" cuando un nodo cae.
2. **El salto silencioso del selector (37/37b) es consecuencia directa de una regla de URL**: las rutas
   "solo de la central" nunca llevan nodo (`nodeRoute.ts:22,95,105,115`) y el selector solo conoce dos
   estados, "un nodo" o "este servidor" (`ServerSelector.tsx:71-77,121`). No hay memoria del último nodo.
3. **Restricción de seguridad que el feedback no ve, y que condiciona el reparto de Ajustes (37)**: el nodo
   rechaza a propósito a un token de flota en `/api/auth*` (tokens, 2FA, sesiones), en `PUT /api/config` y en las
   secciones `web|fleet|central` de la configuración (`auth.py:268-282`, `3748-3790`). Los tokens API, la 2FA y
   la exposición de la consola de un nodo **no se pueden gestionar desde la central** sin debilitar esa guardia.
   Hay que decirlo en la interfaz (§3.c) y es una decisión del dueño si se relaja (§4).
4. **Causa raíz probable del bug del diálogo "Añadir servidor"**: React reutiliza el mismo `<button>` del pie
   entre el paso 3 ("Volver al código", `type=button`) y el paso 2 (envío, `type=submit form=…`); el `setStep(2)`
   se vacía dentro del mismo `click`, así que la acción por defecto de ese clic ya encuentra un botón de envío
   y reenvía el código malo, que solo se borra en éxito (`AddServerDialog.tsx:204,372,395-405`).
5. **Propuesta**: un agregador único (`noust/fleet/aggregate.py`) detrás de `/api/fleet/*` con envoltorio de
   resultados parciales por nodo y caché "servir obsoleto si falla"; un job de flota (`JobType.FLEET`) con
   lotes `serial` y umbral `max_failures` y resultado por nodo; un contexto de consola de tres estados
   (servidor / todos los servidores / central) con memoria del último nodo; `noust fleet authorize
   --access read|deploy|admin` como **techo del token de flota aplicado por el nodo** en `admit_fleet`; y
   `--create-tunnel-user` como cuenta de túnel sin privilegios y por defecto.

---

## 1. Cómo funciona hoy

### 1.1 Alta de un nodo, paso a paso

| Paso | Quién / dónde | Qué hace | Código |
|------|---------------|----------|--------|
| 1 | Central: `noust node key NAME` o `GET /api/nodes/{n}/key` (admin) | Genera un par ed25519 **por nodo** y devuelve `noust fleet authorize --central-key '…' --name <central>` | `web/api/nodes.py:367-391`, `fleet/nodes.py:171-200`, `fleet/keys.py:205-233` |
| 2 | Nodo, como root: `noust fleet authorize` | Comprueba sshd (`AllowTcpForwarding`, `PermitRootLogin`), asegura la consola en loopback, instala la clave con `restrict,port-forwarding,permitopen,permitlisten,command=/usr/bin/false`, crea el token `fleet-<central>` e imprime el código de unión | `fleet/authorize.py:741-863`, `cli/commands/fleet.py:136-229` |
| 3 | Central: `noust node add` o `POST /api/nodes` (sudo mode) | Exige 2FA en la central, comprueba que la huella de la clave del código es la de este nodo, fija la host key del código, guarda el token 0600, abre el túnel y llama a `/api/system/version`; si algo falla, deshace todo | `fleet/nodes.py:202-299`, `fleet/policy.py:19-47` |

El código es `noust-join:v1:` + base64url(JSON) con host key, usuario y puerto SSH, puerto de consola, token,
versión y huella de la clave de la central (`fleet/joincode.py:38,108-129`); el decodificador ya distingue
"no empieza por `noust-join:v1:`" y "versión más nueva" (`joincode.py:151-158`) pero no reconoce un token de
consola pegado por error.

### 1.2 Transporte, autoridad y lo que el nodo no deja hacer

- **Túnel**: un `ssh -N -L` por nodo, abierto a demanda, cerrado tras 600 s sin uso (`fleet/tunnels.py:46`),
  listo en ≤20 s (`:49`), reintento con backoff 1→60 s (`:52-55`), keepalive 15 s ×3 (`:64-65`). Mientras hay un
  fallo reciente, el siguiente intento falla al instante con el mensaje de ssh (`:396-404`): un nodo caído cuesta
  su timeout una vez, no en cada consulta.
- **Cliente**: `NodeClient` síncrono; token solo en `Authorization`; tres cabeceras de autoridad
  `X-Noust-Actor`, `X-Noust-Actor-Scope`, `X-Noust-Elevated` (`fleet/client.py:71-89,239-282`). Un 401 marca el
  nodo `refused` y no se le vuelve a presentar el token hasta `noust node test` (`client.py:178-205,340-354`).
- **Proxy**: `/api/nodes/{n}/api/**` (`web/api/node_proxy.py:735-802`) aplica primero la política de la central,
  pregunta al OpenAPI del nodo (caché 1 h por nodo y versión, `:153`) si la operación exige elevación
  (`x-noust-requires-elevation`) y reenvía con el alcance del operador (`open_upstream`, `:207-240`).
  `/api/nodes/{n}/events` (SSE, con "lease" del túnel para que el reaper no lo cierre, `:846-899`) y
  `/ws/nodes/{n}/**` completan el reenvío. Una sola conexión SSE por pestaña: la del nodo seleccionado.
- **Autoridad del nodo** (`web/auth.py`):
  - `admit_fleet` (`:3666-3730`): el token solo vale desde loopback y sin cabeceras de proxy inverso; el alcance
    efectivo es `X-Noust-Actor-Scope` (ausente o inválido: `read`, falla cerrado).
  - `fleet_refusal` (`:3748-3790`): `FLEET_REFUSED_PREFIXES = /api/auth, /api/nodes, /api/fleet, /api/central,
    /ws/nodes` (`:273`) salvo `FLEET_AUTH_PATHS` (`session`, `verify`, `fleet/revoke`, `:268`);
    `PUT /api/config` y `PUT /api/config/web` (`:277`); `PATCH /api/config` bajo `web|fleet|central` (`:282`).
  - `required_scope` (`:3892-3922`): GET = `read`; `POST /api/jobs/update|rollback`, activar release y
    `deployments/{id}/rebuild|rollback` = `deploy` (`:197-214`); **todo lo demás = `admin`**, crear apps incluido.
  - **No existe techo por nodo**: el token de flota es "admin recortado al alcance del operador de la central";
    el nodo no puede fijar "esta central solo lee". Tampoco hay forma de que la central lea qué puede hacer
    en un nodo (solo lo averigua cuando recibe un 403).
- Cuenta de túnel: `--ssh-user` ya existe (`cli/commands/fleet.py:143-148`, `authorize.py:748`) pero la cuenta debe
  existir (`authorize.py:578-598`), el valor por defecto es `root`, y con `PermitRootLogin no` la autorización
  se rechaza (`authorize.py:216-220`).
- `allow_shell` está en la tabla `nodes` y en `NodeResponse` pero vale siempre 0 y nada lo lee
  (`core/store.py:898-921`, `web/api/nodes.py:119,133`): hueco reservado de la 3.0.

### 1.3 Agregación hoy

- **Backend**: `fleet/status.py` (`fleet_status`, `:143-165`; `MAX_WORKERS=4` `:25`; `NODE_TIMEOUT=20` `:28`;
  tres llamadas por nodo: version, machine, certs) solo para `noust fleet status` (`cli/commands/fleet.py:301-344`).
  `router.py:127-131` monta `/nodes` y `/central`, **no** `/fleet`, aunque el prefijo ya es de la central en ambos
  lados (`auth.py:273`, `panel/src/api/nodeScope.ts:24`): está libre y sin colisión.
- **Consola**: `useFleet.ts` lanza por nodo una consulta `machine` cada 15 s y cuatro más (apps, deploys, certs,
  units) cada 60 s, solo si `machine` respondió (`:34-36,209-212`), todas por el proxy. El comentario de cabecera
  (`:1-9`) explica por qué sondea en vez de abrir un SSE por nodo: el límite de ~6 conexiones HTTP/1.1 por origen.
  "Needs attention" se calcula en el navegador (`features/overview/attention.ts`, `collectAttention`).
- Consecuencias: (a) dos implementaciones de "resumen de flota" (regla 3); (b) 5·N peticiones por ciclo y por
  pestaña; (c) ninguna caché compartida entre pestañas; (d) sin estado "obsoleto": un nodo caído deja celdas
  "not read" (`FleetPage.tsx` `Missing`), no la última lectura con su edad.
- **El estado de alcance del selector es el registro guardado, no una sonda.** `useServerList` lee `/api/nodes`
  (`nodes/servers.ts:40-51`; `nodesQuery`, 15 s, `features/fleet/nodes.ts:96-100`) y ese endpoint no sondea (`web/api/nodes.py:261-272`); el registro
  solo lo escriben `noust fleet status`, `test`, `add` y el 401 del proxy (`fleet/status.py:99-110`,
  `fleet/nodes.py:292,414-422`, `fleet/client.py:205`, `fleet/tunnels.py:519`); un fallo de túnel en el proxy
  (`node_proxy.py:308-325`) **no** lo actualiza. Un nodo que cae sigue verde en el selector hasta que alguien lo
  sondea; la página Fleet lo corrige para sus filas con lecturas frescas, el selector y `NodeNotice` no.

### 1.4 La consola: URL, contexto y Ajustes

- **URL**: cada página existe también bajo `/n/<nodo>/…`; el router reescribe en ambos sentidos
  (`app/nodeRoute.ts:100-130`, instalado en `app/router.ts:20`) y `retainSearchParams(["node"])` en la raíz
  (`routes/__root.tsx:15-16`) conserva el nodo en toda navegación. La API se antepone
  `/api/nodes/<n>` salvo `/api/auth`, `/api/nodes`, `/api/fleet`, `/api/central` (`api/nodeScope.ts:24,52-55`),
  y la caché de TanStack Query se parte por nodo salvo las raíces `auth|nodes|fleet|servers|central` (`:30,149-152`).
- **Rutas solo de la central**: `CENTRAL_ONLY_PATHS = ["/settings","/fleet","/servers","/integrations","/login",
  "/__design"]` (`nodeRoute.ts:22`); `serverPath` las escribe sin nodo (`:94-97`), `input` descarta el `node` de
  cualquier URL que las nombre (`:104-109,115-116`) y `switchTarget` manda a `/` al cambiar de servidor desde ellas
  (`:141-145`).
- **Selector**: solo dos estados. `useNode()` sale de `?node=` (`nodes/useNode.tsx:33-49`); sin nodo, el disparador
  muestra el hostname de la central y marca el radio `@this` (`ServerSelector.tsx:71,77,121`). Se oculta si no hay
  nodos (`:69`). `NodeScope` remonta **todo el shell** al cambiar de nodo (`Fragment key`, `useNode.tsx:45`;
  montado en `routes/_console.tsx:33-35`).
- **Memoria**: ninguna. El único uso de `sessionStorage`/`localStorage` en la consola es tema, idioma, avisos y el
  "continuar bloqueado" (`app/theme.ts`, `locale.ts`, `RenameNotice.tsx`, `features/central/central.ts:120-137`).
- **Barra lateral**: los ítems por servidor solo llevan nodo si la URL actual lo lleva (`Sidebar.tsx:64,72`); "Settings"
  (`:89`, `nav.ts:89-96`) es una ruta central-only, así que pulsarlo en un nodo **quita el nodo** (37).
- **Ajustes** (`routes/_console/settings.tsx:18`, `nav.ts:122-165`): siete pestañas en una sola tira, todas central-only.
  Datos que usa cada una: General y Notificaciones, `/api/config/*` (PUT elevados: `config.py:511,558,614,665`);
  Integraciones, `/api/integrations/github/**`; Tokens y Seguridad, `/api/auth/*` (siempre de la central por
  `nodeScope.ts:24`: por eso no podían ir bajo un nodo sin mostrar en silencio los datos de la central); Acerca de,
  `/api/system/version` (proxiable) pero también `sessionQuery` (hostname/versión **de la central**,
  `AboutSettings.tsx:120`; `InstallationSection` usa además `window.location.origin`).
- **Integraciones/GitHub**: el manifiesto usa el `origin` del navegador y GitHub vuelve a
  `<origin>/integrations/github/callback` (`integrations/github/manifest.py:48,84-110,207-215`; `integrations.py:15-21`),
  una ruta central-only y sin nodo.
- **Hub**: sin apps propias; redirige `/`, `/apps`… a `/fleet` (`features/central/central.ts:106-117`,
  `routes/_console.tsx:15-19`) y la barra lateral oculta lo local (`Sidebar.tsx:64`).

### 1.5 Origen de cada síntoma del feedback

| Ítem | Síntoma | Causa | Dónde |
|------|---------|-------|-------|
| 33 | La central no gestiona "toda" la flota | Sin vistas agregadas, sin `/api/fleet`, sin job masivo; solo navegación por nodo | §1.3, `router.py:127-131` |
| 33 | "Todo lo que se hace en un servidor, desde la central" | Sin API en el nodo: `noust setup` (nginx, certbot, runtimes), gestión de la consola (`noust web …`), actualizar Noust y el SO. Rechazado a propósito: `/api/auth*`, `PUT config/web` | `cli/commands/setup.py`, `auth.py:268-282` |
| 33 | "Desplegar en cualquier servidor desde un sitio" | El asistente no pregunta servidor (nada en `panel/src/features/new-app/*`) y el spec 3.0 lo prometía; en un hub `/apps/new` cae en Fleet | spec 3.0 §"La consola"; `central.ts:111-117` |
| 33 | `noust --node <cmd>` | El spec 3.0 lo describe; no aparece `--node` en `src/noust` ni en `docs/*.md` | `grep` sin resultados |
| 37 | Ajustes te devuelve a la central | `/settings` en `CENTRAL_ONLY_PATHS`; `serverPath`/`input` quitan el nodo | `nodeRoute.ts:22,95,105,115` |
| 37b | Lo mismo en `/fleet` | Idéntico; además el selector no tiene estado "flota/central" y no recuerda de dónde vienes | `ServerSelector.tsx:71-77,121`, `nodeRoute.ts:141-145` |
| 31 | `authorize` pide parar la consola | Sin unidad, `authorize` llama a `web._enable`, que rechaza si hay un demonio de `noust web start -d`: "A console already runs in the background… Stop it first" | `fleet.py:127-132`, `web.py:1829-1833` |
| 34 | Banner con el token maestro a mitad de `authorize` | `_enable` **emite un token maestro nuevo** (jubila el anterior) y lo imprime | `web.py:1866,1878` |
| 35 | Sin progreso ni spinner | `authorize` no emite nada hasta el final; `Logger` tiene `step/substep` pero no spinner | `fleet.py:205-229`, `core/logger.py:354,373` |
| 36 | `authorize` en la central que emitió la clave | Sin detección: nunca compara la clave con las de este equipo | `authorize.py:788-863` |
| Diálogo | "Volver al código" reenvía el código malo | Botón reutilizado entre pasos; el código solo se borra en éxito | `AddServerDialog.tsx:204,372,395-405`, `Button.tsx:71` |
| Diálogo | Se puede pegar un token `noust_` | Sin validación de prefijo en el cliente; el código va en un `password` que oculta lo pegado | `AddServerDialog.tsx:255-264,331` |
| 45 | Clave en `authorized_keys` de root | Valor por defecto y cuenta previa obligatoria; con `PermitRootLogin no` no enrola | `authorize.py:216-220,578-598,748` |
| 45 | Sin permisos por nodo | `admit_fleet` no conoce techo del token | `auth.py:3666-3730` |

---

## 2. Competencia: UX multi-servidor

Las URL son las verificadas. Marco **[docs]** lo leído en documentación oficial, **[terceros]** blogs y foros,
**[no verificado]** lo que no pude confirmar.

### 2.1 Qué hace cada uno

| Producto | Modelo | Listas de flota | Fallo parcial | Acciones masivas | "En qué servidor estoy" |
|----------|--------|-----------------|---------------|------------------|-------------------------|
| **Proxmox Datacenter Manager** ([intro](https://pdm.proxmox.com/docs/introduction.html), [roadmap](https://pdm.proxmox.com/docs/roadmap.html), [1.0](https://proxmox.com/en/about/company-details/press-releases/proxmox-datacenter-manager-1-0)) [docs] | Central que habla con "remotes" por su API con tokens; al quitar un remote se borra el token generado (1.1) | Panel global "resalta problemas"; inventario y lista de invitados de todos los remotes; **Views** (vistas filtradas con permisos propios); descripción de actualizaciones de todos los remotes; tareas de todos los remotes con filtro por remote y resumen de fallos de las últimas 48 h | Las tareas remotas se **sondean con jobs periódicos y se cachean**; en migraciones "prefiere hosts conocidos como alcanzables"; degrada con gracia si un remote no tiene un endpoint | Acciones masivas sobre invitados en la lista central, con "progreso agregado y resultados por elemento" en CLI (hoja de ruta) | Árbol con raíz "Datacenter" = ámbito de todos los remotes, distinto de cada nodo |
| **Proxmox VE bulk actions** ([GUI](https://pve.proxmox.com/pve-docs/chapter-pve-gui.html), [parche](https://lore.proxmox.com/all/20251114145927.3766668-6-d.csapak@proxmox.com/t), [foro](https://forum.proxmox.com/threads/bulk-migration-parallel-jobs-vs-max-workers.74536/latest)) [docs][terceros] | Bulk Start / Shutdown / Migrate | n/a | Los fallos "se imprimen y se guardan (vmid) y se listan juntos al final" | `max_workers` (paralelismo; opción de clúster "Maximal Workers/bulk-action") | n/a |
| **Portainer** ([entornos](https://docs.portainer.io/admin/environments), [Edge](https://docs.portainer.io/admin/environments/add/docker/edge), [Edge stacks](https://docs.portainer.io/user/edge/stacks)) [docs] | Un servidor, muchos "entornos"; **grupos y etiquetas**; acceso por entorno, grupo o política; agente Edge que **sondea** (modo estándar, túnel a demanda) o solo saliente ("async": ping, snapshot y comprobación de órdenes) | Home con un mosaico por entorno; los mosaicos se alimentan de un **snapshot** (5 min por defecto [terceros: [blog](https://oneuptime.com/blog/post/2026-03-20-configure-environment-snapshots-portainer/view)]) que sigue mostrándose aunque el entorno esté caído; "Last check-in" en Edge | Se ve la última lectura y su edad; sala de espera para altas automáticas [terceros] | Edge stacks a **grupos** de entornos; la página del stack muestra el estado por entorno en etapas ("acknowledged, images pre-pulled, deployments received, failed") y una pestaña Environments con logs [docs] | Nombre del entorno arriba del menú lateral |
| **Rancher / Fleet** ([proxy de autorización](https://documentation.suse.com/cloudnative/rancher-manager/v2.11/en/about-rancher/architecture/communicating-with-downstream-clusters.html), [rollout](https://fleet.rancher.io/how-tos-for-users/rollout)) [docs] | El agente del clúster abre un túnel saliente; un proxy **autentica al llamante y pone cabeceras de impersonación** antes de reenviar (equivale a `X-Noust-Actor(-Scope)`) | Lista de clústeres con estado (Active/Unavailable) | Un clúster sin agente aparece "Unavailable" y las operaciones se paran | Fleet: `maxUnavailable` (100 % por defecto), `maxUnavailablePartitions` (0), `autoPartitionSize` (25 %), particiones de hasta 50 clústeres, particiones manuales y `clusterGroup`; estado por clúster (BundleDeployment) | Un clúster seleccionado a la vez |
| **Coolify** ([servidores](https://coolify.io/docs/knowledge-base/server/introduction), [parcheo](https://coolify.io/docs/knowledge-base/server/patching), [conceptos](https://coolify.io/docs/get-started/concepts)) [docs] | Servidores (localhost, de despliegue, de build) por SSH; el recurso elige su servidor (destino) al crearse | No documentan una lista de recursos entre servidores [no verificado que no exista]; cada servidor tiene pestañas (Resources, Terminal, Security/Patches, Metrics, Cleanup) [terceros: [guía](https://azdigi.com/en/blog/self-hosted/coolify-interface-detailed-dashboard-usage-guide)] | n/a | **Parcheo del SO por servidor**: comprobar, listar paquetes, actualizar uno o todos (APT, DNF, Zypper), sin automatismo, y con aviso expreso de que actualizar Docker reinicia Docker y con él todas las apps | Página del servidor |
| **Dokploy** ([servidores remotos](https://docs.dokploy.com/docs/core/remote-servers), [multi-server](https://docs.dokploy.com/docs/core/multi-server)) [docs] | Servidores de despliegue y de build por SSH; la app elige su servidor en su configuración; el resumen de búsqueda indica que exige root y que no admite despliegue sin root [no verificado en la página completa] | Lista de servidores; sin vista de apps entre servidores documentada | n/a | No documentadas | n/a |
| **Laravel Forge / Ploi** ([Recipes](https://laravel.com/forge/docs/recipes), [índice](https://laravel.com/forge/docs/llms.txt), [Ploi](https://ploi.io/features)) [docs] | Servidores con sitios; organizaciones y equipos comparten servidores y recetas; Ploi asigna permisos granulares **por servidor** | No pude verificar una página "todos los sitios" en la documentación | n/a | **Recipes**: se ejecutan en los servidores elegidos, con registro de ejecuciones y salida completa por ejecución, opcionalmente por email | Nombre del servidor en la navegación |
| **Netdata Cloud** ([estados](https://learn.netdata.cloud/docs/netdata-cloud/node-states-and-transitions)) [docs] | Nodos reclamados en "rooms" | Home con recuento por estado | Cuatro estados: **Live, Stale** (desconectado pero con datos vía Parent), **Offline** (sin datos), **Unseen**; los gráficos agregados siguen con los datos disponibles | n/a | Filtro de nodos |
| **Cockpit multi-host** ([guía](https://docs.cockpit-project.org/cockpit-guide/363/guide/feature-machines.html), [deprecación](https://cockpit-project.org/blog/cockpit-322.html)) [docs] | Máquinas adicionales por SSH desde la primera; función **deprecada desde la 322** | n/a | n/a | n/a | Selector de host. Motivo de la deprecación: en la web no se puede aislar a los hosts entre sí ("canales remotos directos, iframe traversal, cache poisoning…"); recomiendan conectar a un único host por sesión |
| **Komodo** ([intro](https://komo.do/docs/intro)) [docs] | Core + agente Periphery en cada servidor; "sin límite de servidores" | Servidores con CPU/mem/disco y alertas; Procedures y Actions para automatizar | n/a | Detalle no documentado en la página leída | n/a |
| **Ansible** ([delegación](https://docs.ansible.com/ansible/2.9/user_guide/playbooks_delegation.html)) [docs] | Inventario | n/a | Resumen por host | `serial` (lotes: `1`, `"20%"`, `[1,5,"20%"]`), `max_fail_percentage` (aborta cuando se **supera**, no al igualar) | n/a |
| **Uptime Kuma** ([wiki](https://github.com/louislam/uptime-kuma/wiki/Status-Page)) | Monitores agrupados en páginas de estado | Lo único que pude verificar: la página se cachea y refresca cada 5 min; el resto (grupos, banner global) es [no verificado] | n/a | n/a | n/a |

### 2.2 Lo que se repite y conviene copiar

1. **Una tabla con columna "Servidor" y filtro de servidor**, no una página por servidor (PDM: lista central de invitados;
   Netdata: Nodes). La fila enlaza a la página del servidor.
2. **El fallo parcial no vacía la tabla.** Se muestran las filas que hay, con su edad, y un aviso persistente con un
   chip por servidor que falló (Netdata: Stale vs Offline; Portainer: snapshot y "Last check-in"; PDM: caché de tareas).
   Distinguir "no responde ahora, dato de hace 3 min" de "sin dato".
3. **Acciones masivas con objetivo por selección o por grupo/etiqueta** (Portainer Edge Groups, Fleet `clusterGroup`,
   PDM Views), **plan previo**, **perilla de despliegue** (`serial`, tope de fallos, canario: Ansible, Fleet, PVE
   `max_workers`) y **resultado por elemento** con etapas (Portainer). Casi nadie ofrece "reintentar solo los fallidos":
   es diferencial barato.
4. **El contexto "todos" es un ámbito propio**, no "el primer servidor": raíz "Datacenter" de Proxmox; nombre del
   entorno en el menú de Portainer.
5. **Impersonación en el proxy** (Rancher) es el mismo patrón que `X-Noust-Actor(-Scope)`: validado.
6. **Cockpit deprecó su multi-host por servir código de la máquina remota bajo el origen de la central.** Noust solo
   cruza JSON por el proxy y la consola es la del origen de la central (CSP estricta, `require-trusted-types-for`):
   esa clase de problema no se aplica **mientras** no se sirva nunca HTML/JS del nodo. Convertirlo en invariante
   de tests (el proxy ya elimina `set-cookie` y solo devuelve una lista blanca de cabeceras, `node_proxy.py:97-123`).
7. **El parcheo del SO existe en los pares** (Coolify, PDM) y siempre con advertencia; la escritura es la parte de
   riesgo, la lectura ("hay N paquetes, hace falta reinicio") es barata y segura.
8. **Root para todo** es la norma (Dokploy lo exige según el resumen de su documentación): una cuenta de túnel sin
   privilegios sería un diferencial de Noust.

---

## 3. Diseño

Principios: regla 3 (una implementación de cada cosa: un agregador, un motor de jobs, un sitio donde el nodo
limita a la central); regla 4 (la guardia en el punto de paso: `admit_fleet`/`fleet_refusal`, no en la consola); el
nodo sigue siendo la autoridad; todo proceso por `CommandRunner`; los errores de sistema, verbatim.

### 3.a Vistas y acciones de flota

#### Backend: agregador único

- Nuevo `src/noust/fleet/aggregate.py`, que **sustituye** el núcleo de `fleet/status.py` (el CLI `noust fleet status`
  pasa a llamarlo; `node_summary`/`fleet_status` desaparecen como implementación aparte) y alimenta `/api/fleet/*`.
  Síncrono y con hilos (como hoy, `ThreadPoolExecutor`), para servir igual al CLI y a la API (`def` en FastAPI o
  `run_in_threadpool`), reutilizando `NodeManager.client(...)` y `NodeClient.get_json(..., actor_scope="read")`.
- Router nuevo `src/noust/web/api/fleet.py` montado en `/api/fleet` (prefijo libre, ver §1.3); `require_auth`,
  solo lecturas con `read`; las acciones (§3.b) son `POST` y exigen `admin` y sudo mode. El nodo sigue rechazando
  `/api/fleet` a un token de flota (`auth.py:273`), así que una central no puede usar un nodo como salto.
- Endpoints (todos con `?node=` repetible, `?refresh=1`, y los filtros que abajo se dicen):

| Endpoint | Sale de (API del nodo) | Filas / columnas |
|----------|------------------------|------------------|
| `GET /api/fleet/summary` | `/api/system/version` (registro), `/api/system/machine` | Por servidor: alcance, versión, latencia, edad del dato, CPU/mem/disco, apps `running/failed/stopped/static`, units fallidas, certificados por caducar, acceso (`read/deploy/admin`), Noust disponible |
| `GET /api/fleet/apps` | `/api/apps`, `/api/deployments?limit=` | Servidor · Aplicación (dominio) · Estado (vocabulario de `core/app_state.resolve_state`) · Tipo · Rama · Layout (`releases`/`inplace`) · Último despliegue (estado, edad, commit) · Puerto |
| `GET /api/fleet/certificates` | `/api/certs` | Servidor · Dominios · Caduca (fecha y días, orden por defecto ascendente) · Renovación automática · Emisor |
| `GET /api/fleet/backups` | `/api/backups`, `/api/backup-schedules`, `/api/backup-destinations` | Servidor · App · Último backup (edad) · Tamaño · Contiene (BD, env, build) · Verificado (ok / fallido / nunca, fecha) · Programación · Destino. Franja superior de **huecos**: apps sin backup, con backup viejo, sin programación |
| `GET /api/fleet/updates` | `/api/system/version` (+ nuevos, §3.b) | Servidor · Noust instalado → disponible (`up_to_date/update_available/on_the_way`, método: apt/dnf/zypper/pip/pipx) · Paquetes del SO pendientes y de seguridad · Reinicio necesario · Comprobado hace |
| `GET /api/fleet/activity` | `/api/audit` de cada nodo + la de la central | Hora · Servidor · Actor (con "on behalf of") · Acción · Recurso · Resultado |
| `GET /api/fleet/jobs` | `/api/jobs/active` de cada nodo + jobs de flota | Lo que corre en cualquier sitio |
| `GET /api/fleet/search?q=` | `/api/apps` cacheado | Para la paleta de comandos: hoy solo lista las apps del servidor seleccionado (`useConsoleCommands.tsx:73`) |

- **"Needs attention" no se reimplementa**: se queda `collectAttention` (TypeScript) alimentado con las filas
  agregadas (cada fila lleva `node`). Solo se mueve al servidor el abanico de peticiones, no la lógica de dominio.
- **Envoltorio común** (siempre HTTP 200 salvo error de la central, p. ej. 423 `central_locked`):

```json
{
  "generated_at": "2026-09-29T10:00:00Z",
  "partial": true,
  "nodes": [
    {"node": "web-1", "state": "ok",          "elapsed_ms": 84,   "fetched_at": "…", "age_s": 0},
    {"node": "web-2", "state": "stale",       "elapsed_ms": 8000, "fetched_at": "…", "age_s": 190,
     "error": {"error": "node_unreachable", "detail": "…", "hint": "…", "output": "ssh: connect to host … (verbatim)"}},
    {"node": "db-1",  "state": "unsupported", "reason": "GET /api/backup-schedules not offered by Noust 2.3"},
    {"node": "edge",  "state": "forbidden",   "reason": "fleet_ceiling"}
  ],
  "items": [ {"node": "web-1", "domain": "shop.example.com", "status": "running"} ]
}
```

  `state` ∈ `ok | stale | unreachable | refused | locked | timeout | unsupported | forbidden`. `error` usa
  el contrato de la API (`node_proxy.py:286-305`: `error/detail/hint/output`) para que la consola lo pinte con
  `ErrorBlock`, verbatim. `unsupported` sale del OpenAPI del nodo que el proxy ya cachea (`NodeSchema.operations`,
  `node_proxy.py:354-393`) en lugar de provocar un 404 por nodo.
- **Concurrencia y plazos**: hilos = `min(N, 16)` (hoy 4, `status.py:25`, pensado para 3 llamadas por nodo); plazo por
  nodo **8 s** en vistas interactivas (hoy 20 s, `status.py:28`) y plazo total 10 s. Los rezagados devuelven
  `timeout` pero **siguen en segundo plano** para calentar la caché; el siguiente sondeo ya trae dato. Un nodo con
  backoff de túnel activo falla al instante (`tunnels.py:396-404`).
- **Caché** (en memoria del proceso de la central, clave `(nodo, recurso, versión del nodo)`):
  TTL `summary` 10 s, `apps` 15 s, `backups` 60 s, `certificates` 300 s, `updates` 1 h (con `?refresh=1`);
  **single-flight** por clave (varias pestañas cuestan lo mismo que una); **servir obsoleto si falla**: al fallar
  un nodo se sirve el último bueno con `state:"stale"` y `age_s`, hasta 1 h (24 h para `updates`/`certificates`).
  Fase 3: persistir el último bueno en el store (`fleet_snapshots`, migración versionada) para que reiniciar la
  central (un NAS) no deje la consola en blanco. Nunca se cachean escrituras ni las páginas de un servidor
  seleccionado (van por el proxy, en vivo).
- **Sonda de alcance**: el resumen actualiza `set_node_status` en cada lectura (como hacía `node_summary`) y un
  hilo ligero de la central (patrón del reaper, `tunnels.py:695-716`) sondea cada 30 s mientras haya un cliente de
  consola conectado, para que **selector y `NodeNotice` dejen de depender de un registro rancio** (§1.3).
- **Eventos/SSE**:
  - Fase 1: sin flujos nuevos; las páginas de flota sondean `/api/fleet/*` (1 petición por página y ciclo, no 5·N),
    con lo que desaparece la razón del comentario de `useFleet.ts:1-9`.
  - Fase 3: `GET /api/fleet/events`. La central se suscribe a `/events` de cada nodo **solo mientras haya al menos
    un suscriptor de flota**, con `tunnels.hold()` (`node_proxy.py:239`), reenvía únicamente `machine`, `app`, `job`
    y `notice` etiquetados con `node` (se descartan los `metrics` de 2 s, `events.py` `METRICS_INTERVAL_SECONDS`),
    y añade `node` (cambio de estado del túnel/registro) y `fleet_job` (progreso de un job masivo). La central en
    sí entra por su `EventHub`, sin túnel. Cola acotada por suscriptor; al desbordarse, evento `resync` que
    invalida las consultas. Revalidación de credencial como `_relay_events` (`node_proxy.py:805-843`). En páginas de flota
    este flujo **sustituye** al del nodo seleccionado (una sola `EventSource` por pestaña).
- **Sin cambios de contrato para los nodos**: todo sale de endpoints que ya existen, salvo `updates` (§3.b) y
  `GET /api/auth/fleet/self` (§3.f).

#### Páginas de la consola

- `/fleet` pasa a ser un **layout con pestañas** (`LinkTabs`, como `settings.tsx`) y un solo ítem "Fleet" en la
  barra lateral: **Resumen** (`/fleet`), **Aplicaciones** (`/fleet/apps`), **Certificados** (`/fleet/certificates`),
  **Copias** (`/fleet/backups`), **Actualizaciones** (`/fleet/updates`), **Actividad** (`/fleet/activity`) y el
  detalle de un job (`/fleet/jobs/$id`). Todas central-level: nunca llevan `?node=`; las filas enlazan a
  `/n/<nodo>/apps/<dominio>` con `ServerLink`. Filtros en la URL (`?server=web-2&server=db-1&status=failed`),
  compartibles.
- Cada tabla: filtro de servidor (multi), filtro por estado, búsqueda, agrupar por servidor (conmutador), orden por
  "lo que falla primero", columna Servidor siempre visible, selección múltiple para acciones.
- **Aviso de resultado parcial** encima de la tabla, persistente y con `role="status"`: "2 de 5 servidores no
  respondieron" y un chip por servidor con forma+color+texto (estado, edad y **error verbatim** desplegable). Las
  filas obsoletas llevan la edad ("hace 3 min"). Nunca se oculta una fila por venir de un servidor caído.
- Hub: estas pestañas **son** su navegación principal (`Sidebar.tsx:64`).
- **Desplegar en cualquier servidor desde un sitio**: botón "Nueva aplicación" en `/fleet/apps` (y en el estado
  vacío de un hub) abre un selector de servidor (lista con alcance, acceso y versión; los de solo lectura o caídos,
  deshabilitados **con su razón**) y navega a `/n/<nodo>/apps/new`. Sustituye el redirect `hubRedirect`
  (`central.ts:111-117`) que hoy solo dice "elige un servidor". El asistente muestra el servidor en su cabecera.
  Desplegar el mismo origen en varios servidores queda para la fase 3 (N creaciones independientes en un job).
- **Cobertura de paridad** (ítem 33): tabla en `docs/CENTRAL.md` con lo que **no** se puede hacer desde la central y por qué
  (`setup`, gestión de la consola, `/api/auth*`, `PUT config/web`), y cuál es el comando en el nodo.
  `noust --node` (no existe) no se implementa: su valor lo cubre el proxy; se retira del spec.

### 3.b Acciones masivas como job de la central

- **Endpoint**: `POST /api/fleet/actions` (sudo mode si alguna operación destino lo marca, decidido con el OpenAPI
  cacheado de cada nodo, `NodeSchema.requires_elevation`; **una** elevación cubre el job, con la ventana de 10 min
  de `is_elevated`, y el job la atestigua con `X-Noust-Elevated` capturada al crearlo; un job no inicia pasos nuevos
  pasados 30 min de esa elevación). `POST …/actions?plan=true` devuelve el plan sin ejecutar.

```json
{ "action": "certs_renew",
  "targets": {"nodes": ["web-1", "web-2"]},
  "strategy": {"serial": 1, "max_failures": 1, "canary": "web-1"},
  "options": {"force": false} }
```

- **Acciones** (primera columna = qué es hoy, segunda = qué falta en el nodo):

| Acción | Endpoint de cada nodo | Estado |
|--------|-----------------------|--------|
| `probe` (probar todos) | `POST /api/nodes/{n}/test` (en la central) | Existe; síncrono, no es job |
| `certs_renew` | `POST /api/certs/renew-all` (`certs.py:181-215`) | Existe (job en el nodo) |
| `backups_run` | `POST /api/backups` por app (`backups.py:345`) | Existe; sub-resultados por app |
| `backups_verify` | `POST /api/backups/{id}/verify` (`backups.py:408`) | Existe |
| `apps_update` / `apps_restart` | `POST /api/jobs/update`, acciones de app | Existen; alcance `deploy` |
| `noust_update` | **Nuevo** `POST /api/system/update` (admin + elevación) y `GET /api/system/update` | Falta. Hoy solo hay el texto `update_command` (`system.py:595-632`, `update_checker.py:631-651`) |
| `os_updates_check` | **Nuevo** `GET /api/system/updates` (`apt-get -s`, `dnf check-update`, `zypper lu`; `security`, `reboot_required`) | Falta; solo lectura, barato |
| `os_updates_apply` | **Nuevo** `POST /api/system/updates` (job) | Falta; **fase 3 y opt-in**: escribe el SO |

- **`noust_update` en el nodo**: por el método de instalación detectado (`update_checker.py:562-628`), ejecuta con
  `CommandRunner` (argv, timeout, `DEBIAN_FRONTEND=noninteractive` por `env=`) **desde una unidad transitoria**
  (`systemd-run --unit noust-self-update --collect`), porque el proceso que lanza el job es la consola y se reinicia.
  Escribe `/var/lib/noust/self-update.json` (de, a, inicio, resultado, cola del journal) que la consola lee al
  volver y expone en `GET /api/system/update`, de modo que la central averigua el resultado aunque se pierda la
  ventana. Un nodo-contenedor (hub en imagen) responde `skipped: container image` con la orden de `docs/CENTRAL.md:222-230`.
  Requiere techo `admin` en el nodo (§3.f) y elevación.
- **Ejecución**: nuevo `JobType.FLEET` en `web/jobs.py:107-125`; el job es persistente, con log y progreso como los
  demás. `total_steps` = nodos objetivo; `result` guarda el estado por nodo y se reescribe en cada transición:

```json
{ "action": "noust_update", "strategy": {"serial": 1, "max_failures": 1},
  "summary": {"succeeded": 2, "failed": 1, "skipped": 1, "queued": 3},
  "nodes": [
    {"node": "web-1", "state": "succeeded", "step": "version 3.1.0 confirmed", "job_id": "a1b2c3d4",
     "started_at": "…", "ended_at": "…"},
    {"node": "web-2", "state": "failed", "step": "waiting for the console",
     "error": {"error": "…", "detail": "…", "output": "journal tail (verbatim)"}},
    {"node": "edge",  "state": "skipped", "reason": "policy: read-only"} ] }
```

  Estados por nodo: `queued · running · succeeded · failed · skipped (policy | unsupported | not_needed | aborted) ·
  unreachable · refused · cancelled`, con forma+texto+color del vocabulario de estados de la consola.
- **Política de despliegue** (Ansible/Fleet/PVE): `serial` = tamaño de lote (por defecto 1 para `noust_update` y
  `os_updates_apply`, 4 para `certs_renew`/`backups_*`; acepta `"25%"`), `max_failures` = **se aborta cuando se supera**
  (semántica de `max_fail_percentage`, no al igualar), `canary` = nodo que va solo primero. Al abortar, los nodos sin
  empezar quedan `skipped: aborted`. **Un solo job de flota por nodo a la vez** (cerrojo por nodo).
- **Por nodo**, el motor hace: (1) *preflight* (alcance, versión, `unsupported` por OpenAPI, techo de acceso, `not_needed`,
  p. ej. ya está en la versión destino); (2) llamada al nodo con `NodeClient.request(actor, actor_scope, elevated)`; si
  el nodo devuelve un `job_id`, sondea `GET /api/jobs/{id}` en el nodo (o consume los `job` del flujo si hay suscriptor)
  y copia estado y cola de log al log del job de la central con el prefijo `[web-2]` (pasa por `Job.add_log`, que
  depura secretos); (3) *post-check*: para `noust_update`, espera a que `/api/system/version` diga la versión esperada
  (cierra y reabre el túnel; plazo 300 s) y compara `/api/system/health` con el de antes; para el SO **nunca reinicia**
  solo: `reboot_required` se muestra y el reinicio es otra acción con `serial:1` y espera de vuelta.
- **Orden por defecto de una actualización de Noust**: primero los nodos, la central al final y **fuera del job** (un
  job no puede reiniciar a su propio proceso); la fila de la central muestra la orden a ejecutar (`update_command`).
  Justificación: la central es el cliente y debe seguir siendo capaz de ver fallar a los nodos; con nodos más viejos
  o más nuevos que la central la consola ya se degrada por capacidades (`nodes/capability.tsx`, `NodeNotice`).
  No hay reversión automática de un paquete, así que la red de seguridad es `serial` + `max_failures:1` + canario.
- **Página del job** (`/fleet/jobs/$id`): cabecera con recuentos (ok/fallidos/en curso/en cola/omitidos, en texto y con
  forma), tabla por nodo con estado, paso, tiempo y enlace al job del nodo (`/n/<nodo>/activity`), salida verbatim
  desplegable, y **"Reintentar los fallidos"** (crea un job nuevo con esos nodos como objetivo). Progreso por el
  mecanismo existente (`job` en `/events`, `/ws/jobs/{id}`).
- **Diálogo de plan** antes de ejecutar: "se ejecutará en 4 servidores; 1 omitido: solo lectura", con el orden de los
  lotes, y "Confirm it's you" si procede.
- **Reinicio de la central a mitad de job**: el job se marca fallido con "the central restarted; node jobs may still
  be running" y conserva los `job_id` de los nodos, que siguen por su cuenta y son consultables.
- **Auditoría**: `fleet.action.<nombre>` en la de la central (objetivos y resumen, nunca secretos) y, en cada nodo,
  cada llamada con el actor `fleet-<central> on behalf of <operador>` (`auth.py:3575-3580`).
- **CLI equivalente** (misma lógica, regla 3): `noust fleet apps|certs|backups|updates [--json] [--node N] [--status S]`;
  `noust fleet run ACTION [--nodes a,b] [--serial 1] [--max-failures 1] [--canary N] [--plan] [-y]`; `noust fleet jobs [ID]`.
  Salida distinta de cero si algún nodo falla; `--json` incluye `nodes[]`.

### 3.c Reparto de Ajustes por ámbito

**Regla que decide el reparto**: lo que el nodo permite a una central es lo que puede vivir bajo `/n/<nodo>/settings/…`;
lo que el nodo prohíbe a una central (`auth.py:268-282`) o es de la propia central vive en `/settings/…` central-level,
con nota visible. **Tokens API y Seguridad de un nodo no se pueden mover al ámbito por servidor sin cambiar esa
guardia**: la consola no puede mostrarlos en silencio (los datos vendrían de la central, `nodeScope.ts:24`).

| Pestaña | Ámbito | Ruta | Datos | Con un nodo seleccionado |
|---------|--------|------|-------|--------------------------|
| General | **Servidor** | `/settings`, `/n/<n>/settings` | `/api/config/{apps-directory,webserver,ssl,backup}` (PUT elevados) | Todo, **salvo** "Console address": `PUT /api/config/web` está rechazado (`auth.py:277`); se muestra en solo lectura con el comando `noust web …` para ese servidor |
| Notificaciones | **Servidor** | `/settings/notifications` | `/api/config/smtp`, `/api/config/notifications/*` | Todo. Cada servidor envía sus propias alertas |
| Integraciones | **Servidor** | `/settings/integrations` | `/api/integrations/github/**` | Todo, con el callback de abajo |
| Acerca de | **Servidor** | `/settings/about` | `/api/system/version`, `/api/system`, `/api/config` | Todo. Corregir `hostname` y "Console address" (hoy son los de la central: `AboutSettings.tsx:120`, `InstallationSection`). Sección nueva **"Acceso de esta central a este servidor"** (§3.f). En la fase 2, botón "Actualizar Noust en este servidor" |
| Servidores | **Central** | `/settings/servers` | `/api/nodes*` | No lleva nodo; columna nueva "Acceso" |
| Central (nueva) | **Central** | `/settings/central` | `/api/central*` | Identidad (`central.name`, `fleet/models.py:385-404`), rol, redes permitidas, huella TLS, estado del sellado y formulario de desbloqueo (`UnlockForm.tsx`); sellar/desellar sigue siendo CLI (`docs/CENTRAL.md`) |
| Seguridad | **Central** | `/settings/security` | `/api/auth/*`, lockout | Son el inicio de sesión, la 2FA y las sesiones **de la central** |
| Tokens API | **Central** | `/settings/tokens` | `/api/auth/tokens` | Son los tokens que llaman **a la central** (que reenvía con su alcance) |

- **URLs**: `CENTRAL_ONLY_PATHS` deja de contener `"/settings"` entero y pasa a `["/fleet", "/settings/servers",
  "/settings/central", "/settings/security", "/settings/tokens", "/integrations", "/login", "/__design"]`
  (`nodeRoute.ts:22`; `under()` ya cubre subrutas). `/settings`, `/settings/notifications`, `/settings/integrations` y
  `/settings/about` pasan a llevar nodo: `/n/web-2/settings/...`. Los enlaces `/settings/security` de
  `AddServerDialog.tsx:154`, `ServersSettings.tsx:106` y `/settings/servers?add` de `FleetPage.tsx:33` siguen válidos.
- **Sin nodos** (servidor suelto): la tira de pestañas es la de hoy, sin grupos ni etiquetas.
- **Con nodos**: la tira se parte en dos grupos con encabezado visible y separador: **"Este servidor · web-2"**
  (General, Notificaciones, Integraciones, Acerca de) y **"Central · nas"** (Servidores, Central, Seguridad, Tokens API),
  cada pestaña central con un `Badge` "Central" (acromático) y, al entrar, una línea bajo el título: "Estos ajustes son
  de la central (nas) y no cambian al elegir otro servidor." El título de página de las pestañas por servidor lleva el
  servidor ("Ajustes · web-2") y `useDocumentTitle` también.
- **Nota bajo la tira cuando hay un nodo seleccionado**: "El inicio de sesión, la 2FA y los tokens de web-2 se gestionan
  en web-2: `noust web token --new`, `noust 2fa enroll`, `noust token create`. Esta central no puede cambiarlos (a
  propósito: una central comprometida podría dejar fuera al operador). [Por qué]".
  `SETTINGS_TABS` gana `scope: "server" | "central"`; `useConsoleCommands.tsx:60-72` (paleta) hereda la etiqueta.
- **Callback de GitHub por nodo** (ruta fija `/integrations/github/callback`, `manifest.py:48`, registrada en GitHub):
  al iniciar el flujo con un nodo seleccionado la consola guarda `{node, state}` en `sessionStorage` (`state` lo genera
  el nodo, `ManifestOut`); `GitHubCallback` lo lee, comprueba el `state`, llama a `manifest/conversions` **por el proxy**
  de ese nodo y termina en `/n/<nodo>/settings/integrations`. Sin cambios en el backend. Limitación a decirlo en la
  pestaña: el webhook necesita la URL pública del nodo (`service.github_hooks_url()`, `None` si no hay
  URL pública); una consola solo en loopback no recibe webhooks.
- **Hub**: General oculta las secciones de despliegue (directorio de apps, servidor web, certificados, copias) y conserva "Console address"; Notificaciones y Acerca de se quedan.
- Los PUT de General y Notificaciones ya exigen elevación y siguen el camino normal (`x-noust-requires-elevation` →
  "Confirm it's you" en la central). Los secretos que se teclean (contraseña SMTP, token de Telegram) viajan por el túnel
  y la central no los guarda.
- Fase 3: "Aplicar estos ajustes a otros servidores" (SMTP/Telegram/backup) como acción de flota.

### 3.d Selector, contexto y navegación

**Tres contextos** (ninguno finge ser otro):

| Contexto | Páginas | Etiqueta del selector | Peticiones |
|----------|---------|-----------------------|------------|
| `server(node)` | `/…` y `/n/<n>/…` por servidor | `web-2` (o el hostname en este servidor) con glifo y palabra si hay problema | Proxy al nodo o local |
| `fleet` | `/fleet/**` | **"All servers"** + recuento y peor estado ("4 servidores · 1 caído") | `/api/fleet/*` |
| `central` | `/settings/{servers,central,security,tokens}` | **"This central · nas"** con `Badge` "Central" | API de la central (no se proxifica) |

- **Selector**: `RadioGroup` con valores `@all`, `@central` (solo se marca cuando estás en ese contexto; elegirlo va a
  `/settings/servers`), `@this` y cada nodo (`ServerSelector.tsx:121` ya usa `@this`). Se muestra siempre que haya nodos
  y también en un contexto de flota/central. Al cambiar de contexto se anuncia por `announce` (`:83-85`): "Viendo todos
  los servidores", "Ajustes de la central", "Servidor web-2". `MachineStrip` cambia: en `fleet`, "4 servidores · 3
  activos · 1 caído · 2 necesitan atención"; en `central`, la máquina de la central etiquetada "nas (central)".
- **Memoria del último servidor**: `lastNode` en `sessionStorage` (por pestaña, patrón de `central.ts:120-137`: dos
  pestañas en dos servidores es un flujo legítimo), escrito por `NodeScope` cada vez que el contexto es `server`
  (`useNode.tsx:33-49`) y también cuando una URL `/n/<n>/fleet` antigua se normaliza a `/fleet`. Se valida contra
  `useServerList()` (nodo borrado → `null`).
  - En `fleet` y `central`, los ítems por servidor de la barra lateral llevan `search={{node: lastNode}}`: "Applications"
    vuelve a `/n/web-2/apps`, no a `/apps` (`Sidebar.tsx:72` hoy solo hereda el nodo de la URL). Bajo esos ítems, una
    etiqueta persistente **"Servidor: web-2"** (o "This server").
  - Cabecera de las páginas de flota y central: chip "Volver a web-2" (enlace a `/n/web-2/…`, la página equivalente si la
    hay, si no el resumen, como `switchTarget`).
  - Un hub sin `lastNode` oculta los ítems por servidor (como ahora, `Sidebar.tsx:64`) hasta elegir uno.
  - "Volver" del navegador funciona: los tres contextos son solo URLs.
- **Todas las páginas por servidor**, cuando hay nodos, llevan el nombre del servidor como sobretítulo mono encima del
  `<h1>` (`PageHeader`): responde "¿en qué servidor estoy?" también sin mirar la barra superior.
- **Remontaje**: `NodeScope` (`useNode.tsx:45`) remonta el shell al cambiar de nodo; la clave pasa a ser el nodo solo en
  `server(...)` y una constante `"~central"` para `fleet` y `central`, de modo que `fleet ⇄ settings` no remonte nada y
  `fleet → nodo` sí (flujo SSE nuevo). El tipo pasa a `SelectedContext = {kind:"server", node} | {kind:"fleet"} |
  {kind:"central"}`, derivado de la ruta; `useNode()` conserva `{node}` para no tocar a los consumidores, y
  `activeNode()` de `nodeScope.ts` sigue leyendo el `?node=` de la ubicación (en `fleet`/`central` no hay: correcto).
- **Esquema de URL final**:
  - por servidor: `/<página>` (este servidor) y `/n/<nodo>/<página>`;
  - flota: `/fleet`, `/fleet/apps`, `/fleet/certificates`, `/fleet/backups`, `/fleet/updates`, `/fleet/activity`,
    `/fleet/jobs/<id>` con filtros `?server=…&status=…` (parámetro `server`, distinto de `node` para no chocar con la
    reescritura);
  - central: `/settings/servers`, `/settings/central`, `/settings/security`, `/settings/tokens`;
  - compatibilidad: `/n/<n>/fleet`, `/n/<n>/settings/servers`… se normalizan (`nodeRoute.ts:104-116`) y ahora además
    fijan `lastNode`.
- **Atajos y paleta**: `g f` flota (existe), `g s` ajustes (ahora respeta el nodo por `retainSearchParams`); la paleta
  gana "Cambiar a <servidor>", "Todos los servidores", y sus resultados de apps salen de `/api/fleet/search` en
  contexto de flota.
- Copy EN/ES en `panel/src/i18n` (frases completas con marcadores), tests de Playwright + axe en ambos temas para cada
  contexto (regla del panel).

### 3.e Correcciones de alta (31, 34, 35, 36 y el diálogo)

**31 — `authorize` debe adoptar una consola en segundo plano.**
- Hoy: sin unidad instalada, `ensure_console_on_loopback` llama a `web._enable` (`fleet.py:127-132`), que rechaza si
  `_running_daemon_pid()` devuelve un PID (`web.py:1829-1833`).
- Cambio: `ensure_console_on_loopback` detecta el demonio antes (`web._running_daemon_pid`, `web.py:1077-1093`), lee su
  `argv` (`psutil`, ya dependencia declarada) con el mismo `_flag_value` (`fleet.py:47-60`) y decide:
  1. demonio en loopback, sin TLS y sin opciones que la unidad no pueda reproducir: **adopta sin preguntar**: `web._stop`
     (`web.py:1468`, SIGTERM y espera) → `_enable` con los mismos host/puerto → `_wait_until_serving`
     (`web.py:1745`); si el servicio no llega a servir, **reinicia el demonio original con su `argv`** y explica;
  2. demonio expuesto más allá de loopback, con TLS o con opciones propias (`--allow-ip`, `--trusted-proxy`): no lo cambia en
     silencio (mismo criterio que `fleet.py:106-122`); pregunta ("Esto lo dejará solo en 127.0.0.1 como servicio.
     ¿Continuar? [s/N]", `-y` lo acepta) o rechaza con la orden exacta si no hay TTY;
  3. sin demonio ni unidad: como hoy.
- Los mensajes del paso (parar, arrancar, esperar) salen por el mismo canal de progreso del ítem 35.

**34 — `authorize` no imprime el token maestro ni lo emite si ya existe.**
- `_enable` gana `issue_token: bool = True` y `banner: bool = True` (`web.py:1786`); `authorize` pasa `banner=False` y
  `issue_token=<no existe hash maestro>`. Con hash existente, el servicio sirve el que hay en disco (ya es así: "the
  service serves whatever hash is on disk", `web.py` junto a `:1866`): **`authorize` deja de jubilar en silencio el
  token del operador**.
- Si no había ninguno (primer arranque), se emite y se imprime **después** del código de unión, en un bloque
  separado y rotulado "Token de acceso a la consola (solo se muestra ahora)"; en `--json` va en un campo
  `console_token` (nulo casi siempre).

**35 — Progreso y spinner.**
- `authorize(..., progress: Callable[[str], None])` (el paquete `noust.fleet` no imprime; el handler sí, regla de
  "handlers sin lógica"). Pasos: `[1/5] sshd`, `[2/5] consola en 127.0.0.1:PORT` (aquí el spinner, es lo lento),
  `[3/5] clave en …/authorized_keys`, `[4/5] token fleet-<central>`, `[5/5] código de unión`. `Logger.step` existe
  (`core/logger.py:354`); falta el spinner: en un TTY con `\r` y solo si `stderr.isatty()`; sin TTY o con `--json`, líneas
  planas por stderr y el JSON solo por stdout. El código de unión va en su propia línea, enmarcado, con la orden
  exacta siguiente.

**36 — Detectar que `authorize` corre en la central que emitió la clave.**
- Antes de cambiar nada, `authorize` comprueba: (a) `--name` == `central_name()` (`fleet/models.py:385-404`) **y** este equipo tiene
  estado de flota (nodos en el store o `fleet/nodes/*` en `secrets_dir()`: funciona **con la central sellada y bloqueada**,
  porque solo lista nombres); (b) si los secretos se pueden leer, la huella de `--central-key` coincide con
  `NodeKeys.public_key(n)` (`keys.py:187-203`) de algún nodo: prueba definitiva.
- Respuesta: `NodeError("Este servidor es la central que emitió esta clave", details="Una central no se
  enrola a sí misma: ya es este servidor. Ejecuta el comando en el servidor que quieres añadir. (Lo ejecutaste en
  <hostname>, que es la central 'nas'.)")`, con `--allow-self` para pruebas. Cubre `docker exec -it noust noust fleet
  authorize` pegado en el terminal equivocado.

**Diálogo "Añadir servidor".**
- **Bug**: `key` propia por acción en los tres pies (`"next"`, `"join-submit"`, `"try-again"`, `AddServerDialog.tsx:304-324,
  362-376,387-407`) para que React no convierta un botón en otro; además `event.preventDefault()` en "Volver al código"; y
  **vaciar `code` cuando el error es del código** (`error.field === "join_code"`, que `joincode._invalid` ya pone en
  `NodeError`, o 400) y enfocar el campo, conservando nombre y dirección. Test nuevo: tras un fallo, pulsar "Back to the join
  code" y comprobar `callsTo("POST /api/nodes")` = 1 (el test actual de `ServersSettings.test.tsx:139-141` solo mira que la
  dirección se conserva).
- **Validación en el cliente** (`classifyJoinCode` en `features/fleet/nodes.ts`, espejo de `JOIN_PREFIX`, `joincode.py:38`):
  `ok | empty | multiline | wrongPrefix | consoleToken | apiToken | newer`; se extrae la línea `noust-join:v1:[A-Za-z0-9_-]+`
  aunque se pegue el bloque entero de la terminal; `noust_tok_`/`wasm_tok_` → "Eso es un token de API, no un código de
  unión"; `noust_`/`wasm_` → "Eso parece un token de acceso a la consola (`noust_…`). El código de unión lo imprime
  `noust fleet authorize` en el servidor y empieza por `noust-join:v1:`"; `noust-join:` con otra versión → "es de un Noust más
  nuevo". Envío deshabilitado hasta `ok`. **Nunca** se repite el valor en el mensaje (lleva un token).
- Como el campo es `type=password` (`:331`) y oculta lo pegado, tras un `ok` se muestra un resumen **sin token**, leído del
  JSON base64url: "Código para `vps1` (SSH root:22, consola 8080, Noust 3.0.0, central `nas`)", y se avisa si la huella no es
  la de la clave del paso 1 o si `central` no es esta central, antes de enviar. Se rellena el usuario SSH.
- El decodificador de Python añade el mismo reconocimiento (`joincode.py:151-158`): `noust_`/`wasm_` → mensaje propio, para el CLI.

### 3.f Cuenta de túnel sin privilegios y permisos por nodo

**Cuenta de túnel (45).** Hoy es opcional y manual (§1.2). Propuesta:
- `noust fleet authorize --create-tunnel-user [NOMBRE]` (por defecto `noust-tunnel`, validado con `validate_ssh_user`,
  `fleet/models.py`). Como root y por `CommandRunner` (regla 1, argv sin shell): `useradd --system --create-home --home-dir
  /var/lib/noust-tunnel --shell <nologin resuelto con shutil.which> noust-tunnel` y `usermod -p '*' noust-tunnel` (campo de contraseña `*`, **no** `!`: cómo trata sshd una cuenta bloqueada con clave pública varía según `UsePAM` y la versión ([hilo de SUSE](https://forums.suse.com/t/sshd-allows-login-for-locked-using-publickey-authentication/21885), [error 442 de OpenSSH](https://lists.mindrot.org/pipermail/openssh-bugs/2003-August/000423.html)); con `*` no hay bloqueo que interpretar. **Hay que probarlo en el arnés Docker**). La línea de `authorized_keys` es la restringida de siempre
  (`authorize.py:270-286`, opciones de [sshd(8)](https://man.openbsd.org/sshd.8): `restrict`, `port-forwarding`,
  `permitopen`, `permitlisten`, `command`), con `from="<IP de la central>"` opcional (`--central-ip`) y el fichero es de
  la cuenta (`AuthorizedKeys._chown`, `authorize.py:464-482`). El túnel de la central ya usa `record.ssh_user`.
- **Por defecto en 3.1 para altas nuevas**; `--ssh-user root` sigue existiendo. Si la cuenta ya existe, se reutiliza; se
  informa de qué se creó (deshacer: `noust fleet deauthorize` borra la línea y, con `--remove-user`, la cuenta).
- Lo que compra: (1) enrola servidores con `PermitRootLogin no`, hoy rechazados (`authorize.py:216-220`); (2) si una
  restricción se saltara (opción mal parseada, un sshd raro), la central obtiene una cuenta `nologin` sin privilegios en vez
  de root; (3) `authorized_keys` de root queda intacto (útil con gestión de configuración); (4) los logs de sshd nombran
  la cuenta. **Lo que no compra**: el token de flota sigue siendo `admin` sobre la API del nodo desde loopback, y comprometer
  la central sigue significando admin en cada nodo: por eso el techo de abajo es la mitigación real.
- Opcional (`--sshd-dropin`, **apagado por defecto** para no ampliar lo que `authorize` toca): `Match User noust-tunnel`
  en `/etc/ssh/sshd_config.d/` con `AllowTcpForwarding local`, `PermitOpen 127.0.0.1:<puerto>`, `PermitTTY no`,
  `ForceCommand /usr/bin/false`, `AuthenticationMethods publickey`, solo si `sshd -T` confirma que incluye ese directorio.
- **Migración** de nodos ya autorizados como root: `noust fleet authorize --create-tunnel-user` de nuevo en el nodo y, en la
  central, un comando nuevo `noust node rekey NOMBRE --join-code -` (y "Volver a autorizar" en Servidores), porque hoy
  `add` rechaza un nombre existente (`fleet/nodes.py:229-235`). `deauthorize --ssh-user root` retira la línea antigua.

**Techo por nodo (45): lo aplica el nodo, en su punto de paso.**
- El token de flota recibe un **techo** `read | deploy | admin` (columna nueva `fleet_ceiling` con valor por defecto `admin`
  en la tabla `api_tokens`, `auth.py:984-996`; migración por `ALTER TABLE`, el patrón de reconstrucción de
  `_allow_fleet_scope`, `:1121-1149`, no hace falta) y se fija con `noust fleet authorize --access read|deploy|admin` (alias
  `--read-only`). Por defecto en 3.1 sigue `admin` (comportamiento de 3.0); se revisa para 4.0.
- **Dónde se aplica**: `admit_fleet` (`auth.py:3666-3730`) hace `granted = min(X-Noust-Actor-Scope, techo)` con `SCOPE_RANK`
  (`:180-181`); el cliente no puede subirlo porque el techo sale del registro del token, no de una cabecera. Un rechazo
  por techo es 403 `fleet_ceiling` con `hint`: "Este servidor autorizó a esta central solo para lectura. En el servidor:
  `noust fleet authorize --access admin …`", auditado con el nombre del token. Las clases ya existen en `required_scope`
  (`:3892-3922`): `read` = "central de solo lectura" (todas las lecturas); `deploy` = puede mover apps existentes
  (update, rollback, activar release) pero no crear, borrar ni configurar; `admin` = todo lo demás salvo lo que
  `fleet_refusal` niega siempre. Cada central tiene su token `fleet-<central>` (`authorize.py:627-637`) y por tanto su
  propio techo: un nodo puede fiarse de una central de monitorización solo para lectura y de otra de operación.
- **"Sin despliegue"** como excepción independiente del techo (poder configurar pero no desplegar) no cabe en tres
  niveles: se propone (fase 3) una lista de **denegación por grupos** guardada en el token (`fleet_deny`: `deploy`,
  `destructive`, `data`) y evaluada dentro de `fleet_refusal` (`auth.py:3748`), con los patrones junto a
  `DEPLOY_SCOPE_PATTERNS` (`:197-214`); un solo sitio. Nombre desconocido → error al autorizar.
- **Que la central lo sepa** (solo para la interfaz, el nodo manda): `GET /api/auth/fleet/self`, añadido a
  `FLEET_AUTH_PATHS` (`auth.py:268`, junto a `session`, `verify` y `fleet/revoke`), devuelve `{token_name, central,
  ceiling, deny, created_at, last_used_at}` **de su propio token**, sin listar otros. La central lo pide al probar,
  al añadir y en cada resumen; `/api/nodes` gana `access`; Servidores muestra la columna "Acceso"; en `/n/<nodo>/…` un
  aviso "Este servidor permite a esta central solo leer: las acciones están desactivadas" y los botones de escritura se
  desactivan con el mismo mecanismo de capacidades (`nodes/capability.tsx`); los planes masivos omiten el nodo con
  `skipped: policy`. La sección "Acceso de esta central a este servidor" (§3.c) enseña lo mismo.
- **Solo se baja desde la central; se sube en el nodo**: `POST /api/auth/fleet/self/downgrade {ceiling}` (también permitido a
  un token de flota, monótono: solo reduce) da un "congelar este servidor" desde la central; volver a subir exige
  `noust fleet authorize --access …` en el nodo, que revoca el token anterior, emite otro y obliga a `node rekey`.
- **Permisos por operador en la central** (que un token de la central sea `read` solo en `web-2`) es otra capa, ya
  enforzada solo en la central; PDM, Portainer y Rancher lo tienen. Queda **fuera** de 3.1: el techo del nodo es el
  límite duro y el permiso por operador se apoyaría en él.

---

## 4. Plan, riesgos y decisiones

### Fases

| Fase | Contenido | Toca |
|------|-----------|------|
| **A** (3.1.0, imprescindible) | 37/37b: contextos, `lastNode`, `CENTRAL_ONLY_PATHS` afinado, Ajustes en dos grupos, sobretítulo de servidor, sonda de alcance. Alta: 31, 34, 35, 36, diálogo (bug y validación), `classifyJoinCode`. Agregador + `/api/fleet/{summary,apps,certificates,backups}` + pestañas de flota + `noust fleet apps|certs|backups` + selector de servidor para "Nueva aplicación" | `fleet/aggregate.py`, `web/api/fleet.py`, `cli/commands/fleet.py`, `authorize.py`, `web.py`, `nodeRoute.ts`, `useNode.tsx`, `ServerSelector.tsx`, `Sidebar.tsx`, `nav.ts`, `settings.tsx`, `AddServerDialog.tsx`, `features/fleet/*` |
| **B** (3.1.x) | Job de flota + `certs_renew`, `backups_run/verify`, `apps_update/restart`, `probe`; página del job, plan y reintento; techo por nodo (`--access`, `fleet/self`, columna Acceso); `--create-tunnel-user` y `node rekey`; actividad de flota; callback de GitHub por nodo | `web/jobs.py`, `web/auth.py`, `core/store.py`, `fleet/authorize.py`, `fleet/nodes.py` |
| **C** | `noust_update` (`/api/system/update`), `os_updates_check/apply`, SSE de flota, `fleet_deny`, `fleet_snapshots`, despliegue multi-servidor, aplicar ajustes a varios | `web/api/system.py`, `web/events.py`, `web/api/node_proxy.py` |

### Pruebas

- Unitarias con el runner falso y el transporte de `httpx` de pruebas: agregador (parcial, obsoleto, `unsupported`, `forbidden`,
  single-flight), `admit_fleet` con techo (matriz `read/deploy/admin` × método × ruta), `fleet_refusal` con deny, detección de
  autoenrolamiento, adopción de demonio (con `psutil` simulado), `classifyJoinCode`.
- `tests/integration/run.py` (dos contenedores systemd): enrolar con `PermitRootLogin no` y cuenta `noust-tunnel`; comprobar que
  `ssh noust-tunnel@nodo` ejecuta `false` y que `-L` a otro puerto y `-R` fallan; central `read` recibe 403 en un POST; `authorize`
  sobre un demonio de fondo; job masivo con un nodo caído y otro de solo lectura.
- E2E (Playwright + axe + CSP, ambos temas): `scripts/console_server.py` con tres nodos (uno caído, uno obsoleto, uno de solo
  lectura) para `/fleet/*`, los tres contextos del selector, volver al último servidor desde Ajustes y Fleet, y el diálogo.

### Riesgos

1. **Presentar Tokens/Seguridad de un nodo como "por servidor" sería mentir**: el nodo los rechaza (`auth.py:268-282`); la
   solución es la nota visible y los comandos del nodo, no un proxy nuevo.
2. **Sonda de alcance y polling**: un hilo por central cada 30 s ×N nodos; se limita con single-flight y solo corre con un
   cliente conectado.
3. **Actualizar Noust por API es ejecución remota de instalación**: equivalente a lo que ya permite un token `admin` (la
   central guarda credenciales equivalentes a root, spec 3.0 §Seguridad), pero conviene exigir techo `admin`, elevación
   y una lista blanca de comandos por método de instalación.
4. **Parcheo del SO**: reinicia servicios (Docker en los pares, [aviso de Coolify](https://coolify.io/docs/knowledge-base/server/patching));
   por eso la lectura va antes que la escritura y nunca se reinicia solo.
5. **Adoptar un demonio** cambia cómo corre la consola del operador (a servicio, en loopback): por eso pregunta si no es
   trivial y restaura el demonio original si falla.
6. **Compatibilidad de versiones**: un nodo 3.0 no tiene `fleet/self`, `system/update` ni techo: el agregador y el motor
   de jobs deben tratarlo como `unsupported` con `access: "admin (unknown)"`, sin errores.

### Decisiones que necesito del dueño

1. **Tokens API y 2FA/sesiones de un nodo desde la central.** Recomiendo **mantener** la guardia
   (`auth.py:268-282`) y mostrar nota y comandos. Alternativa: permitir a un token de flota, con elevación y solo con
   alcance `read`, listar/crear/revocar tokens de ese nodo; el coste es que una central comprometida deja credenciales que
   sobreviven a revocar su token de flota, justo lo que el comentario de `fleet_refusal` (`auth.py:3751-3758`) quiere evitar.
2. **¿Cuenta `noust-tunnel` por defecto en 3.1?** Recomiendo que sí (root queda con `--ssh-user root`); cambia el comando
   que `noust node key` imprime y la documentación de 3.0.
3. **¿Techo por defecto `admin` (compatible) o `deploy`?** Recomiendo `admin` en 3.1 y revisar en 4.0.
4. **¿Parcheo del SO desde la central?** Recomiendo solo lectura ("N paquetes, reinicio necesario") en 3.1 y decidir la
   escritura después de ver la de `noust_update`.
5. **¿Se retira `noust --node` del spec 3.0 y `allow_shell` del modelo?** No existen en el código y el proxy cubre el caso;
   `allow_shell` está reservado sin uso (`store.py:898-921`).
6. **Etiquetas/grupos de nodos** (`env=prod`) como objetivo de acciones (Portainer Edge Groups, Fleet `clusterGroup`, PDM
   Views): recomiendo columna `labels` en `nodes` en la fase B; sin ella el objetivo es "seleccionar a mano".

### Hallazgos colaterales (no pedidos)

- El spec 3.0 prometía `noust --node <cmd>` y que el asistente preguntara el servidor; ninguna de las dos cosas está en el árbol.
- `authorize` jubila el token maestro del operador como efecto lateral (`web.py:1866`) y no lo dice.
- El resumen de flota vive en dos lenguajes (Python `fleet/status.py`, TypeScript `useFleet.ts`): se unifica en §3.a.
- El selector muestra el último estado guardado, no uno vivo (§1.3).
