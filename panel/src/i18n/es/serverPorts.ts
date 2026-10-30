import type { serverPorts as en } from "../en/serverPorts";
import type { Catalog } from "../types";

export const serverPorts: Catalog<typeof en> = {
  label: "los puertos",
  intro: "Lo que escucha en este servidor, de quién es y quién puede llegar a ello.",
  open: "Abrir un puerto",
  openThis: "Abrir el puerto {port}",
  free: "Nada de este servidor usa el puerto {port}.",
  filterLabel: "Filtrar los puertos",
  searchLabel: "Buscar puertos",
  searchPlaceholder: "Un puerto o lo que lo usa",
  count: { one: "{count} puerto", other: "{count} puertos" },
  countFiltered: "{shown} de {total} puertos",
  caption: "Puertos y lo que los usa",
  none: "Ningún puerto coincide.",
  notListening: "Todavía no escucha nada",
  column: {
    port: "Puerto",
    holder: "Lo usa",
    reach: "Quién puede llegar",
  },
  holder: {
    web: "El servidor web",
    ssh: "SSH",
    console: "Esta consola",
    dns: "Noust DNS",
    app: "Aplicación",
    stack: "Stack de Compose de",
    stream: "Puerto publicado",
    database: "Base de datos",
    instance: "Servidor de bases de datos",
    process: "Proceso",
  },
  firewallOff: {
    title: "El cortafuegos está apagado",
    body: "Cualquier puerto que escucha está abierto para todo el mundo.",
    link: "Encenderlo",
  },
  viewsLabel: "Vista de red",
  views: {
    ports: "Puertos",
    firewall: "Cortafuegos",
    published: "Puertos publicados",
    interfaces: "Interfaces",
  },
};
