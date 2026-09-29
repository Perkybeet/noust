# Dashboard y métricas de Noust 3.1: diagnóstico y propuesta

Ítems 39 (gráficos del Overview), 41 (Overview como dashboard) y 51 (pestaña Métricas de las
aplicaciones). Investigación del 2026-09-29 sobre la rama `dev/3.1` (código de la 3.0.0,
commit `3a19e40`). Solo lectura sobre el repositorio: nada de este documento está implementado.

---

## 0. Resumen

### 0.1 Causas raíz

**39(b) "el selector de rango no cambia nada".** La consulta, el SQL y las ventanas son
correctos: con 30 días de historial las cuatro ventanas devuelven 1 h, 24 h, 7 d y 30 d de
verdad (medido contra el backend real). Lo que falla es todo lo que rodea a los datos, y se
suman seis cosas:

1. **El eje X sale de los datos, no de la ventana pedida.** `panel/src/components/ui/Chart.tsx:481`
   declara `x: { time: true }` sin `range`, y uPlot resuelve eso como `[mínimo del dato, máximo
   del dato]` (`snapTimeX`, `node_modules/uplot/dist/uPlot.esm.js:2835-2840`). Una vista de 24 h
   con 1 h de muestras se dibuja como 1 h, sin hueco.
2. **No hay historial que dibujar.** El colector es un hilo del proceso web
   (`src/noust/web/server.py:796`, `src/noust/web/metrics_collector.py:493-513`): solo muestrea
   mientras `noust web` está en marcha. Ningún paquete lo habilita (solo reinician
   `noust-web.service` si ya estaba activo: `obs/debian.postinst:110-114`,
   `rpm/noust.spec:362-363`) y `docs/console.md:197` promete "24 horas, 7 o 30 días" sin decir
   esto. En un servidor donde la consola se arranca a demanda, 7 d y 30 d son un puñado de
   puntos.
3. **Los rótulos dicen lo pedido, no lo recibido.** `resolution_label(window_s)`
   (`src/noust/monitor/timeseries.py:116-138`) nombra la etapa por la anchura de la ventana, no
   por la fuente real, y el gráfico escribe "minute averages" / "hourly averages" sobre
   muestras crudas de 2 s. Reproducido: "Last 30 days, hourly averages" sobre un eje de un minuto.
4. **Los huecos no se dibujan como huecos.** Faltar un tramo de tiempo no produce `null`; uPlot
   solo corta la línea en un `null`, así que una caída de 20 h se dibuja como una recta.
5. **El zoom no vuelve a consultar.** Se aplica sobre ≤400 puntos ya promediados
   (`timeseries.py:69`, `Chart.tsx:553`): acercar 1 h dentro de 24 h deja ~17 puntos.
6. **Solo hay medias**, así que un pico de CPU de 5 minutos desaparece en 7 d y 30 d (media horaria).

Y lo que impidió verlo: `--showcase` escribe 30 días de golpe
(`scripts/console_server.py:5596-5659`), de modo que ni el desarrollo, ni las capturas, ni la
E2E (`panel/e2e/charts.spec.ts`) pasaron nunca por "historial joven".

**39(a) tooltip.** Existe una lectura al pasar el ratón, pero vive en la fila de leyenda encima
del gráfico (`Chart.tsx:663-689`), no junto al cursor; el crosshair es una línea discontinua de
1 px. La E2E la da por diseño ("hovering reads one sample out in the legend row"). Es un vacío
de diseño, no un bug. No hay crosshair compartido entre gráficos.

**39(c) tabla.** Medido en el backend real: al pulsar "View as table" en CPU su `<figure>` pasa de
208 a 250 px (+42), la fila inferior baja 42 px, la sección crece de 562 a 604 px y la tarjeta
vecina se estira (grid con `align-items: stretch`). Causa: `maxHeight: height + 40`
(`Chart.tsx:702`) más borde y lista de marcas dentro de un grid sin alturas fijas. Además la tabla
no sirve: `formatChartTime` no lleva segundos (`Chart.tsx:165-170`) y seis filas seguidas dicen
"21:41".

**Ítem 51 métricas por app.** La ruta de recogida por cgroup **funciona en 3.0.0** para las
tres sospechas del ítem, comprobado en una máquina Ubuntu 24.04 / systemd 255 (la de producción):
nombres legacy `wasm-*` (`service_manager.py:924-930`), plantillas blue/green
(`system-<x>.slice/<x>@blue.service`) y accounting (`DefaultMemoryAccounting=yes`,
`DefaultCPUAccounting=yes`; `io.stat` no existe porque `DefaultIOAccounting=no`, pero no se lee).
Lo que sí está mal:

1. Misma causa 2 de arriba: sin consola en marcha no hay muestras. Es el único fallo simple que
   explica "todas las apps".
2. **La pestaña no dice por qué.** Una app `Running` muestra "No readings in the last 24 hours."
   (`MetricsTab.tsx:149-157`, reproducido con captura) y el subtítulo dice "while the panel runs".
   `metrics_unavailable_reason` (`metrics_collector.py:95-111`) solo la usa el colector: ninguna
   API la expone, y el cliente re-deriva estático y Compose a mano (`MetricsTab.tsx:280,299`).
   PHP-FPM no tiene ni caso: `app_units` devuelve `[]` y la pestaña queda vacía sin motivo.
3. En ≤ 2.0.1 el colector buscaba `wasm-{domain}.service` con puntos: no existe ninguna unidad
   así, no se muestreó ninguna app. Arreglado en 2.1.0. Producción está en 3.0.0 (ítem 51), así
   que queda descartado, pero conviene confirmarlo con `noust --version`.
4. Sin acceso a producción no puedo confirmar cuál de estas es. Va un diagnóstico de 60 s en
   §1.5.4 que lo decide con una sola salida.

### 0.2 Qué propongo

| Bloque | Propuesta en una frase |
|---|---|
| Gráfico (§3.1) | Dominio X fijo = ventana pedida, huecos como huecos y banda "historial desde…", tarjeta de lectura junto al cursor (React, posición por CSSOM), crosshair compartido en la rejilla, teclado con anuncio, zoom que re-consulta, tabla solo en el diálogo. Nunca cambia la altura del marco. |
| Historial (§3.2) | API `GET /api/metrics/query` por lote con rejilla regular, `from/to/step`, etapa real, `first_sample_at` y estado del colector; etapas 5 s/2 h, 1 min/26 h, 10 min/8 d, 1 h/35 d con **media y máximo**; colector que sobrevive a la consola (arrendamiento + demonio). |
| Métricas de app (§3.3) | Un único `SamplingPlan` por app (unidades + motivo) que usan colector y API; ruta del cgroup por `systemctl show -p ControlGroup`; endpoint de estado y estado vacío con motivo y arreglo. |
| Overview (§3.4) | Franja de seis cifras, atención ordenada, tres gráficos (CPU, memoria, red; el disco pasa a su cifra), aplicaciones y actividad. Un `GET /api/overview` por servidor que también alimenta la flota. De 2157 px de alto a ~1250 estimados. |

### 0.3 Cómo se obtuvo la evidencia, y qué no se pudo comprobar

- Lectura de código con línea exacta (secciones 1 y 3).
- **Backend real aislado**: `scripts/console_server.py` (sin y con `--showcase`) y Playwright
  (Chromium), midiendo respuestas de la API, cajas de los gráficos y capturas a 1440 y 390 px.
- **Experimentos con `MetricsStore` y `MetricsCollector` reales** y reloj falso (cuatro
  escenarios de historial) y lectura de cgroups reales en Ubuntu 24.04 / systemd 255 / kernel
  6.6 (sin Docker Compose ni units de aplicación reales).
- Investigación web de Grafana, Netdata, Beszel, Railway, Render, Vercel, Coolify, Dokploy,
  Datadog, Cloudflare y Stripe, de uPlot y de accesibilidad de gráficos (§4). Linear no se pudo
  verificar (la búsqueda no devolvió su documentación) y de Uptime Kuma solo consta lo que
  dice su README.
- **No comprobado**: nada en producción (arennalabs). Lo marcado "hipótesis" en §1.5.2 depende
  de la salida del diagnóstico. Los ficheros de trabajo están en `/tmp/noust-research/` (no
  forman parte del repositorio).

---

## 1. Diagnóstico del código

### 1.1 El recorrido de un dato

```
psutil / cgroups ──► MetricsCollector.sample_once()  (cada 2 s, hilo del proceso web)
  web/metrics_collector.py:196-233
        │ record_many()                                   consolidate() cada 300 s
        ▼                                                 (timeseries.py:315-363)
  metrics.db (SQLite, WAL)   samples (crudo 1 h) ─► consolidated 60 s (24 h) ─► consolidated 3600 s (30 d)
  monitor/timeseries.py                                   solo medias, sin máximos
        │ query(metric, window_s)  timeseries.py:269-313  (UNION de etapas + medias en cubos si > 400)
        ▼
  GET /api/metrics/{metric}?window=1h|24h|7d|30d          web/api/metrics.py:84-114
        │  {metric, window, resolution, points:[[ts,valor]]}   resolution = etiqueta por ANCHURA de ventana
        ▼
  useSeries()  MachineCharts.tsx:41-48   (refetch 30 s en 1h, 5 min si no; keepPreviousData)
        │ alignSeries()  series.ts:19-33  (null solo si otra serie tiene ese instante)
        ▼
  <Chart timestamps values/>   Chart.tsx   uPlot: eje X = [min dato, max dato]  ◄── aquí se pierde la ventana
```

Otros lectores del mismo dato: `AppsTable` (columnas CPU/memoria) recibe la última muestra por
SSE, evento `metrics` cada 2 s (`web/events.py:76,699-703`), y **oculta las columnas si el
snapshot no trae ninguna clave `app.*`** (`features/apps/data.ts:74-76`): un segundo vacío
silencioso.

### 1.2 Ítem 39(b): el selector de rango

**Lo que la cadena hace bien.** El valor `window` viaja intacto: `MachineCharts` →
`metricSeriesQuery` → `?window=24h` → `WINDOWS[window]` → `store.query(window_s=…)`. Con historial
completo (servidor sandbox con `--showcase`) el backend responde:

| Ventana | Puntos | Tramo real | Etiqueta `resolution` |
|---|---|---|---|
| 1h | 362 | 60 min | raw |
| 24h | 400 | 1435 min | minute |
| 7d | 202 | 10037 min | hour |
| 30d | 400 | 43075 min | hour |

**Lo que pasa con historial joven** (sandbox sin `--showcase`, solo el colector en vivo, ~1 min):

| Ventana | Puntos | Tramo real | Etiqueta | Lo que dibuja la consola |
|---|---|---|---|---|
| 1h | 23 | 1 min | raw | 1 min |
| 24h | 24 | 1 min | minute | 1 min, subtítulo "Last 24 hours, **minute averages**" |
| 7d | 26 | 1 min | hour | 1 min, "Last 7 days, **hourly averages**" |
| 30d | 27 | 1 min | hour | 1 min, "Last 30 days, hourly averages"; ticks "21:40 21:40 21:40 21:40 21:40" |

