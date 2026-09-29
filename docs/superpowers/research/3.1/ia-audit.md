# Auditoría de arquitectura de información, UI y copy de la consola (3.1)

Fecha: 2026-09-29 · Rama: `dev/3.1` (consola 3.0.0) · Alcance: todas las rutas, pestañas, diálogos y
asistentes de `panel/`, en inglés y español, a 1440×900, 1920×1080 y 390×844.

Marco: feedback del dueño 20 (topes de ancho), 40 (IA de cada página), 41 (Resumen como dashboard: aquí
solo la IA; las gráficas las investiga otro agente), 42 (jerga: "Releases / Activar releases"), 43 (salto
de layout al cambiar de pestaña) y 52 (webhook de despliegue incomprensible), más la petición de un
sistema de diseño que se respete siempre. La dirección visual de referencia es D8
(`docs/superpowers/specs/2026-09-25-wasm-v2-design.md`).

## Método

- `npm run e2e:screens` con `NOUST_SCREENS_DIR=/tmp/noust-screens-before/seeded`: todas las rutas de
  `panel/e2e/routes.ts`, temas claro y oscuro, 1440 / 1920 / 390. Los specs `@screens` de pestañas y
  asistente escribieron en `/tmp/console-tabs` y `/tmp/console-wizard`.
- Script propio (`/tmp/noust-audit/shoot.mjs`) contra `scripts/console_server.py`:
  - **Máquina vacía**: `console_server.py` no tiene flag de siembra vacía, así que un envoltorio
    (`/tmp/noust-audit/empty_server.py`) sustituye `seed_machine`, `seed_exportable_app` y
    `seed_settings_api_tokens` por no-ops. Da los estados vacíos reales de cada página.
  - Diálogos (crear copia, programación, destino, BD, cron, servicio, sitio, certificado, dominio,
    añadir servidor, token, eliminar app, activar webhook con "Confirma que eres tú"), paleta y menú
    móvil; páginas en español (tema oscuro); pantalla de central bloqueada (`--seal`).
- Longitud de scroll = altura del documento / altura de viewport (vh). 1,00 = cabe sin scroll.
- Capturas citadas en este documento: `docs/superpowers/research/3.1/screens-before/` (PNG, 1440 y 390).
  El resto sigue en `/tmp/noust-screens-before/`.

Limitación: Chromium headless oculta las barras de scroll (`clientWidth` = 1440 también en páginas de
3,8 vh), así que el salto del ítem 43 no se puede medir aquí; se deduce del CSS (ver §4.2).

---

## 1. Resumen ejecutivo

Los diez problemas que más pesan, por orden:

1. **Las páginas "de configuración" son columnas infinitas.** Ajustes de una app: 3,8 vh a 1440 y 6,6 vh
   en móvil (4,8 / 8,1 con vistas previas); Notificaciones 3,4 / 5,5; Servidor 3,7 / 4,7; Resumen 3,0 /
   4,0. Todo lo que no es la primera sección vive bajo el pliegue y no hay índice que lo revele.
2. **Los estados vacíos son más largos que los llenos.** Copias de seguridad vacía mide 1,81 vh frente a
   1,53 vh con datos: tres estados vacíos de ~360 px apilados (copias, destinos, programaciones), y el
   primero ofrece "Nueva copia" cuando no hay ninguna app que copiar. El Resumen vacío pone la llamada a
   "Despliega tu primera aplicación" a 1100 px (bajo el pliegue) detrás de cuatro gráficas vacías; en
   390 px esa tarjeta **se corta** dentro de la tabla (`DataTable.tsx:237-242`).
3. **Configuración y objeto mezclados en la misma columna.** Copias (lista + destinos + programaciones),
   Bases de datos (motores + BDs + usuarios), Servidor (salud + discos + red + procesos + monitor),
   Servicio (resumen + registros + archivo de unidad + zona de peligro). Falta la capa de pestañas o
   paneles que separe "lo que miro" de "lo que configuro".
4. **Jerga de sistema en la superficie.** Releases, layout, in place ("En el sitio"), unit, journal, argv,
   MemoryMax/CPUQuota/TasksMax, blue/green, upstream, drain, payload URL, forge, central, tunnel,
   sealed, `backup.max_per_app`. El caso del dueño (42) no es aislado: afecta a unas 60 cadenas (§5).
5. **El webhook no dice qué despliega.** Ni "qué rama" ni "si está conectado". Con la rama sin fijar
   (`Branch: Not recorded`), `web/api/hooks.py:476-484` despliega **cualquier push a cualquier rama**, y la
   UI no lo dice. Los pings y los push ignorados (rama distinta, firma incorrecta) se auditan pero no se
   muestran, así que el operador no puede comprobar la conexión (ítem 52).
6. **No hay plantillas de página, sino páginas.** El componente `Card` existe y no lo importa ninguna
   feature (0 usos); hay 69 superficies de tarjeta hechas a mano con 7 paddings distintos, 9 constantes
   `LINK` locales, dos implementaciones de pestañas, dos de marco de gráfica, dos de "stepper", y la
   acción primaria vive en la cabecera, en una sección o bajo las pestañas según la página.
7. **Topes de ancho incoherentes (ítem 20).** El shell limita a 1600 px, pero Ajustes de app, Entorno y
   Ajustes globales limitan su contenido a 1152 px (`max-w-6xl`) mientras la cabecera y las pestañas
   siguen a 1600: a 1920 los botones de cabecera quedan 400 px a la derecha del borde del contenido.
8. **Salto al cambiar de pestaña (ítem 43).** `html` no tiene `scrollbar-gutter: stable`
   (`styles/app.css:121-131`): pasar de Dominios (1,00 vh) a Ajustes (3,83 vh) añade la barra y desplaza
   todo ~15 px en navegadores con barras clásicas (Windows, Linux sin overlay). Las pestañas de Dominios
   además cambian de ancho cuando llegan sus contadores.
9. **Móvil: acciones fuera de pantalla.** Las tablas hacen scroll horizontal sin indicarlo y la columna
   de acciones (⋯) no es fija (`DataTable.tsx:161, 289`): en 390 px no se ve cómo restaurar una copia,
   reiniciar una app o editar una variable. Las pestañas cortan texto ("Inte…", "Envir…") sin degradado.
10. **Valores por defecto destructivos y duplicados peligrosos.** "Eliminar aplicación" trae marcados
    "borrar archivos" y "borrar certificado" (`DangerSection.tsx:53-54`). La página de un servicio que es
    la unidad de una app ofrece "Eliminar servicio" sin decir que pertenece a esa app.

Plantillas recomendadas (§3): **Lista**, **Detalle con pestañas**, **Ajustes con navegación lateral**,
**Panel (dashboard)**, **Asistente**, **Editor de archivo** y **Acceso**, más cuatro patrones
transversales: estado vacío compacto vs. primer uso, panel lateral (Drawer), diálogo por tamaños y la
pista de CLI en un único sitio.

---

## 2. Longitud de cada página

vh = alto del documento / alto del viewport. En negrita, lo que supera 2 vh a 1440 o 3 vh a 390.

| Ruta | 1440 con datos | 390 con datos | 1440 vacía | 390 vacía |
|---|---|---|---|---|
| `/login` | 1,00 | 1,00 | - | - |
| `/` Resumen | **3,02** | **3,95** | 1,96 | 2,87 |
| `/fleet` Flota (sin servidores) | 1,00 | 1,00 | 1,00 | 1,00 |
| `/apps` | 1,25 | 1,49 | 1,00 | 1,00 |
| `/apps/new` origen | 1,00 | 1,17 | 1,00 | 1,01 |
| `/apps/new` paso Revisar | **2,75** | - | - | - |
| App · Resumen | 1,12 | 1,81 | - | - |
| App · Despliegues | 1,00 | 1,02 | - | - |
| App · Despliegue (fallido) | 1,46 | 1,76 | - | - |
| App · Registros | 1,03 | 1,11 | - | - |
| App · Métricas | 1,00 | 1,53 | - | - |
| App · Entorno | 1,01 | 1,26 | - | - |
| App · Dominios | 1,00 | 1,19 | - | - |
| App · Diagnóstico (caída) | **2,07** | **3,15** | - | - |
| App · Ajustes | **3,83** | **6,59** | - | - |
| App · Ajustes (sin cortes) | **4,49** | **7,34** | - | - |
| App · Ajustes (vistas previas) | **4,80** | **8,06** | - | - |
| `/databases` | 1,18 | 1,69 | 1,09 | 1,55 |
| `/databases/$engine/$name` | 1,73 | 2,13 | - | - |
| `/services` | 1,42 | 1,62 | 1,00 | 1,00 |
| `/services/$name` | 1,91 | 2,21 | - | - |
| `/cron` | 1,00 | 1,00 | 1,00 | 1,00 |
| `/domains` certificados | 1,00 | 1,00 | 1,00 | 1,00 |
| `/domains?tab=sites` | 1,32 | 1,47 | 1,00 | 1,00 |
| `/domains/sites/$site` | 1,18 | 1,19 | - | - |
| `/backups` | 1,53 | 1,88 | 1,81 | 2,11 |
| `/activity` | 1,15 | 1,29 | 1,00 | 1,00 |
| `/server` | **3,66** | **4,71** | **3,42** | **4,10** |
| Ajustes · General | 1,72 | **3,05** | 1,72 | **3,05** |
| Ajustes · Seguridad | 1,35 | 2,04 | 1,00 | 1,58 |
| Ajustes · Servidores | 1,00 | 1,00 | 1,00 | 1,14 |
| Ajustes · Notificaciones | **3,37** | **5,54** | **3,37** | **5,54** |
| Ajustes · Integraciones | 1,25 | 1,86 | 1,00 | 1,69 |
| Ajustes · Tokens de API | 1,00 | 1,00 | 1,00 | 1,01 |
| Ajustes · Acerca de | 1,74 | 2,58 | 1,74 | 2,58 |

