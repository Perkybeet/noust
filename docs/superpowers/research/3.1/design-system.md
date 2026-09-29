# Sistema de diseño de Noust: inventario, inconsistencias, investigación y propuesta

Fecha: 2026-09-29. Rama `dev/3.1`. Investigación de solo lectura sobre el repositorio (ningún
fichero de código tocado); el único artefacto es este documento.

Encargo del dueño: "un patrón de diseño del producto, diseñado casi en su totalidad, para
respetarse siempre: colores, estructura, todo lo que se te ocurra. La UI/UX es muy importante."

Cómo leerlo: la parte 1 dice qué existe; la parte 2 mide dónde no se cumple (con recuentos y
`fichero:línea`); la parte 3 resume qué hacen los mejores sistemas y qué reglas se pueden
adoptar (con URL); la parte 4 propone el índice del documento normativo con las reglas y valores
concretos; la parte 5 propone cómo hacerlo cumplir automáticamente; la parte 6 el plan y las
decisiones que necesitan al dueño.

Convenciones: las rutas son relativas a `panel/src/` salvo que empiecen por `docs/` o
`panel/`. Los recuentos salen de `rg` sobre código que no es de test, en `features/` salvo que
se diga otro ámbito. Cuando un número es aproximado (heurística con expresión regular) se dice.

---

## 0. Resumen

**Diagnóstico en una frase.** Los fundamentos (tokens, contraste, lenguaje de estado, CSP,
accesibilidad) son excelentes y están probados por test; la disciplina se pierde un nivel más
arriba: los patrones compuestos (tarjeta, aviso, progreso de un job, cabecera de sección, estado
vacío dentro de una tabla, barra de filtros, confirmación de gravedad media) o no existen como
componente, o existen sin uso, y cada feature los reconstruye con clases. La regla 3 del
proyecto ("hay una implementación de cada cosa") se cumple en el backend y se incumple en la
capa de composición de la interfaz.

**Cifras que lo resumen** (detalle en la parte 2):

| Hallazgo | Cifra |
|---|---|
| `Card` existente usado en `features/`, `app/`, `nodes/` | **0** usos (solo la galería) |
| Superficies "tarjeta" escritas a mano (`rounded-card … bg-surface`) | **69** en 39 ficheros, con ≥ 6 paddings distintos |
| Avisos tintados a mano (`border-{ok,warn,fail,accent}/NN`) sin componente `Notice` | **34** bordes tintados, 4 alfas (20/30/40/50), 21 firmas de clase distintas |
| Implementaciones del progreso de un job | **3** (`AppLayout.tsx`, `JobBanner.tsx`, `DeployStep.tsx`) |
| Mapas `TONE_TEXT` redeclarados fuera de `StatusPill.tsx` | **5** ficheros |
| Valores arbitrarios de dimensión de Tailwind (`rounded-[`, `max-w-[`, `h-[`…), sin `dev/` | **152** (97 features, 38 components, 13 app, 4 nodes) + 35 `grid-cols-[…]` |
| `rounded-[4px]`: un cuarto radio que no está en los tokens (6/10/999) | **52** |
| Pasos de espaciado fuera de la escala D8 (`0.5`, `1.5`, `2.5`, `3.5`) | **440** usos (311 en features) |
| Estilos distintos para el "título de subsección" | **4** (14 medium, 13 medium, 14 semibold, 12 muted) |
| `ConfirmDialog` (obliga a teclear el nombre) sin nivel intermedio; la confirmación simple está escrita a mano 4 veces | **18** usos en 17 ficheros |
| Tablas con la columna de estado en 4 posiciones distintas (1.ª a 4.ª) | **26** tablas reales (32 usos de `DataTable`) |

**Decisiones clave propuestas** (desarrolladas en la parte 4):

1. **Mantener D8 y volverlo normativo**, con tres significados de color y solo tres: estado,
   interacción (violeta) e **identidad de serie en gráficas**, esta última en una familia propia
   `--viz-*` que nunca sale de un trazado. La información (`info`) es **acromática**, por diseño.
2. **Estado = color + forma + palabra, sin excepciones.** Una sola tabla de iconos semánticos, un
   solo mapa de tonos, `Badge` nunca para estado, y un glifo propio para "en cola" (hoy usa el
   arco que gira, que miente).
3. **Tokens que faltan**: `--{estado}-border` (para eliminar los alfas `/30 /40 /50`),
   `--radius-chip: 4px`, `--control-sm|md|lg` (28/32/40; 44 con puntero grueso), `--z-*`,
   `--measure-*`, `--width-*` y `--viz-1|2` (validados con la herramienta de dataviz: ΔE CVD 13,7
   claro y 13,3 oscuro; contraste ≥ 3:1).
4. **Espaciado**: reconocer la escala real (medios pasos de 2 px hasta 16 px, más 20/24/32/40/48/64) y
   prohibir lo demás (7, 9, 11, 13, 14, 15 y todo valor arbitrario).
5. **Componentes que faltan o hay que completar**: `Notice` (aviso en 4 severidades + neutral),
   `JobProgress`, `FilterBar`, variantes de `EmptyState` (`page | section | table | inline`),
   `Subsection`, `ConfirmDialog` con niveles de fricción, `PageHeader` con hueco para pestañas y
   título en mono; y **adoptar `Card`** o retirarlo.
6. **Ocho plantillas de página** con tres anchos con nombre (`narrow` 46 rem, `form` 72 rem,
   `wide` 100 rem) y una regla de dónde vive la acción primaria de creación.
7. **Tablas**: identidad primero, estado segundo (siempre `StatusPill inline`), atributos,
   tiempo, números a la derecha en mono, acciones al final. Hoy hay cuatro posiciones para el estado.
8. **Taxonomía de notificaciones** en cinco canales (en línea, aviso de sección, aviso global,
   toast, diálogo) con la regla de cuándo usar cada uno; el toast de éxito se limita a lo que no
   se ve en pantalla.
9. **Fricción proporcional al radio de la explosión** al destruir: sin confirmación (con
   deshacer) / confirmación simple / teclear el nombre. Hoy todo lo destructivo exige teclear.
10. **Cumplimiento automático** por capas: regla ESLint contra valores arbitrarios (ampliando la
    del hex que ya existe), test "de trinquete" con línea base que solo puede bajar, tests de
    tokens ampliados (incluye viz y `*-border`), galería cubierta por axe y capturas, y una
    auditoría de estilos computados en el E2E.

**Necesita decisión del dueño** (parte 6): (a) ¿la serie 1 de las gráficas deja de ser violeta?;
(b) ¿se oficializan los pasos de 2 px?; (c) ¿"identidad primero, estado segundo" en tablas?;
(d) ¿el documento normativo va en inglés (`docs/DESIGN.md`, junto a `BRAND.md`) o en español?;
(e) ¿se acepta la fricción por niveles, que cambia unas 10 de las 18 confirmaciones?

---

## 1. Inventario de lo que existe

### 1.1 Fundamentos

Ficheros: `styles/tokens.css` (125 líneas), `styles/app.css` (316), `styles/fonts.css` (69),
`styles/tokens.test.ts` (172). Cifras globales del panel: 433 ficheros TS/TSX de producción, 157
ficheros de test, 40 specs E2E, 4 281 líneas de catálogo en inglés (23 espacios de nombres).

**Cómo está construido.** Cada color se escribe una sola vez como `light-dark(claro, oscuro)` en
`tokens.css`; `color-scheme: light dark` sigue al sistema y `data-theme` fija un subárbol a un
tema (así funcionan el ajuste de tema y la galería sin duplicar la paleta). `app.css` reapunta
Tailwind v4 a los tokens: `--color-*`, `--text-*`, `--shadow-*`, `--radius-*` y `--animate-*` se
ponen en `initial`, de modo que `bg-red-500` o `text-sm` **no compilan**; solo existen las
utilidades mapeadas en `@theme inline`. Es una garantía por construcción, más fuerte que un lint.

**Tokens de color** (32 declarados con `light-dark`: 30 colores, `--shadow-color` y `--backdrop`; valores
claro / oscuro):

| Rol | Token | Claro | Oscuro |
|---|---|---|---|
| Superficie | `--bg` | `#f7f7f7` | `#111111` |
| | `--bg-sunken` (logs, código, pies) | `#f0f0f0` | `#0b0b0b` |
| | `--surface` (tarjetas, tablas) | `#ffffff` | `#171717` |
| | `--surface-raised` (popups, diálogos) | `#ffffff` | `#1f1f1f` |
| | `--surface-hover` / `--surface-active` | `#f2f2f2` / `#ebebeb` | `#222222` / `#2a2a2a` |
| Borde | `--border` (separa) | `#e3e3e3` | `#2a2a2a` |
| | `--border-strong` (delimita un control, ≥ 3:1) | `#898989` | `#6e6e6e` |
| Texto | `--text` | `#161616` | `#ededed` |
| | `--text-muted` | `#555555` | `#a3a3a3` |
| | `--text-faint` (legible: 4,5:1 en toda superficie) | `#6b6b6b` | `#8c8c8c` |
| Acento (solo interactivo) | `--accent` / `--accent-hover` | `#6a45d5` / `#5b37c2` | `#7152de` / `#7a5be5` |
| | `--accent-soft` / `--accent-text` | `#f0ebfd` / `#5a36c6` | `#1f1937` / `#ae9aff` |
| | `--on-accent` | `#ffffff` | `#ffffff` |
| Estado | `--ok` / `--ok-soft` | `#16784a` / `#e6f4ec` | `#4dc47e` / `#0f2619` |
| | `--warn` / `--warn-soft` | `#8c5800` / `#fbf0da` | `#e3a73c` / `#2a1f0b` |
| | `--fail` / `--fail-soft` | `#c12c24` / `#fceae8` | `#ff736a` / `#301514` |
| | `--fail-strong` (relleno del botón de peligro) | `#c12c24` | `#cf3a30` |
| | `--idle` / `--idle-soft` | `#636363` / `#ececec` | `#9e9e9e` / `#242424` |
| Foco y selección | `--focus` | `#6a45d5` | `#a08bff` |
| | `--selection` / `--match` (búsqueda en logs) | `#ddd3fb` / `#e3dafc` | `#3a2c73` / `#352868` |
| ANSI de logs | `--ansi-blue` / `-magenta` / `-cyan` | `#1f5fbf` / `#a3319f` / `#0e7285` | `#6fa8ff` / `#e58be0` / `#4fc7d6` |
| Velo | `--backdrop` | `rgb(10 10 10 / .28)` | `rgb(0 0 0 / .64)` |

**Otros tokens.** Tipografía: `--font-sans` (Mona Sans Variable con dos fallbacks de métricas
ajustadas para que el intercambio no mueva nada), `--font-mono` (JetBrains Mono Variable, tres
fallbacks y fuentes de emoji porque los logs del CLI llevan glifos), escala `--fs-12/13/14/16/18/
24/32` con interlineados `16/20/20/24/24/32/40`, `--stretch-title: 110%`, `--tracking-title:
-0.018em`, `--tracking-display: -0.028em`. Espacio: `--space: .25rem`. Radios: `--radius-control:
6px`, `--radius-card: 10px`, `--radius-pill: 999px`. Sombras: `--shadow-raised` (una sombra mínima solo
en claro), `--shadow-overlay` (solo overlays). Movimiento: `--duration-fast: 120ms`,
`--duration-base: 180ms`, `--ease-out: cubic-bezier(.16, 1, .3, 1)`; con `prefers-reduced-motion`
ambas duraciones pasan a 0 y `app.css` anula animaciones y transiciones.

**Utilidades propias** (`app.css`): `title` (600, ancho 110 %, tracking), `display`, `mono` (fuente
mono, sin ligaduras, cifras tabulares con cero cortado), `scroll-thin`; animaciones `spin`, `pulse-once`
(un pulso de opacidad al cambiar un estado, nunca movimiento), `breathe` (skeleton), `indeterminate`.

**Lo que verifica `tokens.test.ts`** (146 aserciones de contraste, más estructurales):

- 57 pares de texto ≥ 4,5:1 por tema (texto y sus tres grados sobre 5 superficies; acento sobre
  superficies y sobre `accent-soft`; cada estado sobre 4 superficies y sobre su `-soft`; `on-accent`
  sobre acento, hover y `fail-strong`; ANSI sobre `bg-sunken` y `surface`).
- 16 pares de UI ≥ 3:1 (`border-strong`, `focus`, `accent` sobre 4 superficies; advertencia y fallo
  sobre su fondo suave).
- Superficies, bordes, texto e `idle` son **acromáticos** (R = G = B) en ambos temas.
- En oscuro, cada escalón de superficie es más claro que el anterior (la elevación es luminancia).
- Reducción de movimiento a 0; pila mono con fuente de emoji; fallbacks de métricas declarados.

**Lo que no verifica**: los fondos y bordes tintados con alfa (`bg-fail-soft/50`, `border-warn/40`)
no son tokens, así que su contraste no se comprueba; no hay comprobación de que `@theme inline`
exponga exactamente lo declarado; no hay tests de la escala tipográfica ni de espaciado.

**Escala tipográfica real** (uso, sin `dev/`): `text-13` 393, `text-12` 299, `text-14` 81, `text-18`
13, `text-16` 12, `text-24` 8, `text-32` 2. `font-medium` 210, `font-semibold` 7, `font-normal` 7.
Conclusión: **13 px es el tamaño de trabajo** (el `body` declara 14 y casi todo lo baja a 13/12).
El ancho 110 % de Mona Sans solo se aplica en `title` y `display`.

### 1.2 Componentes `components/ui` (36 exportaciones)

| Componente | Propósito | Variantes / API destacable |
|---|---|---|
| `Button`, `buttonClassName` | Botón único; `buttonClassName` para enlaces que deben parecerlo | `primary`, `secondary` (defecto), `ghost`, `danger`; tamaños `sm` 28, `md` 32, `lg` 40; `loading`, `icon`, `trailingIcon` |
| `IconButton` | Botón cuadrado solo con icono; `label` obligatorio (tooltip + nombre accesible) | `ghost`, `secondary`; `sm` 28, `md` 32; `pressed`, `shortcut` |
| `CopyButton`, `CopyTextButton`, `useCopyState` | Copiar con confirmación anunciada | tamaño `sm` |
| `Input`, `Textarea`, `Select`, `Checkbox`, `Switch` | Controles; `CONTROL_FRAME` comparte borde, foco, inválido, deshabilitado | `Input`: `sm` 28/`md` 32, `mono`, `icon`, `prefix`, `suffix`; `Select` `sm`/`md` |
| `Field` | Etiqueta + control + ayuda + error, cableados | `error` (icono + texto), `optional`, `description`, `nativeLabel` |
| `Dialog`, `DialogClose` | Tarea modal | anchos `sm` 400, `md` 520, `lg` 720, `xl` 1280; footer a la derecha, primaria al final |
| `ConfirmDialog` | Destruir con confirmación tecleando el nombre | 440 px; muestra el fallo del sistema en el propio diálogo; `destructive` |
| `Drawer` | Panel derecho para detalle con la página detrás | `md` 480, `lg` 720; gesto de deslizar |
| `Popover`, `Tooltip`, `TooltipProvider`, `Menu` (+`MenuItem`, `MenuGroup`, `MenuSeparator`) | Flotantes | `MenuItem`: `icon`, `shortcut`, `destructive`; tooltip con retardo de 500 ms |
| `Tabs`, `TabList`, `Tab`, `TabPanel` | Vistas hermanas con estado local | `Tab` con `count`; regla acento bajo la activa; desplaza en móvil |
| `DataTable` | Filas de cosas del mismo tipo | orden con `aria-sort`, `onRowActivate` (la 1.ª columna es el botón primario, flechas entre filas), `rowActions`, `loading` + `skeletonRows`, `empty`, `density` compacta 36 / cómoda 44, `hideBelow` |
| `StatusPill`, `StatusGlyph`, `STATUS`, `stateTextClass` | Lenguaje de estado: 7 estados con glifo distinto + palabra + color | `pill` o `inline`; `sm`/`md`; el cambio de estado pulsa una vez |
| `Badge` | Atributo corto (tipo, cuenta, versión); "para estado, usa StatusPill" | tonos `neutral`, `accent`, `ok`, `warn`, `fail`; `mono` |
| `Card` | Superficie acotada que agrupa un tema | `title`, `description`, `actions`, `footer`, `level 2–4`, `padding none/md` (**sin usos**) |
| `EmptyState` | Lo que muestra una lista o página sin contenido | `icon` (20 px en caja de 40), `title`, `description`, `action`, `command` (con copiar), `level` |
| `Skeleton`, `SkeletonText`, `Spinner` | Carga: forma del contenido / actividad indeterminada | `Spinner` 12–24 px |
| `Progress`, `Meter` | Avance con final conocido / nivel en un rango | `Meter` neutro → ámbar (75 %) → rojo (90 %); valor siempre impreso |
| `Toast`, `toast` | Cola única de resultados | `success`, `error`, `warning`, `info`; máx. 3; éxito 5 s, error persiste; regiones vivas cortés/asertiva; 380 px |
| `Chart` | Serie temporal en uPlot con resumen textual y tabla alternativa | hasta 3 series (acento, `text-muted` discontinua, `text-faint` punteada), marcadores de estado, zoom, ampliar en diálogo |
| `LogViewer` | Visor virtualizado de logs (ANSI → spans) | búsqueda, seguir, ajustar líneas, descargar; líneas warn/error con fondo suave |
| `SystemOutput` | Salida de un programa, verbatim en mono | entra en el orden de tabulación solo si desplaza |
| `Mono`, `Kbd`, `ExternalLink` | Valor de sistema; tecla; enlace externo | `Mono`: `text-[0.92em]`, `tone`, `truncate` |
| Ganchos | `useNeedsScrollFocus`, `useTabStrip` | |

### 1.3 Componentes `components/page` (15 exportaciones)

| Componente | Propósito |
|---|---|
| `Section`, `Sections` | Un tema de página con encabezado, descripción, acciones; `Sections` apila con 32 px. Región con nombre. Nivel 2 (16 px) o 3 (14 px) |
| `ErrorBlock`, `QueryState` | Un fallo como siempre se muestra (qué falló, arreglo arriba, palabras del sistema abajo en mono, reintentar); `QueryState` unifica esqueleto, error, vacío y contenido, y conserva los datos si un refresco falla |
| `AppStatePill`, `DeployStatePill`, `status.ts` | Traducen el vocabulario del backend (tres fuentes) a `StatusPill`; una palabra desconocida se muestra verbatim con la forma "desconocido"; `STATE_RANK` ordena por gravedad |
| `KeyValueList`, `KeyValueListSkeleton` | Hechos sobre una cosa, uno por fila; valores de sistema en mono y copiables |
| `StatTile` | Un dato con una línea de contexto |
| `ResourceMeter` | Uso frente a límite; sin límite, la lectura sola y lo dice |
| `RelativeTime`, `useNow` | "hace 3 min" en vivo con el instante exacto en tooltip |
| `SegmentedControl` | Uno de pocos modos excluyentes (rango de una gráfica) |
| `DangerZone`, `DangerAction` | Acciones irreversibles, al final de la página, cada una explicando qué destruye |
| `CommandHint` | El comando del CLI equivalente, con copiar |
| `useAnnounceChange` | Anuncio de cambios de estado a lectores de pantalla |

