import type { common as en } from "../en/common";
import type { Catalog } from "../types";

export const common: Catalog<typeof en> = {
  area: "Cargando",
  copyButton: {
    label: "Copiar",
    copyCommand: "Copiar comando",
    copyOutput: "Copiar salida",
    copyValue: "Copiar {value}",
    copied: "Copiado al portapapeles",
    failed: "Error al copiar",
  },
  copyTextButton: {
    failed: "Error al copiar. Selecciona el texto y cópialo a mano.",
  },
  dialog: {
    close: "Cerrar",
  },
  field: {
    optional: "Opcional",
  },
  confirmDialog: {
    cancel: "Cancelar",
    typeToConfirm: "Escribe {value} para confirmar",
    failed: "La acción falló. El sistema dijo:",
    whatSystemSaid: "Lo que dijo el sistema",
  },
  select: {
    placeholder: "Seleccionar",
  },
  dataTable: {
    actions: "Acciones",
  },
  logViewer: {
    label: "Salida del registro",
    searchLabel: "Buscar en la salida",
    noMatches: "Sin coincidencias",
    matchPosition: "{current} de {total}",
    previousMatch: "Coincidencia anterior",
    nextMatch: "Siguiente coincidencia",
    follow: "Seguir",
    wrapLines: "Ajustar líneas",
    downloadOutput: "Descargar salida",
    fullLogHint: "Copia o descarga el registro completo, no solo las líneas mostradas.",
    jumpToLatest: "Ir a lo último",
    newLines: "líneas nuevas",
    empty: "Todavía no hay salida.",
  },
  toast: {
    dismiss: "Descartar notificación",
    systemSaid: "Lo que dijo el sistema",
    commandOutput: "La salida propia del comando",
  },
  statusPill: {
    running: "En ejecución",
    deploying: "Desplegando",
    warning: "Aviso",
    failed: "Fallido",
    stopped: "Detenido",
    static: "Estático",
    unknown: "Desconocido",
  },
  errorBlock: {
    tryAgain: "Reintentar",
    systemSaidLabel: "{title}: lo que dijo el sistema",
    commandOutputLabel: "{title}: la salida propia del comando",
  },
  queryState: {
    loading: "Cargando {label}",
    couldNotLoad: "No se pudo cargar {label}",
    couldNotRefresh: "No se pudo actualizar {label}. Lo que sigue es la última respuesta.",
  },
  dangerZone: {
    title: "Zona de peligro",
  },
  keyValueList: {
    empty: "Sin establecer",
  },
  resourceMeter: {
    missing: "Sin lectura",
    ofLimit: "{value} de {limit}",
    limit: "Límite {value}",
    noLimit: "Sin límite establecido",
  },
  chart: {
    noDataFor: "{label}: sin datos",
    seriesReading: "{label}: último {latest}, mínimo {low}, máximo {high}",
    markersInView: { one: "{count} marca en vista.", other: "{count} marcas en vista." },
    seriesLabel: "Series",
    latest: "Último",
    noReading: "sin lectura",
    time: "Hora",
    markersHeading: "Marcas",
    zoomIn: "Acercar",
    zoomOut: "Alejar",
    resetZoom: "Restablecer zoom",
    viewAsTable: "Ver como tabla",
    expand: "Ampliar {title}",
    dataRegion: "Datos de {title}",
    newestFirst: "{title}, más recientes primero",
    dragToZoom: "Arrastra sobre la gráfica para acercar un tramo de tiempo.",
    zoomedRange: "Mostrando de {from} a {to}. Haz doble clic en la gráfica o restablece el zoom para verla completa.",
    keyboardHint:
      "Las flechas izquierda y derecha recorren las muestras, Inicio y Fin van a la primera y la última, Escape limpia la selección.",
    keyboardHintZoomable: " Arrastra sobre la gráfica con el puntero para acercar, o usa los botones de zoom.",
  },
};