Lectura: las páginas de **datos** (listas, registros, métricas) están bien; las que se descontrolan son
las de **configuración** y los **paneles** que apilan secciones heterogéneas.

---

## 3. Plantillas de página que la consola debe estandarizar

Cada página pertenece a una plantilla. La plantilla fija: el tope de ancho, dónde va la acción
primaria, dónde va el estado, qué puede haber antes del contenido principal y cómo se comporta en 390 px.

### 3.0 Reglas comunes

- **Contenedor**: el shell ya fija `max-w-[1600px]` (`app/Shell.tsx:161`). Lo que cambia por plantilla es
  la rejilla interior, **nunca** un `max-w` distinto para el contenido que deja la cabecera más ancha.
- **Cabecera** (`PageHeader`): migas → título → una frase → acciones a la derecha (secundarias, luego la
  primaria, luego ⋯ con el resto). Siempre icono ⋯ (`IconButton`), nunca "⋯ Acciones" con texto.
- **Una sola acción primaria por vista**, en la cabecera o en la barra de la pestaña activa. Nunca en una
  sección intermedia ni en tamaño `sm`.
- **Encima del pliegue a 1440**: estado + acción primaria + el inicio del contenido principal. A 390:
  estado + acción primaria.
- **Un h2 no repite el h1 ni la pestaña** ("Backups" bajo "Backups").
- **Pista de CLI** (`CommandHint`): una por vista, en el pie del contenido, alineada a la izquierda. Nunca
  entre secciones ni en la columna de descripción.
- `html { scrollbar-gutter: stable; }` global.

### T1. Lista (índice de recursos)

Para: Aplicaciones, Bases de datos, Servicios, Cron, Dominios, Copias (pestaña de copias), Actividad,
Tokens de API, Servidores de la flota.

```
┌─────────┬────────────────────────────────────────────────────────────────────┐
│ sidebar │ Título                                  [Secundaria] [+ Primaria] ⋯│
│         │ Una frase: qué es esto                                            │
│         │ [Pestaña A 12] [Pestaña B 3]            (solo si hay subconjuntos) │
│         │ [Buscar… /] [Estado ▾] [Tipo ▾]                        17 elementos│
│         │ ┌────────────────────────────────────────────────────────────────┐ │
│         │ │ Estado   Nombre              Tipo     Métrica   Último      ⋯  │ │
│         │ │ ● En…    shop.example.net    Next.js  3 %       hace 2 h    ⋯  │ │
│         │ └────────────────────────────────────────────────────────────────┘ │
│         │ $ noust list                                        (pie, siempre) │
└─────────┴────────────────────────────────────────────────────────────────────┘
390 px:   Título / [+ Primaria] / [Buscar] [Filtros (2)] / filas-tarjeta:
          ┌──────────────────────────────┐
          │ ● En ejecución          ⋯    │  ← ⋯ siempre visible
          │ shop.example.net             │
          │ Next.js · hace 2 h           │
          └──────────────────────────────┘
```

- Nada entre cabecera y tabla salvo filtros y, si hace falta, **un** aviso.
- Lo que configura la lista (programaciones, destinos, motores, usuarios) va a **pestañas** de la misma
  página o a su Ajustes; nunca debajo de la tabla.
- Detalle de fila ligero (historial de ejecuciones, copias de una app) → **Drawer** 480/720 px.
- Columnas que en el 90 % de filas dicen "-" o lo mismo ("Never verified", "Disabled", "-") se quitan.
- Vacío: **un** `EmptyState` de primer uso en lugar de la tabla (sin cabeceras de columna), con una
  acción que sea posible ahora mismo.

### T2. Detalle con pestañas

Para: Aplicación, Base de datos, Servicio, Sitio, Nodo de la flota, Despliegue (subpágina).

```
Aplicaciones ›
shop.example.net  ● En ejecución  Next.js · :3001 · shop.example.net ↗
                                             [Reiniciar] [Actualizar] [⋯]
[Resumen] [Despliegues] [Registros] [Métricas] [Entorno] [Dominios] [Copias] [Ajustes]
───────────────────────────────────────────────────────────────────────────────
│ contenido de la pestaña: T1 (Despliegues), visor (Registros), gráficas,    │
│ o T3 (Ajustes). La pestaña no repite el título en un h2.                   │
```

- Cabecera y pestañas son comunes y estables (misma altura en todas las pestañas: evita saltos).
- Pestañas = URLs. En 390 px: degradado en los bordes de la tira y la activa centrada; cabecera con la
  acción primaria visible además de ⋯ (hoy solo queda ⋯).
- Si el recurso está roto, un **banner de estado** entre cabecera y pestañas (causa + acción), visible en
  todas las pestañas, no solo en Resumen.

### T3. Ajustes con navegación lateral

Para: Ajustes globales y la pestaña Ajustes de una app (y de una BD o servicio si crecen).

```
(cabecera de T2 o título "Ajustes")
┌────────────────────┬──────────────────────────────────────────────────────┐
│ General            │ Despliegues                                          │
│ Despliegues      ◀ │ Cómo llegan las versiones nuevas a producción.       │
│ Despliegue auto.   │ ┌ Vuelta atrás instantánea ─────────── Desactivada ┐ │
│ Recursos           │ │ Una frase + beneficio.         [Activar…]        │ │
│ Exportar           │ └──────────────────────────────────────────────────┘ │
│ ────────────       │ ┌ Comprobación de arranque ────────────────────────┐ │
│ Eliminar           │ │ campos                                            │ │
│                    │ └──────────────────────────────────────────────────┘ │
│                    │ ▓▓ barra fija: "2 cambios sin guardar" [Descartar] [Guardar] │
└────────────────────┴──────────────────────────────────────────────────────┘
nav 200 px + contenido ≤ 880 px (55 rem); el resto del ancho queda vacío a la derecha,
pero la cabecera y la nav ocupan el borde izquierdo, así que no hay desalineación.
390 px: la nav se convierte en una lista (índice) y cada sección es su propia URL.
```

- Cada subsección es una URL (`/apps/$d/settings/deploys`) y **cabe en ≤ 1,5 vh**.
- Un solo patrón de formulario: tarjeta con campos + **barra de guardado fija** cuando hay cambios. Nada de
  cinco botones "Guardar cambios" por página.
- Lo destructivo en su subsección propia al final ("Eliminar"), no en la misma columna que los campos.
- Solo lectura (tipo, directorio, unidad) va en la subsección General como lista clave-valor compacta, no
  compitiendo con formularios.

### T4. Panel (dashboard)

Para: Resumen, Flota, Servidor (pestaña Salud).

```
Resumen                                     [1h 24h 7d 30d]  [+ Nueva aplicación]
┌ Apps 14/17 ─┐┌ Fallos 2 ───┐┌ Despliegues 24h ┐┌ Certificados ┐┌ Copias ────┐
│ en marcha   ││ ✕ clientes… ││ 8 · 1 fallido   ││ 1 caduca 12d ││ 5/17 apps  │
└─────────────┘└─────────────┘└─────────────────┘└──────────────┘└────────────┘
┌ Requiere atención (máx. 5 · ver todo) ─────────┐┌ Actividad reciente ──────────┐
│ ✕ clientes.example.com  El servicio falló  [Ver]││ 12:03 tienda desplegó #13 ✓ │
│ ⚠ example.com  Certificado caduca en 12 d  [Ver]││ …                           │
└─────────────────────────────────────────────────┘└─────────────────────────────┘
┌ CPU ─────┐┌ Memoria ─┐┌ Red ─────┐┌ Disco ───┐   ← 4 en fila a ≥1280, 2×2 debajo
└──────────┘└──────────┘└──────────┘└──────────┘
```

- Encima del pliegue a 1440: fila de estado + atención + actividad. Las gráficas después.
- El panel **resume y enlaza**; no reproduce listas completas (hoy el Resumen repite la tabla entera de
  Aplicaciones, 17 filas).
- Vacío: sustituye todo por una **lista de primeros pasos** (§4.9), no por cuatro gráficas vacías.

### T5. Asistente

Para: Nueva aplicación (página completa), Añadir servidor, Activar despliegue automático, Migrar a
vuelta atrás instantánea, Restaurar copia (diálogo con pasos).

```
Página completa (≥1024):                  Diálogo con pasos (lg, 720 px):
┌ carril ──┬ paso ─────────────┬ resumen ┐  ┌ Título ───────────────────── × ┐
│ ✓ Origen │ Título del paso    │ Origen  │  │ ① Autorizar ─ ② Unir ─ ③ Listo │
│ ● Direcc.│ una frase          │ Dominio │  │ contenido del paso             │
│ ○ Config.│ campos (≤ 1,2 vh)  │ Tipo    │  ├────────────────────────────────┤
│ ○ Variab.│                    │ …       │  │ [Atrás]        [Continuar]     │
│ ○ Desple.│                    │         │  └────────────────────────────────┘
├──────────┴────────────────────┴─────────┤
│ [Atrás]                     [Continuar] │  ← barra fija
└─────────────────────────────────────────┘
```

- Un stepper, una forma: numerado, "hecho / actual / pendiente", horizontal en diálogos y vertical en
  página; sin repetir "Paso 1 de 3" en texto.