Las cuatro ventanas son el mismo minuto con distinto subtítulo. Ese es el "same data, same span" del
dueño. Con `MetricsStore` y reloj falso (2 s por muestra, `consolidate()` como en producción):

| Escenario | Ventana | Puntos | Primer→último | Tramo real / pedido |
|---|---|---|---|---|
| A. colector 45 min | 1h | 301 | 08:21→09:06 | 0,75 h / 1 h |
|  | 24h | 13 | 08:21→09:03 | 0,69 h / 24 h |
|  | 7d | 2 | 08:21→08:41 | 0,33 h / 168 h |
|  | 30d | 1 | 08:21 | 0 h / 720 h (la consola pinta "Collecting") |
| B. colector 6 h | 24h | 101 | 03:06→09:03 | 5,95 h / 24 h |
|  | 7d | 15 | 03:06→08:41 | 5,59 h / 168 h |
| C. 3 días seguidos | 24h | 400 | día anterior 09:07→09:03 | 23,9 h / 24 h |
|  | 7d | 106 | 24 sep 09:00→27 sep 08:41 | 71,7 h / 168 h |
|  | 30d | 41 | idem | 70,3 h / 720 h |
| D. 3 días, 20 h caído, 1 h | 24h | 67 | 09:07→09:03 | 23,9 h: **la API no marca el hueco** y la línea lo une |

**Causas, por peso:**

1. **Eje X derivado de los datos** (`Chart.tsx:481`). uPlot: `snapTimeX = [dataMin, dataMax]`. Nada de
   lo que la página sabe (la ventana, `now`) llega al eje. `MetricsTab.tsx:146` recorta con
   `clip(points, range, now)` solo por el borde inferior y tampoco fija el eje.
2. **No hay historial** cuando la consola no ha estado en marcha (colector acoplado a `noust web`).
   Además `consolidate()` no corre hasta 300 s después de arrancar (`metrics_collector.py:159`,
   `_consolidated_at = self._clock()`), y solo corre mientras hay colector: tras parar la consola
   las filas crudas viejas esperan al siguiente arranque (en la copia del `metrics.db` de
   desarrollo: 3958 filas crudas de hace tres días y 9 consolidadas).
3. **Rótulos que mienten**: `resolution_label` (`timeseries.py:116-138`), `MachineCharts.tsx:178-182`
   (`spacing`) y `windowLabel`. Además `keepPreviousData` (`MachineCharts.tsx:41-48`) deja los datos
   de la ventana anterior con el rótulo de la nueva mientras carga.
4. **Huecos**: `alignSeries` solo introduce `null` entre series; un tramo sin muestras no crea
   ningún punto. La guía del servicio civil británico es explícita: "do not join the points either
   side of the missing data point, even if the line is dotted" (§4).
5. **Zoom cliente sobre datos ya reducidos**: `query(..., max_points=400)` y `_bucket_means`
   (`timeseries.py:378-421`) promedian en cubos de `ceil(ventana/400)` s (216 s en 24 h,
   1512 s en 7 d) y el diálogo solo puede rescalar esos puntos (`zoomStep`, `Chart.tsx:828-841`).
6. **Solo medias.** Etapa horaria = media de medias de minuto; los picos no sobreviven.
7. **Ceguera de pruebas**: la semilla de 30 días. El propio comentario de `seed_showcase_machine_metrics`
   dice que sin ella "every range would read 'Collecting samples.'".

Descartado: que la petición no cambie (la URL, la clave de caché `["metrics","series",metric,{window}]`
y el cuerpo cambian), que el store no guarde 24 h–30 d (lo guarda si el colector corre), y que
falle el recorte SQL (los cuatro escenarios devuelven lo que hay).

### 1.3 Ítem 39(a): sin tooltip

Lo que ya hay y funciona (medido en el navegador): `cursor.points` de 6 px, línea vertical
discontinua (`--border-strong`, cumple 3:1 según `tokens.test.ts`), y una fila superior "21:40 — CPU
2.7%" que sigue al cursor (`Chart.tsx:540-544` → estado `cursor`, `Chart.tsx:663-689`), con teclado
(←/→/Home/End/Esc, `Chart.tsx:625-654`) y región `role="status"` limitada a un anuncio cada 400 ms
(`Chart.tsx:690-692`).

Por qué se percibe como ausente: la lectura está encima del gráfico, en 12 px de texto, y
el gráfico no tiene nada pegado al puntero. Faltan: tarjeta junto al cursor, valor del pico, la
etapa ("media de 1 min"), crosshair en el resto de gráficos, y aclarar los huecos. Otro obstáculo
para sincronizar: **el ancho del eje de valores es distinto en cada gráfico**
(`valueAxisSize`, `Chart.tsx:140-146` y `:505`), así que el mismo instante cae en píxeles
distintos (en la captura, CPU empieza a x≈57 y Network a x≈85).

### 1.4 Ítem 39(c): "View as table"

- Botón por tarjeta (`Chart.tsx:979-989`) que sustituye el `<div>` del plot por una tabla con
  `maxHeight: height + 40` (`Chart.tsx:702`) + borde + lista de marcas (`Chart.tsx:742-763`).
- Medido: `figure` de CPU 208→250 px; fila 2 desplazada +42 px; sección 562→604; la tarjeta de Memoria se
  estira sin cambiar su contenido (huecos en blanco bajo su gráfico).
- Inútil como dato: `formatChartTime` da `HH:MM` (no segundos, no fecha bajo 12 h), con muestras cada
  2 s salen filas idénticas; hasta 400 filas en una caja de 6 filas visibles; sin resumen, sin
  exportar; dos botones por gráfico (tarjeta y diálogo) con el mismo nombre.
- Sí está bien y debe conservarse: el resumen textual (`summarise`, `Chart.tsx:253-275`) en el
  `aria-label` del gráfico, `region` enfocable, `caption`, orden "newest first".

### 1.5 Ítem 51: métricas por aplicación

#### 1.5.1 Cómo se recogen

1. `MetricsCollector._app_units` (`metrics_collector.py:335-371`), cada 30 s: `get_store().list_apps()`;
   descarta las que `metrics_unavailable_reason` rechaza (solo `docker-compose`) y pide sus unidades a
   **`ServiceManager.app_units(app)`** (`service_manager.py:824-930`), el mapeo único: filas de la tabla
   `services`; para monorepo, ficheros de unidad marcados; unidad `<nombre>` o legacy `wasm-<nombre>`
   **si su fichero existe en `/etc/systemd/system`** (líneas 924-930); `@blue`/`@green` en zero-downtime;
   `[]` si `is_static` o `app_type == "static"` (que incluye PHP-FPM, guardado como estático).
2. Cada 2 s, por unidad: `unit_cgroup_path(CGROUP_ROOT, unit)` (`:389-409`) →
   `/sys/fs/cgroup/system.slice/<unit>.service/` o, para instancias de plantilla,
   `system-<prefijo escapado>.slice/<unit>.service`; lee `memory.current` y el contador
   `usage_usec` de `cpu.stat`; CPU = delta / (Δt · 1e6) · 100 (puede pasar de 100: es % de una CPU);
   una app con varias unidades es la suma.
3. Claves `app.<dominio>.mem.bytes` y `app.<dominio>.cpu.percent` (`:328-331`); la pestaña
   (`MetricsTab.tsx:61,69`) usa `app.domain` como el colector.
4. Cualquier fallo de lectura es `log.debug` y la app se salta ese tick (`:306`): **silencio total**.

#### 1.5.2 Hipótesis del ítem contra la evidencia

| Hipótesis | Veredicto | Evidencia |
|---|---|---|
| El colector solo muestrea mientras corre `noust-web` y el historial es corto | **Confirmada como diseño; probable causa de "todas las apps"** | `server.py:796`; los paquetes no habilitan la unidad; `docs/console.md:14-16` ofrece foreground, `-d` o servicio. Sin consola en marcha: sin muestras. Sin historial, la pestaña (rango por defecto 24 h, `ranges.ts:35`) muestra un tramo de minutos o "No readings". |
| Accounting de systemd apagado (CPU/Memory/IO) | **Descartada en Ubuntu 24.04**; posible en otras distros | Verificado aquí: `DefaultMemoryAccounting=yes`, `DefaultCPUAccounting=yes`, `DefaultIOAccounting=no`. Unidades de servicio reales tienen `memory.current` y `cpu.stat`. El kernel documenta que `cpu.stat` "exists whether the controller is enabled or not". `io.stat` no existe, pero Noust no lo lee. `app.service.j2` no fija ninguna directiva `*Accounting`: depende del valor por defecto de la distro. |
| Nombre de unidad legacy `wasm-*` no resuelto | **Descartada en 3.0.0**; era cierta hasta 2.0.1 | `app_units` devolvió `['wasm-bodas-arennalabs-com']` con un fichero legacy simulado. En v2.0.0/2.0.1 el colector usaba `wasm-{domain}.service` con puntos (`git show v2.0.0:src/wasm/web/metrics_collector.py:263`); el fix es de 2.1.0. |
| Plantillas blue/green | **Descartada** | Ruta real comprobada con `getty@tty1` → `system-getty.slice/getty@tty1.service`; `MetricsCollector` real leyó memoria y CPU de él. |
| Compose sin cgroup de unidad | **Cierta y por diseño**; falta el motivo en la API | Unidad `oneshot`; Coolify hace lo mismo ("Resource metrics are not available for Docker Compose applications"). |
| Bug de rango del ítem 39 | **Parcial**: explica "no cambia nada" y "casi vacío", no "sin datos" | §1.2. |
| PHP-FPM / static | **Hallazgo nuevo**: sin muestreo y sin motivo | `app_units` → `[]`; la pestaña solo trata `static` y `docker-compose` (`MetricsTab.tsx:280,299`). Render tampoco muestra CPU/memoria de sitios estáticos, pero lo dice. |
| App parada | **Por diseño** (no hay cgroup) y sin motivo | 4 de 16 apps de arennalabs estaban Stopped (memoria de producción). |
| Doble `metrics.db` según cómo se arranca la consola | **Hipótesis a comprobar** | `default_metrics_db_path` (`timeseries.py:141-154`) elige por probar el sistema de ficheros (¿`/var/lib/noust` escribible? si no, `~/.local/share/noust`): la misma clase de trampa que `store-location-trap`. |

#### 1.5.3 Lo que sí está mal aunque el cgroup funcione

- **Hilo sin supervisión**: `_run` (`:190-194`) no captura nada; una excepción no prevista mata el hilo en silencio
  y `latest()` sigue sirviendo el último snapshot por SSE (números congelados, historial parado).
  Regla 2: el límite de error debe capturar **y registrar**.
