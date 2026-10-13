import type { buildExtras as en } from "../en/buildExtras";
import type { Catalog } from "../types";

export const buildExtras: Catalog<typeof en> = {
  cache: {
    title: "Caché de compilación",
    description: "Lo que descargan los gestores de paquetes, los módulos de Go y Cargo se conserva entre despliegues, fuera de cada release, para que una compilación solo traiga lo que cambió.",
    loading: "Cargando la caché de compilación",
    readFailed: "No se pudo leer la caché de compilación",
    size: "Tamaño",
    path: "Guardada en",
    none: "Vacía",
    empty: "Vaciar la caché",
    emptyTitle: "¿Vaciar la caché de compilación de {domain}?",
    emptyDescription: "La próxima compilación descarga todo de nuevo, así que tarda más. No se toca nada de lo que está en marcha.",
    emptyAction: "Vaciar la caché",
    emptied: "Se vació la caché de compilación de {domain}",
    nothingFreed: "La caché de compilación de {domain} ya estaba vacía",
    rebuild: "Reconstruir sin caché",
    rebuildDescription: "Compila de nuevo desde cero la versión que está en marcha: se vacía la caché, se reinstalan las dependencias y las imágenes se construyen sin sus capas.",
    fromTerminal: "Lo mismo, desde un terminal",
  },
  processes: {
    title: "Tipos de proceso",
    description: "Lo que el repositorio dice que ejecuta, en processes: de noust.yaml o en un Procfile.",
    loading: "Leyendo los tipos de proceso",
    readFailed: "No se pudieron leer los tipos de proceso",
    used: "Lo usan los despliegues",
    declared: "Declarado",
    problem: "Un Procfile que no se puede usar",
    source: "Declarado en {source}",
    fromTerminal: "Lo mismo, desde un terminal",
  },
};
