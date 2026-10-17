import type { metricsExport as en } from "../en/metricsExport";
import type { Catalog } from "../types";

export const metricsExport: Catalog<typeof en> = {
  title: "Métricas para un recolector",
  description: "Prometheus, Grafana Alloy, Netdata y un Collector de OpenTelemetry leen desde una sola dirección el valor más reciente de cada métrica de este servidor, de cada aplicación y de cada contenedor.",
  addressLabel: "Dirección que consultar",
  copyAddress: "Copiar la dirección que consultar",
  configLabel: "Una entrada de Prometheus para ella",
  copyConfig: "Copiar la entrada de Prometheus",
  credential: "El recolector necesita un token que solo pueda leer, en el archivo indicado arriba. Crea uno en {tokens}; no hace falta nada más, y no puede cambiar nada.",
  tokensLink: "Tokens de API",
  createToken: "Crear el token desde un terminal",
  fromTerminal: "El mismo texto, desde un terminal",
};