### 1.4 Galería de diseño (`dev/`)

`/__design` solo existe en desarrollo (`dev/routes.tsx`). Tres bloques: **Fundamentos** (color con
contraste calculado en vivo desde `tokens.css?raw`, lenguaje de estado en ambos temas, tipografía,
espacio y forma, elevación), **Componentes** (botón, icono, campos, diálogos y drawers, pestañas y
menús, estado y feedback, insignias y valores, tarjeta y estado vacío, tabla, gráfica, logs, logo) y
**Kit de página** (sección, clave-valor, tiempo y números, `QueryState`, estados de app/deploy,
tiles y medidores, comando, zona de peligro).

Lo que **no** cubre: el shell (sidebar, topbar, machine strip, selector de servidor), plantillas de
página, avisos (no existe el componente), formularios completos, iconografía, movimiento, densidad,
paleta de gráficas, ejemplos de contenido. Y **no está en el E2E**: ninguna spec de `e2e/`
referencia `__design`, así que ni axe ni CSP ni capturas la vigilan.

### 1.5 App shell

- `Shell.tsx`: `flex min-h-dvh`; sidebar fija de 240 px (`w-60`, solo ≥ 1024 px) + columna con
  `Topbar` de 56 px pegajosa y `<main>` en `mx-auto max-w-[1600px]` con relleno 16/24/32 px y
  `pt-6 lg:pt-8`. Enlace de salto, `RenameNotice`, `NodeNotice`, `ErrorBoundary` por ruta; foco al `h1`
  tras cada navegación; `scroll-padding-top: 4.5rem` para que la barra no tape el foco (WCAG 2.4.11).
- `Sidebar.tsx`: cuatro grupos separados por espacio (no por etiquetas): Resumen y Flota; Aplicaciones,
  Bases de datos, Servicios, Cron; Dominios y Backups; Actividad y Servidor; Ajustes y versión al pie.
  Filas de 32 px, activo = `surface-active` + peso 500; contador rojo con glifo `failed` en Aplicaciones
  y Servicios. Un hub oculta lo que solo es local.
- `Topbar.tsx`: `ServerSelector` (nombre del servidor en mono), `MachineStrip` (lectura de la máquina),
  buscador `⌘K` (paleta de comandos), panel de sesión (tema, idioma, atajos, cerrar sesión), menú móvil.
- `PageHeader.tsx`: `h1` `title text-24` con foco programático, descripción `text-14` con tope 68ch,
  migas, acciones a la derecha "la más importante al final", `mb-8`.
- `LinkTabs.tsx`: pestañas cuyo estado es la URL (para app y ajustes), `h-10`, regla de acento bajo la
  activa; `Tabs` (estado local) solo lo usa Dominios.
- Overlays globales: `CommandPalette` (640 px), `ShortcutsDialog`, `Drawer` del menú móvil, `Announcer`
  (dos regiones vivas, cortés y asertiva). Atajos: `g` + letra para ir a, `/` buscar, `?` ayuda.

### 1.6 Cumplimiento actual

- ESLint (`panel/eslint.config.js`): `strictTypeChecked`, `jsx-a11y strict`, reglas de hooks, y **una**
  regla de diseño: `no-restricted-syntax` contra literales con hex (`#rrggbb`) fuera de tests y
  `styles/`. No ve `rgb()`, `color-mix()` ni valores arbitrarios.
- Tailwind reiniciado: paleta, escala de texto, sombras y radios por defecto no compilan.
- Vitest: 157 ficheros de test (componentes con axe), `tokens.test.ts`, `contrast.test.ts`, tests de
  catálogos i18n (texto vacío, marcadores idénticos, plurales completos).
- Playwright (40 specs): `pages.spec.ts` recorre cada ruta de `routes.ts` en ambos temas con axe
  (WCAG 2.2 AA, cero violaciones), captura de violaciones de CSP y de errores de consola en todos los
  tests, `layout-shift.spec.ts` con límite CLS 0,05, barrido en español, `e2e:screens` con capturas de
  toda ruta en ambos temas y 390 px.
- **No hay** ningún control sobre: valores arbitrarios, espaciado, radios fuera de escala, uso de
  componentes (que se use `Card`, que se use `Notice`), títulos, iconos de estado, `z-index`.

---

## 2. Inconsistencias: recuentos y ejemplos

Método: `rg` y scripts sobre `panel/src` (sin tests). Cuando el recuento es de una expresión regular
sobre JSX multilínea se marca "≈". Todo lo listado es reproducible.

### 2.1 Resumen por gravedad

| # | Hallazgo | Recuento | Gravedad |
|---|---|---|---|
| I-1 | `Card` sin usar; 69 superficies a mano | 0 / 69 | Alta |
| I-2 | Sin componente de aviso; 34 bordes tintados con 4 alfas | 34 / 21 firmas | Alta |
| I-3 | Tres implementaciones del progreso de un job | 3 | Alta |
| I-4 | Helpers duplicados: mapa de tonos (×5) y marcador de celda vacía `Nothing` (×4, uno sin motivo accesible) | 5 / 4 | Media (regla 3) |
| I-5 | Dos vocabularios de icono para el mismo estado | 33 `StatusGlyph` vs ≈ 50 lucide de estado | Alta |
| I-6 | Estado por color solo o por canal incompleto | ≈ 10 sitios | Alta (WCAG 1.4.1) |
| I-7 | Cuatro estilos de "título de subsección"; 40 encabezados a mano | 4 / 40 | Media |
| I-8 | Cabecera de página: acción primaria en tres sitios, hack `-mt-4`, título mono ausente | 6 / 4 / 21 | Media |
| I-9 | Estados vacíos: 41 `EmptyState` + 8 a mano + parches por `className` | 8 / 19 | Media |
| I-10 | Tablas: cuatro posiciones para el estado, filtros con cuatro anchos | 26 / 7 barras | Media |
| I-11 | Botones: rol → variante sin regla (Hecho/Cerrar, Guardar) | ver detalle | Baja–Media |
| I-12 | Espaciado: la escala D8 no describe el código | 440 | Media |
| I-13 | Valores arbitrarios de dimensión | 152 (+ 35 `grid-cols`) | Alta (sin control) |
| I-14 | Etiquetas a mano y diálogos de confirmación reimplementados | 13 / 3 | Media |
| I-15 | Fricción al destruir sin niveles | 18 + 4 a mano | Media (UX) |
| I-16 | Carga y microcopia | 5 `...` vs 11 `…` | Baja |
| I-17 | Mono: `<Mono>` 9 vs utilidad a mano 132, tamaños 12/13/14 | 132 | Baja–Media |
| I-18 | `z-index`, sombras y colores crudos fuera de tokens | 6 | Baja |
| I-19 | Color de series de gráfica: acento (interactivo) y sin familia de identidad | — | Media |
| I-20 | Galería sin E2E ni cobertura de shell/plantillas | — | Media |
| I-21 | Estados de vocabulario: "en cola" gira; "revertido" gris pero cuenta como atención | 2 | Baja |

### 2.2 Detalle

**I-1. `Card` existe y no se usa.** `components/ui/Card.tsx` (título 14 semibold, cabecera
`px-5 pt-4 pb-3`, cuerpo `p-5`, pie `bg-bg-sunken/60`) no se importa en ningún fichero de `features/`,
`app/` ni `nodes/`. En su lugar hay **69 apariciones** de `rounded-card … bg-surface` en 39 ficheros y
68 `shadow-raised` en features. Además hay tres constantes privadas de "superficie":
`features/app/settings/panel.ts:2` (`PANEL`), `features/settings/NotificationSettings.tsx:33`
(`SURFACE`) y la propia de `features/settings/SettingsForm.tsx:88` (`SettingsFormCard`, que sí tiene
sentido como componente de formulario). El relleno interior de esas superficies varía: `px-4 py-3.5`
(`backups/StorageUsageBar.tsx:68`, `fleet/FleetAttention.tsx:316`, `overview/NeedsAttention.tsx:333`,
`server/MonitorCard.tsx:142`, `databases/EnginesStrip.tsx:80`), `px-5 py-2`
(`settings/SecuritySettings.tsx:76`, `settings/AboutSettings.tsx:172`,
`settings/github/GitHubIntegration.tsx:59`), `p-5` (`settings/TwoFactorSection.tsx:534`),
`p-4` (`databases/SqlConsole.tsx:60`, `overview/MachineCharts.tsx:125`), `px-4 py-1`
(`databases/DatabasePage.tsx:39`) y `px-4 py-3` (`fleet/FleetPage.tsx:196`): **seis rellenos** para
"un panel". Otros dos componentes del kit tampoco tienen ningún uso fuera de la galería: `Progress` y
`SkeletonText`; `Popover` y `Tabs` tienen uno cada uno (`app/Topbar.tsx`, `domains/DomainsPage.tsx`).

**I-2. No existe un componente de aviso.** Un aviso (banner/callout) es un bloque tintado con icono y
texto. Hoy es una receta de clases repetida: `border-fail/30` ×10, `border-warn/40` ×9,
`border-ok/30` ×5, `border-ok/40` ×4, `border-warn/50` ×2, `border-warn/30` ×2, `border-fail/40`,
`border-accent/40`; fondos `bg-fail-soft` (15), `bg-warn-soft` (17), `bg-ok-soft` (8; cuentas de todo `src/`,
con las de los propios componentes del kit) más variantes `/40` y `/50`; radio `rounded-control` en unos y `rounded-card` en otros; rellenos
`p-3`, `p-4`, `px-3 py-2.5`, `px-4 py-3`, `px-4 py-3.5`. Salen **21 firmas de clase distintas** para
unos 32 avisos. Ejemplos: `settings/servers/ServersSettings.tsx:98`, `central/CentralLockedNotice.tsx:66`,
`backups/MisplacedBackupsNotice.tsx:24`, `domains/SiteConfigPage.tsx:44,69,87,104`,
`services/UnitEditor.tsx:21,37`, `app/settings/LimitsSection.tsx:46`,
`app/settings/ReleasesSection.tsx:111`, `app/settings/HealthCheckForm.tsx:202`,
`app/settings/RetentionForm.tsx:130`, `app/settings/PreviewsSection.tsx:94`,
`app/settings/MigrationPlanView.tsx:88`, `app/settings/ZeroDowntimePanel.tsx:92`,
`databases/CreateUserDialog.tsx:101`, `settings/CreateTokenDialog.tsx:77`,
`new-app/InspectionReadout.tsx:45`, `new-app/SourceStep.tsx:88`, `auth/LoginForm.tsx:165,183`,
`auth/ElevateDialog.tsx:121`, `settings/SessionsSection.tsx:215`, `app/AppOverview.tsx:237`,
`domains/DnsVerdict.tsx:83`. Cinco componentes con nombre de aviso lo reimplementan:
`nodes/NodeNotice.tsx:8` (constante `NOTICE`), `app/RenameNotice.tsx:45`, `domains/JobBanner.tsx`,
`central/CentralLockedNotice.tsx`, `backups/MisplacedBackupsNotice.tsx`, y `ErrorBlock`
(`components/page/QueryState.tsx:63`, `border-fail/30 bg-fail-soft/50`). 55 `role="alert|status|note"`
puestos a mano en 38 ficheros. Consecuencia técnica: los grises translúcidos y los fondos con alfa
**no pasan por `tokens.test.ts`**, así que el contraste del texto sobre esas superficies no está
garantizado (el fondo real depende de lo que haya debajo).

**I-3. Tres implementaciones del progreso de un job.** El mismo elemento (`Spinner` ámbar + verbo +
paso actual en `code` mono) escrito tres veces con marcos distintos: `features/app/AppLayout.tsx:82-112`
(`JobStatus`: `rounded-control px-3 py-2`, sin `role`), `features/domains/JobBanner.tsx:20-75`
(mismo marco, con `role="status"`, y su éxito es otro marco `border-ok/30 bg-ok-soft/40`) y
`features/new-app/DeployStep.tsx:114-130` (`rounded-card px-4 py-3`, título 14). El fallo se resuelve
igual en los tres (`ErrorBlock` + `IconButton` de descartar), pero cada uno lo reescribe.

**I-4. Mapas de tono duplicados.** `components/ui/StatusPill.tsx:35` **exporta** `TONE_TEXT`; aun así
lo redeclaran `features/apps/AppsTable.tsx:19`, `features/app/AppOverview.tsx:42`,
`features/app/metrics/MetricsTab.tsx:32`, `features/app/diagnose/DiagnoseTab.tsx:25-27` (y añade
`TONE_RAIL` y `TONE_SOFT` propios) y `features/domains/CertificateStatus.tsx:5-10`; `Chart.tsx:101`
tiene `TONE_TOKENS`. Es la regla 3 aplicada a la UI. Lo mismo con el marcador de celda vacía: `function Nothing` está
redeclarada en `features/apps/AppsTable.tsx:22`, `features/services/ServicesTable.tsx:16`,
`features/cron/CronJobsTable.tsx:14` (las tres con el motivo en `sr-only`) y
`features/activity/ActivityTable.tsx:16` (sin motivo: un `-` mudo), y hay 6 `<span className="text-fg-faint">-</span>`
sueltos en `app/deployments/DeploymentsTab.tsx`, `databases/DatabasesTable.tsx`,
`app/settings/WebhookSection.tsx` y la propia `ActivityTable.tsx`.

**I-5. Dos vocabularios de icono para el mismo estado.** `StatusGlyph` define 7 siluetas (punto,
arco, triángulo, cruz, anillo, cuadrado, interrogante) con 33 usos directos y 40 en `StatusPill`. Pero
los mismos significados se dibujan además con iconos de lucide: éxito con `CircleCheck` (12),
`Check` (4), `ShieldCheck` (4); advertencia con `TriangleAlert` (18), `CircleAlert` (6),
`ShieldAlert`, `CircleMinus`, `CircleDashed`, `LockKeyhole`; fallo con `X`
(`app/AppOverview.tsx:133`, `domains/DnsVerdict.tsx:26`), con `CircleAlert` (`Field`, `ErrorBlock`,
`domains/SiteConfigPage.tsx:46`) o con `StatusGlyph state="failed"` (`app/AppOverview.tsx:239`). El
mismo "todo bien" es `ShieldCheck` en `domains/SiteConfigPage.tsx:71` y `services/UnitEditor.tsx:39`, y
**también es el icono de navegación de Dominios** (`app/nav.ts`): un icono que significa "seguridad",
"correcto" y "sección". Otras colisiones: Servicios usa `Cog` y Ajustes usa `Settings` (dos
engranajes); la información usa `Info` en `text-fg-muted` (5 sitios) y en `text-fg-faint`
(`app/settings/ZeroDowntimePanel.tsx:79`).

**I-6. Estado por color solo o por canal incompleto** (regla D9: "nada depende solo del color").

- `Badge` para estado, contra su propia docstring ("Para estado, usa StatusPill"):
  `fleet/FleetPage.tsx:99` (`tone="warn"`), `app/environment/EnvironmentTab.tsx:236` (`tone="fail"`).
- Líneas de error solo en rojo, con texto pero sin forma, frente al patrón de `Field` (icono +
  texto): `auth/LoginForm.tsx:166,184`, `auth/ElevateDialog.tsx:122`,
  `settings/SessionsSection.tsx:216`, `cron/CronJobDialog.tsx:53`, `settings/CreateTokenDialog.tsx:66`,
  `settings/EmailChannel.tsx:333` (`<p className="text-13 text-fail">`).
- `Meter` / `ResourceMeter` (`components/ui/Progress.tsx:66-70`): el relleno pasa de neutro a ámbar
  (≥ 75 %) y a rojo (≥ 90 %) con solo el porcentaje impreso: el **nivel** (aviso/fallo) se transmite
  solo por color; el número está, la palabra y la forma no.
- `domains/ConfigEditor.tsx:62`: la línea con error se marca con `bg-fail-soft font-medium text-fail`
  en el margen de números, sin glifo (el mensaje está en otro sitio).
- `services/ServiceDetailPage.tsx:108`: indicador de flujo con un punto `bg-ok/bg-warn/bg-fg-faint`
  crudo más una palabra; funciona, pero usa un punto propio en vez de `StatusGlyph`.
- Verificación cuantitativa con la herramienta de dataviz: los cuatro colores de estado, tratados
  como paleta categórica, tienen ΔE (OKLab ×100) **3,3 (claro) y 3,4 (oscuro) bajo deuteranopía**
  entre rojo y verde/ámbar, muy por debajo de cualquier umbral. No es un defecto (el estado nunca va
  solo en color), pero demuestra que **la forma y la palabra son requisito funcional, no adorno**.

**I-7. Encabezados con cuatro estilos para el mismo papel.** Encabezados a mano (`<h1>`–`<h4>`) en
features y nodes: 40 (más 2 en `app/` y `components/`), de los cuales con clase distinta: `h3 text-14 font-medium` ×10, `h3 text-13
font-medium` ×9, `h2 title text-18` ×6 (pasos del asistente: `new-app/SourceStep.tsx:234`,
`ReviewStep.tsx:140`, `RecipeReview.tsx:140`, `DeployStep.tsx:173`, `ImportReview.tsx:149`; y
`app/deployments/DeploymentPage.tsx:301`), `h3 text-14 font-semibold` (`settings/channelParts.tsx:163`),
`h2 text-14 font-semibold` (`backups/MisplacedBackupsNotice.tsx:28`), `h2 text-14 font-medium`
(`nodes/capability.tsx:119`), `h3 text-12 font-medium text-fg-muted` (`server/ServerPage.tsx:64`),
`h4 font-medium` ×2 (`app/settings/WebhookSection.tsx:78,84`). `Section` ya define nivel 2 =
`title text-16` y nivel 3 = `title text-14` (59 usos), `Card` usa `text-14 font-semibold` sin `title`, y
`SettingsSection` usa `title text-16`. El papel "título de subsección dentro de un panel" tiene
**cuatro estilos**: 14 medium, 13 medium, 14 semibold, 12 muted.

**I-8. Cabecera de página.**