- **Sin razón visible** (ver §0.1).
- `memory.current` **incluye caché de página** (documentación del kernel): una app Node/PHP con mucho
  E/S parece crecer sin fin. Docker resta `inactive_file` ("On cgroup v2 hosts, the cache usage is
  defined as the value of `inactive_file`").
- **Tres implementaciones de "muestrear la máquina"** (regla 3): `web/machine.py:read_machine`,
  `web/metrics_collector.py:_system_pairs`, `monitor/metrics.py:collect_resource_metrics`. El disco
  del strip mide el sistema de ficheros de `apps_directory` (`machine.py:292-346`); el gráfico mide
  `/` (`metrics_collector.py:259`): pueden discrepar.
- El colector escribe cada 2 s `mem.total_bytes`, `disk.total_bytes` y `swap` aunque no cambian.
- Un colector fuera de `noust.web` es imposible hoy: importar `noust.web.metrics_collector` arrastra
  `noust/web/__init__.py` → `web/auth.py` → `fastapi` (probado sin fastapi: `ModuleNotFoundError`).

#### 1.5.4 Diagnóstico de 60 segundos para producción (solo lectura)

```bash
noust --version                                     # ≥ 2.1 para que el colector busque bien las unidades
systemctl is-active noust-web wasm-web 2>&1; pgrep -af 'noust web|wasm web'
find / -xdev \( -path '*noust*' -o -path '*wasm*' \) -name 'metrics.db*' 2>/dev/null | xargs -r ls -la
stat -fc %T /sys/fs/cgroup                          # cgroup2fs = jerarquía unificada
systemctl show -p DefaultMemoryAccounting -p DefaultCPUAccounting

DB=/var/lib/noust/metrics.db   # cambiar si el find de arriba muestra otro
sqlite3 "$DB" "select count(*), datetime(min(ts),'unixepoch'), datetime(max(ts),'unixepoch') from samples;
               select substr(metric,1,4) k, count(distinct metric) from samples group by k;
               select metric, datetime(max(ts),'unixepoch') from samples where metric like 'app.%'
                 group by metric order by 2 desc limit 20;"
```

```python
# python3 en la máquina de producción: usa el mismo mapeo que el colector
from noust.core.store import get_store
from noust.managers.service_manager import ServiceManager
from noust.web.metrics_collector import CGROUP_ROOT, metrics_unavailable_reason, unit_cgroup_path

manager = ServiceManager(verbose=False)
for app in get_store().list_apps():
    why = metrics_unavailable_reason(app)
    units = manager.app_units(app)
    if not units:
        print(f"{app.domain:34} {app.app_type:14} sin unidad (static/php-fpm)")
        continue
    for unit in units:
        d = unit_cgroup_path(CGROUP_ROOT, unit)
        print(f"{app.domain:34} {app.app_type:14} {unit:34} cgroup={'sí' if d.is_dir() else 'NO'} "
              f"memory.current={'sí' if (d / 'memory.current').exists() else 'NO'} "
              f"cpu.stat={'sí' if (d / 'cpu.stat').exists() else 'NO'}{'  (compose)' if why else ''}")
```

Si el binario `sqlite3` no está, el mismo SQL sirve con `python3 -c` y el módulo `sqlite3`. Cómo leerlo: proceso web ausente o última muestra vieja → causa 2 (colector); dos `metrics.db` → trampa de
ruta; `cgroup=NO` con la unidad activa → jerarquía v1/híbrida o `Slice=` propio; `cgroup=sí` y
`memory.current=NO` → accounting apagado; todo `sí` pero sin claves `app.*` → colector parado o hilo muerto
(buscar `Traceback` en `journalctl -u noust-web`). (El script Python fue probado con un almacén simulado.)

### 1.6 Retención y downsampling: lo que hay y sus límites

El diseño es RRD razonable (`timeseries.py:1-30`): crudo 1 h (`RAW_RETENTION_SECONDS=3600`), medias de
1 min 24 h, medias de 1 h 30 d; `consolidate` es agregación SQL idempotente con cortes alineados a cubo
(las etapas no se solapan por construcción: `consolidate` borra exactamente lo que agrega).
Límites reales: sin máximos (RRDtool guarda `AVERAGE` **y** `MAX` por RRA), sin noción de `UNKNOWN` (RRDtool: "heartbeat" y
`xff`), 7 d se compone de medias horarias (salvo el último día) que luego se vuelven a promediar en cubos de 25 min, `max_points` 400 fijo, y una etiqueta de
resolución por ventana.

Coste medido (tabla SQLite `WITHOUT ROWID` con nombres de métrica realistas, `VACUUM`):

| Diseño | 16 apps (41 métricas) | 100 apps (209 métricas) |
|---|---|---|
| Actual: 2 s×1 h, 1 min×23 h, 1 h×30 d | 160 k filas, 9,5 MB | 815 k filas, 52,9 MB |
| Propuesto: 5 s×2 h, 1 min×26 h, 10 min×8 d, 1 h×35 d, con máximo | 205 k filas, 14,0 MB | 1,04 M filas, 77,0 MB |
| Ejemplo del dueño: 10 s×24 h, 1 min×7 d, 5 min×30 d, con máximo | 1,12 M filas, 76,6 MB | 5,72 M filas, 421 MB |

El ejemplo del dueño es razonable con ~16 apps (77 MB); a 100 apps (421 MB) ya no. La propuesta cuesta +47 % de
disco por tener 7 d a 10 min y el máximo.

### 1.7 Otros defectos hallados

- **Ticks repetidos** en tramos cortos ("21:40" cinco veces, `formatChartTime` sin segundos).
- Siete peticiones por refresco en Overview (cpu, mem, mem.total, rx, tx, disk, disk.total) más las de atención: unas 14 al cargar.
  El SSE `metrics` (2 s) no se usa para añadir el punto vivo: el gráfico de 1 h avanza cada 30 s.
- El gráfico de **Disco** dibuja `used` en `[0, total]`: una línea plana (captura). No informa; el dato útil es
  el margen y la tendencia.
- Diferencia visible de densidad al pasar de etapa (7 d, red) por medias en cubos de tamaño distinto.
- `MachineCharts.tsx:186-196` protege "menos de 2 puntos" (su comentario: con uno, "an empty frame with a collapsed time axis"); `MetricsTab.tsx` no tiene esa guarda.
- Dos fuentes de la razón "no hay métricas" (colector Python y cliente).

---

## 2. Cómo lo hacen otros

### 2.1 Producto por producto

| Producto | Rango y retención | Lectura / tooltip | Estados vacíos y huecos | Lección para Noust |
|---|---|---|---|---|
| **Grafana** | Ventana explícita por panel; series con huecos según "Connect null values" (Never / Always / Threshold) y "Disconnect values" | Tooltip mode Single / All / Hidden, "Hover proximity", orden de valores; en el dashboard, "Graph tooltip": Default / **Shared crosshair** / Shared tooltip | Soft min/max para no magnificar ruido | Crosshair compartido como ajuste de rejilla; huecos con umbral; ejes con mínimos blandos (Disco) |
| **Netdata** | Etapas ×60: 1 s (14 d), 1 min (3 meses), 1 h (2 años), 1 GiB cada una | Al pasar, se **pausa** y muestra valores por dimensión; teclado con Shift/Alt para zoom y selección; barra de dimensiones | Columna "Info" del tooltip avisa de "no data for the selected points" | Etapas con multiplicador 60; decir en el propio tooltip cuando falta el dato |
| **Beszel** | Registros 1m, 10m, 20m, 120m, 480m, cada uno por **promedio** de los menores (tolerancia: 9 de 10 minutos) | Gráficos por sistema | — | Otro almacén que solo promedia: sin máximo, los picos se pierden |
| **Railway** | Hasta 30 d; CPU, memoria, disco, red; Sum vs Replica | Líneas punteadas al iniciar cada despliegue; serie continua entre despliegues | No recoge métricas de aplicación | Noust ya tiene las marcas de despliegue: paridad |
| **Render** | 7 / 14 / 30 d según plan; agregación mín/máx/media; valor o % del máximo | Controles sobre el gráfico | CPU/memoria **excluye los sitios estáticos**, y lo documenta | Nuestro estado "estático" es estándar; falta decirlo también para PHP-FPM y Compose |
| **Vercel** | Selector de tiempo y calendario | Pasar el ratón muestra el valor del instante y la granularidad; **arrastrar para seleccionar y botón "Zoom In"** | — | Zoom = selección + botón (alternativa sin arrastre) |
| **Coolify** | 5 y 10 min en vivo, 30 min, 1 h, 12 h, 1 sem, 30 d; retención local 7 d por defecto | Charts de servidor y por recurso | Sin Sentinel, la página dirige a activarlo. "The chart can offer a longer range than the retained data." No hay métricas para Compose | Es nuestro caso: decir "no hay colector" y dibujar el hueco |
| **Dokploy** | Refresco 20 s; retención 2 días por defecto | Servidor y contenedor; umbrales de alerta | Solo lo que corre | Umbrales editables junto a la serie |
| **Datadog** | Modo pantalla completa con **minimapa** | Cursor y zoom compartidos entre widgets | — | Zoom y navegación en el diálogo, no en la tarjeta |
| **Cloudflare** | Por defecto 24 h; selector desplegable | **Arrastrar** sobre el gráfico para elegir rango; "X" para volver | — | Reset visible |
| **Stripe Home** | Rango con **comparación con el periodo anterior** | Widgets personalizables | Avisos accionables (disputas, verificaciones) junto a las cifras | Home = avisos + cifras + gráficos; cada aviso trae su acción |
| **Uptime Kuma** | README: gráfico de ping, intervalos de 20 s, páginas de estado | — | — | Solo confirmado lo que dice el README |

### 2.2 Patrones que adoptar

1. **El rango es el dominio del eje**, no solo una consulta (así funciona el selector de tiempo de Grafana; Coolify
   reconoce en su documentación que el gráfico puede ofrecer más rango del que hay retenido, que es nuestro fallo).
2. **Crosshair compartido por defecto** (Grafana lo eleva a ajuste del dashboard); tooltip compartido solo por opción.
3. **Zoom que vuelve a consultar**, con botón además del arrastre (Vercel) y navegación en pantalla completa (Datadog).
4. **Etapas ×60** con promedio y máximo (Netdata; Beszel solo promedio).
5. **Decir qué falta**: Netdata en el tooltip, Coolify en la página, Render en la documentación.
6. **Cifras con sparkline y variación** (panel Stat de Grafana: valor + gráfico opcional + "percent change").
7. **Home = aviso, cifra, gráfico** (Stripe). Grafana: "Tell a story: … large to small or general to specific";
   cargas mentales bajas.

### 2.3 Accesibilidad de gráficos

- **WCAG 2.2 SC 1.4.13** (contenido al pasar o enfocar): la tarjeta debe ser **descartable** sin mover el
  puntero ni el foco, **puntero-alcanzable** y **persistente** hasta que se quite el disparador o se descarte.
- **SC 2.5.7 (arrastre)**: todo lo que se hace arrastrando (zoom) debe tener alternativa de un solo puntero: los
  botones Zoom in/out y campos Desde/Hasta.
- **SC 4.1.3 (mensajes de estado)**: la región `role="status"` educada, con anuncios espaciados, ya cumple; no anunciar
  en `hover`.
- **Tabla como alternativa**: Highcharts recomienda "always include the Accessibility module" (descripción textual,
  teclado, ARIA y tabla de datos vía export-data); la guía del servicio civil británico exige texto o tabla
  equivalentes, etiquetas directas mejor que leyendas, y descarga de datos accesible. TanStack Charts: el tooltip
  es complementario; anclado, se comporta como diálogo no modal, se cierra con Escape y devuelve el foco al gráfico;
  "use live announcements sparingly"; la tabla enlazada da los valores exactos.
- **Teclado** (ApexCharts documenta el mismo modelo): izquierda/derecha entre puntos, Home/End, Enter/Espacio, Escape.
- **Chartability** (50 heurísticas, POUR + Comprometido/Asistivo/Flexible; lista corta de 14 pruebas de 20-40 min) como
  lista de revisión.

### 2.4 Lo que uPlot ya trae

Versión instalada 1.6.32 (`panel/node_modules/uplot/dist/uPlot.d.ts`):

- `scales.x.range` acepta función `(u, dataMin, dataMax) => [min, max]`: es la palanca para fijar el dominio.
- `series.gaps` (refinador con `uPlot.addGap`) y `null` para huecos; la demo `missing-data.js` muestra ambos,
  incluido detectar saltos de X mayores que un `delta`.
- `cursor.sync` (`key`, `scales`, `match`, `setSeries`) con `uPlot.sync(key)`: sincroniza por valor de escala; solo por eventos de
  ratón, por lo que el teclado necesita un estado propio (contexto React).
- `cursor.dataIdx`, `cursor.hover.prox`, `posToIdx`, `setCursor({left, top})`.
- La demo `cursor-tooltip.html` construye el tooltip como elemento DOM en el hook `setCursor`; nosotros lo hacemos con
  React y posición por CSSOM (permitido por la CSP; ya se hace con las marcas, `Chart.tsx:799`).

---

## 3. Propuesta

### 3.1 Componente de gráfico

**Principios.** (1) El marco nunca cambia de altura por el estado (cargando, vacío, parcial, tabla). (2) El eje es la
ventana. (3) Todo estado vacío dice por qué. (4) Lo que aparece al pasar el ratón también existe sin ratón. (5) Nada
de HTML por cadena ni `<style>`: React + CSSOM.

**Contrato nuevo (`ChartProps`)**

| Prop | Tipo | Para qué |
|---|---|---|
| `domain` | `[from, to]` (s) | Eje X fijo. Sustituye a "min/max de los datos". |
| `step` | `number` (s) | Anchura de la rejilla; umbral de hueco = 2,5·step; texto "media de 1 min". |
| `timestamps` / `series[].values` | rejilla regular con `null` | El servidor entrega la rejilla (§3.2), sin `alignSeries`. |
| `series[].peak?` | `(number\|null)[]` | Máximo por cubo (línea tenue o solo tarjeta). |
| `firstSampleAt` | `number \| null` | Banda "Historial desde…" entre `from` y ese instante. |
| `emptyReason` | `{code, …}` | Estado vacío con motivo (§3.3). |
| `group` | `ChartGroup` | Crosshair compartido. |

**Dominio fijo y huecos.** `scales.x = { time: true, auto: false, range: () => domainRef.current }`; en cada refresco:
`setData(data, false)` y `setScale("x", zoom ?? domain)` (hoy `Chart.tsx:604` hace `setData(data)` y solo rescala si hay zoom).
Huecos: `null` en la rejilla (uPlot corta la línea) y, para datos irregulares, `series.gaps` con `uPlot.addGap` cuando
el salto supere 2,5·`step`. Nunca se une a través de un hueco (guía del servicio civil británico). Bandas neutras
(sin color de estado) rayadas para "sin historial" y "sin muestras"; la banda se dibuja en el `draw` hook (ya existe,
`Chart.tsx:526-537`) y su rótulo es DOM (React), no canvas, para que sea texto accesible.

**Línea de verdad** (una por sección y en el diálogo, `aria-live="polite"` al cambiar de rango):
`Mostrando 28 sep 21:40 – 29 sep 21:40 · medias de 1 min · historial desde 29 sep 20:12`. Es la respuesta directa a
"el selector no cambia nada": aunque falten datos, el rótulo y el eje dicen lo mismo que la petición.

**Tarjeta de lectura junto al cursor**

- Elemento React dentro del contenedor del gráfico (`position: relative`), posicionado con
  `style={{ transform: `translate(${x}px, ${y}px)` }}` (CSSOM, como las marcas), **actualizado por ref en
  `requestAnimationFrame`** desde el hook `setCursor` de uPlot, sin re-render por movimiento.
- Contenido: instante completo (con segundos si `step < 60`, con fecha si la ventana ≥ 24 h), etapa ("media de 1 min"),
  una fila por serie (muestra trazo/discontinuo + nombre + valor en mono con cifras tabulares), fila "Pico 92 %" si hay
  máximo, y "Sin datos: el colector no estaba en marcha" si el punto es `null`. Ancho mínimo fijo para que no baile.
- Colocación: a ≥12 px del cursor, invierte a la izquierda cerca del borde derecho, se sujeta dentro del marco.
- WCAG 1.4.13: la **duplicación permanente** de esos valores en la fila de leyenda (que ya existe y no depende del
  ratón) es el canal persistente; **Esc** la descarta (escucha en `document` mientras es visible); **clic o Enter la
  ancla** (persiste, texto seleccionable, se cierra con Esc o clic fuera; sin controles focusables dentro, así que
  `aria-hidden="true"` es legal). El puntero puede atravesarla (`pointer-events: none` solo en la tarjeta flotante; la
  anclada no lo tiene).
- Táctil: un toque coloca el cursor, otro fuera lo quita; el arrastre horizontal en el diálogo selecciona.

**Crosshair compartido.** `ChartGroup` (contexto React) guarda `{ index | null, source }` sobre **la rejilla común**
que devuelve la API por lote, de modo que el índice vale para todos los gráficos. Cada gráfico posiciona su cursor con
`setCursor({ left: valToPos(t), top: -10 })` (línea vertical sin punto horizontal) y actualiza su fila de lectura; solo
el gráfico bajo el puntero muestra la tarjeta (modo Grafana "Shared crosshair"). Para que el mismo instante caiga en
el mismo píxel, el grupo fija **un único ancho de eje de valores** (el máximo de sus miembros) en lugar de
`valueAxisSize` por gráfico (`Chart.tsx:505`); alternativa más económica en espacio: rótulos del eje dentro del plot.
Por qué no `uPlot.sync`: solo se alimenta con ratón, y el teclado también debe sincronizar.

**Teclado** (el gráfico es `role="application"` con `aria-roledescription="chart"`, `Chart.tsx:772-786`, ya correcto)

| Tecla | Efecto |
|---|---|
| ← / → | Muestra anterior/siguiente (nulls incluidos: "21:40, sin datos") |
| Re Pág / Av Pág | ±10 muestras |
| Inicio / Fin | Primera/última muestra de lo visible |
| Enter o Espacio | Ancla la tarjeta en la muestra |
| Escape | Descarta tarjeta anclada; si no hay, el diálogo se encarga (`Chart.tsx:643-648`) |
| `[` y `]` (diálogo) | Marca inicio y fin de una selección; Enter hace zoom |

Anuncio por `role="status" aria-live="polite" aria-atomic="true"` limitado a 400 ms (`useThrottled`, ya existe): "21:40, CPU
12,4 %, pico 31 %". Al pasar el ratón no se anuncia (SC 4.1.3 no lo pide y sería ruido).

**Diálogo ampliado** (ya existe, `Chart.tsx:856-929`):

1. Selector de rango repetido + campos **Desde/Hasta** (alternativa a arrastrar, SC 2.5.7).
2. Zoom: arrastre, botones −/+, `[` `]`, y **reset**. **Cada zoom vuelve a consultar** (`from/to`) para elegir una etapa más fina; el texto
   lo dice: "Mostrando 09:10 – 10:40 · medias de 1 min". Hoy zoom = rescale de 400 puntos.
3. Pestañas **Gráfico | Datos**, misma altura (nunca cambia el cuerpo del diálogo). En **Datos**: resumen (mínimo, media, p95,
   máximo, último) y tabla con `<caption>`, cabecera fija, hora con segundos cuando corresponde, columnas de serie + pico,
   y "Descargar CSV" (mejor un endpoint `text/csv` del servidor: sin `blob:` y reutilizable por la CLI).
4. Opcional (v2): minimapa estilo Datadog.

**La tabla sale de la tarjeta.** Se elimina el botón por tarjeta; queda **Ampliar**. La alternativa accesible en la página es
el resumen textual del `aria-label` (ya existe) + el diálogo. En la sección Machine, un único botón **Datos** abre un diálogo con una tabla
conjunta (Hora | CPU | Memoria | Entrada | Salida | Disco) porque la rejilla es común. Cero reflujo por construcción.

**Estados con motivo**

| Estado | Marco | Texto |
|---|---|---|
| Cargando | Esqueleto de la misma altura (ya existe) | "Cargando…" en `sr-only` |
| Vacío por colector parado | Eje dibujado + banda | "Noust no está registrando métricas. La consola las registra mientras corre; la última lectura es de las 09:12. `noust web enable` la deja siempre activa." |
| Recogiendo | Eje dibujado, 1-2 puntos | "Grabando desde las 21:40 (hace 12 s). El gráfico se rellena solo." |
| Parcial | Banda "Historial desde…" | El rótulo de verdad |
| App sin muestreo | Cuerpo de vacío del §3.3 | Motivo + arreglo |
| Muestras viejas | Chip | "Última muestra hace 4 min" (si > 3·step) |
| Error | `ErrorBlock` (ya existe) | Salida del sistema literal |

**Unidades y formato** (todo por `lib/format.ts`, una sola implementación): CPU `%` 0-100 (de app: "% de una CPU", eje
`max(100, pico)`, línea de límite si hay `CPUQuota`); memoria en `B/KB/MB/GB` (`formatBytes`), eje a `MemoryMax` o `mem.total`; red `B/s` (`formatBytesRate`); **disco:
eje automático con mínimo blando** y línea de capacidad, no `[0,total]` (Grafana: soft min/max); tres cifras
significativas; separador decimal por idioma. Los umbrales (80 %, 95 %) se dibujan como trazos etiquetados con texto,
y el tramo por encima toma el color de estado con forma distinta (regla: color solo para estado).

**CSP y Trusted Types**: sin `innerHTML`; la leyenda de uPlot sigue apagada; el cursor y los puntos de uPlot ya se
posicionan por `style` (CSSOM). Sin fuentes de datos: `blob:` solo si se descarga en cliente (evitado).

**Copia (i18n)**: frases completas con marcadores en `panel/src/i18n/{en,es}` (`common.chart.*`); las etiquetas de estado
nunca fragmentadas ("Mostrando {from} – {to}").

### 3.2 Historial y rango (backend)

#### a) API de consulta por lote

```
GET /api/metrics/query?metric=cpu.percent&metric=mem.used_bytes&window=24h
GET /api/metrics/query?metric=…&from=1790628180&to=1790714500        (zoom)
```

```jsonc
{
  "from": 1790628180, "to": 1790714500, "step": 60,
  "resolution": "1m",                      // etapa REAL leída: raw | 1m | 10m | 1h
  "first_sample_at": 1790710898,           // null si no hay nada
  "collector": { "state": "running", "host": "daemon", "since": 1790700000, "last_sample_at": 1790714495 },
  "series": [ { "metric": "cpu.percent", "points": [[1790628180, 12.4, 31.0], [1790628240, null, null], …] } ]
}
```

- **Rejilla regular** `from + k·step` entre `from` y `to`; `null` donde no hay cubo. Todas las series comparten
  marcas de tiempo: se van `alignSeries` y las 7 peticiones (1 por gráfico o serie más el techo) pasan a 1.
- `[ts, media, máximo]`. El techo (`mem.total_bytes`, `disk.total_bytes`) va como metadato de la serie, no como serie.
- Guardas en el punto de estrangulamiento (regla 4), dentro de `MetricsStore.query_range()`, no en el endpoint: rango
  máximo 35 d, ≤ 1500 puntos, `step` múltiplo de la etapa, `from<to`.
- Declarar `@router.get("/query")` **antes** de `/{metric:path}` (`metrics.py:84`), que de lo contrario lo captura.
  Mantener el endpoint antiguo (delegando, con `resolution` ya honesto) hasta 3.2. Modelos por
  `web/pydantic_compat.py` (pydantic 1.10 en Ubuntu 24.04/Debian 12), regenerar `openapi.json` y `schema.gen.ts`.
- `GET /api/metrics` añade `collector` y la ruta de `metrics.db`.

#### b) Etapas y retención

| Etapa | Paso | Retención | Filas/métrica | Sirve |
|---|---|---|---|---|
| raw | 5 s | 2 h | 1440 | 1 h (720 puntos) y el zoom reciente |
| 1m | 1 min, media + máx | 26 h | 1560 | 24 h (1440 puntos) |
| 10m | 10 min, media + máx | 8 d | 1152 | 7 d (1008 puntos) |
| 1h | 1 h, media + máx | 35 d | 840 | 30 d (720 puntos) |

- Cada ventana del selector cae **exactamente** en una etapa nativa, sin `_bucket_means` en tiempo de consulta. Al hacer zoom,
  `step = span/720` elige la etapa más fina que cubra el tramo.
- Tamaño medido: 14 MB con 16 apps, 77 MB con 100 (§1.6). La retención de la etapa horaria puede ser configurable
  (`metrics.retention_days`, hasta ~400 d si el ENS lo pide).
- `consolidated(metric, resolution, ts, value, max_value)`; migración con `PRAGMA user_version` (`SCHEMA_VERSION = 1`
  existe en `timeseries.py:165` pero no se usa) y `ALTER TABLE … ADD COLUMN max_value REAL` (filas antiguas con `NULL` →
  se devuelve la media).
- **Honestidad de etiquetas**: `resolution` es la etapa leída de verdad; nunca "hour" si solo había crudo.
- Deduplicar `mem.total_bytes`, `disk.total_bytes` y `swap` (escribir cada 5 min o al cambiar).
- **Un solo muestreo de máquina** (`noust/monitor/sampler.py`): `read_machine`, `_system_pairs` y
  `collect_resource_metrics` pasan a leer de ahí; el disco es el de `apps_directory` en ambos sitios (o dos métricas con nombre).

#### c) El colector deja de depender de la consola

Opciones:

| Opción | Coste | Riesgo | Veredicto |
|---|---|---|---|
| A. Dejarlo en la consola y decirlo en la UI | Pequeño | Sigue habiendo huecos cada vez que se cierra | Necesario igualmente (estado y motivo) |
| **B. Demonio `noust-metrics.service`** (root, `Nice=10`, `MemoryMax=96M`, `Restart=always`), habilitado por el paquete, por `noust web enable` y por `noust fleet authorize` | Medio | Un proceso más | **Recomendada** |
| C. Ejecutarlo dentro de `noust-monitor` | Pequeño | Acopla historial y alertas; hoy hay que activarlo con `noust monitor enable` (el ítem 14 propone vigilar por defecto) | Alternativa si el dueño prefiere un solo demonio |

Reglas de implementación: (1) mover `MetricsCollector` de `noust/web/` a `noust/monitor/collector.py` (no importa
FastAPI: hoy `import noust.web.metrics_collector` falla sin él); (2) **arrendamiento** en `metrics.db`
(`collector(id, pid, host, started_at, heartbeat_at, interval_s)`); `start()` toma el arrendamiento solo si el latido
tiene más de 3·intervalo, y la consola arranca su hilo **solo si no hay un demonio vivo** y lo vuelve a comprobar cada
30 s (regla 4: en `MetricsCollector.start`, no en los llamadores); (3) `consolidate()` **al arrancar**, no 300 s después
(`metrics_collector.py:159`); (4) el bucle atrapa en el límite de error, **registra con traza** y sigue, con
`last_error` y contador en el estado (regla 2); (5) intervalo 5 s; (6) la ruta de `metrics.db` sale siempre de
`core/paths.py`, se escribe en el diario al arrancar y `noust doctor` avisa si existe más de un `metrics.db`; `noust health` gana una comprobación "Métricas" (colector vivo, edad de la última muestra).

### 3.3 Métricas de app por tipo de unidad

**Un solo plan de muestreo (regla 3).** `noust/monitor/plan.py`:

```python
@dataclass(frozen=True)
class SamplingPlan:
    domain: str
    source: Literal["cgroup", "docker", "none"]
    units: tuple[UnitRef, ...]        # nombre + ControlGroup real
    reason: Reason | None             # None = se muestrea
```

El colector consume `plan.units`; la API expone `plan.reason`; la consola deja de re-derivar. `metrics_unavailable_reason`
se disuelve en el plan.

**La ruta del cgroup se pregunta a systemd, no se calcula.** Una sola llamada cada 30 s por el `CommandRunner`:
`systemctl show -p Id,ActiveState,ControlGroup,MemoryAccounting,CPUAccounting <unidades…>`. `ControlGroup` es
la ruta autoritativa para nombres legacy, plantillas, escapes y cualquier `Slice=` que alguien añada en un drop-in;
`unit_cgroup_path` (calculada) queda de reserva para los tests.

| Tipo | Unidad(es) | Fuente | Hoy | Propuesta |
|---|---|---|---|---|
| Node/Next/Python… en sitio o con releases | `<nombre>` o `wasm-<nombre>` | cgroup | Funciona | Igual + estado y razón |
| Blue/green | `<n>@blue`, `<n>@green` | cgroup vía `ControlGroup` | Suma ambas | Igual; series por color solo si el dueño lo pide |
| Monorepo | N unidades | cgroup | Suma | Suma + desglose por workspace en el diálogo |
| Docker Compose | `oneshot` | ninguna | Sin muestreo, sin API | Motivo `compose`. **Fase 2**: contenedores del proyecto (`docker ps --filter label=com.docker.compose.project=<n>` por el runner, cgroup `system.slice/docker-<id>.scope`) |
| Estático | ninguna | ninguna | Estado propio en cliente | Motivo `static`. **Fase 3**: tráfico desde el access log de nginx (peticiones, 4xx/5xx), como Vercel/Render |
| PHP-FPM | pool dentro de `phpX.Y-fpm.service` | ninguna | Vacío sin motivo | Motivo `php_fpm_shared`. Opción: sumar PSS de los procesos `php-fpm: pool <n>` con psutil (somos root) |
| Parada | unidad inactiva | ninguna | "No readings" | Motivo `stopped` con desde cuándo; el historial previo sigue visible |
| cgroup v1 / híbrida | otra ruta | — | Nada | Motivo `cgroup_v1`; no soportar (Ubuntu ≥22.04, Debian ≥11 son v2) |
| Accounting apagado | cgroup sin `memory.current` | — | Nada | Motivo `accounting_off` + botón (modo sudo): drop-in `MemoryAccounting=yes CPUAccounting=yes` y `daemon-reload`; avisa que hace falta reiniciar para cifras exactas (el kernel solo carga a la cgroup lo nuevo) |
| Sin unidad | `app_units` da un nombre sin fichero | — | Nada | Motivo `unit_missing` |
| Colector parado o hilo muerto | — | — | Nada | Motivo `collector_stopped` (estado de §3.2c) |

**Endurecimiento barato**: añadir `MemoryAccounting=yes` y `CPUAccounting=yes` a `app.service.j2` y `app@.service.j2`
(hoy ausentes): las unidades que Noust genera dejan de depender del valor por defecto de la distro. Las de 1.x lo
recibirán en su próximo `update` que reescriba la unidad.

**API de estado**: `GET /api/apps/{domain}/metrics` →
`{ sampled, source, reason: {code, params}|null, units:[{name, active_state, control_group, cgroup_exists, memory_current, cpu_stat}], last_sample_at, collector }`.
La consola lo pide solo cuando la serie viene vacía. La lectura de `systemctl show` va por el `CommandRunner` (regla 1) y
solo en diagnóstico, no en cada tick. Guardas en `MetricsStore`/`ServiceManager`, no en el endpoint (regla 4).

**Estado vacío de la pestaña** (`EmptyState` + `SystemOutput`): título, motivo en una frase, arreglo y la salida literal
(`ControlGroup=`, ruta comprobada, `MemoryAccounting=`) en mono, siguiendo la regla "un error del sistema no se
parafrasea". Ejemplos:

| Código | EN | ES |
|---|---|---|
| `collector_stopped` | "Noust is not recording metrics right now. The console records them while it runs; the last reading is from {time}. Keep it running with `noust web enable`." | "Noust no está registrando métricas ahora. La consola las registra mientras está en marcha; la última lectura es de las {time}. Déjala siempre activa con `noust web enable`." |
| `accounting_off` | "systemd is not counting memory for {unit}. Turn accounting on, then restart the app for exact numbers." | "systemd no está contando la memoria de {unit}. Activa el conteo y reinicia la aplicación para tener cifras exactas." |
| `stopped` | "{domain} has been stopped since {time}, so there is nothing to measure. Earlier readings stay in the chart." | "{domain} está parada desde las {time}, así que no hay nada que medir. Las lecturas anteriores siguen en el gráfico." |
| `compose` | "Docker Compose runs in Docker's own cgroups, which Noust does not read yet. Use `docker stats`." | "Docker Compose corre en los cgroups de Docker, que Noust todavía no lee. Usa `docker stats`." |
| `php_fpm_shared` | "PHP-FPM pools share one service, so per-app numbers are not available." | "Los pools de PHP-FPM comparten un servicio, así que no hay cifras por aplicación." |

**Señales adicionales por unidad** (todas verificadas en la máquina de pruebas, coste ~un `read` cada una): `working
set` = `memory.current − inactive_file` (como Docker) en lugar de `memory.current`; `memory.max` como límite
dibujado aunque la unidad no lo declare; `memory.events` → `oom_kill` como **marca sobre el gráfico** (reutiliza
`ChartMarker`, estado fallo, "Terminada por falta de memoria a las 03:12"); `cpu.stat` → `nr_throttled` cuando hay
`CPUQuota`; `memory.pressure`/`cpu.pressure` (PSI) como señal de **saturación** (método USE de Grafana / cuarta señal de
oro del SRE de Google); `pids.current`. `io.stat` solo si `IOAccounting=yes` (opcional, apagado por defecto en systemd).

### 3.4 Overview: de pila de secciones a panel

**Hoy** (medido en 1440×900 con la semilla): 2157 px de alto (2788 a 390 px), cinco secciones apiladas. En 900 px
se ve Necesita atención (~350 px con cuatro avisos) y la primera fila de gráficos justo hasta el pliegue (empiezan
en y≈660); en 390 px solo el título y los avisos. No hay cifras clave; despliegues y actividad son dos listas separadas;
el gráfico de Disco es una línea plana y la tabla de aplicaciones ocupa ~370 px para ocho filas.

**Principios.** Estado primero, calma, achromático: el color solo aparece cuando una cifra es un problema, con glifo +
palabra (como `FailCount` en `FleetPage.tsx`). Densidad "de un vistazo": cada bloque responde a una pregunta.
Grafana pide que cada gráfico represente algo evidente y que el conjunto vaya "large to small or general to specific".

**Orden de bloques (servidor):**

1. Cabecera: título, `Acciones ▾` (menú Base UI: Copia de seguridad ahora, Renovar certificados pendientes, Buscar
   actualizaciones, Añadir servidor en una central) y `Nueva aplicación` primario. Las acciones que tocan algo van
   detrás del modo sudo que ya define la API (`x-noust-requires-elevation`).
2. **Franja de seis cifras** (`StatTile`, ya existe, con enlace): Aplicaciones, Despliegues hoy, Certificados, Copias
   24 h, Disco, Actualizaciones.
3. **Necesita atención** (ya existe): tres filas visibles y "y N más"; sin avisos, una sola línea "Nada necesita atención"
   con glifo, no una sección vacía. Cada aviso trae su acción (Stripe): Renovar ahora, Reiniciar, Diagnosticar, Abrir servicio.
4. **Máquina**: línea de verdad + selector + **Datos**; tres gráficos (CPU, Memoria, Red) con crosshair compartido. El Disco
   deja de ser gráfico de tarjeta: vive en su cifra (sparkline + pronóstico) y se amplía en el diálogo.
5. **Aplicaciones** (⅔): tabla compacta, las 8 peores primero, con CPU y memoria y motivo cuando no hay ("—" con
   `title` accesible del motivo, no un guion mudo), y "Ver las N".
6. **Actividad** (⅓): línea temporal de hoy y ayer, reutilizando `mergeActivity` de `features/activity/data.ts` (trabajos
   + auditoría, ya fusionados y ordenados; sin `admin` la auditoría se omite con una nota, como `ActivityPage`). Glifo + verbo +
   sujeto + relativo. Sustituye a "Recent deployments".
7. **Flota** (solo central): §3.4.2.

**Encima del pliegue (1440×900):** cabecera, seis cifras, avisos y la fila completa de gráficos: barra superior 56 px +
cabecera ~130 + cifras ~100 + avisos ~240 (tres filas y "y N más") + cabecera de Máquina ~60 → los gráficos empiezan en y≈620 y
caben enteros (~230 px) antes de los 900 px; Aplicaciones y Actividad quedan debajo. **A 390×844:** cabecera compacta, seis cifras
en 2×3 (~270 px), avisos (dos visibles y "mostrar más") y la cabecera de Máquina; el primer gráfico queda justo bajo el pliegue.
Lo primero que se ve es "cómo estoy y qué falla". Tamaño estimado de página: ~1250 px a 1440 px de ancho (hoy 2157).

#### 3.4.1 Cifras clave: contenido, estado y datos

| Cifra | Muestra | Neutral | Aviso (glifo + palabra) | Fallo | Datos hoy | Falta |
|---|---|---|---|---|---|---|
| Aplicaciones | `12 running` + `1 failed · 4 stopped` | ok | — | ≥ 1 en `failed` | `GET /api/system/machine` (`apps`) y evento SSE `machine` (5 s) | — |
| Despliegues hoy | `5` + `4 ok · 1 failed`, último | ok | — | el último despliegue de una app sigue fallido | `GET /api/deployments?limit=200` (recuento en cliente, incorrecto si hay > 200/día) | Contador por rango en el servidor (`since`) |
| Certificados | `2 expire in ≤ 21 days`, próximo | todos válidos | expiran < 21 d (`CERT_WARNING_DAYS`, `attention.ts:20`) | caducado | `GET /api/certs` (`days_remaining`) | — |
| Copias 24 h | `15 of 17 apps`, última | todas con copia | apps con calendario y sin copia en 24 h | última copia fallida | `GET /api/backups`, `GET /api/backup-schedules` | Cálculo servidor "apps sin copia"; si no hay calendarios: "Sin copias configuradas" enlazando a Copias |
| Disco | `76 % free`, sparkline 7 d, **"full in ~64 days"** | > 20 % libre y > 90 d | < 20 % o < 30 d | < 10 % o < 7 d | `machine.disk` | Pronóstico (abajo) |
| Actualizaciones | `14 pending · 3 security`, reinicio necesario | 0 | seguridad > 0 o reinicio | — | **No existe** | `GET /api/system/updates` (abajo) |

Umbrales por defecto configurables más adelante (`overview.*` en `config.yaml`); ninguno cambia la forma, solo el tono.

**Pronóstico de disco**: regresión lineal por mínimos cuadrados sobre `disk.used_bytes` de la etapa horaria de los
últimos ≤ 14 d (≥ 3 d de historial, pendiente > 0), servidor. Si no hay base suficiente devuelve `null` con motivo
(`insufficient_history` / `not_growing`); una limpieza que borra logs invierte la pendiente y se dice "no crece". La
interfaz siempre dice "estimado".

**Actualizaciones del sistema**: nuevo gestor `managers/updates.py` compartido con la página Servidor (ítems 32 y 46: una sola
implementación): `/var/lib/update-notifier/updates-available` y `/run/reboot-required` en Debian/Ubuntu, `dnf check-update` /
`zypper lu` por el `CommandRunner` en Fedora/openSUSE, con caché de 6 h, `checked_at` y botón "Comprobar ahora".

#### 3.4.2 Un solo `GET /api/overview` por servidor, y la flota

```jsonc
{
  "server": { "name": "host-1", "version": "3.1.0", "role": "server" },
  "apps": { "running": 12, "failed": 1, "stopped": 4, "static": 3 },
  "deploys": { "since": "2026-09-29T00:00:00+02:00", "total": 5, "succeeded": 4, "failed": 1, "last_at": "…" },
  "certificates": { "total": 18, "expiring": 2, "expired": 0, "next_days": 12 },
  "backups": { "apps": 17, "with_backup_24h": 15, "failed_24h": 0, "last_at": "…" },
  "disk": { "used": 0, "total": 0, "forecast_full_days": 64, "forecast_reason": null },
  "updates": { "available": 14, "security": 3, "reboot_required": true, "checked_at": "…" },
  "spark": { "cpu": [/* 60 puntos */], "mem": [/* 60 puntos */] }      // opcional: ?spark=1
}
```

Es un agregador de los gestores (CertManager, BackupManager, store…), con caché de 15-30 s, no una reimplementación
(regla 3). **La central hace una llamada por nodo** por el `node_proxy` (que ya reenvía todo por el túnel): la
flota no reimplementa nada de lo que hace un nodo, y hoy `useFleet` lanza cinco consultas por nodo
(`useFleet.ts`: machine, apps, deploys, certs, units) que se reducen a una. La lista de avisos ordenados sigue en cliente
(`collectAttention`, `attention.ts`) en 3.1; moverla al servidor es tarea de 3.2.

**Central con rol `server`** (Overview con selector `Este servidor | Flota`, `?scope=fleet`): debajo de las seis cifras, la
franja de flota: "4 servidores · 3 accesibles · 1 no accesible", tabla de hasta 5 filas (estado con glifo + palabra, versión,
CPU/memoria, apps en marcha/falladas, avisos) y "Abrir flota". Las cifras del selector cambian de alcance; los gráficos siguen
siendo del propio servidor.

**Hub** (hoy `/` redirige a `/fleet`, `routes/_console.tsx:15-18`): la página de inicio es este mismo panel con alcance flota
fijo: seis cifras agregadas (Servidores, Aplicaciones, Despliegues hoy, Certificados, Copias, Actualizaciones), avisos de
todos los servidores (`FleetAttention`, ya existe), **una miniatura CPU/memoria por servidor** (`spark` del propio `/api/overview`,
una por nodo) y la tabla de servidores. Sin gráficos de la máquina del hub (no despliega nada).

#### 3.4.3 Wireframes

Escritorio 1440 px (columna de contenido de ~1136 px, la barra lateral de 240 px es un resumen). Servidor:

```text
┌────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ▲ noust    │ host-1 up 20d │ Load ▁▂▁ 0.8 │ CPU 34% Mem 32% Disk 24% │ Units ●5 ×2 ○2 │     ⌕ Search ⌃K             │
├────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│            │                                                                                                        │
│ Overview   │ Overview                                                  [Actions ▾]  [＋ New application]             │
│ Fleet      │ host-1 · noust 3.1.0 · updated 21:40:12                                                                │
│            │                                                                                                        │
│ Apps    ×1 │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│ Databases  │ │ Applications │ │ Deploys today│ │ Certificates │ │ Backups 24 h │ │ Disk         │ │ Updates      │  │
│ Services ×2│ │ 12 running   │ │ 5            │ │ 2 expire     │ │ 15 of 17 apps│ │ 76% free     │ │ 14 pending   │  │
│ Cron       │ │ ✕ 1 failed   │ │ 4 ok · ✕ 1   │ │ in ≤ 21 days │ │ ✕ 0 failed   │ │ full in ~64 d│ │ 3 security   │  │
│            │ │ ○ 4 stopped  │ │ last 14:32   │ │ next: 12 d   │ │ last 02:00   │ │ ▁▁▂▂▃▃ 7 d   │ │ reboot needed│  │
│ Domains    │ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘  │
│ Backups    │                                                                                                        │
│            │ ┌─Needs attention 4 ────────────────────────────────────────────────────────────────────────────────┐  │
│ Activity   │ │ ✕ shop.brumaria.es    Service failed · last deploy failed      [View log] [Diagnose]              │  │
│ Server     │ │ ✕ queue-worker        Unit failed                              [Open service]                     │  │
│            │ │ ⚠ blog.nortewave.com  Certificate expires in 12 days           [Renew now]                        │  │
│            │ │   and 1 more                                                    [Show all 4]                      │  │
│            │ └───────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│            │                                                                                                        │
│            │ Machine  Sep 28 21:40 → Sep 29 21:40 · 1-min avg · since 20:12   [1h|24h|7d|30d] [Data]                │
│            │ ┌───────────────────────────────┐ ┌───────────────────────────────┐ ┌───────────────────────────────┐  │
│            │ │ CPU                        ⤢  │ │ Memory                     ⤢  │ │ Network                    ⤢  │  │
│            │ │ 21:40  CPU 34%  peak 92%      │ │ 21:40  Used 5.1 GB            │ │ 21:40  In 4 KB/s  Out 2 KB/s  │  │
│            │ │ 100%┤       ╱╲        ╱╲      │ │ 16 GB┤─────────────────────   │ │ 10 KB┤ ╱╲  ╱╲ ╱╲    ╱╲        │  │
│            │ │     │ ░░░╱  ╲__╱╲_╱  ╲___     │ │      │░░░░░░░░░░░░░░░░░░░░░   │ │      │╱  ╲╱  ╲╱  ╲__╱ ╲_      │  │
│            │ │    0┴──────────────────────   │ │    0 B┴────────────────────   │ │   0 B┴─────────────────────   │  │
│            │ │       21:00      ·      now   │ │       21:00      ·      now   │ │       21:00      ·      now   │  │
│            │ └───────────────────────────────┘ └───────────────────────────────┘ └───────────────────────────────┘  │
╞════════════╪════════════════════════════════════ pliegue a 900 px (1440 x 900) ═════════════════════════════════════╡
│            │ ┌─Applications 17 ───────────────────────────────────────┐ ┌─Activity ──────────────────────────────┐  │
│            │ │ State    App                CPU   Mem     Deploy       │ │ Today                                  │  │
│            │ │ ✕ Failed shop.brumaria.es   -     -       ✕ 6 h        │ │ ● 14:32 Deploy #35 ok                  │  │
│            │ │ ● Run.   api.kestrelworks   2%    180 MB  ● 3 d        │ │         app.wrenfield.io               │  │
│            │ │ ● Run.   app.wrenfield.io   1%    240 MB  ◌ 6 m        │ │ ✕ 09:12 Deploy #34 failed              │  │
│            │ │ ● Run.   panel.corvane.io   4%    150 MB  ● 4 d        │ │         shop.brumaria.es               │  │
│            │ │ ◇ Static status.kestrel...  n/a   n/a     ● Sep 19     │ │ ● 02:00 Backup 15 apps                 │  │
│            │ │                               [All 17 applications]    │ │ Yesterday                              │  │
│            │ └────────────────────────────────────────────────────────┘ │ ● 21:05 Cert renewed                   │  │
│            │                                                            │         blog.nortewave                 │  │
│            │                                                            │          [All activity]                │  │
│            │                                                            └────────────────────────────────────────┘  │
└────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Central con rol `server`: lo que cambia (selector de alcance y franja de flota; el resto como en un servidor):

```text
┌────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ▲ noust    │ host-1 up 20d │ Load ▁▂▁ 0.8 │ CPU 34% Mem 32% Disk 24% │ Units ●5 ×2 ○2 │     ⌕ Search ⌃K             │
├────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│            │                                                                                                        │
│ Overview   │ Overview   Scope: [ This server | Fleet ]                    [Actions ▾]  [＋ New application]          │
│ Fleet      │ host-1 · central (server) · noust 3.1.0                                                                │
│            │                                                                                                        │
│ Apps    ×1 │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│ Databases  │ │ Applications │ │ Deploys today│ │ Certificates │ │ Backups 24 h │ │ Disk         │ │ Updates      │  │
│ Services ×2│ │ 12 running   │ │ 5            │ │ 2 expire     │ │ 15 of 17 apps│ │ 76% free     │ │ 14 pending   │  │
│ Cron       │ │ ✕ 1 failed   │ │ 4 ok · ✕ 1   │ │ in ≤ 21 days │ │ ✕ 0 failed   │ │ full in ~64 d│ │ 3 security   │  │
│            │ │ ○ 4 stopped  │ │ last 14:32   │ │ next: 12 d   │ │ last 02:00   │ │ ▁▁▂▂▃▃ 7 d   │ │ reboot needed│  │
│ Domains    │ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘  │
│ Backups    │                                                                                                        │
│            │ ┌─Fleet  4 servers · 3 reachable · ✕ 1 unreachable ─────────────────────────────────────────────────┐  │
│ Activity   │ │ Server        State           Version  CPU  Mem  Apps run/fail  Attention                         │  │
│ Server     │ │ host-1 (this) ● Reachable     3.1.0    34%  32%  12 / 1         ✕ 4                               │  │
│            │ │ vps-tambor    ● Reachable     3.1.0    12%  41%  6 / 0          ⚠ 1                               │  │
│            │ │ vps-norte     ✕ Unreachable   -        -    -    - / -          ✕ ssh: connection refused         │  │
│            │ │ vps-sur       ● Reachable     3.0.0 ↑  8%   22%  9 / 1          ⚠ 2  update available             │  │
│            │ │                                                             [Open fleet]                          │  │
│            │ └───────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│            │                                                                                                        │
│            │ ┌─Needs attention 4 ────────────────────────────────────────────────────────────────────────────────┐  │
│            │ │ ✕ shop.brumaria.es    Service failed · last deploy failed      [View log] [Diagnose]              │  │
│            │ │ ✕ queue-worker        Unit failed                              [Open service]                     │  │
│            │ │ ⚠ blog.nortewave.com  Certificate expires in 12 days           [Renew now]                        │  │
│            │ │   and 1 more                                                    [Show all 4]                      │  │
│            │ └───────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│            │                                                                                                        │
│            │ (Machine · Applications · Activity: as on a plain server)                                              │
└────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Hub (misma pieza con alcance flota, sin gráficos propios):

```text
┌────────────┬────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ▲ noust    │ central-1 up 41d │ CPU 3% Mem 12% Disk 9% │ Servers ●3 ✕1              ⌕ Search ⌃K                     │
├────────────┼────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│            │                                                                                                        │
│ Fleet      │ Fleet                                                            [Actions ▾]  [＋ Add a server]         │
│            │ central (hub) · noust 3.1.0 · this server deploys nothing                                              │
│ Domains*   │                                                                                                        │
│ Backups*   │ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│            │ │ Servers      │ │ Applications │ │ Deploys today│ │ Certificates │ │ Backups 24 h │ │ Updates      │  │
│ Activity   │ │ 3 of 4 reach.│ │ 27 running   │ │ 11           │ │ 6 expire     │ │ 37 of 41 apps│ │ 3 servers    │  │
│ Server     │ │ ✕ 1 unreach. │ │ ✕ 2 failed   │ │ 10 ok · ✕ 1  │ │ in ≤ 21 days │ │ ✕ 1 failed   │ │ behind Noust │  │
│            │ │ ○ 0 locked   │ │ ○ 6 stopped  │ │ last 14:32   │ │ on 3 servers │ │ oldest 26 h  │ │ 9 security   │  │
│ * = fleet  │ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘  │
│            │                                                                                                        │
│            │ ┌─Needs attention (all servers) 3 ──────────────────────────────────────────────────────────────────┐  │
│            │ │ ✕ vps-norte           Unreachable · ssh: connection refused           [Test connection]           │  │
│            │ │ ✕ vps-sur/shop-api    Service failed                                   [Open on vps-sur]          │  │
│            │ │ ⚠ vps-sur             Noust 3.0.0 · this central runs 3.1.0            [Open server]              │  │
│            │ └───────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│            │                                                                                                        │
│            │ Servers, last hour (CPU / memory)                                        [1h|24h|7d]                   │
│            │ ┌───────────────────────────────┐ ┌───────────────────────────────┐ ┌───────────────────────────────┐  │
│            │ │ host-1                        │ │ vps-tambor                    │ │ vps-sur                       │  │
│            │ │ CPU  34% ▁▂▃▂▁▂▅▃             │ │ CPU  12% ▁▂▃▂▁▂▅▃             │ │ CPU   8% ▁▂▃▂▁▂▅▃             │  │
│            │ │ Mem  32% ▃▃▃▄▄▄▄▅             │ │ Mem  41% ▃▃▃▄▄▄▄▅             │ │ Mem  22% ▃▃▃▄▄▄▄▅             │  │
│            │ └───────────────────────────────┘ └───────────────────────────────┘ └───────────────────────────────┘  │
│            │                                                                                                        │
│            │ ┌─Fleet  4 servers · 3 reachable · ✕ 1 unreachable ─────────────────────────────────────────────────┐  │
│            │ │ Server        State           Version  CPU  Mem  Apps run/fail  Attention                         │  │
│            │ │ host-1 (this) ● Reachable     3.1.0    34%  32%  12 / 1         ✕ 4                               │  │
│            │ │ vps-tambor    ● Reachable     3.1.0    12%  41%  6 / 0          ⚠ 1                               │  │
│            │ │ vps-norte     ✕ Unreachable   -        -    -    - / -          ✕ ssh: connection refused         │  │
│            │ │ vps-sur       ● Reachable     3.0.0 ↑  8%   22%  9 / 1          ⚠ 2  update available             │  │
│            │ │                                                             [Open fleet]                          │  │
│            │ └───────────────────────────────────────────────────────────────────────────────────────────────────┘  │
└────────────┴────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Móvil 390 px (una columna; cifras 2×3; los gráficos se apilan, sin carrusel horizontal que un teclado no alcanzaría):

```text
┌──────────────────────────────────────┐
│ ▲   host-1  ✕ 2               ⌕  ◉  ☰│
├──────────────────────────────────────┤
│ Overview                             │
│ [＋ New application]  [Actions ▾]     │
│                                      │
│┌─────────────────┐┌─────────────────┐│
││ Applications    ││ Deploys today   ││
││ 12 running      ││ 5               ││
││ ✕ 1 failed · ○ 4││ 4 ok · ✕ 1      ││
││                 ││                 ││
│└─────────────────┘└─────────────────┘│
│┌─────────────────┐┌─────────────────┐│
││ Certificates    ││ Backups 24 h    ││
││ 2 expire ≤ 21 d ││ 15 of 17 apps   ││
││ next in 12 d    ││ last 02:00      ││
││                 ││                 ││
│└─────────────────┘└─────────────────┘│
│┌─────────────────┐┌─────────────────┐│
││ Disk            ││ Updates         ││
││ 76% free        ││ 14 pending      ││
││ full in ~64 d   ││ 3 security      ││
││                 ││                 ││
│└─────────────────┘└─────────────────┘│
│                                      │
│ Needs attention 4                    │
│┌────────────────────────────────────┐│
││ ✕ shop.brumaria.es                 ││
││   Service failed · last deploy     ││
││   failed   [View log][Diagnose]    ││
││ ✕ queue-worker  Unit failed        ││
││ ⚠ blog.nortewave.com               ││
││   Certificate expires in 12 d      ││
││   [Renew now]                      ││
││   Show 1 more                      ││
│└────────────────────────────────────┘│
│ Machine  [1h|24h|7d|30d]             │
│ 24 h · 1-min averages · since 20:12  │
╞══════ pliegue a 844 px ══════════════╡
│┌────────────────────────────────────┐│
││ CPU                            ⤢   ││
││ 21:40  CPU 34%  peak 92%           ││
││ ░░░╱╲__╱╲___╱╲__                   ││
││ 21:00           now                ││
│└────────────────────────────────────┘│
│┌────────────────────────────────────┐│
││ Memory  ... (same)                 ││
││ Network ... (same)                 ││
│└────────────────────────────────────┘│
│ [ Data table ]                       │
│ Applications 17          [All ›]     │
│┌────────────────────────────────────┐│
││ ✕ Failed  shop.brumaria.es         ││
││ ● Running api.kestrelworks.io      ││
││           CPU 2% · Mem 180 MB      ││
││ ● Running app.wrenfield.io ...     ││
│└────────────────────────────────────┘│
│ Activity                             │
│┌────────────────────────────────────┐│
││ Today                              ││
││ ● 14:32 Deploy #35 ok              ││
││ ✕ 09:12 Deploy #34 failed          ││
││ ● 02:00 Backup 15 apps             ││
│└────────────────────────────────────┘│
└──────────────────────────────────────┘
```

Notas de diseño: las cifras son enlaces con nombre accesible compuesto ("Applications: 12 running, 1 failed, 4 stopped")
igual que `UnitTally` en `MachineStrip.tsx`; la franja es un `<section aria-label>` con `<ul>`; la línea temporal es un
`<ol>` con `<time>`; cada estado lleva glifo y palabra; a 390 px el título de la página pierde la descripción; las tablas pasan a
tarjetas con la misma información y los motivos vacíos.

### 3.5 Pruebas y arnés

- **Unidad, Python**: `MetricsStore.query_range` con reloj falso (los escenarios A-D como parametrizados: dominio, rejilla
  con `null`, etapa real, `first_sample_at`), consolidación idempotente con máximo, migración de `user_version`, guardas
  (rango > 35 d, `step` inválido).
- **Colector**: árbol de cgroups falso para nombre nuevo, legacy, plantilla, `ControlGroup` con `Slice=` propio,
  `memory.current` ausente (accounting off), v1; el hilo sobrevive a una excepción y la registra; arrendamiento con dos
  procesos.
- **Plan de muestreo**: una tabla por tipo de app → `reason` esperado (static, php-fpm, compose, parada, sin unidad).
- **Consola (vitest)**: dominio fijo con 1 h de datos en 24 h (el `range` devuelve `[from,to]`), huecos, tarjeta (posición,
  inversión en el borde), teclado (anuncio con `null`), grupo (índice compartido), estados con motivo, cifras del
  Overview y su tono.
- **E2E (Playwright, ambos temas, axe, CSP)**: cambiar de rango con historial de 1 h en un servidor sin `--showcase` y
  comprobar que el eje cubre 24 h (atributos `data-domain-from/to` del plot) y que la línea de verdad cambia; abrir y cerrar el
  diálogo de Datos **sin que ninguna caja del grid cambie** (`boundingBox` antes y después); tarjeta visible al pasar,
  descartable con Esc, anclable; crosshair en dos gráficos; axe con tarjeta anclada; sin violaciones de CSP. Una prueba
  nueva **sin `--showcase`** (hoy solo hay semilla de 30 d: `console_server.py:5596-5659`).
- **Arnés de integración** (`tests/integration/run.py`, contenedor systemd): desplegar una app (en sitio con unidad
  legacy `wasm-*`, con releases y blue/green), esperar N muestras (N=3, 15 s) y aserciones: `query` no vacío,
  `status.reason == null`; app parada → `stopped`; estática → `static`; accounting apagado con un drop-in → `accounting_off`.
- **Chartability** como lista de revisión manual antes de publicar (14 pruebas, 20-40 min).

### 3.6 Orden de trabajo, esfuerzo y riesgos

| Fase | Contenido | Esfuerzo | Depende de |
|---|---|---|---|
| **P0** (arreglos con retorno inmediato) | Dominio fijo + línea de verdad + etiquetas honestas (`resolution` real); tabla fuera de la tarjeta (diálogo con pestañas); `consolidate()` al arrancar; bucle del colector con captura y registro; estado vacío con motivo en `MetricsTab` para static/compose/php-fpm/parada y "colector parado" | S-M | — |
| **P1** | Etapas 5 s/1 m/10 m/1 h con máximo; `/api/metrics/query` y `MetricsStore.query_range`; huecos y banda de historial; `ChartGroup`, tarjeta y teclado; zoom con re-consulta; `SamplingPlan` + `systemctl show ControlGroup` + `GET /api/apps/{d}/metrics` | L | P0 |
| **P2** | `GET /api/overview`, franja de cifras, línea temporal, flota (central/hub), gestor de actualizaciones (compartido con ítems 32 y 46), pronóstico de disco | L | P1, ítems 32/33 |
| **P3** | Demonio `noust-metrics` con arrendamiento y mover el colector fuera de `noust/web`; muestreo de contenedores Compose y de pools PHP-FPM; tráfico de estáticos desde nginx; OOM/PSI/`throttled` | M-L | P1 |

Riesgos: migración SQLite con `max_value` en instalaciones vivas (probar con la copia de producción, no instalar código
sin publicar en arennalabs); `uPlot.setScale`/`setData` con dominio fijo y refrescos cada 30 s (probar que un zoom no se
pierde en el refetch); tarjeta y `role="application"` con lectores de pantalla (Chartability); coste de un demonio
adicional; disparidad entre `disk` del strip y del gráfico si no se unifica.

### 3.7 Decisiones que necesito del dueño

1. ¿Cómo corre la consola en arennalabs: `noust web enable`, `noust web start -d` o a mano? Decide el diagnóstico de
   §1.5.4 y si el demonio de §3.2c es urgente.
2. ¿Demonio `noust-metrics` habilitado por defecto en el paquete (opción B), dentro de `noust-monitor` (C), o solo
   mensaje en la UI (A)?
3. ¿Retención de la etapa horaria fija en 35 d o configurable hasta ~400 d (ENS)? El coste es mínimo (~840 filas por métrica y
   35 d; unas 8760 por año).
4. En un hub, ¿la portada es este panel con alcance flota (lo que propongo) o se mantiene la redirección a `/fleet`?
5. Actualizaciones del sistema: ¿basta con leer el estado que dejan `update-notifier`/`dnf`, o se permite a Noust ejecutar
   `apt-get update` para refrescarlo?
6. ¿Interesa medir tráfico de las apps estáticas (peticiones y errores desde el access log de nginx)? Vercel y Render lo dan;
   sería la única métrica útil para ellas.

---

## 4. Fuentes

Consultadas el 2026-09-29.

**Producto**
- Grafana, visualización de series temporales (tooltip, huecos, mínimos blandos): https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/time-series/
- Grafana, ajustes del dashboard ("Graph tooltip": Shared crosshair / Shared tooltip): https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/modify-dashboard-settings/
- Grafana, buenas prácticas de dashboards (USE, RED, "tell a story"): https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/
- Grafana, visualización Stat (valor + sparkline + variación): https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/stat/
- Netdata, base de datos por etapas (1 s / 1 min / 1 h): https://learn.netdata.cloud/docs/netdata-agent/configuration/database
- Netdata, gráficos (tooltip, "Info", modificadores de teclado): https://learn.netdata.cloud/docs/dashboards-and-charts/netdata-charts
- Beszel, registros agregados (1m…480m): https://github.com/henrygd/beszel/blob/main/internal/records/records.go
- Railway, métricas: https://docs.railway.com/observability/metrics
- Render, métricas de servicios: https://render.com/docs/service-metrics
- Vercel, Observability: https://vercel.com/docs/observability
- Coolify, métricas: https://coolify.io/docs/core/observability/monitoring/metrics y Sentinel: https://coolify.io/docs/core/observability/monitoring/sentinel
- Dokploy, monitorización: https://docs.dokploy.com/en/docs/core/monitoring/overview
- Datadog, modo pantalla completa con minimapa: https://www.datadoghq.com/blog/full-screen-graphs
- Cloudflare, rango de tiempo y selección por arrastre: https://developers.cloudflare.com/analytics/network-analytics/configure/time-range/ y https://developers.cloudflare.com/web-analytics/configuration-options/filters/
- Stripe, Home del Dashboard: https://support.stripe.com/questions/dashboard-home-page-charts-for-business-insights y https://docs.stripe.com/dashboard/basics
- Uptime Kuma (README): https://github.com/louislam/uptime-kuma

**uPlot**
- Demos (tooltip, sincronización, huecos): https://github.com/leeoniya/uPlot/tree/master/demos ; `cursor-tooltip.html`, `sync-cursor.html`, `missing-data.js` en el mismo directorio.
- Tipos instalados: `panel/node_modules/uplot/dist/uPlot.d.ts` (1.6.32) y `uPlot.esm.js:2835-2840`.

**Accesibilidad**
- WCAG 2.2, SC 1.4.13: https://www.w3.org/WAI/WCAG22/Understanding/content-on-hover-or-focus.html
- WCAG 2.2, SC 2.5.7 arrastre: https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html
- WCAG 2.2, SC 4.1.3 mensajes de estado: https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html
- Highcharts, módulo de accesibilidad: https://www.highcharts.com/docs/accessibility/accessibility-module
- Analysis Function (Gobierno del Reino Unido), gráficos: https://analysisfunction.civilservice.gov.uk/policy-store/data-visualisation-charts/
- TanStack Charts, accesibilidad: https://tanstack.com/charts/latest/docs/guides/accessibility
- ApexCharts, accesibilidad: https://apexcharts.com/docs/accessibility/
- Chartability: https://chartability.fizz.studio/

**Sistema**
- Kernel, cgroup v2 (`memory.current`, `memory.events`, `cpu.stat`, PSI): https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- systemd, valores por defecto de accounting: https://manpages.debian.org/trixie/systemd/systemd-system.conf.5.en.html
- Docker, `stats` (memoria sin caché, `inactive_file`): https://docs.docker.com/reference/cli/docker/container/stats/
- RRDtool, `AVERAGE`/`MAX`, heartbeat y `xff`: https://linux.die.net/man/1/rrdcreate
- Google SRE, cuatro señales de oro: https://sre.google/sre-book/monitoring-distributed-systems/

Las páginas de WCAG 2.5.7 y 4.1.3 y el capítulo de SRE no se abrieron en esta sesión (son referencias estándar); el resto se leyó o se obtuvo por búsqueda como se indica en §0.3.

**Código y pruebas de esta investigación**
- `panel/src/components/ui/Chart.tsx`, `panel/src/features/overview/*`, `panel/src/features/app/metrics/*`,
  `src/noust/web/api/metrics.py`, `src/noust/monitor/timeseries.py`, `src/noust/web/metrics_collector.py`,
  `src/noust/web/machine.py`, `src/noust/managers/service_manager.py`, `scripts/console_server.py`.
- Experimentos y capturas en `/tmp/noust-research/` (scratch): `exp_store.py`, `exp_collector.py`, `exp_units.py`,
  `shots.mjs`, `reflow.mjs`, `appmetrics.mjs`, `size.py`.