- Botón de avance siempre habilitado; si falta algo, al pulsar se señala el campo (hoy "I ran it: next"
  aparece deshabilitado sin explicación).

### T6. Editor de archivo

Para: configuración de un sitio (nginx), archivo de unidad de un servicio.

```
Migas / nombre ● estado [Desactivar] [⋯]
Aviso: "Se prueba con nginx -t antes de guardar"      Sirve: a, b, c · Archivo: /etc/…
┌ editor a altura de viewport (min 60vh) ───────────────────────────────┐
└────────────────────────────────────────────────────────────────────────┘
▓▓ barra fija: Sin cambios · [Descartar] [Probar] [Probar y guardar]
```

Nunca en la misma página que registros y zona de peligro (hoy el servicio apila las tres cosas).

### T7. Acceso

Login y central bloqueada: columna centrada de **un** ancho (400 px; hoy 360 y 416), marca + host
arriba, un campo, un botón, y la alternativa por CLI debajo.

### Patrones transversales

| Patrón | Regla |
|---|---|
| Estado vacío de **primer uso** | Una por página, ocupa el lugar del contenido, `py-12`, icono + título + una frase + una acción posible + comando. |
| Estado vacío **en línea** | Dentro de una sección o tabla: una línea de texto + enlace de acción, sin icono ni borde discontinuo, ≤ 56 px. |
| Panel lateral (Drawer) | Detalle de una fila (ejecuciones de cron, copias de una app, entregas del webhook, formulario de un canal). 480 px formularios, 720 px registros. |
| Diálogo | sm 440 (confirmaciones), md 560 (formularios de ≤ 6 campos), lg 720 (dos columnas o pasos). |
| Zona de peligro | Solo en T3 (subsección "Eliminar") o en el ⋯ de la cabecera con confirmación; opciones destructivas **desmarcadas** por defecto. |
| Pista de CLI | Pie de la vista; en T3, una por subsección al final. |
| Colores de estado | Verde solo "en marcha / sirviendo / correcto"; "Activado", "Habilitado", "Activo" (config) en neutro con icono. |

---

## 4. Página por página

Formato: **Propósito** · **Pliegue** (1440 / 390) · **Scroll** · **Problemas** · **Rediseño**.

### 4.1 Shell (sidebar, barra superior, menú móvil, paleta)

- **Propósito**: orientarse y saltar; estado de la máquina siempre visible.
- **Problemas**:
  - "Flota" es el segundo ítem del sidebar aunque el servidor no sea una central y no tenga servidores
    (`app/nav.ts:54`); en un servidor normal es ruido prominente y duplica Ajustes › Servidores.
  - Grupos del sidebar sin etiqueta: "Dominios" y "Copias" van juntos pero "Servicios" y "Cron" con las
    apps; "Servidor" y "Actividad" al fondo. Mezcla de objetos del operador y de sistema.
  - La barra superior repite CPU/Memoria/Disco que también muestran Resumen y Servidor. En español
    trunca: "Thin…", "Memor…" (`app/MachineStrip.tsx:124`; captura `topbar-es-1440.png`). Con la flota,
    el selector de servidor y el nombre del host se repiten uno junto al otro ("lon-2 ▾ lon-2 up 9h").
  - Menú móvil correcto; no hay navegación inferior (aceptable).
- **Rediseño**:
  - Sidebar en tres grupos con etiqueta discreta: **Trabajo** (Resumen, Aplicaciones, Bases de datos,
    Copias de seguridad, Dominios), **Sistema** (Servicios, Cron, Servidor, Actividad), **Flota** (solo si
    `central.role` o hay servidores; si no, se llega desde Ajustes › Servidores). Ajustes al pie.
  - Barra superior: host + estado de unidades + búsqueda; las tres barras de uso pasan a un único
    indicador compacto que abre un popover (libera ancho en ES y en 1280).
  - Con la flota, el selector de servidor sustituye al nombre del host, no se suma.

### 4.2 Comportamiento global: ancho y salto de layout (ítems 20 y 43)

- **Topes**: `Shell.tsx:161` 1600 px; `SettingsTab.tsx:57`, `EnvironmentTab.tsx:343` y
  `routes/_console/settings.tsx:21` 1152 px (`max-w-6xl`); `NewAppWizard.tsx:396` 46 rem; diálogos 440-1280.
  A 1920 la cabecera y las pestañas de una app llegan a 1850 px y los Ajustes acaban en 1460 px.
- **Salto**: sin `scrollbar-gutter` (`styles/app.css:121-131`), cualquier cambio de pestaña entre una
  vista corta (Dominios, Tokens, Registros ≈ 1,0 vh) y una larga desplaza la página el ancho de la barra.
  Las pestañas de `/domains` muestran sus contadores solo cuando llegan los datos
  (`features/domains/DomainsPage.tsx:32-37`): la tira cambia de ancho. `e2e/layout-shift.spec.ts` solo
  mide cargas en frío y en headless sin barras: no puede ver ninguno de los dos.
- **Rediseño**: `scrollbar-gutter: stable` en `html`; contadores de pestaña con hueco reservado
  (`min-w` + cifras tabulares); topes por plantilla (§3); el test de CLS debe lanzar Chromium sin
  `--hide-scrollbars` y cambiar de pestaña, no solo cargar.

### 4.3 Iniciar sesión (`/login`)

- **Propósito**: entrar con el token (y 2FA).
- **Pliegue**: todo, en ambos tamaños. **Scroll**: 1,00.
- **Problemas**: correcto. Pista "Print it on the server with `noust web token`" bien. Ancho 360 px vs.
  416 px de la pantalla de bloqueo.
- **Rediseño**: T7; mismo ancho que la pantalla de bloqueo.

### 4.4 Central bloqueada

