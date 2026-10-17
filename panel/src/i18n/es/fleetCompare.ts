import type { fleetCompare as en } from "../en/fleetCompare";
import type { Catalog } from "../types";

export const fleetCompare: Catalog<typeof en> = {
  metrics: {
    title: "Comparar servidores",
    description: "Una misma medida de todos los servidores en la misma ventana, del historial de cada uno. La memoria, el swap y el disco son la parte de lo que tiene cada servidor.",
    metricLabel: "Qué comparar",
    windowLabel: "Hasta cuándo atrás",
    metric: {
      cpu: "CPU",
      memory: "Memoria",
      swap: "Swap",
      disk: "Disco",
      load: "Carga (1 minuto)",
      net_rx: "Red recibida",
      net_tx: "Red enviada",
    },
    window: { "1h": "1 hora", "6h": "6 horas", "24h": "24 horas", "7d": "7 días", "30d": "30 días" },
    caption: "{metric} de todos los servidores, últimas {window}",
    latest: "Ahora",
    average: "Media",
    peak: "Pico",
    noReading: "Este servidor no registró nada en la ventana",
    gaps: { one: "{count} servidor no respondió, así que falta aquí.", other: "{count} servidores no respondieron, así que faltan aquí." },
    none: "Ningún servidor registró esto en la ventana.",
    loadFailed: "No se pudieron comparar los servidores",
  },
  logs: {
    scopeLabel: "Qué servidores",
    thisServer: "Este servidor",
    everyServer: "Todos los servidores",
    appNote: "Buscando en todos los servidores: el filtro de aplicación vale para un solo servidor.",
    summary: { one: "{count} línea que coincide, de {servers} servidores.", other: "{count} líneas que coinciden, de {servers} servidores." },
    nothing: "Ningún servidor tiene una línea que coincida.",
    perServer: "Hasta {limit} líneas de cada servidor.",
    loadFailed: "No se pudo buscar en los registros de los servidores",
  },
};