- Acción primaria de creación: en `PageHeader actions` (botón `md`) en 6 páginas
  (`apps/AppsPage.tsx:83`, `cron/CronPage.tsx:51`, `services/ServicesPage.tsx:59`,
  `backups/BackupsPage.tsx:62`, `overview/OverviewPage.tsx:124`, `fleet/FleetPage.tsx:229`); en la
  cabecera de una `Section` como botón `sm` en Bases de datos (`databases/DatabasesPage.tsx:56`); sin
  acción en Actividad, Dominios y Servidor.
- `PageHeader` cierra con `mb-8` y no tiene hueco para pestañas ni avisos; tres páginas lo deshacen con
  márgenes negativos: `routes/_console/settings.tsx:18`, `features/app/AppLayout.tsx:166`,
  `features/services/ServiceDetailPage.tsx:215` (`-mt-4 mb-8`) y `features/activity/ActivityPage.tsx:141`
  (`-mt-4 mb-6`). En total 21 márgenes negativos en `features/`, `routes/`, `app/`, `nodes/`.
- El hueco `description` sirve para cosas distintas: hechos (`AppLayout.tsx`), un `StatusPill`
  (`ServiceDetailPage.tsx:168`), una frase (`DatabasePage.tsx:72`), hechos (`SiteConfigPage.tsx:278`).
- Los identificadores de sistema (dominio, unit, base de datos, site) son títulos de página en Mona
  Sans; `PageHeader` no tiene opción `mono`.
- Secciones que repiten el título de la página: `backups/BackupsPage.tsx:79` (`Section title="Backups"`
  bajo el `h1` "Backups"), `databases/DatabasesPage.tsx:40`.
- Espaciado entre secciones: `<Sections>` (32 px) en 13 sitios y `flex flex-col gap-8` escrito a mano
  en 8 (`backups/BackupsPage.tsx:75`, `databases/DatabasesPage.tsx:36`, `databases/DatabasePage.tsx:84`,
  `server/ServerPage.tsx:462`, `app/diagnose/DiagnoseTab.tsx:214`, `app/AppOverview.tsx:517`,
  `services/ServiceDetailPage.tsx:234`, `app/environment/EnvironmentTab.tsx:343`), más `gap-6` en el
  asistente y `DeploymentPage.tsx:296`.

**I-9. Estados vacíos.** 41 `EmptyState` (28 ficheros) frente a 8 reconstrucciones a mano:
cajas discontinuas en `new-app/GitHubSource.tsx:208`, `new-app/RecipeGallery.tsx:108`,
`settings/EmailChannel.tsx:112`, `app/deployments/DeploymentPage.tsx:202`,
`app/deployments/ReleasesSection.tsx:266`; párrafos sueltos en `cron/CronRunsDrawer.tsx:47`,
`app/environment/PasteDialog.tsx:153`; y una tarjeta con glifo "todo en orden" en
`overview/NeedsAttention.tsx:333-338`. El componente se adapta con parches de `className`:
`border-0 py-8` ×12 (dentro de una `DataTable`), `py-16` ×7 (página entera), `py-10`, `py-12`;
`level={2}` (página) en 14 usos y nivel 3 (sección) en los otros 17. Falta una variante `table` y otra `page`.

**I-10. Tablas.** 32 usos de `DataTable` (26 tablas reales y 6 esqueletos de carga). Posición de la columna de
estado: **primera** en Aplicaciones (`apps/AppsTable.tsx:121`), Servicios (`services/ServicesTable.tsx:46`),
Despliegues recientes (`overview/RecentDeployments.tsx:41`), entregas de webhook
(`app/settings/WebhookSection.tsx:107`), Actividad (`result`) y Cron (`enabled`); **segunda** en
Despliegues (`app/deployments/DeploymentsTab.tsx:61`, `id, status`) y Flota (`server, reachability`);
**tercera** en Servidores (`name, address, status`), Tokens (`name, scope, state`) y Sites
(`name, server, state`); **cuarta** en Certificados (`name, names, expires, state`). Como `DataTable`
convierte la primera columna en el botón primario de la fila cuando hay `onRowActivate`, el patrón "estado
primero" choca con el "nombre primero" que sigue el resto. Alineación numérica correcta en 18 de 22
columnas de cantidad; excepciones: `services/ServicesTable.tsx` (`uptime`, a la izquierda),
`fleet/FleetPage.tsx` (`apps`, `units` a la izquierda; `cpu`, `memory`, `disk` alineadas pero sin `mono`).
Densidad: `compact` (36 px) en 7 tablas de historial y `comfortable` (44 px) en el resto, sin regla
escrita. Barras de filtro: 7 con `role="search"`, anchos del buscador `sm:w-56` (Actividad), `sm:w-64`
(Sites, Certificados), `sm:w-72` (Aplicaciones), `sm:w-80` (Cron, Servicios); alineación `items-end` en 3,
`items-center` en 3 y `gap-x-4 gap-y-2` en Servicios. Acciones de fila: 19 tablas con `rowActions`, todas
con menú `MoreHorizontal` (33 usos): esto sí es consistente.

**I-11. Botones.** 257 `<Button` en features: `secondary md` 98 (defecto), `primary md` 73,
`ghost md` 17, `danger md` 15, `secondary sm` 33, `ghost sm` 14, `primary sm` 4, `primary lg` 3.
La distribución es buena; lo que falta son reglas de rol:

- Botón terminal de un diálogo ("Hecho"/"Cerrar"): ≈ 21 secundarios frente a ≈ 9 primarios
  (`databases/CreateUserDialog.tsx:73` es primario; `databases/GrantDialog.tsx:82` es secundario).
- Guardar: `SettingsFormCard` pasa a `primary` solo cuando hay cambios
  (`settings/SettingsForm.tsx:110`); `services/UnitEditor.tsx:154`, `app/settings/LimitsSection.tsx:252`,
  `RetentionForm.tsx:124`, `HealthCheckForm.tsx:232` y `PreviewsSection.tsx:389` son `primary` fijo con
  `disabled={!dirty}`.
- `primary` con `sm` (fuera de tarjetas): `databases/DatabasesPage.tsx:56` es el único de nivel de
  sección; los demás (`server/MonitorCard.tsx:84`, `settings/channelParts.tsx:187`,
  `new-app/RecipeGallery.tsx:69`) están dentro de una tarjeta.