- **Propósito**: desbloquear los secretos sellados o seguir sin ellos.
- **Pliegue**: todo. **Scroll**: 1,00.
- **Problemas**: copy de criptografía ("sealed at rest", "central", "passphrase that is written
  nowhere"). "Continue without unlocking" bien resuelto.
- **Rediseño**: T7. Copy: "Esta consola está bloqueada. Las claves que llegan a tus servidores están
  cifradas con una frase de paso que no se guarda en ningún sitio. Escríbela para volver a conectar."

### 4.5 Resumen (`/`) · T4 (ítem 41, solo IA)

Capturas: `overview-1440.png`, `overview-390.png`, `overview-empty-1440.png`, `overview-empty-390.png`.

- **Propósito**: "¿está todo bien? si no, ¿qué hago primero?".
- **Pliegue 1440**: "Requiere atención" con 6 elementos ocupa todo el pliegue; las gráficas empiezan a
  850 px. **390**: título, botón y los tres primeros avisos.
- **Scroll**: 3,02 / 3,95; vacío 1,96 / 2,87.
- **Problemas**:
  - Repite la tabla completa de Aplicaciones (17 filas) y una tabla de despliegues recientes: el panel
    es una copia de otras dos páginas más cuatro gráficas.
  - "Requiere atención" mezcla fallos de apps con hallazgos del monitor (nc, xmrig): mismo peso visual
    para "el servicio falló" y "un proceso se llama nc". Acciones alineadas a la derecha, lejos del texto.
  - Gráficas: eje X con la misma hora repetida ("21:30 21:30") en la ventana de 1 h, ticks de memoria
    raros (19 GB / 9,3 GB) y disco en "0.98 TB" frente a "931 GB" en el eje.
  - **Vacío**: la invitación a desplegar la primera app está a 1100 px, detrás de "Nada requiere
    atención" y cuatro tarjetas "Collecting samples"; muestra cabeceras de tabla vacías; en 390 la tarjeta
    de primer uso se corta horizontalmente dentro de la tabla (`DataTable.tsx:237-242`).
- **Rediseño** (T4): fila de 5 indicadores (apps en marcha / fallos / despliegues 24 h / certificados /
  cobertura de copias) → "Requiere atención" (máx. 5, separado en "Fallos" y "Avisos del monitor") junto a
  "Actividad reciente" → gráficas en una fila. Quitar la tabla completa de apps (enlace "Ver las 17").
  Vacío: **lista de primeros pasos** (Desplegar la primera aplicación · Programar copias · Activar
  notificaciones · Activar verificación en dos pasos), con cada paso tachado al completarse; las gráficas
  plegadas hasta que haya muestras.

### 4.6 Flota (`/fleet`) · T4

Capturas: `fleet-empty-1440.png`; con nodos, `docs/assets/console/fleet.png`.

- **Propósito**: estado de todos los servidores y qué necesita atención en cada uno.
- **Pliegue 1440**: con nodos, "Requiere atención" (4) llena el pliegue; la tabla de servidores empieza a
  650 px. Vacía: el estado vacío. **390**: estado vacío completo.
- **Scroll**: 1,00 vacía.
- **Problemas**:
  - Vacía en un servidor normal: 4 líneas de jerga ("central", "SSH tunnel", "forward that one port",
    "drives it through its own API") y un comando (`noust node key web-2`) que no es el primer paso.
  - Con nodos, el objeto principal (la tabla de servidores) va segundo.
  - "Gestionar servidores" lleva a Ajustes › Servidores: dos sitios para lo mismo.
- **Rediseño**: sidebar solo con flota (§4.1). T4 con la tabla de servidores primero (una fila por
  servidor con su estado agregado) y "Requiere atención" como columna o panel lateral a ≥1280. Alta y
  baja de servidores aquí (acción primaria "Añadir servidor"); Ajustes › Servidores queda para la política
  (claves, sellado). Vacío: "Gestiona varios servidores desde esta consola. Cada servidor autoriza esta
  consola una vez; esta consola nunca obtiene una shell allí." + [Añadir servidor].

### 4.7 Aplicaciones (`/apps`) · T1

Capturas: `apps-390.png`.

- **Propósito**: encontrar una app y ver su estado; crear una.
- **Pliegue**: 1440, cabecera + filtros + 14 filas; 390, cabecera + filtros + 10 filas.
- **Scroll**: 1,25 / 1,49; vacía 1,00.
- **Problemas**:
  - En 390 la columna ⋯ queda fuera de pantalla (scroll horizontal sin indicio). El nombre largo de la
    vista previa desborda la celda.
  - "Type" en minúsculas y mono (`nextjs`, `nodejs`) cuando es una etiqueta de producto (Next.js).
  - "Static" se pinta en verde como "Running": no es un estado de ejecución.
  - Puertos aleatorios como columna de primer nivel; faltan CPU/memoria (existen en el componente,
    `withReadings`, pero no se activan aquí).
  - Vacía: bien (una tarjeta, una acción). Copy con jerga: "gives it a systemd unit, a site and a
    certificate".
- **Rediseño**: T1 con filas-tarjeta en móvil; columnas Estado · Aplicación (+ dominio) · Tipo (nombre
  legible) · CPU · Memoria · Último despliegue · ⋯; puerto al detalle. Estado "Estático" neutro con icono.

### 4.8 Nueva aplicación (`/apps/new`) · T5

Capturas: `new-app-review-1440.png`.

- **Propósito**: de un repositorio a una app servida con HTTPS.
- **Pliegue**: paso Origen completo en 1440 y casi completo en 390. Paso Revisar: el campo **Dominio** está
  a 790 px (tras el resultado de la inspección y "Deploy as") y "Continuar" a 2400 px.
- **Scroll**: Origen 1,00 / 1,17; Revisar 2,75 (1440).
- **Problemas**:
  - "Revisar" es en realidad cinco pasos en uno: detección, dirección, ejecución, modo de despliegue,
    carpetas persistentes, límites y 8 variables de entorno.
  - Jerga: "Deploy as", "Releases / In place", "as WASM 1.x did" (nombre antiguo en la UI,
    `i18n/en/newApp.ts:206`), "Persistent paths … linked from `shared/`", "Web server: nginx / Apache".
  - La rama es opcional y "vacía = rama por defecto" (`i18n/en/newApp.ts`), pero no se advierte de que sin
    rama fijada el webhook desplegará cualquier push (§4.16).
  - La ruta del directorio temporal (`/tmp/noust-console-…/src/storefront`) se muestra como si fuera el
    origen.
- **Rediseño** (T5, página completa): Origen → **Dirección** (dominio, HTTPS) → **Configuración** (tipo,
  puerto, comandos; "Avanzado" plegado: servidor web, modo de despliegue, carpetas que se conservan,
  límites) → **Variables** (solo las requeridas arriba; las que tienen valor por defecto plegadas) →
  **Desplegar** (resumen, ya está bien). Barra de pie fija y resumen lateral a ≥1280.

### 4.9 App · Resumen · T2

Capturas: `app-overview-1440.png`.

- **Propósito**: estado de esta app y la siguiente acción.
- **Pliegue**: 1440, cabecera, 4 indicadores, Dominios, Webhook y casi todo "Runtime"; 390, cabecera
  (solo ⋯, sin Reiniciar/Actualizar), pestañas cortadas y los 4 indicadores.
- **Scroll**: 1,12 / 1,81.
- **Problemas**:
  - "Webhook" dice "No push has deployed this app yet" tanto si está desactivado como si está activo y
    esperando: no dice el estado.
  - "Runtime" mezcla datos de soporte (PID principal, directorio) con decisiones ("Starts at boot: No" en
    una app en marcha debería ser un aviso, no un dato).
  - "Resources" (lecturas de CPU/memoria) queda al fondo de la columna derecha, debajo de una pista de CLI
    que está en mitad de la página (`features/app/AppOverview.tsx:405`).
  - "Recent deploys" es una fila de puntos sin leyenda. "Layout: In place".
  - Solo la pestaña Resumen muestra el banner de "último despliegue fallido".
- **Rediseño**: indicadores → fila "Recursos" (CPU/memoria vs. límite, mini gráficas) → dos columnas:
  Dominios + **Despliegue automático** (estado: activo en `main` / desactivado / sin conexión) a la
  izquierda, "Cómo se ejecuta" compacto a la derecha. Avisos accionables ("No arranca con el sistema ·
  Activar"). Pista de CLI al pie. Banner de fallo en todas las pestañas (T2).

### 4.10 App · Despliegues y detalle de despliegue · T2 + T1

Capturas: `app-deployments-1440.png`.

- **Propósito**: historial; volver atrás; abrir el log de uno.
- **Pliegue**: 1440, historial (10 filas) + panel "Releases" a la derecha: el mejor patrón de la consola.
  390, la tabla y, debajo, el panel.
- **Scroll**: 1,00 / 1,02. Detalle fallido 1,46 / 1,76.
- **Problemas**:
  - Identificadores de release (`20260929-182059-2a8b7c4`) como título de cada versión; "Serving",
    "Previous", "Roll back to this".
  - En apps en el sitio el panel se llama "Rollback points" y muestra copias de seguridad: dos conceptos
    distintos con el mismo lugar y distinta copia.
  - Detalle de un despliegue fallido: "Roll back…" visible con un párrafo que explica que no se puede
    (debería no mostrarse); el log completo empieza a 885 px (el extracto de error arriba lo compensa).
  - Paginación "Load older deploys" en esta tabla; ninguna otra lista pagina igual (Actividad "Load more").
- **Rediseño**: mantener. Copy: panel "Versiones" con "En producción desde hace 2 h", "Anterior",
  "Volver a esta versión"; título de cada versión = fecha + commit (el id completo en mono secundario).
  Ocultar "Volver atrás" cuando no aplica. Un único componente de paginación ("Cargar más").

### 4.11 App · Registros · T2

- **Propósito**: ver qué está pasando ahora.
- **Pliegue**: el visor ocupa el viewport en ambos tamaños. **Scroll**: 1,03 / 1,11 (24 px de scroll de
  página además del scroll del visor: doble scroll).
- **Problemas**: solo el journal de la unidad; los logs de nginx (acceso/errores) no están aquí aunque
  Diagnóstico los lee. Sin filtro de nivel ni de tiempo. La pista de CLI está arriba a la derecha (única
  página donde va ahí).
- **Rediseño**: selector de fuente (Aplicación · Servidor web: accesos · Servidor web: errores), filtro
  por nivel (error/aviso), visor a `100dvh − cabecera` exacto para evitar el doble scroll; pista al pie.

### 4.12 App · Métricas · T2

- **Propósito**: ¿consume demasiado? ¿desde cuándo?
- **Pliegue**: 1440, las dos gráficas y "Deploys in this range"; 390, CPU y parte de memoria.
- **Scroll**: 1,00 / 1,53.
- **Problemas**: bien. Dos implementaciones de marco y rangos: `features/overview/MachineCharts.tsx:124`
  (`ChartFrame`, `WINDOWS`) y `features/app/metrics/MetricsTab.tsx:77` (`Frame`, `RANGES`), con valores
  por defecto distintos (1 h en Resumen, 24 h aquí). Sin métricas HTTP (peticiones, errores, latencia).
- **Rediseño**: un componente de gráfica y un conjunto de rangos (lo decide el agente de gráficas).

### 4.13 App · Entorno · T2

Capturas: `app-environment-1440.png`.

- **Propósito**: ver y cambiar variables; aplicar reiniciando.
- **Pliegue**: 1440, tabla completa (9 variables); 390, 6 filas con la columna Visibilidad cortada y sin
  acciones visibles.
- **Scroll**: 1,01 / 1,26.
- **Problemas**:
  - Todas las filas muestran `••••••••`, también las marcadas "Shown: nothing about it looks like a
    secret": el operador lee "Mostrado" y ve un valor oculto.
  - Columna "Visibility" con frases truncadas ("Hidden: its name sugg…"); párrafo explicativo de 2 líneas
    antes de la tabla.
  - La ruta del `.env` (`/tmp/…/.env`) como segunda línea de la descripción.
  - En 390 los iconos de revelar/editar/borrar quedan fuera.
- **Rediseño**: columnas Nombre · Valor (visible si es "normal", oculto con 👁 si es secreto) · Tipo
  ("Secreto" / "Normal" con icono y tooltip "por el nombre", "marcado por ti") · ⋯. El párrafo pasa a un
  tooltip del encabezado "Tipo". Ruta del `.env` en el ⋯ de la pestaña. Filas-tarjeta en móvil.

### 4.14 App · Dominios · T2

- **Propósito**: añadir alias o redirecciones y ver el certificado.
- **Pliegue**: todo en 1440; en 390, tabla y certificado.
- **Scroll**: 1,00 / 1,19.
- **Problemas**: h2 "Domains" repite la pestaña (`features/domains/AppDomainsTab.tsx:210`). "Certificate:
  Covered" (jargon, verde). Párrafo sobre el dominio primario siempre visible.
- **Rediseño**: sin h2; acción "Añadir dominio" en la barra de la pestaña; "Con HTTPS" / "Sin HTTPS"; el
  párrafo del primario como tooltip del badge "Principal".

### 4.15 App · Diagnóstico · T2

Capturas: `app-diagnose-1440.png`.

- **Propósito**: ¿por qué está caída y cómo la arreglo?
- **Pliegue 1440**: veredicto + primera comprobación. **390**: veredicto.
- **Scroll**: 2,07 / 3,15.
- **Problemas**:
  - El titular del veredicto es texto de systemd a tamaño display: "The unit failed to start
    (Result=exit-code, exit status 1); see the last journal lines below." Y la comprobación "Journal"
    que contiene esas líneas está **plegada y marcada "Passed"**.
  - Sin acciones de arreglo en el veredicto (ver registros, volver a la versión anterior, reiniciar).
  - Todas las comprobaciones fallidas se expanden: `ss -ltnp` completo (10 líneas) para decir "nada
    escucha en 3005".
- **Rediseño**: veredicto en lenguaje de producto ("La app se cierra al arrancar") + salida verbatim en
  mono debajo + acciones sugeridas [Ver registros] [Volver a la versión anterior] [Reiniciar]. Las líneas
  del journal que cita, abiertas. Solo la primera comprobación fallida expandida; las superadas en una
  línea resumen "7 comprobaciones correctas". Considerar mover Diagnóstico a una acción ("Diagnosticar")
  de la cabecera y al banner de fallo, y liberar la pestaña para **Copias** (§4.21).

### 4.16 App · Ajustes · T3 (ítems 42 y 52)

Capturas: `app-settings-1440.png`, `app-settings-390.png`, `app-settings-inplace-es-1440.png`.

- **Propósito**: cambiar cómo se construye, despliega y protege esta app; exportarla o eliminarla.
- **Pliegue 1440**: "Source and runtime" (solo lectura) y la tarjeta "Enable releases". **390**: la mitad
  de la lista clave-valor de solo lectura.
- **Scroll**: 3,83 / 6,59 (sin cortes 4,49 / 7,34; vistas previas 4,80 / 8,06; en el sitio ES 3,93).
- **Problemas**:
  - Siete temas en una columna: origen (lectura), releases, sin cortes, comprobación de salud, límites,
    webhook, vistas previas, exportar, zona de peligro; siete pistas de CLI intercaladas.
  - **Ítem 42**: "Releases" / "Enable releases" / "Plan the migration" / "Migrate to releases" / "Layout:
    In place" ("Disposición: En el sitio"). El beneficio (volver atrás en segundos) está en la tercera
    frase de un párrafo. "Sin cortes" aparece en una app en el sitio solo para decir que no se puede, y
    con un texto del backend en inglés dentro de la UI española
    (`src/noust/deployers/bluegreen.py:308`).
  - **Ítem 52**: "Deploy webhook — A push to the repository deploys the app. The forge signs each delivery
    with a secret Noust checks." No dice qué rama, no dice si está conectado, "Recent deliveries" solo
    lista push que desplegaron (los pings, los push a otra rama y las firmas incorrectas se registran en
    auditoría, `web/api/hooks.py:448-484`, pero no se ven). Las instrucciones de GitHub/GitLab son texto
    corrido y solo aparecen una vez, al crear el secreto. Si la app ya recibe push por la GitHub App de
    Integraciones, la sección no lo dice.
  - Límites: "MemoryMax. At least 64 MB.", "CPUQuota.", "TasksMax: processes and threads".
  - Zona de peligro con "Also delete its files" y "Also delete its certificate" **marcados por defecto**
    (`features/app/settings/DangerSection.tsx:53-54`).
  - A 1920 el contenido acaba a 1460 px mientras la cabecera llega a 1850 (`SettingsTab.tsx:57`).
- **Rediseño** (T3), subsecciones:
  1. **General**: origen, rama (editable cuando exista), comandos, puerto, tipo; datos de sistema (unidad,
     usuario, directorio) en lista compacta.
  2. **Despliegues**: "Vuelta atrás instantánea" (estado + [Activar…] que abre el asistente de migración:
     Plan → Confirmar → Resultado); "Versiones que se guardan"; "Comprobación de arranque"; "Despliegues
     sin cortes" solo si la app es elegible (si no, una línea: "Requiere la vuelta atrás instantánea").
  3. **Despliegue automático** (webhook guiado, ver abajo) y **Vistas previas de pull requests**.
  4. **Recursos**: memoria, CPU, procesos; nombres de systemd en tooltip.
  5. **Exportar**.
  6. **Eliminar**: opciones desmarcadas; el diálogo enumera lo que se borra.
- **Webhook guiado** (asistente en diálogo lg + tarjeta de estado):
  - Estado siempre visible: *Desactivado* · *Esperando la primera entrega* · *Conectado: los push a
    `main` despliegan esta app · último push hace 2 h (#13)* · *Problema: 3 entregas con firma incorrecta
    desde ayer*.
  - Paso 1 **Qué despliega**: rama (por defecto la rama registrada; si no hay, aviso "Sin rama fijada,
    cualquier push a cualquier rama desplegará esta app" y propuesta de fijar `main`).
  - Paso 2 **Añádelo en GitHub/GitLab/Gitea**: pestañas por plataforma, enlace directo a
    `https://github.com/<owner>/<repo>/settings/hooks/new` cuando el origen es GitHub, URL y secreto con
    copiar, "Tipo de contenido: application/json", "Evento: solo push".
  - Paso 3 **Comprueba la conexión**: "Esperando el ping de GitHub…" → "Conectado: GitHub envió un ping
    hace 5 s" (leyendo esos registros de auditoría).
  - Lista de entregas con **todas** las entregas y su resultado: desplegó · ignorada (otra rama) · firma
    incorrecta · ping.
  - Si la GitHub App ya cubre el repositorio: "Esta app ya se despliega con cada push a `main` mediante la
    GitHub App de este servidor; no necesitas un webhook."

### 4.17 Bases de datos (`/databases`) · T1 con pestañas

Capturas: `databases-1440.png`.

- **Propósito**: encontrar una BD, crear una, conectar una app.
- **Pliegue 1440**: motores (4 tarjetas, 160 px) + tabla de BDs; usuarios a 780 px. **390**: las 4
  tarjetas de motor en 2×2 llenan el pliegue; la lista empieza a 720 px.
- **Scroll**: 1,18 / 1,69; vacía 1,09 / 1,55.
- **Problemas**: la cabecera no tiene acción primaria (`DatabasesPage.tsx:35`); "New database" es `sm` y
  vive en la sección, cuyo h2 repite el h1 (`DatabasesPage.tsx:40-52`). Motores (infraestructura) antes que
  las BDs (lo que se busca). "Tables: 0" en todas. No se ve qué app usa cada BD.
- **Rediseño**: T1. Cabecera [+ Nueva base de datos]; pestañas **Bases de datos** · **Usuarios** ·
  **Motores**; en la pestaña principal, una tira de una línea con el estado de los motores ("PostgreSQL 16 ●
  · MySQL 8 ● · Redis ○ parado · MongoDB no instalado") que enlaza a Motores. Columna "Usada por" (app).

### 4.18 Base de datos (`/databases/$engine/$name`) · T2

Capturas: `database-1440.png`.

- **Propósito**: conectar, consultar, copiar.
- **Pliegue 1440**: lista clave-valor de 5 filas a todo el ancho + inicio de la consola SQL. **390**: la
  lista clave-valor.
- **Scroll**: 1,73 / 2,13.
- **Problemas**: 5 datos en 210 px de alto a 1136 px de ancho; estado vacío de copias de 280 px; el
  constructor de cadena de conexión al fondo; "⋯ Actions" con texto (`DatabasePage.tsx:77`), distinto del ⋯
  de la app.
- **Rediseño**: T2 con cabecera (motor, tamaño, dueño como metadatos en línea) y pestañas **Conexión**
  (cadena, usuarios con acceso) · **Consola SQL** · **Copias** (estado vacío en línea). ⋯ como icono.

### 4.19 Servicios (`/services`) · T1

Capturas: `services-1440.png`.

- **Propósito**: workers y demonios propios; ver qué falla.
- **Pliegue 1440**: 14 de 20 filas. **390**: 11 filas.
- **Scroll**: 1,42 / 1,62; vacía 1,00.
- **Problemas**: mezcla unidades de apps (que ya se gestionan en Aplicaciones), temporizadores de cron y
  copias, e instancias azul/verde; columnas "Since" y "Memory" con "-" en todas las filas, "Boot: Disabled"
  en todas; prefijos heredados `wasm-` visibles; copy "systemd unit".
- **Rediseño**: T1 con filtro por tipo (Workers · Unidades de apps · Temporizadores · Sistema), por defecto
  "Workers"; las unidades de apps enlazan a la app. Quitar columnas vacías.

### 4.20 Servicio (`/services/$name`) · T2 + T6

Capturas: `service-1440.png`.

- **Propósito**: estado de un worker, sus registros, su unidad.
- **Pliegue 1440**: resumen (6 filas) + inicio de registros. **390**: resumen.
- **Scroll**: 1,91 / 2,21.
- **Problemas**: resumen, registros, editor de unidad y zona de peligro en la misma columna. Para la
  unidad de una app, "Delete service" sin mencionar la app (duplica, en la UI, la regla 3).
- **Rediseño**: pestañas **Resumen** · **Registros** · **Archivo de unidad** (T6). Si la unidad pertenece a
  una app: banner "Este servicio es shop.example.net · Abrir la aplicación" y sin eliminar aquí.

### 4.21 Cron (`/cron`) · T1

- **Propósito**: tareas programadas: cuándo, si fallan.
- **Pliegue**: todo. **Scroll**: 1,00 / 1,00; vacía 1,00.
- **Problemas**: el mejor T1 de la consola. "Enabled" en verde (no es ejecución). Diálogo: "Run as an argv,
  without a shell"; la previsualización mezcla `*-*-* 02:00:00` con "3:00:00 AM GMT+1" en formato en-US y
  mono (zona del servidor vs. del navegador sin decirlo).
- **Rediseño**: mantener. Estado neutro; en el diálogo, "Hora del servidor (UTC): 02:00 · en tu hora:
  03:00" y fechas con `lib/format`.

### 4.22 Dominios y certificados (`/domains`) · T1 con pestañas

Capturas: `domains-1440.png`.

- **Propósito**: ¿qué nombres sirvo, con HTTPS, y qué caduca?
- **Pliegue**: completo (6 certificados); sitios 17 filas a 1,32 vh.
- **Scroll**: 1,00 / 1,00; sitios 1,32 / 1,47.
- **Problemas**: el título promete "Dominios" pero no hay lista de dominios (solo certificados y sitios de
  nginx). Sin acciones en la cabecera (`DomainsPage.tsx:29`): cada pestaña pone las suyas bajo la tira, con
  otro componente de pestañas (`Tabs` + `?tab=`) distinto del de app y ajustes (`LinkTabs`). "Sites",
  "Test and reload nginx", "HTTP only" son vocabulario de nginx. Contadores que aparecen tarde (§4.2).
- **Rediseño**: pestañas **Dominios** (todos los nombres de todas las apps: app, rol, HTTPS, DNS) ·
  **Certificados** · **Sitios del servidor web** (avanzado). La acción primaria de la pestaña activa sube a
  la cabecera. Un solo componente de pestañas enrutadas.

### 4.23 Sitio (`/domains/sites/$site`) · T6

- **Propósito**: editar a mano la configuración de nginx de un sitio.
- **Pliegue**: cabecera + editor. **Scroll**: 1,18 / 1,19.
- **Problemas**: no enlaza a la app a la que pertenece; barra de guardado no fija.
- **Rediseño**: T6 con enlace a la app y barra fija.

### 4.24 Copias de seguridad (`/backups`) · T1 con pestañas (el ejemplo del dueño)

Capturas: `backups-1440.png`, `backups-390.png`, `backups-empty-1440.png`.

- **Propósito**: "¿están protegidas mis apps?" y "restaura esta". Crear una copia a mano es secundario.
- **Pliegue 1440**: barra de almacenamiento + filtros + 10 filas; destinos a 950 px y programaciones a
  1170 px. **390**: barra, filtros y 6 filas; la columna ⋯ (restaurar) fuera de pantalla.
- **Scroll**: 1,53 / 1,88; **vacía 1,81 / 2,11** (más larga que llena).
- **Problemas**:
  - Lista plana de copias: con 17 apps × 10 copias serían 170 filas sin agrupar; la pregunta "qué app no
    tiene copia reciente" no tiene respuesta directa.
  - Tres estados vacíos apilados de ~360 px cada uno (`py-16`), cada uno con icono, título, frase, botón
    y comando. "Nueva copia" como primera acción cuando no hay apps.
  - h2 "Backups" repite el h1 (`BackupsPage.tsx:79`). Columna "Verified" con "Never verified" en todas
    las filas; "Includes: env" como chip mono.
  - Barra de disco con unidades mezcladas en una frase ("277 GB used, 729 GB free of 0.98 TB") y la ruta
    del directorio en la línea de resumen.
  - Diálogo de programación: "systemd timer", "Local retention", `backup.max_per_app`, "pruned".
- **Rediseño**:
  - Cabecera [Programar copias] [+ Copia ahora]; metadatos en una línea ("10 copias · 2,5 KB · disco al 28
    %").
  - Pestañas **Aplicaciones** (por defecto) · **Todas las copias** · **Programaciones** · **Destinos**.
  - **Aplicaciones** = tabla de cobertura: App · Última copia (hace 9 h ✓ / "Nunca" en ámbar) ·
    Programación ("Diaria 02:00" / "Sin programar") · Fuera del servidor (vault-r2) · Copias · ⋯ (Copiar
    ahora, Restaurar la última, Ver copias → Drawer con la lista de esa app).
  - Vacío sin apps: **un** estado "Las copias protegen tus aplicaciones. Despliega una primero." [Nueva
    aplicación]. Vacío con apps y sin copias: la tabla de cobertura con todas en "Nunca" y un banner
    "Ninguna aplicación tiene copias. [Programar una copia diaria para todas]".
  - La misma información por app en una pestaña **Copias** de la app (§4.15).

### 4.25 Actividad (`/activity`) · T1

Capturas: `activity-1440.png`.

- **Propósito**: ¿qué pasó y quién lo hizo?
- **Pliegue**: 14 filas. **Scroll**: 1,15 / 1,29.
- **Problemas**: dominada por intentos de inicio de sesión; "Failed: second factor required but not
  presented" es el primer paso normal de la 2FA y sale en rojo; actor "Session 2e5fdfa4"; recurso
  `/api/auth/login` (ruta interna).
- **Rediseño**: filtro por defecto "Operaciones" (despliegues, cambios, trabajos) con "Accesos" aparte;
  agrupar el par "pidió 2FA / entró" en una fila; actor humano ("Tú, desde 127.0.0.1", nombre del token,
  "vía central X"); recurso = objeto (app, BD), no la ruta de la API.

### 4.26 Servidor (`/server`) · T4 con pestañas

Capturas: `server-1440.png`.

- **Propósito**: salud de la máquina y qué la está consumiendo.
- **Pliegue 1440**: salud (motivos + comprobaciones) y los indicadores de sistema. **390**: la salud.
- **Scroll**: 3,66 / 4,71; vacía 3,42 / 4,10.
- **Problemas**:
  - Cinco secciones heterogéneas; el **monitor de recursos**, con hallazgos de seguridad (un minero
    `xmrig`), está al final, a 3000 px.
  - 12 puntos de montaje, 9 de ellos bind-mounts de Docker Desktop idénticos; 25 procesos.
  - "Update available v3.1.0" escondido en una tarjeta de 90 px; Acerca de lo repite.
  - Severidad inconsistente: la app caída es "Warning" (ámbar) aquí y fallo (rojo) en Resumen.
  - Unidades formateadas en el backend ("729.4GB free / 1006.9GB total") junto a las de `lib/format`.
- **Rediseño**: pestañas **Salud** (T4: motivos con acciones, hallazgos del monitor arriba si los hay,
  actualización disponible como banner) · **Recursos** (CPU/memoria/disco/red; montajes reales, "Mostrar
  todos" para el resto) · **Procesos** · **Monitor** (configuración).

### 4.27 Ajustes (contenedor) · T3

Capturas: `settings-general-1440.png`, `settings-notifications-1440.png`, `settings-about-1440.png`.

- **Problemas comunes**: 7 pestañas horizontales (en 390 cortan "Inte…" sin degradado); tres patrones de
  página distintos: descripción a la izquierda + tarjeta a la derecha (General, Seguridad,
  Notificaciones, Integraciones), apilado a todo el ancho (Tokens, Servidores, Acerca de) e Integraciones
  rompiendo su propia rejilla ("Installations" y la zona de peligro). Contenido a 1152 px bajo una
  cabecera de 1600.
- **Rediseño**: T3 con navegación lateral agrupada: **Servidor** (General, Notificaciones,
  Integraciones) · **Acceso** (Seguridad, Tokens de API) · **Flota** (Servidores) · **Acerca de**.
  Preferencias del navegador (idioma, tema) solo en el menú de usuario, fuera de la configuración del
  servidor.

#### 4.27.1 General

- **Pliegue 1440**: directorio de apps y servidor web. **Scroll**: 1,72 / 3,05.
- **Problemas**: cinco tarjetas con su propio "Save changes"; cada una con su pista de CLI en la columna
  izquierda; "Language" (preferencia del navegador) mezclado con configuración del servidor; la ruta del
  archivo de configuración arriba como primera información.
- **Rediseño**: un formulario, una barra de guardado; idioma fuera; ruta del config en Acerca de.

#### 4.27.2 Seguridad

- **Pliegue**: 2FA y 11 sesiones. **Scroll**: 1,35 / 2,04.
- **Problemas**: "488 of 8 backup codes left" (el denominador no es el total inscrito); tabla de sesiones
  sin límite; la política de bloqueo es de solo lectura pero parece un formulario.
- **Rediseño**: 2FA · Sesiones (5 más recientes + "Ver todas") · Política de acceso plegada.

#### 4.27.3 Servidores (flota) + asistente "Añadir servidor"

Capturas: `dialog-add-server-1440.png`.

- **Pliegue**: completo. **Scroll**: 1,00.
- **Problemas**: jerga ("central", "SSH tunnel … forward that one port"); asistente con "Step 1 of 3" y un
  stepper horizontal distinto del de Nueva aplicación (`features/settings/servers/AddServerDialog.tsx:32`);
  "I ran it: next" deshabilitado hasta pulsar "Show the command".
- **Rediseño**: T5 en diálogo; paso 1 muestra el comando directamente (el nombre se rellena con un valor
  sugerido); botón "Ya lo ejecuté, continuar" siempre activo.

#### 4.27.4 Notificaciones

- **Pliegue 1440**: interruptor general + formulario Webhook + inicio de Slack. **Scroll**: **3,37 /
  5,54** (igual vacía).
- **Problemas**: cinco canales con su formulario completo abiertos aunque no estén configurados (el de
  correo solo, 400 px); el interruptor general está apagado ("Nothing is sent") pero la página no lo
  convierte en el primer paso; el evento "Certificate expiring" existe con la nota "Not sent by this
  version of Noust yet" (una casilla que no hace nada); "Private destinations" y "Link in notifications"
  son avanzados al mismo nivel.
- **Rediseño**: estado general arriba ("Las notificaciones están desactivadas · [Activar]"); **lista de
  canales** (fila por canal: estado, destino, [Configurar]/[Probar]) con el formulario en un Drawer;
  **eventos** como lista compacta; "Avanzado" plegado. Quitar eventos no implementados.

#### 4.27.5 Integraciones (+ vuelta de GitHub)

- **Pliegue**: GitHub App + instalaciones. **Scroll**: 1,25 / 1,86.
- **Problemas**: "Installations" sin columna izquierda (rompe la rejilla); zona de peligro a todo el ancho.
  Las páginas de retorno de GitHub (`/integrations/github/callback`) son correctas (1,00).
- **Rediseño**: T3; tarjeta GitHub con estado, instalaciones, eventos; "Quitar la GitHub App" en ⋯ con
  confirmación.

#### 4.27.6 Tokens de API

- **Pliegue**: completo. **Scroll**: 1,00.
- **Problemas**: h2 "Tokens for automation" debajo de la pestaña "API tokens"; "Active" en verde.
- **Rediseño**: T1 dentro de T3; acción primaria arriba; estado neutro.

#### 4.27.7 Acerca de

- **Pliegue**: versión e instalación. **Scroll**: 1,74 / 2,58.
- **Problemas**: 9 comandos en una tabla "From a terminal" (manual dentro de la UI); "pip install --upgrade
  noust" como forma de actualizar aunque la instalación sea un paquete deb/rpm.
- **Rediseño**: versión + actualización (comando según el método de instalación) + instalación + enlaces;
  comandos útiles plegados o en la documentación.

### 4.28 Diálogos

Capturas: `dialog-backup-schedule-1440.png`, `dialog-cron-1440.png`.

| Diálogo | Tamaño | Problemas | Recomendación |
|---|---|---|---|
| Crear copia | lg, 2 columnas | "Build artefacts", "node_modules", "Docker volumes" al mismo nivel que ".env" y "Databases" | Incluir: Archivos (siempre) · .env · Bases de datos; resto en "Avanzado" |
| Nueva programación | lg | "systemd timer", "Local retention", `backup.max_per_app`, "pruned"; sin opción "todas las apps" | "Cuántas guardar aquí", "Borrar pasados N días"; opción "Todas las aplicaciones" |
| Añadir destino | lg | backends de rclone por nombre técnico | agrupar por "Servidor propio (SFTP)", "Almacenamiento S3", "Nube" |
| Nueva BD | md | correcto | - |
| Nueva tarea cron | md | argv; hora en-US mono; zona sin nombrar | ver §4.21 |
| Nuevo servicio | lg | "Restart policy: always" crudo | "Reiniciar si se cae: siempre / si falla / nunca" |
| Crear sitio / Emitir certificado | md | vocabulario nginx/certbot | título en lenguaje de producto; plantilla "Proxy inverso" explicada |
| Añadir dominio (app) | md | correcto, con comprobación DNS | - |
| Confirma que eres tú | sm | correcto | - |
| Eliminar app | md | opciones destructivas marcadas | desmarcadas; resumen de lo que se borra |
| Volver atrás | md | "Roll back…" existe aunque no aplique | ocultar si no aplica |
| Crear token | md | correcto | - |
| Paleta de comandos | 640 | correcta | añadir acciones de la página actual |

---

## 5. Inconsistencias recurrentes (con ubicación)

1. **`Card` sin usar**: `components/ui/Card.tsx` tiene 0 importaciones en `features/`; hay 69 superficies
   `rounded-card border border-border bg-surface` hechas a mano con paddings `px-4`, `p-4`, `p-5`, `py-3`,
   `py-3.5`, `p-3`, `px-5`… y una constante propia `PANEL` (`features/app/settings/panel.ts:2`).
2. **Enlaces de texto sin componente**: constantes `LINK` locales en `features/new-app/JobOutcome.tsx:20`,
   `features/app/diagnose/DiagnoseTab.tsx:29`, `features/app/settings/panel.ts:5`,
   `features/fleet/FleetAttention.tsx:25`, `features/app/deployments/DeploymentPage.tsx:44`,
   `features/overview/RecentDeployments.tsx:23`, `features/app/AppOverview.tsx:43`,
   `components/ui/ExternalLink.tsx:30`, `features/overview/NeedsAttention.tsx:63`,
   `features/app/deployments/ReleasesSection.tsx:23`, más 21 cadenas en línea. Hace falta `TextLink`.
3. **Acción primaria en tres sitios**: cabecera (Apps, Copias, Cron, Servicios, Resumen, Flota), sección
   con `size="sm"` (`features/databases/DatabasesPage.tsx:35-52`), barra bajo pestañas
   (`features/domains/DomainsPage.tsx:29`), sección de Ajustes (Tokens).
4. **Menú ⋯ de cabecera en dos formas**: `IconButton` en la app, `Button` con texto "Actions" en la BD
   (`features/databases/DatabasePage.tsx:77`).
5. **h2 que repite h1 o pestaña**: `BackupsPage.tsx:79`, `DatabasesPage.tsx:40`, `AppDomainsTab.tsx:210`,
   Tokens ("Tokens for automation").
6. **Escala de títulos sin regla**: h3 a `text-13` (`MachineCharts.tsx:190`, `MetricsTab.tsx:153`) y a
   `text-14` (`WebhookSection.tsx:261`, `HealthCheckForm.tsx:248`, `settings/ReleasesSection.tsx:230`); h2
   a 18, 16 y 14. Uso real: `text-13` ×369, `text-12` ×282, `text-14` ×69, `text-16` ×10, `text-18` ×11.
7. **Estados vacíos en cuatro alturas**: `py-16` ×13, `py-8` ×12, `py-12` ×2, `py-10` ×1; apilables; dentro
   de `DataTable` se cortan en móvil (`DataTable.tsx:237-242`) y muestran cabeceras de columna.
8. **Pista de CLI en 37 sitios** sin posición fija (arriba a la derecha en Registros, a media página en
   `AppOverview.tsx:405`, en la columna izquierda en Ajustes, bajo cada formulario en Ajustes de app).
9. **Tres patrones de página de ajustes** (§4.27) y formularios con guardado por tarjeta.
10. **Topes de ancho**: `Shell.tsx:161`, `SettingsTab.tsx:57`, `EnvironmentTab.tsx:343`,
    `routes/_console/settings.tsx:21`, `NewAppWizard.tsx:396`.
11. **Dos pestañas**: `LinkTabs` (rutas; `app/LinkTabs.tsx:28` oculta la barra sin degradado) y `Tabs` con
    `?tab=` en Dominios, con contadores tardíos.
12. **Dos steppers**: `features/new-app/StepRail.tsx` (vertical, relleno) y
    `features/settings/servers/AddServerDialog.tsx:32` (horizontal, contorno, más "Step 1 of 3").
13. **Dos marcos de gráfica y dos juegos de rangos**: `overview/MachineCharts.tsx:124` + `windows.ts:9` y
    `app/metrics/MetricsTab.tsx:77` + `ranges.ts:33` (regla 3).
14. **Acciones de fila fuera de pantalla en móvil**: `DataTable.tsx:161` (`overflow-x-auto`) y `:289`
    (columna de acciones no fija).
15. **Semántica de color**: verde para "Enabled" (cron, sitios), "Active" (tokens), "Static" (apps) y
    "Covered" (certificados); D8 reserva el verde para "en marcha".
16. **Severidad distinta para el mismo hecho**: app caída = rojo en Resumen, "Warning" en Servidor.
17. **Unidades mezcladas**: "0.98 TB" junto a "277 GB" (`StorageUsageBar.tsx:75-76`); "1006.9GB" sin
    espacio desde el backend en Servidor.
18. **Fechas**: relativas en tablas, en-US mono en el diálogo de cron, ISO en certificados.
19. **Texto del backend sin traducir en la UI española**: `src/noust/deployers/bluegreen.py:308` (motivo de
    no elegibilidad de "sin cortes"), veredictos de Diagnóstico.
20. **Valores destructivos por defecto**: `DangerSection.tsx:53-54`.
21. **Cabecera de app en móvil**: solo ⋯; Reiniciar y Actualizar desaparecen.
22. **"Flota" siempre en el sidebar**: `app/nav.ts:54`.

---

## 6. Jerga y copy: reescrituras propuestas

Criterio: la superficie habla del resultado para el operador; el término técnico va en mono, en un
tooltip o en la línea secundaria, nunca como etiqueta principal. La salida del sistema sigue verbatim.
La tabla del glosario de `panel/src/i18n/README.md` debe cambiar donde se indica.

| Dónde | EN actual | EN propuesto | ES actual | ES propuesto |
|---|---|---|---|---|
| Ajustes app, Resumen app | Layout | How deploys work | Disposición | Modo de despliegue |
| idem | Releases | Instant rollback | Releases | Vuelta atrás instantánea |
| idem | In place | Single folder | En el sitio | Carpeta única |
| idem | Each deploy is a release; rollback is instant | Each deploy is kept apart; going back takes seconds | Cada despliegue es una release; revertir es instantáneo | Cada despliegue se guarda aparte; volver atrás lleva segundos |
| idem | Updated in its directory | Rebuilt in the same folder on every deploy | Se actualiza en su propio directorio | Se recompila en la misma carpeta en cada despliegue |
| Ajustes app | Enable releases | Turn on instant rollback | Activar releases | Activar la vuelta atrás instantánea |
| idem | Plan the migration / Migrate to releases | Check what changes / Turn it on | Planificar la migración / Migrar a releases | Ver qué cambia / Activar |
| Despliegues | Releases (panel), Serving, Previous | Versions, Live, Earlier | Releases, Sirviendo, Anterior | Versiones, En producción, Anterior |
| idem | Roll back to this | Go back to this version | Revertir a esta | Volver a esta versión |
| idem | Rollback points (en el sitio) | Backups you can go back to | Puntos de reversión | Copias a las que puedes volver |
| Glosario i18n | release → "release (la, las releases)" | - | - | "versión" en la UI; "release" solo en CLI y rutas |
| Ajustes app | Health check | Startup check | Comprobación de salud | Comprobación de arranque |
| idem | What a new version must answer before it serves. | Before switching traffic, Noust requests this path and waits for an answer. | Qué debe responder una versión nueva antes de servir. | Antes de pasar el tráfico, Noust pide esta ruta y espera respuesta. |
| Ajustes app | Zero downtime / Blue/green activation… | Zero-downtime deploys: the new version starts beside the old one and traffic moves when it answers. | Sin cortes / Activación blue/green… | Despliegues sin cortes: la versión nueva arranca junto a la anterior y el tráfico pasa cuando responde. |
| idem | Drain | Keep the old version for (seconds) | Vaciado | Mantener la anterior durante (segundos) |
| idem | nginx's upstream names port {port}. | (quitar) | (quitar) | (quitar) |
| Ajustes app | Deploy webhook | Deploy on push | Webhook de despliegue | Desplegar al hacer push |
| idem | A push to the repository deploys the app. The forge signs… | Pushes to {branch} deploy this app. / Any push to any branch deploys this app: pin a branch to limit it. | Un push al repositorio despliega la app. La plataforma firma… | Los push a {branch} despliegan esta app. / Cualquier push a cualquier rama despliega esta app: fija una rama para limitarlo. |
| idem | Payload URL | Webhook URL | URL de carga útil | URL del webhook |
| idem | forge | GitHub, GitLab or Gitea | plataforma | tu plataforma Git |
| idem | Deliveries / Recent deliveries | Pushes received | Entregas / Entregas recientes | Push recibidos |
| idem | Enabled / Disabled | On · waiting for the first push / On · connected / Off | Activado / Desactivado | Activo · esperando el primer push / Activo · conectado / Desactivado |
| Ajustes app | Resource limits · What systemd lets the app's unit use | Resource limits · The most this app may use of the server | Límites de recursos · Qué puede usar la unidad de la app según systemd | Límites de recursos · Lo máximo que la app puede usar del servidor |
| idem | MemoryMax. At least 64 MB. | At least 64 MB. (MemoryMax en tooltip) | MemoryMax. Al menos 64 MB. | Al menos 64 MB. |
| idem | Tasks · TasksMax: processes and threads | Processes and threads | Tareas | Procesos e hilos |
| Resumen app | Uptime | Uptime | **Actividad** (choca con la página Actividad; `es/appPages.ts:89`) | Tiempo en marcha |
| Resumen app | Runtime | How it runs | Tiempo de ejecución | Cómo se ejecuta |
| Resumen app, servicio | Main PID | Process ID | PID principal | ID de proceso |
| varios | Unit / systemd unit | Service (valor en mono) | Unidad | Servicio |
| Registros | Journal | Logs | journal | Registros |
| Cron, servicio | Run as an argv, without a shell. | Runs directly, not through a shell: pipes, && and $VARS do not work. | Se ejecuta como un argv, sin un shell. / sin una shell. (inconsistente: `es/cron.ts:96`, `es/services.ts:118`) | Se ejecuta directamente, sin shell: no funcionan tuberías, && ni $VARIABLES. |
| Cron | Commands run on a schedule, as systemd timers. | Commands this server runs on a schedule. | Comandos que se ejecutan con una programación, como temporizadores de systemd. | Comandos que el servidor ejecuta según un horario. |
| Servicios | Show all units | Show system services too | Mostrar todas las unidades | Mostrar también los del sistema |
| Servicio nuevo | Restart policy: always | Restart when it stops: always | Política de reinicio: always | Reiniciar si se detiene: siempre |
| Copias | Snapshots of your applications, their schedules and the storage they use. | Copies of your apps' files, .env and databases, and when they are made. | Instantáneas de tus aplicaciones, sus programaciones y el almacenamiento que usan. | Copias de los archivos, el .env y las bases de datos de tus apps, y cuándo se hacen. |
| Copias | Verified · Never verified | Integrity · Not checked | Verificada · Nunca verificada | Integridad · Sin comprobar |
| Programación | on a systemd timer | on a schedule | en un temporizador de systemd | según un horario |
| idem | Local retention · Server default (backup.max_per_app) | How many to keep here · Server default ({value}) | Retención local · Predeterminado del servidor (backup.max_per_app) | Cuántas guardar aquí · Valor del servidor ({value}) |
| idem | Max age · pruned | Delete after (days) | Antigüedad máxima | Borrar pasados (días) |
| Destinos | …over rclone. | Other places a copy of each backup is sent: SFTP, S3, B2, cloud drives. | …mediante rclone. | Otros sitios a los que se envía una copia: SFTP, S3, B2 o nubes. |
| Dominios | Covered / Not covered | HTTPS / No HTTPS | Cubierto / No cubierto | Con HTTPS / Sin HTTPS |
| Dominios | Sites | Web server sites | Sitios | Sitios del servidor web |
| Entorno | Visibility · Shown: nothing about it looks like a secret | Type · Plain | Visibilidad · Mostrado: nada en él parece un secreto | Tipo · Normal |
| idem | Hidden: its name suggests a secret | Secret (by its name) | Oculto: … | Secreto (por el nombre) |
| Nueva app | Deploy as | App type | Desplegar como | Tipo de aplicación |
| idem | …as WASM 1.x did. | (quitar la referencia) | …como hacía WASM 1.x. | (quitar) |
| idem | Persistent paths · linked from shared/ | Folders kept between deploys (uploads, SQLite) | Rutas persistentes | Carpetas que se conservan entre despliegues |
| Apps vacío | …gives it a systemd unit, a site and a certificate. | …runs it as a service and serves it over HTTPS. | …le da una unidad de systemd, un sitio y un certificado. | …la ejecuta como servicio y la sirve con HTTPS. |
| Diagnóstico | Probable cause · (texto de systemd como titular) | Most likely: the app crashes when it starts · (texto verbatim debajo) | Causa probable | Lo más probable: la app se cierra al arrancar |
| idem | Every probe… | Every check… | Cada prueba… | Cada comprobación… |
| Actividad | Failed · second factor required but not presented | Waiting for the 2FA code | Fallido · … | Esperando el código de verificación |
| Resumen | Monitor notice: name-pattern | Suspicious process name | Aviso del monitor: name-pattern | Nombre de proceso sospechoso |
| Flota vacía | A fleet is several Noust servers run from one console, this central's. The central reaches each server through an SSH tunnel… | Manage several servers from this console. Each one authorizes this console once; it never gets a shell there. | Una flota son varios servidores Noust gestionados desde una sola consola, la de esta central. La central llega a cada servidor por un túnel SSH… | Gestiona varios servidores desde esta consola. Cada uno la autoriza una vez; nunca obtiene una shell allí. |
| Bloqueo | Its secrets are sealed at rest… | The keys that reach your servers are encrypted with a passphrase that is stored nowhere. | Sus secretos están sellados en reposo… | Las claves que llegan a tus servidores están cifradas con una frase de paso que no se guarda en ningún sitio. |
| Acerca de | Update from a terminal: pip install --upgrade noust | (según el método: apt/dnf/zypper/pip) | idem | idem |
| Notificaciones | Certificate expiring · Not sent by this version of Noust yet. | (quitar hasta que exista) | idem | (quitar) |

---

## 7. Orden sugerido

1. Base del sistema: `scrollbar-gutter`, topes por plantilla, `Card`/`TextLink`/`PageShell` con slots
   (cabecera, pestañas, barra de acciones, pie de CLI), una sola pestaña enrutada, un solo stepper, un
   estado vacío en dos variantes, filas-tarjeta y acciones fijas en `DataTable` móvil.
2. T3 para Ajustes de app (con webhook guiado y "vuelta atrás instantánea") y Ajustes globales
   (Notificaciones con Drawer por canal).
3. Copias (cobertura por app + pestañas) y pestaña Copias en la app.
4. Resumen como T4 con primeros pasos; Servidor con pestañas; Bases de datos y Servicio con pestañas.
5. Barrido de copy (§6) y del glosario de `panel/src/i18n/README.md`.
6. Un test E2E de salto al cambiar de pestaña con barras de scroll reales, y un umbral de longitud por
   plantilla (p. ej. ninguna vista T1/T3 por encima de 2 vh a 1440 con la semilla estándar).

## Apéndice: capturas copiadas

`docs/superpowers/research/3.1/screens-before/`: `overview-1440`, `overview-390`, `overview-empty-1440`,
`overview-empty-390`, `apps-390`, `new-app-review-1440`, `app-overview-1440`, `app-deployments-1440`,
`app-environment-1440`, `app-diagnose-1440`, `app-settings-1440`, `app-settings-390`,
`app-settings-inplace-es-1440`, `databases-1440`, `database-1440`, `services-1440`, `service-1440`,
`domains-1440`, `backups-1440`, `backups-390`, `backups-empty-1440`, `activity-1440`, `server-1440`,
`fleet-empty-1440`, `settings-general-1440`, `settings-notifications-1440`, `settings-about-1440`,
`dialog-add-server-1440`, `dialog-backup-schedule-1440`, `dialog-cron-1440`, `topbar-es-1440` (recorte).
