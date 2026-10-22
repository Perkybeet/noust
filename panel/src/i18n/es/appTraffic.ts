import type { appTraffic as en } from "../en/appTraffic";
import type { Catalog } from "../types";

export const appTraffic: Catalog<typeof en> = {
  traffic: {
    title: "Quién pide qué",
    description: "Del registro de accesos del servidor web, por horas. No se guarda ninguna cadena de consulta, y un visitante es su red, no su dirección.",
    windowLabel: "Ventana del tráfico",
    window: { "24h": "24 horas", "7d": "7 días", "30d": "30 días", "90d": "90 días" },
    loading: "Cargando el tráfico",
    loadFailed: "No se pudo leer el tráfico",
    requests: "Peticiones",
    requestsDetail: "{ok} respondidas bien",
    errors: "Errores del servidor",
    errorsDetail: "{share} de las peticiones",
    clientErrors: "Errores del cliente (4xx)",
    served: "Datos servidos",
    visitors: "Visitantes",
    visitorsDetail: "Direcciones distintas con su navegador",
    latency: "Tiempo de respuesta (p95)",
    latencyDetail: "La mitad tardó menos de {median}",
    chartTitle: "Peticiones en el tiempo",
    hourly: "totales por hora",
    daily: "totales por día",
    hourCell: "total de 1 hora",
    dayCell: "total de 1 día",
    noRequests: "Ninguna petición en esta ventana.",
    rankings: {
      paths: "Páginas más pedidas",
      networks: "Redes",
      statuses: "Códigos de estado",
      referrers: "De dónde venían",
    },
    rankingEmpty: "Aún nada.",
  },
  containers: {
    title: "Por contenedor",
    description: "Cada contenedor del stack por separado, para que el servicio que lee el disco sea el nombrado.",
    roles: {
      cpu: "CPU por contenedor",
      memory: "Memoria por contenedor",
      disk_read: "Lectura de disco por contenedor",
      disk_write: "Escritura de disco por contenedor",
      net_rx: "Recibido por contenedor",
      net_tx: "Enviado por contenedor",
    },
    busiest: "Se muestran los {shown} más activos de {total} contenedores.",
  },
};