- Botones de envío o primarios deshabilitados por campo vacío o no válido: ≈ 22 (script sobre los
  `<Button>` con `disabled={…}` sin contar `!dirty`, `!matches`, cargando), p. ej.
  `services/CreateServiceDialog.tsx:139` (`!valid`), `cron/CronJobDialog.tsx:148` (`!valid`),
  `backups/DestinationDialog.tsx:127` (`!canSubmit`), `databases/CreateDatabaseDialog.tsx:70`,
  `settings/CreateTokenDialog.tsx:197`, `auth/ElevateDialog.tsx:99`. Primer lo desaconseja ("valida al
  enviar y guía con mensajes"); Supabase deshabilita solo por `!isDirty`.

**I-12. Espaciado.** D8 declara `4-8-12-16-20-24-32-40-48-64`. El código usa pasos de 2 px: de las
utilidades de padding, margen y gap, `0.5` (2 px) 149, `1.5` (6 px) 175, `2.5` (10 px) 97, `3.5`
(14 px) 19: **440** usos (311 en features). `gap-1.5` 88 y `gap-2.5` 38 en features; `mt-0.5` 70,
`py-2.5` 28, `py-3.5` 16. Valores fuera de toda escala: `7` (28 px: `mb-7` en
`auth/LoginPage.tsx:43` y `central/CentralLockScreen.tsx:47`; `pl-7` ×6 en
`app/settings/ReleasesSection.tsx`), `15`, `6.5`. La escala D8 describe la **maquetación**
(entre secciones y páginas); el **interior de los componentes** ya vive en una escala de 2 px que
nadie ha escrito.

**I-13. Valores arbitrarios de Tailwind.** Fuera de `dev/` hay 152 valores arbitrarios de dimensión
(`rounded-[` 54, `max-w-[` 48, `h-[` 15, `max-h-[` 8, `shadow-[` 6, `w-[` 4, `pt-[` 4, `mt-[` 3, `z-[` 2,
`tracking-[` 2, `text-[` 2, y uno de `top`, `pl`, `left`, `bg`) y 35 `grid-cols-[…]` (estructurales). No cuento
`has-[`, `data-[` ni `transition-[…]`, que son variantes legítimas. Detalle:

- `rounded-[4px]` **52** (41 en features, 7 en components, 4 en app) + `rounded-[5px]` (el `Badge`,
  `components/ui/Badge.tsx`) y `rounded-[2px]`: el sistema usa de hecho un cuarto radio (4 px: teclas,
  chips, foco de migas, enlaces) que no está en los tokens (6/10/999).
- `grid-cols-[...]` 35 (27 en features): plantillas de dos columnas donde cada página inventa el ancho de
  la etiqueta (4,5 / 5 / 5,5 / 6 / 6,5 / 7 / 13 rem).
- `max-w-[...]` 48 usos y ≈ 27 medidas distintas: `72ch`, `68ch` ×3, `64ch`, `60ch` ×3, `52ch` ×2, `46rem`, `32ch`,
  `30ch`, `28rem`, `26rem` ×2, `24rem`, `22.5rem`, `20rem`, `18rem`, `13rem`, `10rem`, `9rem`, `8rem`,
  `460px`, `480px` ×2. Y los `max-w-` con nombre (`md`, `2xl`, `4xl`, `6xl`) que ya existen conviven.
- Alturas fijas (`h-[`, `max-h-[`: 23): `h-[26rem]` ×5, `h-[34rem]` ×4, `h-[24rem]` ×2, `h-[max(24rem,calc(100dvh-22rem))]`
  (`app/logs/LogsTab.tsx:127`) y esqueletos que imitan un control con números mágicos:
  `h-[1.3125rem]` (`backups/StorageUsageBar.tsx:48`), `h-[1.875rem]` y `h-[2.375rem]`
  (`settings/EmailChannel.tsx:179,189`), `mt-[1.625rem]` ×3 para alinear un botón con una etiqueta
  (`new-app/PersistentPathsField.tsx:56`, `new-app/EnvironmentFields.tsx:87,130`).
- Anchos de diálogo: `Dialog` 400/520/720/1280, `ConfirmDialog` 440
  (`components/ui/ConfirmDialog.tsx:89`), `RestoreBackupDialog` y `BrowseDestinationDialog` 480
  (`backups/RestoreBackupDialog.tsx:68`, `backups/BrowseDestinationDialog.tsx:93`),
  `DatabaseBackups` 460 (`databases/DatabaseBackups.tsx:75`), paleta 640, drawer 480/720: **ocho** anchos.
- Anchos de página: `max-w-[1600px]` (`app/Shell.tsx:161`), `max-w-6xl` en ajustes y
  `app/settings/SettingsTab.tsx:57` y `app/environment/EnvironmentTab.tsx:343`, `max-w-[46rem]` en el
  asistente, `max-w-2xl` en `settings/github/GitHubCallback.tsx:42`, `max-w-3xl` en `routes/_console.tsx:23`,
  `max-w-[26rem]` en la pantalla de bloqueo.
- El lint de hex existe; para todo lo anterior no hay ninguno.

**I-14. Etiquetas y confirmaciones reimplementadas.** 13 `<label>` a mano frente a 117 `Field`:
`backups/RestoreBackupDialog.tsx:86,113`, `backups/BrowseDestinationDialog.tsx:114,135`,
`backups/ScheduleDialog.tsx:80,95`, `app/RollbackDialog.tsx:41`, `domains/CreateSiteDialog.tsx:223`,
`new-app/ImportFile.tsx:44`, `new-app/ReviewStep.tsx:48`, con tres estilos de etiqueta (`text-13
font-medium text-fg`, `text-13 text-fg-muted`, `text-12 text-fg-muted`). Tres diálogos de "teclea el
nombre" reimplementan `AlertDialog.Popup` en lugar de usar `ConfirmDialog`: `RestoreBackupDialog`,
`BrowseDestinationDialog`, `DatabaseBackups`. El error bajo un campo es icono + `text-13 text-fail` en
`Field` y solo texto en otros sitios (I-6).

**I-15. Fricción al destruir sin niveles.** `ConfirmDialog` (`confirmText: string` obligatorio) se usa
18 veces en 17 ficheros, todas "teclea el nombre": eliminar una aplicación, pero también quitar un job de
cron (`cron/CronJobRowActions.tsx`), una programación de backup (`backups/SchedulesSection.tsx`), un
destino, revocar un token o eliminar un site. La confirmación **simple** existe compuesta a mano con
`Dialog` + botón `danger` (`app/AppActions.tsx:126-150` para parar una app,
`services/ServiceDetailPage.tsx:268`, `new-app/NewAppWizard.tsx:526`,
`domains/SiteConfigPage.tsx:401`), sin mostrar el fallo verbatim como hace `ConfirmDialog`. Y no existe
el nivel "sin confirmación, con deshacer". El feedback posterior es un `toast.success` en 85 sitios
(117 llamadas a `toast.*` en total: 20 `error`, 13 `info`, 4 `warning`).

**I-16. Carga y microcopia.** La carga está bien resuelta (151 `Skeleton`, 8 `Spinner` todos en
`text-warn`, 33 `aria-busy`, 17 `QueryState`, 139 `ErrorBlock`). Detalles: 5 cadenas con `...` frente a 11
con `…` en el catálogo inglés (`i18n/en/backups.ts:21,23`, `services.ts:135,136`, `databases.ts:119`,
p. ej. "Loading applications..."). El tono es uniforme (0 "Please/Oops/Sorry/Successfully", 127 "Could not …").

**I-17. Mono.** `<Mono>` se usa 9 veces; la utilidad `mono` puesta a mano, 132. Con tamaños `mono
text-12` ×45, `text-13` ×18, `text-14` ×3, más `text-[0.92em]` en `Mono` y `RelativeTime`. `translate="no"`
se repite a mano 146 veces.

**I-18. Fuera de tokens.** `z-[60]` (`components/ui/Toast.tsx:98`) y `z-[70]` (`app/Shell.tsx:34`), y
las capas 0/10/30/40/50/60/70 sin documentar. Sombras y colores crudos que el lint del hex no ve:
`shadow-[inset_0_1px_0_rgb(255_255_255/0.16)]` y `…/0.14` (`components/ui/Button.tsx:18,27`),
`shadow-[0_1px_2px_rgb(0_0_0/0.25)]` (`components/ui/Switch.tsx:53`),
`bg-[color-mix(in_oklab,var(--fail-strong)_86%,black)]` (`Button.tsx:28`). Repetición: 76
`focus-visible:outline-2` con 5 variantes de `outline-offset`, cuando `app.css` ya define el foco global.

**I-19. Color de las series.** `Chart.tsx:100-101`: serie 1 = `--accent` (el color de "lo interactivo"),
series 2 y 3 = `--text-muted` y `--text-faint` con trazos discontinuo y punteado. Es una decisión limpia
para ≤ 3 series pero no hay familia de identidad para nada más, y los tokens `--ansi-*` (los únicos
"cromáticos" que no son estado ni acento) **no sirven**: validados con la herramienta, tres ANSI dan
ΔE 3,6 (claro) y 1,4 (oscuro) bajo deuteranopía, y el cian queda bajo el suelo de croma.

**I-20. Galería.** Ver 1.4: sin E2E, sin shell ni plantillas ni avisos.

**I-21. Vocabulario de estado.** `components/page/status.ts`: "en cola" (`queued`, `pending`) se dibuja con
el estado `deploying` (arco ámbar que **gira**): parece "en curso" cuando no se está haciendo nada.
"Revertido" (`rolled_back`) se dibuja como `stopped` (gris) pero con `attention: true`: gris en pantalla,
cuenta en "Necesita atención".

### 2.3 Lo que está bien (conviene conservarlo)

`StatusPill` (3 canales), `QueryState`/`ErrorBlock` (palabras del sistema verbatim con el arreglo
arriba), formato de números y fechas centralizado (`lib/format.ts`: solo `cron/data.ts:114` y
`app/metrics/ranges.ts:92,101` crean `Intl.DateTimeFormat` directamente), `RelativeTime` (49 usos),
menús de fila en todas las tablas, catálogos i18n tipados, foco al `h1`, regiones vivas por urgencia,
`DataTable` con orden y teclado, `Skeleton` con la forma del contenido, la CLS vigilada, el contraste
de tokens probado, y la elevación por luminancia en oscuro.

---

## 3. Investigación: qué hacen los mejores sistemas y qué se puede adoptar

**Método y límites.** Documentación oficial leída el 2026-09-29. Las páginas de Carbon 11 sobre
tablas, notificaciones y estados se truncan al leerlas con la herramienta; para Carbon uso el MDX de
notificaciones en GitHub, la versión 10 del patrón de estados y lo que el sitio devuelve en búsqueda
(se marca cuando es así). De Railway, Render y Coolify no existe un sistema de diseño público: solo cito
lo que su documentación dice de los estados; **no encontré** principios de diseño publicados de Railway
(la búsqueda no devolvió nada útil). Las cifras de color propias las calculé con la herramienta de
dataviz (anexo B).

### 3.1 Vercel Geist

- Diez escalas de color (`backgrounds`, `gray`, `gray-alpha`, `blue`, `red`, `amber`, `green`, `teal`,
  `purple`, `pink`), cada una de diez pasos con papel fijo: 1-3 fondos de componente (defecto, hover,
  activo), 4-6 bordes (defecto, hover, activo), 7-8 fondos de alto contraste, 9-10 texto e iconos
  (secundario, primario). Regla explícita: si el fondo por defecto de un componente es el de la página,
  usa el paso 1 como hover y el 2 como activo. https://vercel.com/geist/colors
- Materiales: superficies (`base` y `small`, radio 6; `medium` y `large`, radio 12) y flotantes (tooltip 6,
  menú 12, modal 12, pantalla completa 16): a más elevación, más sombra y más radio.
  https://vercel.com/geist/materials
- Tipografía por **rol**: `heading` (72 a 14), `copy` (24 a 13, más interlineado, para varias líneas),
  `label` (20 a 12, una línea, prioriza la densidad), `button` (16/14/12), con variantes `-mono` y
  modificadores Subtle/Strong. https://vercel.com/geist/typography
- **Adoptable**: nombrar la tipografía por rol y no solo por tamaño (la tabla 4.1.3); los tres estados
  interactivos (defecto, hover, activo) como pasos de superficie (Noust ya tiene `surface`,
  `surface-hover`, `surface-active`). **No adoptado**: escalar el radio con la elevación (Noust usa 10 px
  en tarjetas y overlays; una escala menos es una regla menos).

### 3.2 Linear

- "Structure should be felt, not seen" y "Don't compete for attention you haven't earned": la barra
  lateral se volvió "unos tonos más apagada" para que mande el contenido, se suavizó el contraste de
  bordes, se redujeron separadores e iconos y se compactaron las pestañas.
  https://linear.app/now/behind-the-latest-design-refresh
- El rediseño anterior pasó a LCH (perceptualmente uniforme) y a un tema generado por **tres
  variables** (base, acento, contraste) en vez de 98; "limitar cuánto croma entra en los cálculos"
  para mantener la neutralidad; alineación de etiquetas, iconos y botones que "no se ve, se siente";
  texto e iconos neutros más contrastados. https://linear.app/now/how-we-redesigned-the-linear-ui
- **Adoptable**: el cromo más apagado que el contenido (regla P4); un solo acento por vista. Noust ya
  cumple el resto (superficies sin croma, sidebar en `fg-muted` con iconos en `fg-faint`).

### 3.3 GitHub Primer (la fuente más útil para Noust)

- Color por **rol × función × énfasis**: `fgColor`/`bgColor`/`borderColor` × accent, success, danger,
  attention, done… × default, emphasis, muted, inset, subtle (`bgColor-success-emphasis` frente a
  `-muted`). Noust ya sigue esa forma: `--ok`/`--ok-soft` son fg/muted y `--fail-strong` es emphasis.
  https://primer.style/product/primitives/color/
- Tamaños de control 28/32/40 px, escala de 4 px, breakpoints 320/544/768/1012/1280/1400; con puntero
  grueso los huecos de las pilas suben de 8 a 16 px. https://primer.style/product/primitives/size/
- **Notificaciones**: `Banner` (página o sección, arriba del cuerpo, cerca de donde ocurrió la
  interacción), `InlineMessage` (validación de campo y mensajes junto a la acción), `Dialog` (fallos
  críticos que requieren intervención). **Sin toasts** por accesibilidad. Seis severidades: info,
  warning, critical, success ("con moderación, solo cuando no es evidente por el resto de la UI"),
  unavailable, upsell. Los mensajes de sistema no se descartan hasta que se resuelve el problema.
  https://primer.style/product/ui-patterns/notification-messaging/
- **Experiencias degradadas**: "no ocultes ni minimices que algo va mal"; página de error solo si
  falla la experiencia primaria; banner `warning` global con enlace al estado; en un elemento pequeño,
  sustituir por mensaje con icono de aviso; en un área grande, `Blankslate` con icono de alerta en color
  **apagado**; nunca desactivar controles por disponibilidad (un tooltip necesita un elemento
  focalizable); ocultar contadores sin dato; como mucho 5 mensajes de caída por página.
  https://primer.style/product/ui-patterns/degraded-experiences/
- **Carga**: menos de 1 s, nada ("un parpadeo distrae"); 1-3 s indeterminado; 3-10 s determinado si se
  puede; más de 10 s determinado y tarea de fondo; mostrar cada elemento de una colección en cuanto llega;
  `aria-busy` y `role="status"`. https://primer.style/product/ui-patterns/loading/
- **Vacíos**: gráfico, texto primario (título), secundario opcional, acción primaria, acción secundaria
  (enlace). Tono acogedor si la función no se usó, neutro si está vacía por su naturaleza, específico si es
  un error ("No se pudo enviar el formulario. Faltaban campos", no "Hubo un problema").
  https://primer.style/product/ui-patterns/empty-states/
- **Formularios**: apilado vertical; etiquetas de 3 palabras como máximo, en sentence case; validar al
  enviar y solo después de la primera validación en línea; un campo inválido siempre lleva un mensaje que
  explica **por qué**; la ayuda se oculta cuando hay error; no deshabilitar el botón de enviar.
  https://primer.style/product/ui-patterns/forms/
- **Guardado**: explícito por defecto; automático solo para controles imperativos (interruptor,
  segmentado, select único); "nunca mezcles patrones de guardado en un formulario"; confirmar con mensaje
  en línea si no recarga y con banner si recarga. https://primer.style/product/ui-patterns/saving/
- **Borrar**: "una confirmación interrumpe a todos y se descarta por costumbre; un deshacer protege
  sin interrumpir". "**Ajusta la fricción al radio de la explosión**: escribir para confirmar cuando el
  impacto es de toda la organización o el repositorio, un diálogo simple para un recurso y nada para lo
  reversible." Botón de peligro con el verbo concreto ("Delete repository"), nunca "Sí"; no añadir un
  banner de éxito por defecto pero **anunciar siempre** a lectores de pantalla; devolver el foco a un
  padre estable. https://primer.style/product/scenario-patterns/delete
- **Botones**: una sola primaria por página, siempre que se pueda, al final del grupo; texto corto y en
  sentence case. https://primer.style/product/components/button/guidelines/
- **Tabla**: orden inicial intuitivo (lo más reciente primero); números a la derecha con cifras
  tabulares; unas 20 filas por página; truncar es el último recurso (con tooltip); la columna de acciones
  no tiene cabecera visible; vacío = `Blankslate` en lugar de la tabla; **celdas vacías en blanco**; no
  usar tabla para datos jerárquicos, celdas casi siempre vacías o texto largo.
  https://primer.style/product/components/data-table/guidelines/
- **Sudo mode de GitHub**: 2 h de sesión que se renueva con cada acción sensible; se pide "aunque ya
  hayas iniciado sesión"; varios métodos (passkey, llave, app, TOTP, contraseña). Noust: 10 min, TOTP o
  token maestro. https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/sudo-mode

### 3.4 Stripe

- Sistema de color en CIELAB: "cada color tiene el mismo valor de contraste a un nivel dado", con lo que
  dos colores separados al menos **5 niveles** cumplen texto pequeño (4,5:1) y al menos **4 niveles**
  iconos y texto grande (3:1): el contraste es propiedad de la estructura.
  https://stripe.com/blog/accessible-color-systems
- Patrones de Stripe Apps: layout (página completa, disposición de gráficas, listas, controles de
  filtro), estado (comunicar estado, vacío, carga, progreso por pasos, pantallas de espera), acciones
  (enlace de vuelta, botones de acción); "el estilo personalizado se limita a propósito" para conservar la
  coherencia y la accesibilidad. https://docs.stripe.com/stripe-apps/patterns ·
  https://docs.stripe.com/stripe-apps/design
- **Adoptable**: los **controles de filtro** como patrón único (chips para filas de tabla, `Select` para el
  rango de una gráfica) → `FilterBar`; y limitar la personalización de estilo por feature (regla P6).

### 3.5 Atlassian

- Mensajes: **Banner** solo para mensajes críticos de sistema (pérdida de datos o de funcionalidad), arriba
  y empujando el contenido; **Flag** para confirmaciones y acuses con poca interacción; **Section
  message** para algo ocurrido en una sección, sobre el área afectada; **Inline message** para acción
  requerida; **Empty state**; **Modal dialog** para una tarea corta. Severidades con forma y color: info
  (círculo con "i"), éxito (marca), aviso (triángulo), error (rombo), descubrimiento.
  https://atlassian.design/foundations/content/designing-messages
- Vacíos: dos tipos, *empty state* (el usuario vació o terminó algo) y *blank slate* (función nunca
  usada). Título informativo en sentence case sin puntuación (salvo pregunta), 1-2 frases, CTA con verbo
  imperativo de 1-2 palabras que complemente el título, evitar varios CTA, no dejar en un callejón sin
  salida. https://atlassian.design/foundations/content/designing-messages/empty-state
- Errores: razón + problema + cómo actuar (+ consecuencia); **"si no conoces la razón, no te la inventes: di
  que algo ha ido mal y ofrece una solución"**; 1-2 frases; título de 3-4 palabras; sin "sorry" ni
  "please"; CTA imperativo. https://atlassian.design/foundations/content/designing-messages/error-messages
- Espaciado con base de 4 px y tokens de superficie y elevación.
  https://atlassian.design/foundations/tokens/design-tokens/

### 3.6 IBM Carbon (y Dynatrace para estado)

- **Estados**: tres niveles de atención (alta: requiere acción inmediata; media: feedback; baja:
  informativo o desconocido); "más de 5 o 6 indicadores empieza a cargar al usuario"; *icono* cuando hay
  espacio y el contenido necesita la máxima atención, *forma* cuando hay que escanear muchos datos en poco
  espacio, *texto* cuando no hace falta acción; al consolidar varios estados, el color del de mayor
  atención representa al grupo. https://v10.carbondesignsystem.com/patterns/status-indicator-pattern/
- Dynatrace: cinco niveles (ideal, good, neutral, warning, critical) con forma (círculo, triángulo, rombo)
  y símbolo; "inclúyelos siempre al comunicar avisos y críticos"; "sobre-comunicar estados abruma";
  indicadores de sección "junto al encabezado". https://developer.dynatrace.com/design/patterns/status-and-health/
- **Notificaciones**: *inline* (persisten hasta que se descartan o se resuelve, cerca de lo relacionado, en
  el pie del formulario), *toast* (pueden cerrarse solos a los 5 s, llevan botón de cierre porque cubren
  contenido, se apilan), *actionable* (persisten), *callout* (no descartable, carga con la página); título
  corto sin punto, cuerpo de 1-2 frases, acción de 1-2 palabras.
  https://raw.githubusercontent.com/carbon-design-system/carbon-website/main/src/pages/components/notification/usage.mdx
- **Tabla**: varios tamaños de fila (la guía de estilo da compacta 24, corta 32, defecto 48 y alta 64; la de uso
  habla de cinco, de extra grande a extra pequeña),
  ordenación con flecha solo en la columna ordenada, barra de acciones por lote (que desactiva las de fila),
  "si se espera carga, usa esqueletos en vez de spinners", cabecera a dos líneas y truncada con tooltip.
  https://carbondesignsystem.com/components/data-table/usage/ (lectura parcial) ·
  https://carbondesignsystem.com/components/data-table/style (por búsqueda)
- **Movimiento**: *productivo* (sutil, se aparta: microinteracciones, revelar información, renderizar tablas
  y visualizaciones; `fast-01` 70 ms, `moderate-01` 150 ms) y *expresivo*; la duración crece con el tamaño
  del cambio. https://carbondesignsystem.com/elements/motion/overview/
- **Adoptable**: consolidar con la mayor gravedad (el contador de la sidebar ya lo hace); forma para
  escanear, pastilla cuando hay sitio (formalizar la regla de `appearance`); movimiento productivo
  (120/180 ms están en su rango).

### 3.7 Radix Colors: semántica de 12 pasos

1 fondo de app, 2 fondo sutil, 3 fondo de elemento, 4 fondo en hover, 5 fondo activo o seleccionado,
6 bordes y separadores sutiles, 7 borde de elemento y anillo de foco, 8 borde en hover, 9 fondo sólido,
10 fondo sólido en hover, 11 texto de bajo contraste, 12 texto de alto contraste.
https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale

Equivalencia con Noust: 1 = `bg`; 2 = `bg-sunken`; 3 = `surface`; 4 = `surface-hover`; 5 = `surface-active`;
6 = `border`; 7-8 = `border-strong`; 9-10 = `accent`/`accent-hover` y `fail-strong`; 11-12 = `text-muted`/`text`.
Lo que Radix tiene **por matiz** y a Noust le falta para los estados: los pasos 3-5 (Noust tiene el 3 como
`*-soft`) y 6-7 (bordes: de ahí `--{estado}-border`). **No se propone** una escala de 12 pasos por matiz:
serían 60 tokens para cuatro estados y un acento que solo gastan dos o tres pasos.

### 3.8 Grafana (series temporales)

- Huecos en la serie: `never` (no conectar), `always`, o umbral; tooltip único/todos/oculto; leyenda
  como lista o tabla; mínimos y máximos *soft* para que "pequeñas variaciones no se magnifiquen cuando la
  serie es casi plana"; "cuidado con apilar: puede crear gráficas engañosas".
  https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/time-series/
- Buenas prácticas de paneles: de general a específico y de grande a pequeño; USE (infraestructura) y RED
  (servicios); CPU en porcentaje y no en bruto; "azul significa bueno, rojo significa malo"; la prueba de
  carga cognitiva: "¿puedo decir qué representa cada gráfica sin pensarlo?".
  https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/
- **Adoptable** (reglas de gráfica, 4.1.9): hueco = línea cortada, no interpolar; ejes de porcentaje fijos
  0-100; no apilar por defecto; título que dice qué es y en qué unidad.

### 3.9 Plataformas de despliegue y Supabase

- Railway: `Initializing` → `Building` → `Deploying` → `Active` (cuando pasa el health check), más `Failed`,
  `Crashed`, `Completed`, `Removed`. https://docs.railway.com/reference/deployments
- Render: `created`, `build_in_progress`, `update_in_progress`, `live`, `deactivated`, `build_failed`,
  `update_failed`, `canceled`, `pre_deploy_*`; insignia "Live" en el despliegue actual; "Build failed" apunta
  al log de compilación y "Deploy failed" al de ejecución.
  https://render.com/changelog/see-your-service-s-deploys-on-a-new-page (por búsqueda)
- Coolify: verde = running (healthy), rojo = exited o failed, amarillo = deploying o starting, gris =
  stopped; valores `running:healthy`, `running:unhealthy`, `degraded`.
  https://coolify.io/docs/applications/operations/overview (por búsqueda)
- Lectura para Noust: los siete estados de `StatusPill` coinciden con el consenso verde/ámbar/rojo/gris.
  Falta distinguir **en cola** de **en curso** (Railway `Initializing`), y marcar el despliegue **actual** con
  una insignia (Render "Live"). Noust ya nombra la fase que falló (`PhaseTimeline`), que es el equivalente de
  "Build failed / Deploy failed".
- **Supabase Design System** (declara inspirarse en Radix, shadcn y Geist): tres anchos de contenedor,
  *small* (ajustes, formularios, páginas hijas de ajustes), *default* (listas, tablas, detalle) y *full*
  (logs, código, editores, gráficas); cabecera de página **opcional** ("omítela cuando el área de trabajo se
  explica sola"); ajustes = secciones con título y descripción y los campos dentro de una `Card` con pie
  Cancel/Save (Save deshabilitado si no hay cambios); *Dialog* para tareas cortas, ***Sheet*** para
  formularios largos, *Alert dialog* (un párrafo, decisión binaria), *Text confirm dialog* (destructivo,
  el botón espera a que el texto coincida) y descarte con "Keep editing" / "Discard changes" ("mantén
  Cancel no destructivo"); tablas con identificador → estado → resto, importes a la derecha; Table, Data
  Table y Data Grid según necesites ordenar, filtrar o virtualizar.
  https://supabase.com/design-system/docs/ui-patterns/layout ·
  https://supabase.com/design-system/docs/ui-patterns/modality ·
  https://supabase.com/design-system/docs/ui-patterns/forms ·
  https://supabase.com/design-system/docs/ui-patterns/tables
- **Adoptable**: los tres anchos con nombre son exactamente lo que le falta a Noust (I-13); la pareja
  Dialog/Drawer que ya tiene coincide con Dialog/Sheet; *text confirm* es el nivel 3 de fricción.

### 3.10 Estándares

- WCAG 2.2 **2.5.8 Tamaño del objetivo (mínimo)**, AA: objetivo de puntero de al menos 24 × 24 px CSS, con
  excepciones (espaciado, equivalente, en línea, agente de usuario, esencial). Noust: `sm` = 28 px cumple.
  https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html
- WCAG 2.2 **2.4.11 Foco no oculto (mínimo)**, AA: el foco no puede quedar totalmente tapado por contenido
  del autor; se cumple con `scroll-padding` bajo cabeceras pegajosas (Noust: `scroll-padding-top: 4.5rem`).
  https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html
- NN/g, esqueletos: para esperas de menos de 10 s y páginas completas; spinner para módulos sueltos de
  2-10 s; barra de progreso a partir de 10 s; igualar el layout final; animación sutil; nada por debajo de
  1 s. https://www.nngroup.com/articles/skeleton-screens/

### 3.11 Tabla de síntesis

| Tema | Regla adoptada | Fuente | Noust hoy |
|---|---|---|---|
| Color | Tres pasos interactivos (defecto, hover, activo) como superficies | Geist, Radix | Cumple |
| Color | Función × énfasis (fg / soft / border) por estado | Primer, Radix | Falta `border` |
| Color | Contraste como propiedad comprobada por test | Stripe | Cumple (146 aserciones), sin cubrir alfas |
| Estado | Icono/forma/texto según espacio y atención; consolidar con la mayor gravedad | Carbon, Dynatrace | Cumple, sin regla escrita |
| Estado | Siempre color + forma + palabra | Carbon, Atlassian, WCAG 1.4.1 | Incumplido en ≈ 10 sitios |
| Jerarquía | Una primaria por página, al final del grupo | Primer | Cumple en general |
| Jerarquía | Cromo más apagado que contenido | Linear | Cumple |
| Notificación | Banner / sección / en línea / diálogo / toast con reglas de uso | Primer, Atlassian, Carbon | Sin componente de aviso |
| Notificación | Éxito con moderación; sin banner de éxito al borrar; anunciar siempre | Primer | 85 `toast.success` |
| Destrucción | Fricción proporcional; deshacer antes que confirmar | Primer, Supabase | Un solo nivel |
| Carga | Nada < 1 s; esqueleto con la forma final; progreso > 10 s; `aria-busy` | Primer, NN/g, Carbon | Cumple |
| Degradado | No ocultar el fallo; conservar datos; icono apagado en áreas grandes | Primer | Cumple (`QueryState`) |
| Vacío | Título + descripción + acción; tono según el caso; sin callejones | Primer, Atlassian | Cumple con parches |
| Formulario | Apilado; sentence case; error dice por qué; ayuda se oculta con error | Primer | Cumple |
| Guardado | Explícito; no mezclar patrones | Primer | Cumple |
| Tabla | Identidad primero; números a la derecha con cifras tabulares; vacío = estado vacío | Primer, Supabase | Estado en 4 posiciones |
| Layout | Tres anchos con nombre; cabecera opcional | Supabase | Anchos ad hoc |
| Movimiento | Productivo, 70-150 ms | Carbon | 120/180 ms |
| Objetivo táctil | ≥ 24 px (AA), 44 px en puntero grueso | WCAG 2.5.8, Primer | 28/32/40 px, sin `coarse` |
| Series | ≤ 3, hueco visible, sin apilar, eje 0-100 | Grafana, dataviz | Parcial |

---

## 4. Propuesta: el documento normativo del sistema de diseño

**Nombre y sitio sugeridos.** `docs/DESIGN.md` (junto a `docs/brand/BRAND.md`), con la galería
`/__design` como especificación ejecutable y los tests de la parte 5 como su policía. Este documento, en
español, queda como fundamentación. Cada regla lleva un identificador (`C-2`, `K-7`…) que los tests, los
mensajes de lint y las revisiones pueden citar. "Debe" y "nunca" son normativos; "recomendado" no lo es.

**Índice propuesto**

1. Principios · 2. Fundamentos (color, datos y gráficas, tipografía, espaciado, forma y capas,
movimiento, iconografía, densidad) · 3. Layout · 4. Plantillas de página · 5. Componentes (guía de uso) ·
6. Patrones · 7. Contenido · 8. Accesibilidad · 9. Cumplimiento · 10. Gobierno del sistema · Anexos
(tokens, glosarios, cómo reproducir las medidas).

### 4.1 Principios

- **P-1. El color es una afirmación.** Solo significa estado, "esto se puede accionar" o identidad de una
  serie en una gráfica. Todo lo demás es acromático (D8).
- **P-2. Todo estado se dice de tres maneras:** color, forma y palabra. Los cuatro colores de estado son
  indistinguibles entre sí para un deuteranope (ΔE 3,3-3,4 frente a un mínimo de 8): la forma y la
  palabra son requisito funcional.
- **P-3. La estructura se siente, no se ve** (Linear): un borde de 1 px y un cambio de superficie bastan;
  nada decorativo.
- **P-4. No compitas por una atención que no te has ganado** (Linear, Primer): una acción primaria por
  vista; la navegación, más apagada que el contenido; un solo elemento con acento fuerte por zona.
- **P-5. El sistema habla verbatim.** Lo que imprimen nginx, systemd, certbot, psql o git no se parafrasea
  ni se traduce ni se recorta: en mono, copiable, con el arreglo encima. El marco (título, sugerencia) es del
  catálogo.
- **P-6. Una implementación por patrón.** Una feature compone, no re-estiliza. Un patrón que aparece
  tres veces sube al kit (regla 3 del proyecto aplicada a la UI).
- **P-7. La fricción es proporcional al radio de la explosión** (Primer): nada para lo reversible, un
  diálogo para un recurso, teclear el nombre para lo irreversible o de gran alcance.
- **P-8. Denso donde se trabaja, generoso donde se decide,** y nada se mueve al llegar los datos
  (CLS ≤ 0,05, ya vigilado).
- **P-9. Siempre se sabe en qué servidor se está.** En una flota el contexto se dice con texto y posición,
  nunca con un matiz (que ya significa estado).

### 4.2 Fundamentos

#### 4.2.1 Color

- **C-1. Cuatro familias con significado exclusivo.** Neutros (sin croma: `bg`, `bg-sunken`, `surface`,
  `surface-raised`, `surface-hover/active`, `border`, `border-strong`, `text`, `text-muted`, `text-faint`);
  interactivo (`accent*`, `focus`, `selection`, `match`); estado (`ok`, `warn`, `fail`, `idle`); identidad
  de serie (`viz-1`, `viz-2`, solo dentro de un trazado). Nada más lleva croma. Excepción declarada:
  `--ansi-*`, colores del programa reproducidos en `LogViewer`.
- **C-2. Tres tokens por estado:** `--x` (texto e iconos), `--x-soft` (fondo) y `--x-border` (borde de un
  bloque tintado). **Nunca** modificadores de alfa sobre tokens de color (`/30`, `/40`, `/50`): un token
  se comprueba por test, un alfa mezclado con lo que haya debajo no. Excepciones aprobadas: el velo del
  diálogo (`--backdrop`) y el desenfoque de la barra superior (`bg-bg/85`).
- **C-3. `info` no tiene color.** Un mensaje informativo es neutro: `surface-raised`, `border`, icono `Info`
  en `text-muted`. Información no es un estado; el acento violeta tampoco lo es (P-1). Ya lo hacen
  `NodeNotice` y `RenameNotice`.
- **C-4. El acento es solo para lo accionable o seleccionado:** botón primario, enlace, pestaña activa,
  interruptor activado, indicador de paso, foco y selección. Nunca para decorar ni para datos. Un botón
  primario por vista (P-4).
- **C-5. Texto en tres grados,** los tres ≥ 4,5:1 sobre toda superficie: `text` (contenido), `text-muted`
  (secundario), `text-faint` (meta: marcas de tiempo, ayudas, placeholders; nunca decorativo).
- **C-6. Superficies en cuatro niveles:** `bg-sunken` (logs, código, pies de diálogo), `bg` (página),
  `surface` (tarjetas, tablas), `surface-raised` (todo lo flotante: menú, popover, diálogo, drawer, toast).
  Más los estados `surface-hover` y `surface-active` (los tres pasos interactivos de Geist/Radix).
- **C-7. Bordes:** `border` separa (hairline decorativo); `border-strong` delimita un control (≥ 3:1). Nunca
  `border-strong` para separar contenido; nunca `border` para delimitar un control.
- **C-8. Texto sobre un bloque tintado:** contenido en `text`; icono y palabra de estado en `--x`. (El test
  cubre `text` sobre `*-soft` y `x` sobre `x-soft`.)
- **C-9. Prohibido:** hex en el código (ya lintado), `rgb()`, `color-mix()` y sombras de color en clases o
  estilos; bajar la opacidad de un texto para "atenuarlo" (para eso están `text-muted` y `text-faint`); el
  violeta del casco del logo (`#7b61ff`) como color de UI.
- **C-10. Progreso en curso = ámbar.** Coherente con `Spinner` y `StatusPill deploying`. `Progress` hoy es
  violeta y no lo usa ninguna feature: pasa a `--warn` o se elimina (decisión menor).

**Tokens nuevos de color** (valores calculados; claro / oscuro; el borde es el color de estado mezclado al
35 % sobre `surface`; contraste medido contra `surface` y contra `*-soft`):

| Token | Claro | Oscuro | vs surface (claro / oscuro) | vs soft (claro / oscuro) |
|---|---|---|---|---|
| `--ok-border` | `#add0c0` | `#2a543b` | 1,67 / 2,07 | 1,47 / 1,85 |
| `--warn-border` | `#d7c5a6` | `#5e4924` | 1,69 / 2,10 | 1,50 / 1,89 |
| `--fail-border` | `#e9b5b2` | `#683734` | 1,79 / 1,87 | 1,54 / 1,76 |
| `--idle-border` | `#c8c8c8` | `#464646` | 1,67 / 1,90 | 1,42 / 1,64 |
| `--viz-1` (azul) | `#0274c7` | `#3496ef` | 4,85 / 5,77 | n/a |
| `--viz-2` (magenta) | `#9b2673` | `#ca549d` | 7,20 / 4,50 | n/a |

El borde tintado es decorativo (el bloque lleva icono y texto, WCAG 1.4.11 no lo exige) pero debe verse:
el test exige ≥ 1,4:1 contra su fondo suave. Se retira además el único `border-accent/40`
(`settings/github/GitHubIntegration.tsx:115`): una tarjeta destacada usa `border-accent` a secas.

#### 4.2.2 Datos y gráficas

- **V-1. Una gráfica responde una pregunta.** Título con la magnitud y la unidad ("Memory"), descripción con
  la ventana ("Last 30 minutes"). Rango temporal en la URL y el mismo `SegmentedControl` para todas las
  gráficas de la página.
- **V-2. Máximo tres series.** A partir de la cuarta: pequeños múltiplos (una gráfica por serie) o "Otras".
  Nunca se generan colores. Nunca se apila por defecto (Grafana: "puede crear gráficas engañosas").
- **V-3. Identidad de serie:** serie 1 `--viz-1`, serie 2 `--viz-2`, serie 3 `--text-muted` discontinua
  (4-3), serie 4 (excepcional) `--text-faint` punteada (1,5-3). Además de color, cada serie se distingue
  por trazo y por leyenda con etiqueta directa; el color nunca es el único canal.
  *Validación* (herramienta de dataviz, ambos temas, sobre `surface`): pareja azul-magenta ΔE CVD **13,7**
  (claro) y **13,3** (oscuro) frente a un objetivo de 8; visión normal **25,0** y **24,7** frente a un suelo
  de 15; contraste entre 3,99 y 7,20 sobre `surface`, `bg`, `bg-sunken` y `surface-hover` (siempre ≥ 3:1, el mínimo
  de WCAG 1.4.11 para marcas gráficas). Distancia de matiz (OKLCH): azul 250°
  y magenta 345° frente a acento 289° (39° y 56°), fallo 28° (43° del magenta), aviso 70-78° y
  ok 154-157°: no se confunden con estado ni con el acento.
- **V-4. Una serie que es un estado** ("fallos por hora") lleva el token de estado más glifo, nunca `viz-*`; y
  no se mezclan ambas cosas en una gráfica. Los `--ansi-*` **no** valen como serie (validados: ΔE 3,6 claro
  y 1,4 oscuro entre cian y magenta bajo deuteranopía).
- **V-5. El violeta no es color de serie** (recomendado; alternativa: mantenerlo solo para gráficas de una
  serie). Razón: el violeta significa "puedes actuar"; una línea violeta sugiere que es interactiva.
- **V-6. Marcadores de evento** (despliegues) con el lenguaje de estado: color, forma y palabra
  (`ChartMarker.state`), como hoy.
- **V-7. Huecos:** la línea se corta (sin interpolar) y la tabla alternativa dice "no reading". Porcentajes
  con eje fijo 0-100. Solo la serie 1 lleva relleno (8 %).
- **V-8. Toda gráfica** lleva resumen textual en el `aria-label` del canvas, tabla alternativa y ampliar en
  diálogo (`Chart`, ya). Fuente del canvas: mono 11 px (excepción declarada: el canvas no usa la escala CSS).
- **V-9. Medidores** (`Meter`, `ResourceMeter`): relleno neutro hasta el 75 %, ámbar hasta el 90 %, rojo
  desde entonces, y **al pasar a aviso o fallo aparecen glifo y palabra** (`aria-valuetext` incluye el
  nivel). Hoy solo cambia el color (I-6).
- **V-10. Cifras:** siempre con unidad y formateadas con `lib/format.ts`; cifras tabulares.

#### 4.2.3 Tipografía

Mona Sans para la interfaz, JetBrains Mono para todo valor del sistema. Nombrar por **rol** (Geist), no
por tamaño:

| Rol | Uso | Tamaño / interlínea | Peso y utilidad |
|---|---|---|---|
| Display | Solo `h1` de acceso y bloqueo | 32/40 | `display` (600, ancho 110 %) |
| Título de página | `h1` de `PageHeader` | 24/32 | `title` |
| Título de sección | Todo `h2`: `Section`, `SettingsSection`, paso del asistente, título de diálogo y drawer | 16/24 | `title` |
| Título de subsección | Todo `h3`: `Section level=3`, título de tarjeta, `DangerAction` | 14/20 | `title` |
| Cuerpo | Prosa: descripción de página, diálogo, estado vacío | 14/20 | 400, medida ≤ 68ch |
| UI denso (defecto) | Botones, tablas, campos, listas, clave-valor, tooltips | 13/20 | 400; 500 en acciones y etiquetas |
| Etiqueta de campo | `Field` | 13/20 | 500 |
| Ayuda y meta | Descripción de campo, marcas de tiempo, cabecera de tabla, badge, tecla, contexto de un tile | 12/16 | 400; 500 en cabeceras y badges |
| Lectura destacada | Valor de un `StatTile`, titular de diagnóstico | 18/24 | `title` (sans, cifras proporcionales) |
| Valor de sistema | Rutas, puertos, commits, IDs, unit, comandos | 0,92 em del texto que lo rodea (12 en 13, 13 en 14) | `<Mono>` |
| Salida de sistema | `SystemOutput`, `LogViewer` | 12 mono | verbatim |

- **Y-1.** Solo estos tamaños: 12, 13, 14, 16, 18, 24, 32 (los siete de `--fs-*`). Nada de `text-[…]`.
- **Y-2.** Pesos: 400 y 500. El 600 solo lo pone `title`/`display`. Prohibido `font-semibold` y `font-bold`
  sueltos (hoy 7; el título de toast y de tarjeta pasan a `title`).
- **Y-3.** Toda cifra que cambia o se compara va en mono o en `tabular-nums` (columnas numéricas a la
  derecha).
- **Y-4.** Todo valor de sistema se compone con `<Mono>` (mono, sin ligaduras, `translate="no"`); la
  utilidad `mono` suelta solo existe dentro de `components/ui`. Hoy: 132 usos sueltos frente a 9 de `<Mono>`.
- **Y-5.** Sentence case en todo; sin mayúsculas continuas, sin cursiva salvo el valor vacío de una consulta
  SQL, sin negrita en línea (énfasis = 500).
- **Y-6.** Medidas: prosa ≤ 68ch (`--measure`), ayuda ≤ 52ch (`--measure-help`), tabla sin medida.
- **Y-7.** Un identificador de sistema como título de página (dominio, unit, base de datos, site) va en mono
  (`PageHeader mono`).
- **Y-8.** 24 px es solo el `h1`; el titular del diagnóstico (`app/diagnose/DiagnoseTab.tsx:116`, que sube a
  24 en `sm`) usa 18.

#### 4.2.4 Espaciado

- **S-1. La escala es la de Tailwind en medios pasos hasta 16 px y de 4 en 4 desde ahí:** 0, 0,5, 1, 1,5, 2,
  2,5, 3, 3,5, 4, 5, 6, 8, 10, 12, 16 (= 0, 2, 4, 6, 8, 10, 12, 14, 16, 20, 24, 32, 40, 48, 64 px). Es la que
  el código ya usa (440 medios pasos); D8 la describía solo en su parte de maquetación. **Prohibidos**: 7, 9,
  11, 13, 14, 15, 6,5 y todo valor arbitrario (hoy ≈ 10 usos: `mb-7` en `auth/LoginPage.tsx:43` y
  `central/CentralLockScreen.tsx:47`, `pl-7` ×6 en `app/settings/ReleasesSection.tsx`).
- **S-2. Ritmo vertical de página** (una sola vez, en el kit):
  cabecera → contenido 32 (`PageHeader mb-8`); entre secciones 32 (`Sections`, nunca `gap-8` a mano);
  encabezado de sección → contenido 16; hermanos dentro de una sección 16; campos de un formulario 20;
  grupos de un formulario 24; fila de clave-valor mínimo 40; página: `px-4 sm:px-6 lg:px-8`, `pt-6 lg:pt-8`,
  `pb-16`.
- **S-3. Relleno de superficies:** panel/tarjeta 20 (`p-5`), con cabecera `px-5 pt-4 pb-3`; tarjeta compacta
  16 (`p-4`); aviso de página `px-4 py-3`; aviso en línea (dentro de formulario o diálogo) `px-3 py-2.5`;
  diálogo `px-5` con cabecera `pt-5`, cuerpo `py-4`, pie `px-5 py-3`; drawer `px-5 py-4`; celda de tabla
  `px-3` (`first:pl-4`). Se retiran `px-4 py-3.5`, `px-5 py-2` y `px-4 py-1`.
- **S-4. Nada de números mágicos para alinear.** `Field` ofrece un hueco `action` para el botón adyacente
  (adiós `mt-[1.625rem]` ×3) y los esqueletos usan las alturas de control (D-1), no `h-[2.375rem]`.
- **S-5. Medidas con nombre** (`--measure` 68ch para prosa, `--measure-help` 52ch para ayuda) y anchos de página
  con nombre (L-2).
  Prohibido `max-w-[…ch|rem|px]`.

#### 4.2.5 Forma, elevación y capas

- **F-1. Cuatro radios:** `chip` 4 (teclas, badges, chips de código, foco de enlaces; hoy `rounded-[4px]` ×52
  y `[5px]` en `Badge`), `control` 6 (botones, campos, filas de menú, aviso en línea), `card` 10 (tarjetas,
  tablas, diálogos, drawers, popups, aviso de página), `pill` 999 (`StatusPill`, indicador de pestaña,
  medidores). Nada más.
- **F-2. Bordes de 1 px.** Anillo de foco de 2 px con desplazamiento 2, definido **una vez** en `app.css`; no se
  repite `focus-visible:outline-2` en componentes (hoy 76 veces, con 5 desplazamientos distintos) salvo
  `-outline-offset-2` en contenedores con `overflow`.
- **F-3. Elevación en cuatro capas:** `bg-sunken` < `bg` < `surface` (con `--shadow-raised`, solo en claro) <
  `surface-raised` con `--shadow-overlay` (todo lo flotante). Sin más sombras. El resalte interior del botón
  primario (`inset 0 1px 0`) pasa a un token `--shadow-inset-highlight` o se elimina.
- **F-4. Capas `z` con nombre:** `--z-sticky: 30` (topbar), `--z-backdrop: 40`, `--z-overlay: 50` (diálogo,
  drawer, menú, popover), `--z-toast: 60`, `--z-skip: 70`. Prohibido `z-[…]` y cualquier otro número.
- **F-5. Franja de estado** (borde izquierdo de 2 px con token de estado, hoy `shadow-[inset_2px_0_0_var(--warn)]`
  en `LogViewer` y `TONE_RAIL` en `DiagnoseTab`) se define una vez como utilidad `state-rail-{tone}`.

#### 4.2.6 Movimiento

| Qué | Duración / curva | Nota |
|---|---|---|
| Hover, pulsación y foco de color | `--duration-fast` 120 ms, `--ease-out` | solo propiedades de color y sombra |
| Aparición de tooltip, menú, popover | 120 ms, opacidad + escala 0,97 → 1 | `POPUP_MOTION` |
| Diálogo, drawer, toast, indicador de pestaña | `--duration-base` 180 ms | el drawer entra desde la derecha |
| Cambio de estado de una pastilla | un pulso de opacidad (0,9 s) | nunca desplazamiento |
| Esqueleto | `breathe` 1,6 s | |
| Spinner / indeterminado | 0,8 s lineal / 1,4 s | |

- **M-1.** Con `prefers-reduced-motion` todo pasa a 0 (ya) y el estado en curso sigue legible por forma y
  palabra.
- **M-2.** Nada anima el layout (CLS ≤ 0,05); las listas no se reordenan animadas.
- **M-3.** *Propuesta a medir:* si el dato llega en menos de ~300 ms no debe verse el esqueleto (reservar el
  hueco desde el primer frame y pintar el esqueleto tras 300 ms). Primer: "menos de 1 s, no muestres nada".
- **M-4.** Movimiento *productivo* (Carbon): sutil y apartado; nunca decorativo.

#### 4.2.7 Iconografía

- **Ic-1. Una librería** (lucide, hoy 113 iconos distintos) **más** `StatusGlyph` propio. Los significados
  *semánticos* (éxito, aviso, error, información, bloqueado, más acciones, borrar, externo, copiar) salen de
  un mapa único `components/ui/icons.ts`; los iconos de *objeto* (navegación, entidades) se importan libres.
- **Ic-2. Tamaños:** 12 (glifo de estado en fila densa), 14 (`size-3.5`: botón `sm`, línea de 13 px),
  16 (`size-4`: botón `md`, aviso, menú, campo), 20 (`size-5`: estado vacío), 24 o más solo en ilustración.
  Ningún tamaño intermedio.
- **Ic-3. Trazo** el de lucide (2); nunca `strokeWidth` salvo `Check` en `Checkbox`. `StatusGlyph` está
  dibujado para 10-16 px; no se escala a más de 24.
- **Ic-4.** Decorativo = `aria-hidden="true"`. Un icono sin texto solo dentro de `IconButton` con `label`.
- **Ic-5. Un icono, un significado.** Colisiones a corregir: Dominios `ShieldCheck` → `Globe`; Servicios `Cog`
  → `ServerCog` (existen en el lucide fijado); `ShieldCheck` queda para seguridad verificada (TLS válido);
  "correcto" = `CircleCheck`.
- **Ic-6. Dos vocabularios, cada uno en un sitio:** `StatusGlyph` dice el **estado de una entidad** (app,
  servicio, certificado, job, nodo); el icono de `Notice` dice la **severidad de un mensaje** (información,
  éxito, aviso, error). Ningún icono de estado de lucide fuera de `Notice`, `Field` y `Toast`.
- **Ic-7.** Color del icono = color del texto vecino, o el de estado, o `fg-faint` en la barra lateral; nunca
  acento salvo icono interactivo activo.

#### 4.2.8 Densidad y tamaño de controles

- **D-1. Alturas de control como tokens:** `--control-sm` 28, `--control-md` 32 (defecto), `--control-lg` 40
  (solo acceso, desbloqueo y "Deploy" del asistente). Con `@media (pointer: coarse)`: 36 / 40 / 44. Como
  todo control lee el token, un cambio de una línea en `app.css` cubre el táctil (hoy `pointer-coarse:` se
  usa 5 veces, solo para ocultar teclas).
- **D-2. Filas de tabla:** compacta 36 para historiales que se escanean (despliegues, entorno, sesiones,
  entregas, despliegues recientes); cómoda 44 para inventarios que se abren (aplicaciones, servicios,
  bases de datos). Carbon usa 24/32/48/64; con dos alturas basta.
- **D-3. Sin conmutador global de densidad** (todavía): los tokens de D-1 lo harían barato si hiciera falta.
- **D-4. Zonas:** densa (13/12 px, filas 36-44) en tablas, logs, clave-valor; generosa (14-16 px, huecos de
  24-32) en cabeceras, confirmaciones y estados vacíos.

### 4.3 Layout

- **L-1. Shell fijo:** barra lateral 240 px (≥ 1024 px), barra superior 56 px pegajosa, contenido con relleno
  16/24/32. Un hub no ofrece las páginas locales.
- **L-2. Tres anchos de página con nombre** (Supabase: small/default/full; hoy hay ≥ 8 medidas):
  `narrow` 46 rem (736 px: asistente, callback de GitHub, formularios sueltos), `form` 72 rem (1152 px = el
  `max-w-6xl` de ajustes, ajustes de app y entorno) y `wide` 100 rem (1600 px = el del shell: datos, tablas,
  gráficas). Los fija la plantilla mediante un envoltorio `Page width="…"`, no la feature.
- **L-3. Sin rejilla de 12 columnas.** Tres primitivas: `Sections` (pila de 32), `Split` (etiqueta a la
  izquierda, contenido a la derecha: el `5fr/9fr` de `SettingsSection` y el de `KeyValueList`) y `TileRow`
  (fila de `StatTile`). Prohibido `grid-cols-[…]` con anchos de columna propios (hoy 35).
- **L-4. Breakpoints:** de layout solo `sm` (640) y `lg` (1024): teléfono (< 640: una columna, menú en drawer),
  tableta (640-1023: una columna ancha, menú en drawer), escritorio (≥ 1024: barra fija). `md` y `xl` solo para
  ocultar columnas (`hideBelow`) y el buscador de la barra superior. Consultas de contenedor (`@min-*`) para
  componentes que se adaptan a su caja (`MachineStrip`).
- **L-5. Viewports de referencia** para capturas y E2E: 390, 768 y 1440.
- **L-6.** Ninguna página hace scroll horizontal; solo tablas, logs y pestañas lo hacen dentro.

### 4.4 Plantillas de página

Toda página es una de ocho plantillas; una página nueva elige una o justifica una nueva.

| # | Plantilla | Páginas | Estructura y reglas | Ancho |
|---|---|---|---|---|
| T-1 | Panel | Resumen, Servidor, Flota | `PageHeader` (≤ 1 acción) → `Sections`: "Necesita atención" primero y solo si hay, tiles y medidores, gráficas, tablas recientes | `wide` |
| T-2 | Colección | Aplicaciones, Servicios, Cron, Bases de datos, Copias, Actividad, Sites y Certificados | `PageHeader` con **la acción primaria de creación** (`md`) → `FilterBar` → `DataTable`. Sin datos: `EmptyState page` sustituye barra y tabla. Filtrado sin filas: `EmptyState table` con "Clear filters" | `wide` |
| T-3 | Recurso con pestañas | App | `PageHeader`: migas → título mono → hechos (`StatusPill` + versión + URL) → acciones (primaria al final, resto en un menú). Debajo, hueco `tabs` con `LinkTabs`; `JobProgress` entre cabecera y pestañas. Danger zone al final de Ajustes | `wide` (`form` en Ajustes y Entorno) |
| T-4 | Recurso sin pestañas | Servicio, Base de datos, Site, Despliegue | Igual que T-3 sin pestañas; contenido en `Sections` | `wide` |
| T-5 | Ajustes | Ajustes, Ajustes de app | `LinkTabs` → un `SettingsSection` por tema (izquierda: título, descripción y comandos CLI; derecha: `SettingsFormCard` con pie Descartar + Guardar). Guardado explícito por formulario, nunca mezclado con interruptores inmediatos en el mismo. DangerZone al final | `form` |
| T-6 | Asistente | Nueva aplicación | `StepRail`; un `h2` por paso; primaria abajo a la derecha, "Atrás" a la izquierda; el último paso muestra `JobProgress` | `narrow` |
| T-7 | Acceso y bloqueo | Login, bloqueo de la central | Componente `AuthCard` (nuevo): centrado, 26 rem, título `display` (hoy son dos `<h1>` a mano: `auth/LoginPage.tsx:42`, `central/CentralLockScreen.tsx:46`), un botón `lg` | 26 rem |
| T-8 | Registro a pantalla completa | Logs, log de despliegue | `LogViewer height="fill"` (= `max(24rem, 100dvh − 22rem)`); su barra de herramientas sobre el log; sin tarjeta alrededor | `wide` |

- **T-9. La acción primaria de creación vive en la cabecera de la página** (T-2), no en una sección, y en
  tamaño `md`. `DatabasesPage.tsx:56` (botón `sm` en la cabecera de una `Section`) es la excepción a corregir.
- **T-10. La cabecera es opcional** cuando el área de trabajo se explica sola (Supabase); nunca se repite su
  título como `Section` (`backups/BackupsPage.tsx:79`, `databases/DatabasesPage.tsx:40`).
- **T-11.** `PageHeader` gana `tabs` (hueco pegado bajo la cabecera; retira los `-mt-4`), `mono` (título) y
  `status` (hueco junto al título para el `StatusPill`); `description` es solo prosa.

### 4.5 Componentes: guía de uso

Formato: úsalo para · no lo uses para · reglas.

| Componente | Úsalo para | No lo uses para | Reglas |
|---|---|---|---|
| `Button` | Una acción con verbo | Navegar (enlace: `buttonClassName`) | K-1: una `primary` por vista; `sm` dentro de tarjetas, filas y barras; `lg` solo acceso y desbloqueo; `danger` solo en el paso final de una confirmación o en `DangerAction`; `ghost` en barras de herramientas y "Descartar" |
| `IconButton` | Acción conocida y repetida (más, cerrar, copiar) | Acción única y principal | `label` obligatorio; `sm` en filas |
| `Field` + `Input`, `Select`, `Textarea` | Todo control de formulario | — | K-2: prohibido `<label>` suelto (hoy 13); error = icono + texto debajo; ayuda visible |
| `Switch` | Ajuste que se aplica **al instante** | Un campo de un formulario con "Guardar" | Primer: no mezclar guardado explícito y automático |
| `Checkbox` | Opción de un formulario con guardado explícito; selección múltiple | Ajustes inmediatos | |
| `Dialog` | Tarea corta con foco | Lectura larga, formularios largos (drawer) | Anchos `sm` 400 (confirmación) · `md` 520 (formulario) · `lg` 720 (revisión) · `xl` 1280 (solo mirar). Primaria a la derecha y al final |
| `Drawer` | Detalle con la página detrás (log, unit, certificado) o formulario largo | Confirmar | `md` 480, `lg` 720 |
| `ConfirmDialog` | Destruir; ver X-2 | Acciones reversibles | `confirm` = `simple` (diálogo de 400 px) o `type` (teclear el nombre); el fallo se muestra verbatim |
| `Menu` | Acciones de fila y "más acciones" | Navegación | ≤ 7 elementos; destructivas al final tras separador |
| `Tooltip` | Complemento de un icono | Única fuente de información; botones con texto | retardo 500 ms |
| `Popover` | Ayuda no crítica y paneles pequeños (sesión) | Confirmaciones | 1 uso hoy |
| `LinkTabs` / `Tabs` | Vistas hermanas; `LinkTabs` si el estado es la URL | Más de 8 pestañas | K-3: por defecto `LinkTabs` (compartible); `Tabs` solo sin URL |
| `DataTable` | Filas de cosas del mismo tipo | Datos jerárquicos, texto largo, pocas filas (lista) | X-7 |
| `StatusPill` / `StatusGlyph` | Estado de una entidad | Un atributo (`Badge`) | X-1 |
| `Badge` | Atributo corto (tipo, versión, cuenta) | Estado (I-6: hoy 2 usos) | tonos de estado prohibidos salvo cuenta con severidad |
| `Card` | **Todo panel**; sustituye a las 69 superficies a mano | Decorar | K-4: `size` `md`/`sm`, `as`, `interactive`; se **adopta** o se elimina (0 usos hoy) |
| `Notice` (nuevo) | Un mensaje persistente cerca de lo afectado | Resultado efímero de una acción (toast); validación de campo (`Field`) | K-5: `tone` `info` (neutral) `success` `warning` `error`; `size` `page` o `inline`; título opcional; acción; descartable; `role="status"`, `alert` solo con `tone="error" live` |
| `JobProgress` (nuevo) | El job que el operador acaba de lanzar | Historial (Actividad) | K-6: unifica los tres actuales; en curso (spinner ámbar + verbo + paso), éxito (descartable), fallo (verbatim) |
| `FilterBar` (nuevo) | Búsqueda y filtros de una colección | — | K-7: `role="search"`, buscador `w-72`, `items-center`, huecos de 8, `/` enfoca, estado en la URL |
| `EmptyState` | Vacío | Error | K-8: `variant` `page` (h2, `py-16`, con `command`) · `section` (h3, `py-12`) · `table` (sin borde, `py-8`) · `inline` (una línea) |
| `Section` / `Sections` | Un tema de página | Título de tarjeta (`Card`) | K-9: prohibido `<h1>`–`<h4>` a mano fuera de `components/` y `app/` (hoy 40) |
| `KeyValueList` | Hechos de una cosa | Datos tabulares | valores de sistema en mono y copiables |
| `Toast` | Resultado de una acción que **no se ve** en pantalla | Éxito visible; errores de carga (`QueryState`) | X-2 |
| `Meter` / `Progress` | Nivel en un rango / avance con final conocido | — | V-9, C-10 |
| `Skeleton` | Carga de datos con la forma final | Acciones (botón `loading`) | `aria-busy` en la región |
| `Spinner` | Actividad indeterminada corta dentro de un control o `JobProgress` | Cargar contenido (esqueleto) | tono ámbar |
| `Chart` | Serie temporal | Comparar categorías | V-1..V-10 |
| `LogViewer`, `SystemOutput` | Salida del sistema verbatim | Texto propio del producto | mono, copiable, descargable |

**K-10. Reglas de composición.** Una feature no escribe `rounded-card border … bg-surface`, ni una receta
de aviso, ni un encabezado, ni un `<label>`, ni un marcador de celda vacía: importa el componente. Si no
existe, se propone en el kit (P-6).

### 4.6 Patrones

**X-1. Mostrar el estado.** Ocho estados con glifo distinto: `running` (punto), `deploying` (arco que gira),
**`queued` (anillo discontinuo, estático: nuevo)**, `warning` (triángulo), `failed` (cruz), `stopped` (anillo),
`static` (cuadrado), `unknown` (interrogante). Cada contexto tiene una forma fija:

| Contexto | Forma |
|---|---|
| Cabecera de un recurso | `StatusPill md` (pastilla) |
| Columna de tabla | `StatusPill inline sm` (glifo y palabra), ancho `w-32` |
| Contadores de barra lateral y barra superior | `StatusGlyph` de 10 px y número; el nombre accesible es una frase ("2 services failed") |
| Texto corrido o tooltip | La palabra con `stateTextClass` y su glifo si es aviso o fallo |
| Agregados | El color y el glifo del estado más grave del grupo (Carbon) |
| Nunca | Solo color; `Badge`; un punto de color propio |

Correcciones al vocabulario (I-21): "en cola" deja de girar; "revertido" (`rolled_back`) pasa a un estado
gris con `attention` coherente (o se documenta por qué cuenta como atención estando gris); el despliegue
actual lleva una insignia "Live"/"Current" (Render).

**X-2. Acciones, confirmación y sudo.**

- *Jerarquía:* una primaria por vista; en cabecera a la derecha y **al final**; en tarjeta, fila o barra
  `sm`; en fila, menú `MoreHorizontal` con nombre accesible "Actions for {name}".
- *Fricción proporcional al radio de la explosión* (P-7):

| Nivel | Cuándo | Componente | Ejemplos |
|---|---|---|---|
| N0 | Reversible o de efecto visible e inmediato | Sin confirmación (y deshacer si existe) | Activar/desactivar un job, editar un valor guardado, borrar una variable en un borrador |
| N1 | Un recurso; se puede volver a crear; interrumpe pero no pierde datos | `ConfirmDialog confirm="simple"` (400 px, botón `danger` con verbo y objeto, Cancelar a la izquierda) | Parar una app, quitar un dominio de una app, quitar un job de cron, una programación o un destino de backup, revocar un token, quitar un servidor de la flota, borrar un usuario de BD, borrar o revocar un certificado |
| N2 | Pérdida de datos irrecuperable o alcance amplio | `ConfirmDialog confirm="type"` (teclear el nombre) | Eliminar una aplicación, `drop` de una base de datos, eliminar un servicio (unit), eliminar un site, borrar un backup, restaurar sobre datos existentes |

  Es una propuesta a validar: hoy las 18 confirmaciones existentes son N2 y las simples están escritas a mano
  (I-15). Aplicarla convierte unas 10 de las 18 en N1.
- *Después de actuar:* la propia interfaz cambia (la fila desaparece, el estado cambia) y se **anuncia** a
  lectores de pantalla (`useAnnounceChange`); el foco vuelve a un elemento estable. **Toast solo si el resultado
  no se ve** (acción sobre algo que ya no está en la vista, job encolado con enlace). Nada de `toast.success`
  para lo que ya se ve (hoy 85). Primer: "no añadas un banner de éxito por defecto".
- *Sudo mode:* toda acción que el backend marca con `require_elevated` muestra su candado; al pulsarla se abre
  una sola vez "Confirma que eres tú" y la acción **se reintenta sola** al confirmar. El diálogo dice **qué** se
  va a hacer y **en qué servidor**. El `403 elevation_required` no es un error para el operador. Evaluar
  (backend): renovar el plazo de 10 min con cada acción sensible, como GitHub.

**X-3. Jobs y progreso.** Un job tiene cuatro estados (en cola, en curso con paso, correcto, fallido o
cancelado) y se ve en tres sitios: `JobProgress` donde el operador lo lanzó (cabecera de app, pestaña de
dominios, último paso del asistente), su fila en Actividad y, si se fue de la página, un toast al terminar. El
log en vivo abre en `JobLogDrawer`/`LogViewer`; las fases, en `PhaseTimeline`. El inicio y el éxito se anuncian
con cortesía; el fallo, con aserción, con el error verbatim.

**X-4. Errores con salida verbatim.** `ErrorBlock` = título (qué falló, ≤ 4 palabras: "Could not load
applications") + arreglo arriba (el `hint` del backend gana sobre el del catálogo) + lo que dijo el sistema en
mono (`SystemOutput`, sin recortar, copiable) + "Try again". El error de un campo va en `Field`; el de una acción,
en un toast `error` persistente con `detail`; el de una carga, en `QueryState`. `role="alert"` solo para el
resultado de algo que el operador acaba de hacer (si no, una máquina caída gritaría desde cada sección). Nunca
"Oops" ni "Sorry". Si no se conoce la causa: "Something went wrong" y la salida, sin inventar (Atlassian).

**X-5. Vacío, carga y parcial.** Todo dato tiene cuatro estados: esqueleto (forma reservada, `aria-busy`, texto
`sr-only`), error, vacío y contenido (`QueryState` ya lo hace). *Umbrales de espera* (Primer, NN/g): menos de 1 s,
nada que parpadee (M-3); de 1 a 10 s, esqueleto con la forma final para una página o lista, `Spinner` solo dentro de un
control o un módulo pequeño; más de 10 s, progreso determinado si se conoce el final y, si no, un job en segundo plano
con su `JobProgress` (X-3); cada elemento de una colección se pinta en cuanto llega. *Parcial:* si un refresco falla, los datos
anteriores se quedan con un `ErrorBlock compact` encima. *Degradado* (una dependencia opcional falla):
sustituir el bloque afectado por `Notice warning` o `EmptyState` con icono de aviso en `text-muted`, **no**
desactivar controles, ocultar los contadores sin dato y jamás pintar "undefined" (Primer). *Celda vacía:* un
solo componente `EmptyCell` (guion largo `–` con el motivo en `sr-only`) sustituye a las cuatro copias de
`Nothing` y a los 6 `-` sueltos. (Primer deja la celda en blanco; Noust conserva el guion porque un
lector de pantalla necesita saber que hay "nada" y por qué.)

**X-6. Formularios.**

- Apilado en vertical; siempre `Field` (K-2); etiqueta de ≤ 3 palabras en sentence case; el campo es obligatorio
  por defecto y se marca lo `Optional`; ayuda de 12 px bajo el control; error de 13 px con `CircleAlert`
  debajo. Noust **mantiene la ayuda visible junto al error** (Primer la oculta): el formato es lo que arregla
  el error; divergencia consciente.
- Validación: al enviar; tras el primer intento fallido, en línea al salir del campo (`onBlur`); manda el servidor
  (`fields` del 422). El botón de enviar se deshabilita por "sin cambios" (`!dirty`) o por un campo obligatorio
  todavía vacío; **un valor inválido no lo deshabilita**: el botón sigue activo y el servidor explica por qué
  (Primer). Hoy ≈ 22 botones se deshabilitan por campo vacío o inválido; los `!valid` y `!canSubmit`
  (`services/CreateServiceDialog.tsx:139`, `cron/CronJobDialog.tsx:148`, `backups/DestinationDialog.tsx:127`) son los
  que hay que revisar primero. Es una decisión menor (6.2).
- Guardado explícito por formulario: pie con indicador de cambios (`punto ámbar` + "Unsaved changes"),
  "Discard" (ghost) y "Save" (secundario limpio, primario con cambios: el patrón de `SettingsFormCard`, que pasa
  a ser el único). `Switch` = guardado inmediato y no convive con un "Guardar" en el mismo formulario.
- Valores de sistema en mono, sin autocompletado ni corrección; unidad como `suffix`; secretos mostrados una vez
  con `CopyButton` y un botón "Done".
- Anchos: `w-full` con tope `max-w-md` (28 rem) por defecto; `w-36` para puerto o tiempo; el botón adyacente, en
  el hueco `action` de `Field`.
- Grupos relacionados en `<fieldset>` con `legend`; los pasos largos, en drawer (Supabase: Sheet).

**X-7. Tablas.**

- *Orden de columnas:* **identidad primero** (nombre, dominio, id: es el nombre accesible de la fila y el botón
  primario cuando hay `onRowActivate`), **estado segundo** (siempre `StatusPill inline sm`, `w-32`), atributos
  descriptivos, tiempo, números (a la derecha, en mono, cifras tabulares), y al final las acciones de fila en
  un menú sin cabecera visible. Aplica a Aplicaciones, Servicios, Despliegues recientes, entregas de webhook,
  Actividad y Cron (hoy estado primero); Servidores, Tokens, Sites y Certificados suben el estado a la 2.ª
  posición.
- *Orden inicial:* lo más reciente primero en historiales; alfabético en inventarios. Ordenar por gravedad
  (`STATE_RANK`) es un clic en la columna de estado.
- *Densidad:* D-2. *Truncado:* último recurso, con `title`/tooltip y siempre en mono para valores de sistema.
- *Vacío:* `EmptyState table` dentro de `empty`; celdas vacías con `EmptyCell`.
- *Selección múltiple y lotes:* no existen; si aparecen, barra de lote sobre la tabla y se desactivan las
  acciones de fila (Carbon).
- *Paginación:* ≈ 20 filas por página o "Load more"; virtualizar solo pasadas 100 filas.
- *Cabecera de columna:* 12 px, 500, `text-muted`, ordenable con botón y `aria-sort`.

**X-8. Filtros y búsqueda.** `FilterBar` (K-7): búsqueda `type="search"` con icono, tecla `/` y valor en la URL
(`q`); filtros como `Select sm` o `SegmentedControl`; acciones secundarias a la derecha. El filtro vive en la URL
(enlace compartible). Un filtro que vacía la tabla muestra "No matches" con "Clear filters".

**X-9. Gráficas en una página.** Un `SegmentedControl` de rango por página; dos columnas desde 1024 px, una por
debajo; cada gráfica con su `h3`, su resumen y su acción de ampliar; V-1..V-10.

**X-10. Contexto de flota ("¿en qué servidor estoy?").** Hoy: el nombre de la máquina, en mono, siempre en la barra
superior (`MachineStrip`, y el selector cuando hay nodos, con glifo y palabra si el nodo tiene problemas), título del
documento con el nombre del nodo,
`NodeNotice` si la versión difiere, prefijo de ruta `/n/<servidor>/`, y `useNode` en `ErrorBlock`. **Falta** que
las **acciones peligrosas lo digan**: ni `ConfirmDialog`, ni `ElevateDialog`, ni los toasts de mutación nombran el
servidor. Reglas:

1. En un nodo, todo diálogo de confirmación, el de sudo y todo toast de mutación llevan el servidor
   (`Delete shop.example.com on web-2`), con `Mono`.
2. En un nodo, la primera miga es el servidor.
3. El nombre del servidor ya está siempre en la barra superior; no se repite en la lateral.
4. **Ningún matiz de marco ni de fondo** distingue un servidor de otro (el color ya significa estado): el
   contexto se dice con texto en mono y posición fija.
5. Un hub no ofrece páginas locales; el selector siempre es visible cuando hay nodos.

**X-11. Teclado.** `⌘K` paleta; `g` + letra para ir a una sección; `/` enfoca la búsqueda de la página; `?` lista
los atajos; ninguna tecla suelta sin modificador salvo con el foco en una lista o tabla; cada atajo aparece en
`ShortcutsDialog` y en el tooltip de su control (`shortcut`).

**X-12. Tiempo real.** El estado de un flujo en vivo ("Live", "Reconnecting…") usa `StatusGlyph` y palabra
(hoy `services/ServiceDetailPage.tsx:108` usa un punto propio). Los números que cambian cada pocos segundos no
son regiones vivas (`MachineStrip`, ya).

**X-13. Paridad con la terminal.** Toda operación con equivalente en el CLI lo muestra con `CommandHint`
(37 usos) y los estados vacíos de página llevan `command`. Es parte de la identidad del producto.

**X-14. Taxonomía de notificaciones.** Un evento usa **un** canal, nunca dos (hoy `JobStatus` documenta que su
fallo "ya lo anuncia el toast": es la regla bien aplicada). Seis canales:

| Canal | Cuándo | Componente | Persistencia | ARIA | Ejemplo en Noust |
|---|---|---|---|---|---|
| Error de campo | Validación de un control | `Field` (`error`) | Hasta corregir | `aria-invalid` y `aria-describedby` | "Name is required" |
| Bloque de error | Un fallo de carga o de acción, con la salida del sistema | `ErrorBlock`, `QueryState` | Hasta reintentar | `alert` solo si `live` | "Could not load applications" |
| Aviso de sección | Algo persistente sobre una sección o formulario: advertencia previa a una acción, resultado guardado, explicación de un bloqueo | `Notice` (`inline` o `page`) | Hasta descartar o resolver; los avisos de sistema no se descartan | `status`; `alert` solo `error` vivo | "Saved the health check", plan de migración con avisos |
| Aviso global | Estado que afecta a toda la consola | `Notice` en el hueco del shell, sobre el contenido | No descartable hasta resolver (el informativo sí) | `status` | `CentralLockedNotice`, `NodeNotice`, `RenameNotice` |
| Toast | Resultado de una acción cuyo efecto **no se ve** en pantalla; job encolado con enlace | `toast.*` | Éxito e info 5 s; error y aviso hasta cerrar; máximo 3 apilados | Regiones cortés y asertiva | "Update of shop.example.com queued" |
| Diálogo | Decisión que bloquea, o fallo crítico que exige intervención | `Dialog`, `ConfirmDialog` | Hasta decidir | `alertdialog` en confirmaciones | "Delete application" |

Reglas: el toast lleva título con el verbo en pasado, una descripción opcional, el `detail` del sistema verbatim y
como mucho una acción; un aviso informativo es neutral (C-3); "éxito" con moderación y solo si no es evidente por
el resto de la interfaz (Primer); ninguna notificación paraliza la página salvo el diálogo.

### 4.7 Contenido

- **W-1. Voz:** directa, técnica, sin adornos. Sentence case en todo. Sin exclamaciones, sin "please", "sorry",
  "oops", "successfully" (hoy 0). Español de "tú", infinitivos en los botones, sin "vosotros" (`i18n/README.md`).
- **W-2. Errores:** qué falló (título ≤ 4 palabras) + qué hacer (una frase, imperativa) + lo que dijo el sistema
  (verbatim). Una causa desconocida no se inventa.
- **W-3. Botones:** en un diálogo, verbo y objeto ("Delete application", nunca "Confirm"); en la página, una o
  dos palabras ("Deploy", "Restart"); el toast reutiliza el verbo en pasado ("Deploy" → "Deployed shop.example.com").
- **W-4. Verbos** (glosario cerrado):

| Verbo | Significado | Ejemplos |
|---|---|---|
| Create | Algo nuevo que Noust genera | backup, database, token, service |
| Add | Vincula algo que ya existe | domain, destination, server, variable |
| New | Nombre de botón de página de una entidad de primer nivel | "New application", "New service" |
| Delete | Destruye datos que Noust posee | application, backup, site, service |
| Remove | Desvincula; la cosa sigue existiendo fuera | destination, server de la flota, domain, schedule |
| Revoke | Invalida una credencial | token, session, certificate, privilege |
| Drop | Solo SQL: la palabra del sistema | database, role |
| Discard | Descarta cambios sin guardar | "Discard changes" |
| Enable / Disable | Reversible | cron job, webhook, releases |
| Stop / Start / Restart | Ciclo de vida de una unit | app, service |
| Roll back | Volver a una release anterior | release |
| Save / Apply | Save persiste; Apply persiste y ejecuta | |

- **W-5. Números, unidades y fechas** siempre por `lib/format.ts`: tamaños binarios (1 KB = 1024 B) con B, KB,
  MB, GB, TB; porcentajes con un decimal por debajo de 10; duraciones con las dos mayores unidades ("2m 05s");
  relativo "3m ago" / "hace 3 min" con el instante exacto en tooltip (`RelativeTime`); las marcas de tiempo
  que el navegador no sabe situar (systemd con zona con nombre) se muestran verbatim. Nunca `toFixed`,
  `toLocale*` ni `Intl` sueltos (a migrar: `cron/data.ts:114`, `app/metrics/ranges.ts:92,101`).
- **W-6. Vacíos:** título = qué es este lugar y qué falta ("No applications yet"); descripción = una o dos
  frases con la siguiente acción; CTA con verbo imperativo de 1-2 palabras; tono acogedor si nunca se usó,
  neutro si es vacío por naturaleza; con `command` cuando haya equivalente en el CLI (Primer, Atlassian).
- **W-7. Puntuación:** puntos suspensivos siempre `…` (U+2026), nunca `...` (5 cadenas a migrar:
  `en/backups.ts:21,23`, `en/services.ts:135,136`, `en/databases.ts:119`). Título sin punto final; cuerpo con punto.
- **W-8. Lo que no se traduce:** la salida del sistema, los comandos, las rutas, los nombres de unit, los nombres
  de producto y los autónimos de idioma. Sí se traduce todo lo que una persona lee o escucha (i18n/README).
- **W-9. Glosario de estados EN/ES:** Running / En ejecución; Deploying / Desplegando; Queued / En cola;
  In progress / En curso; Succeeded / Correcto; Failed / Fallido; Stopped / Detenido; Static / Estático;
  Warning / Aviso; Unknown / Desconocido; Rolled back / Revertido; Cancelled / Cancelado; No answer / Sin
  respuesta; Restarting / Reiniciando; Building / Compilando. (Extiende la tabla de `i18n/README.md`.)

### 4.8 Accesibilidad

- **A-1.** WCAG 2.2 AA **verificada, no declarada:** axe con etiquetas `wcag22aa` (que incluye `target-size`) en cada
  ruta y en ambos temas, y la galería añadida al barrido.
- **A-2. Foco:** siempre visible (2 px `--focus`, desplazamiento 2); enlace de salto; foco al `h1` tras cada
  navegación; foco devuelto y atrapado en diálogos; `scroll-padding-top: 4.5rem` (2.4.11).
- **A-3. Objetivos:** ≥ 24 px (2.5.8); los controles miden 28/32/40 y 36/40/44 con puntero grueso (D-1).
- **A-4. Estado:** color + forma + palabra (P-2), con test de siluetas distintas.
- **A-5. Regiones vivas:** cortés para progreso y éxito, asertiva para fallos y solo para el resultado de una
  acción del operador; nunca para líneas de log ni para cifras que cambian cada pocos segundos; un único
  `Announcer`.
- **A-6. Teclado:** todo alcanzable; flechas entre filas de tabla; menús y diálogos según APG; `aria-sort`; el
  tooltip complementa y nunca es la única fuente.
- **A-7. Gráficas:** resumen textual y tabla alternativa; el color nunca es el único canal (V-3).
- **A-8. Movimiento reducido** (M-1), **idioma** (`lang` actualizado, `translate="no"` en valores de sistema),
  **zoom** al 200 % y reflow a 320 px sin scroll horizontal de página.

### 4.9 Gobierno del sistema

- **G-1.** Todo cambio de token o de componente actualiza en el mismo PR la galería, `docs/DESIGN.md` y los
  tests. La galería es la especificación ejecutable.
- **G-2. Regla de tres:** un patrón que aparece por tercera vez se sube al kit antes de escribirlo otra vez.
- **G-3. Excepciones** con comentario `// design-exception: <id de regla> <motivo>` en la línea; el test de
  trinquete las cuenta y las lista; una excepción sin motivo falla.
- **G-4.** Cambios que afectan a `panel/src` exigen `npm run build` y commitear `src/noust/web/static` (regla del
  proyecto).
- **G-5.** Una regla nueva entra con su medida actual como línea base y solo puede bajar (parte 5).

---

## 5. Cumplimiento automático

**Principio:** lo que se puede comprobar con una máquina no se deja a la revisión. Capas, de la más barata a la
más cara; todas bloquean en CI porque `npm test` y `npm run lint` ya lo hacen.

### 5.1 Lo que ya se cumple por construcción

Paleta, escala tipográfica, sombras y radios de Tailwind reiniciados (no compilan `bg-red-500` ni `text-sm`);
regla de hex; CSP estricta con Trusted Types y E2E que falla ante cualquier violación; axe en cada ruta y tema;
CLS ≤ 0,05; contraste de tokens; catálogos i18n tipados. **Lo que falta** es lo que cae fuera de esas
garantías: valores arbitrarios, espaciado, radios, uso de componentes, iconos, títulos, capas.

### 5.2 Capa 1: tokens (`styles/tokens.test.ts`, Vitest)

1. `--ok|warn|fail|idle-border` existen en ambos temas y su contraste con `surface` y con su `-soft` es ≥ 1,4:1.
2. **Viz:** `--viz-1|2` ≥ 3:1 sobre `surface`, `bg`, `bg-sunken` y `surface-hover`; ΔE OKLab ×100 entre ellos ≥ 8
   bajo protanopía y deuteranopía (Machado 2009, severidad 1) y ≥ 15 en visión normal; distancia de matiz OKLCH
   ≥ 35° respecto de `accent` y de cada estado. Son ≈ 40 líneas (OKLab y las matrices) en `lib/contrast.ts`, que
   ya tiene `contrastRatio` y `parseHex`.
3. **Paridad:** cada `--color-*` de `@theme inline` en `app.css` apunta a un token declarado en `tokens.css`, y
   cada token de color declarado tiene su `--color-*` (o está en una lista de excepciones: `--ansi-*`).
4. **Contrato de valores:** existen `--radius-chip: 4px`, `--control-sm|md|lg`, `--z-*`, `--measure` y
   `--width-*` con los valores de esta propuesta; `--fs-*` es exactamente el conjunto {12, 13, 14, 16, 18, 24, 32}.
5. **Estado:** cada `Status` tiene un glifo distinto y una clave de etiqueta distinta; el glifo de `queued` no
   lleva `animate-spin` (test sobre `STATUS` y sobre el SVG renderizado).
6. `TEXT_PAIRS` y `UI_PAIRS` incluyen los tokens nuevos, y **un token de color declarado que no aparece en ningún
   par** (ni está en la lista de excepciones: `--ansi-*` ya tiene el suyo, `--backdrop` no es sólido) hace fallar el test:
   cada color nace con, al menos, un par verificado.

### 5.3 Capa 2: ESLint (`npm run lint`, cero avisos)

Se amplía el `no-restricted-syntax` que ya prohíbe el hex (hay que **fusionar** los selectores en el mismo
arreglo, no añadir otra regla que lo pise). Boceto (no ejecutado: esta investigación es de solo lectura):

```js
"no-restricted-syntax": ["error",
  { selector: "Literal[value=/#[0-9a-fA-F]{3,8}\\b/]", message: "Use a design token (var(--...))." },
  // Valores arbitrarios de dimensión en clases (no las variantes has-[ / data-[ / transition-[).
  { selector: "Literal[value=/(^|[\\s:])-?(m[trblxy]?|p[trblxy]?|gap(-[xy])?|space-[xy]|size|w|h|min-w|max-w|min-h|max-h|text|rounded(-[a-z]{1,2})?|top|right|bottom|left|inset(-[xy])?|z|leading|tracking|basis|shadow)-\\[/]",
    message: "Arbitrary value: use a token or a named utility (rules S-1, S-5, F-1, F-4, Y-1)." },
  { selector: "TemplateElement[value.raw=/…misma expresión…/]", message: "… (idem, plantillas)" },
  // Sin rgb()/hsl()/color-mix() en clases: el lint del hex no los ve.
  { selector: "Literal[value=/(rgb|hsl|oklch|color-mix)\\(/]", message: "Colour functions belong in tokens.css (C-9)." },
  // Componentes que no se reconstruyen.
  { selector: "JSXOpeningElement[name.name=/^h[1-4]$/]", message: "Use Section / Subsection / PageHeader (K-9)." },
  { selector: "JSXOpeningElement[name.name='label']", message: "Use Field (K-2)." },
  { selector: "JSXAttribute[name.name='style']", message: "Inline styles only in the allow-listed files (chart, log viewer)." },
],
"no-restricted-imports": ["error", { paths: [
  { name: "lucide-react", importNames: ["CircleCheck","TriangleAlert","CircleAlert","OctagonAlert","ShieldCheck","ShieldAlert","Info"],
    message: "Semantic icons come from components/ui/icons.ts (Ic-1, Ic-6)." } ] }],
```

Y un bloque `overrides` que **desactiva** esas reglas en `src/components/ui/**`, `src/components/page/**`,
`src/app/**`, `src/dev/**` y tests (ahí viven los ladrillos). Fuera de ahí, cada infracción exige un
`// design-exception:` (G-3). Las reglas de estructura del selector de JSX se pueden restringir a
`src/features/**` y `src/nodes/**`.

### 5.4 Capa 3: trinquete de reglas de texto (`styles/design-rules.test.ts`, Vitest)

Para lo que un selector no expresa bien (patrones que cruzan varias clases o varios ficheros) y para migrar sin
romper: un test que lee todos los `.ts/.tsx` de producción con `import.meta.glob("../**/*.{ts,tsx}", { eager:
true, query: "?raw", import: "default" })` (excluye tests, `dev/` y `*.gen.ts`), cuenta coincidencias por regla y
las compara con una **línea base versionada** (`styles/design-baseline.json`). El recuento puede bajar, nunca
subir; si baja, el test falla pidiendo `npm run design:baseline` para fijar el nuevo mínimo (así el progreso no
se pierde). Reglas y línea base actual (todas medidas en esta investigación):

| Regla | Expresión (resumen) | Base | Meta |
|---|---|---|---|
| `hand-surface` | `rounded-card` con `bg-surface` en la misma cadena, fuera de `components/ui/Card` | 69 | 0 |
| `tinted-border` | `border-(ok\|warn\|fail\|accent)/\d+` | 34 | 0 |
| `alpha-on-token` | `(bg\|text\|border\|divide)-(ok\|warn\|fail\|accent\|fg\|surface\|bg)…/\d+` | 56 | ≤ 2 (`bg-bg/85`) |
| `arbitrary-dimension` | la expresión del lint | 152 | ≤ 20 (allowlist) |
| `chip-radius` | `rounded-\[4px\]` | 52 | 0 (→ `rounded-chip`) |
| `off-scale-space` | espaciados 7, 9, 11, 13, 14, 15, 6,5 | ≈ 10 | 0 |
| `tone-map` | `TONE_TEXT\|TONE_SOFT\|TONE_RAIL` declarados fuera de `StatusPill` | 5 ficheros | 0 |
| `empty-cell` | `function Nothing` y `text-fg-faint">-</span>` | 4 + 6 | 0 |
| `mono-utility` | `className` con `\bmono\b` fuera de `components/ui` | 132 | ≤ 20 |
| `raw-heading` | `<h[1-4]` en `features/` y `nodes/` | 40 | 0 |
| `raw-label` | `<label` fuera de `Field` | 13 | 0 |
| `dashed-empty` | `border-dashed` fuera de `EmptyState` | 5 | 0 |
| `spinner-outside-kit` | `<Spinner` en `features/` (los 8 llevan `text-warn`; 3 son un progreso de job) | 8 | ≤ 3 |
| `semibold` | `font-semibold\|font-bold` | 7 | 0 |
| `z-arbitrary` | `z-\[` | 2 | 0 |
| `focus-ring-repeat` | `focus-visible:outline-2` | 76 | ≤ 10 |
| `confirm-hand-rolled` | `AlertDialog.Popup` fuera de `ConfirmDialog` | 3 | 0 |

Además, dos comprobaciones binarias (sin línea base): `Card`, `Notice` y `EmptyState` **se importan** desde
`features/` al menos una vez cada uno (detecta un componente muerto: `Card` y `Progress` hoy tienen 0 usos), y
ningún `--viz-*` se referencia fuera de `Chart`.

### 5.5 Capa 4: E2E (Playwright)

- **Galería como puerta de diseño.** Un proyecto `design` que arranca Vite en desarrollo (la galería no está en
  el build) y visita `/__design` en ambos temas: axe con las mismas etiquetas, captura de CSP y de errores de
  consola, y `toHaveScreenshot` a 1440 y 390 px. La galería se amplía con lo que falta (shell, plantillas,
  `Notice`, formulario completo, iconos, movimiento, gráfica con `--viz-*`, tabla con las cuatro variantes de
  vacío). Una regresión visual de un componente deja de depender de que alguien mire.
- **`design-contract.spec.ts`: auditoría de estilos computados** en cada ruta de `routes.ts`, en ambos temas y en
  390/1440: recorre los elementos visibles y comprueba que `font-size` ∈ {12, 13, 14, 16, 18, 24, 32} px (salvo el
  canvas), `border-radius` ∈ {0, 4, 6, 10, ≥ 999}, anchos de borde ∈ {0, 1, 2}, `font-family` ∈ {sans, mono,
  emoji}, y que todo `color`, `background-color` y `border-color` es **gris (croma < 2/255) o coincide con un
  token** resuelto en ese tema (la técnica de la sonda ya está en `Chart.readPalette`). Atrapa lo que ningún
  análisis estático ve: estilos en línea, opacidades, valores heredados de una librería. Objetivos táctiles ya
  los cubre axe (`target-size`, `wcag22aa`).
- **Regla de "una primaria":** en cada ruta, a lo sumo un `Button variant="primary"` visible fuera de un diálogo
  abierto (`page.locator('[data-variant=primary]')`; hace falta que `Button` exponga `data-variant`).
- **Servidor en las acciones peligrosas:** en la ruta `/n/<servidor>/…`, el diálogo de confirmación contiene el
  nombre del servidor (X-10).

### 5.6 Revisión visual (lo que sigue siendo de una persona o de un modelo)

Las capturas de `e2e:screens` (ambos temas, 1440 y 390 px) se revisan con una lista corta derivada de los
principios: ¿una sola primaria?, ¿el estado se lee en escala de grises?, ¿hay algo coloreado que no sea estado
ni acción?, ¿se mueve algo al cargar?, ¿el servidor se nombra en lo peligroso?, ¿el vacío dice qué hacer?

### 5.7 Lo que no se propone (a propósito)

Una escala de 12 pasos por matiz (60 tokens para cinco matices que gastan tres); un conmutador de densidad;
más radios; un tema oscuro generado por fórmula; Style Dictionary o tokens en JSON (CSS + Tailwind ya son la
fuente única); Storybook (la galería propia ya corre bajo la CSP estricta y no añade dependencia).

---

## 6. Plan, orden y decisiones

### 6.1 Orden de trabajo (cada fase es un PR que se puede publicar)

| Fase | Contenido | Riesgo visual |
|---|---|---|
| 0. Tokens sin cambio de aspecto | `--*-border`, `--viz-*`, `--radius-chip`, `--control-*`, `--z-*`, `--measure`, `--width-*`; `@theme inline`; tests de 5.2 | Ninguno |
| 1. Ladrillos nuevos | `Notice`, `JobProgress`, `FilterBar`, `EmptyState variant`, `EmptyCell`, `Subsection`, `AuthCard`, `Page`/`Split`, `PageHeader tabs/mono/status`, `ConfirmDialog confirm`, `Field action`, estado `queued`, `Meter` con glifo; `Card` ampliada; mapa de iconos; ampliar la galería | Ninguno hasta migrar |
| 2. Trinquete y lint | Reglas de 5.3 y 5.4 con la línea base **de hoy** (no exigen migrar) | Ninguno |
| 3. Migración por familias, bajando la línea base | (a) avisos → `Notice` (34 bordes); (b) superficies → `Card` (69); (c) progreso de job → `JobProgress` (3); (d) encabezados → `Section`/`Subsection` (40); (e) vacíos y celdas (8 + 4 + 6); (f) tablas: orden de columnas (6 tablas); (g) `rounded-[4px]` → `rounded-chip` (52); (h) `mono` → `<Mono>` (132); (i) etiquetas → `Field` (13); (j) confirmaciones por niveles (≈ 10 diálogos) | Bajo: unificar rellenos mueve 1-2 px; validar con las capturas |
| 4. Contrato en el navegador | `design-contract.spec.ts`, proyecto `design`, servidor en diálogos de flota | Ninguno |
| 5. Decisiones de producto | Color de serie 1, `Progress`, sudo con renovación | Visible |

Cada cambio bajo `panel/src` exige `npm run build` y commitear `src/noust/web/static`. El orden 0 → 1 → 2
deja el sistema **protegido antes de migrar**, de modo que la migración no pueda deshacerse sola.

### 6.2 Decisiones que necesitan al dueño

1. **Color de la serie 1 de las gráficas:** hoy violeta (el color de lo accionable). Propuesta: azul `--viz-1`
   (V-5) y el violeta queda solo para lo interactivo. Alternativa: mantenerlo para gráficas de una serie.
2. **Escala de espaciado:** oficializar los medios pasos (S-1), porque el código ya los usa 440 veces, o migrar a
   4 px estricto (≈ 440 cambios con riesgo visual). Se recomienda oficializar.
3. **Estado segundo en las tablas** (X-7): cambia 6 tablas (Aplicaciones, Servicios, Despliegues recientes,
   entregas de webhook, Actividad, Cron) que hoy ponen el estado primero. Alternativa: aceptar "estado primero"
   como regla y mover Servidores, Tokens, Sites, Certificados, Despliegues y Flota. Se recomienda identidad
   primero por accesibilidad de la fila y por el consenso (Primer, Supabase).
4. **Idioma y sitio del documento normativo:** `docs/DESIGN.md` en inglés (como `BRAND.md` y el resto de
   `docs/`) o en español (como las especificaciones de `docs/superpowers/`).
5. **Fricción por niveles** (X-2): acepta que unas 10 de las 18 confirmaciones de teclear pasen a un diálogo
   simple.
6. **Menores:** `Progress` en ámbar o eliminarlo; `Card` adoptada o eliminada (se recomienda adoptarla);
   sudo con renovación del plazo (backend); demora de 300 ms del esqueleto (M-3, a medir); botones de envío
   deshabilitados por campo vacío (X-6: dejarlos, o seguir a Primer y habilitarlos siempre).

### 6.3 Riesgos y límites de esta investigación

- Todos los recuentos son de esta fecha y de la rama `dev/3.1`; los que vienen de una expresión regular sobre JSX
  multilínea se marcan "≈". La lista completa de comandos está en el anexo D.
- La clasificación de los botones por rol (I-11) es heurística sobre las claves del catálogo; la distribución por
  variante y tamaño es exacta, la asignación a "Hecho/Cerrar/Guardar" es aproximada.
- Las reglas ESLint y el test de trinquete de la parte 5 son bocetos: no se ejecutaron (la tarea era de solo
  lectura). Habrá que ajustar las expresiones al ejecutarlas por primera vez para obtener la línea base exacta.
- De Railway, Render y Coolify solo se cita lo que su documentación dice de los estados; no hay sistema de
  diseño público que citar. Carbon se leyó en parte (ver el aviso de la parte 3).
- Las medidas de contraste y de ΔE de `--viz-*` y `--*-border` son reproducibles (anexos A y B) pero **no están
  aún en `tokens.css`**: son una propuesta calculada, no un cambio aplicado.

---

## Anexos

### Anexo A. Lista completa de tokens (existentes y propuestos)

| Familia | Token | Estado |
|---|---|---|
| Superficie | `--bg`, `--bg-sunken`, `--surface`, `--surface-raised`, `--surface-hover`, `--surface-active` | existe |
| Borde | `--border`, `--border-strong` | existe |
| Texto | `--text`, `--text-muted`, `--text-faint` | existe |
| Acento | `--accent`, `--accent-hover`, `--accent-soft`, `--accent-text`, `--on-accent` | existe |
| Estado | `--ok`, `--ok-soft`, `--warn`, `--warn-soft`, `--fail`, `--fail-soft`, `--fail-strong`, `--idle`, `--idle-soft` | existe |
| Estado | `--ok-border`, `--warn-border`, `--fail-border`, `--idle-border` | **nuevo** |
| Foco y selección | `--focus`, `--selection`, `--match` | existe |
| Identidad de serie | `--viz-1`, `--viz-2` | **nuevo** |
| ANSI | `--ansi-blue`, `--ansi-magenta`, `--ansi-cyan` | existe (solo `LogViewer`) |
| Velo | `--backdrop` | existe |
| Tipografía | `--font-sans`, `--font-mono`, `--fs-12…32`, `--lh-12…32`, `--stretch-title`, `--tracking-title`, `--tracking-display` | existe |
| Medida | `--measure` (68ch), `--measure-help` (52ch) | **nuevo** |
| Espacio | `--space` (0,25 rem) | existe |
| Forma | `--radius-control` 6, `--radius-card` 10, `--radius-pill` 999 | existe |
| Forma | `--radius-chip` 4 | **nuevo** |
| Elevación | `--shadow-color`, `--shadow-raised`, `--shadow-overlay` | existe |
| Elevación | `--shadow-inset-highlight` | **nuevo** (o eliminar el resalte) |
| Movimiento | `--duration-fast` 120, `--duration-base` 180, `--ease-out` | existe |
| Controles | `--control-sm` 1,75 rem, `--control-md` 2 rem, `--control-lg` 2,5 rem (36/40/44 con puntero grueso) | **nuevo** |
| Capas | `--z-sticky` 30, `--z-backdrop` 40, `--z-overlay` 50, `--z-toast` 60, `--z-skip` 70 | **nuevo** |
| Anchos | `--width-narrow` 46 rem, `--width-form` 72 rem, `--width-wide` 100 rem | **nuevo** |

### Anexo B. Cómo se validó la paleta de series y los bordes de estado

Herramienta: `scripts/validate_palette.js` del skill de dataviz (ΔE en OKLab ×100; simulación de
Machado, Oliveira y Fernandes 2009 a severidad 1; umbrales: CVD ≥ 8 objetivo y 6 suelo, visión normal ≥ 15,
contraste ≥ 3:1, luminosidad OKLCH 0,43-0,77 en claro y 0,48-0,67 en oscuro, croma ≥ 0,10).

```
node validate_palette.js "#0274c7,#9b2673" --mode light --surface "#ffffff"
  Lightness band PASS · Chroma floor PASS · CVD PASS (13,7 deutan; tritan 27,6)
  Normal-vision PASS (25,0) · Contrast >= 3:1 PASS
node validate_palette.js "#3496ef,#ca549d" --mode dark  --surface "#171717"
  Lightness band PASS · Chroma floor PASS · CVD PASS (13,3 deutan; tritan 28,5)
  Normal-vision PASS (24,7) · Contrast >= 3:1 PASS
```

Contraste de `--viz-1|2` sobre `bg`, `bg-sunken`, `surface-hover`: claro 4,53/6,72, 4,26/6,31, 4,34/6,43;
oscuro 6,07/4,74, 6,33/4,94, 5,12/3,99 (todos ≥ 3:1).

Resultados que **motivan** decisiones: (a) los tres `--ansi-*` como serie fallan (claro: croma del cian 0,087 y
ΔE 3,6; oscuro: ΔE 1,4 y luminosidad fuera de banda); (b) los cuatro colores de estado, como paleta categórica,
dan ΔE 3,3 (claro) y 3,4 (oscuro) bajo deuteranopía entre rojo y ámbar o verde y 11,0/14,7 en visión normal:
demuestran por qué el estado necesita forma y palabra (P-2); el validador es de paletas categóricas y no
juzga un estado que siempre va acompañado; se cita como medida, no como suspenso.

Los bordes de estado se calcularon como el color de estado al 35 % sobre `surface` (`#ffffff` / `#171717`);
tabla de contraste en 4.2.1.

### Anexo C. Fuentes

- Vercel Geist: https://vercel.com/geist/colors · https://vercel.com/geist/materials ·
  https://vercel.com/geist/typography · https://vercel.com/geist/introduction
- Linear: https://linear.app/now/behind-the-latest-design-refresh ·
  https://linear.app/now/how-we-redesigned-the-linear-ui
- GitHub Primer: https://primer.style/product/primitives/color/ · https://primer.style/product/primitives/size/ ·
  https://primer.style/product/ui-patterns/notification-messaging/ ·
  https://primer.style/product/ui-patterns/degraded-experiences/ · https://primer.style/product/ui-patterns/loading/ ·
  https://primer.style/product/ui-patterns/empty-states/ · https://primer.style/product/ui-patterns/forms/ ·
  https://primer.style/product/ui-patterns/saving/ · https://primer.style/product/scenario-patterns/delete ·
  https://primer.style/product/components/button/guidelines/ ·
  https://primer.style/product/components/data-table/guidelines/ ·
  https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/sudo-mode
- Stripe: https://stripe.com/blog/accessible-color-systems · https://docs.stripe.com/stripe-apps/patterns ·
  https://docs.stripe.com/stripe-apps/design
- Atlassian: https://atlassian.design/foundations/content/designing-messages ·
  https://atlassian.design/foundations/content/designing-messages/empty-state ·
  https://atlassian.design/foundations/content/designing-messages/error-messages ·
  https://atlassian.design/foundations/tokens/design-tokens/
- IBM Carbon y Dynatrace: https://v10.carbondesignsystem.com/patterns/status-indicator-pattern/ ·
  https://raw.githubusercontent.com/carbon-design-system/carbon-website/main/src/pages/components/notification/usage.mdx ·
  https://carbondesignsystem.com/components/data-table/usage/ · https://carbondesignsystem.com/components/data-table/style ·
  https://carbondesignsystem.com/elements/motion/overview/ · https://developer.dynatrace.com/design/patterns/status-and-health/
- Radix Colors: https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale
- Grafana: https://grafana.com/docs/grafana/latest/panels-visualizations/visualizations/time-series/ ·
  https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/best-practices/
- Plataformas: https://docs.railway.com/reference/deployments ·
  https://render.com/changelog/see-your-service-s-deploys-on-a-new-page · https://coolify.io/docs/applications/operations/overview ·
  https://supabase.com/design-system (layout, modality, forms, tables bajo `/docs/ui-patterns/`)
- Estándares: https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html ·
  https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html ·
  https://www.nngroup.com/articles/skeleton-screens/

### Anexo D. Cómo reproducir los recuentos

Todos desde `panel/src`, con `rg` (ripgrep); "no test" = `--glob '!*.test.*'`.

```
# I-1 Card sin usar y superficies a mano
rg -w 'Card' --glob '!*.test.*' -g '*.tsx' features app nodes                     # 0
rg -c 'rounded-card[^"]*bg-surface|bg-surface[^"]*rounded-card' -g '*.tsx' features   # 69 en 39 ficheros
# I-2 avisos tintados
rg -o 'border-(ok|warn|fail|accent)/[0-9]+' --glob '!*.test.*' -g '*.tsx' .        # 34
# I-4 helpers duplicados
rg 'TONE_TEXT\s*(:|=)' --glob '!*.test.*' -g '*.tsx' .                             # 6 declaraciones (1 canónica)
rg 'function Nothing|const Nothing' --glob '!*.test.*' -g '*.tsx' features          # 4 (+1 diálogo no relacionado)
# I-12 medios pasos de espaciado
rg -o --pcre2 '(?<![a-z0-9.-])-?(p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|gap-x|gap-y)-(0\.5|1\.5|2\.5|3\.5)\b' \
   --glob '!*.test.*' --glob '!dev/**' -g '*.tsx' features app components nodes        # 440
# I-13 valores arbitrarios de dimensión (la expresión de 5.3), por carpeta
rg -o --pcre2 '<expresión de 5.3>' --glob '!*.test.*' -g '*.tsx' features components app nodes   # 152
# I-15 confirmaciones
rg -o '<ConfirmDialog\b' --glob '!*.test.*' -g '*.tsx' features                     # 18
# Botones: variante × tamaño (script Python sobre <Button ...> con regex, ver 2.2 I-11)
```

Las tablas de columnas (I-10) salen de un script que extrae los `id:` de cada `columns`/`columnsFor` de
`features/**`; las cifras de contraste y ΔE, del validador del anexo B y de un cálculo directo de mezclas sRGB
(`#rrggbb` = 35 % del color de estado + 65 % del fondo).
