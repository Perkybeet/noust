import type { connections as en } from "../en/connections";
import type { Catalog } from "../types";

export const connections: Catalog<typeof en> = {
  title: "Código en tus servidores",
  description: "La central guarda la App de GitHub. Presta a cada servidor un token para los repositorios que despliega y le pasa los pushes.",
  tableCaption: "Cómo llega cada servidor a sus repositorios",
  loading: "Cargando cómo llegan los servidores a sus repositorios",
  column: { server: "Servidor", state: "Estado", tokens: "Tokens" },
  state: {
    leased: "Tiene un token",
    none: "Nada que prestar",
    independent: "Independiente",
    ceiling: "Limitado por el servidor",
    unreachable: "Sin conexión",
    no_app: "Aún sin aplicación",
    unknown: "Sin leer aún",
  },
  tokens: {
    none: "Sin tokens",
    count: { one: "{count} token", other: "{count} tokens" },
    expires: "Se renueva antes de acabar, en {time}",
    uncovered: { one: "{count} repositorio sin token", other: "{count} repositorios sin token" },
  },
  renew: {
    all: "Renovar ahora",
    one: "Renovar",
    label: "Renovar los tokens de {server}",
    failed: "No se pudieron renovar los tokens",
  },
  deliveries: {
    title: "Pushes reenviados",
    delivered: { one: "{count} entregado", other: "{count} entregados" },
    pending: { one: "{count} en espera", other: "{count} en espera" },
    failed: { one: "{count} fallido", other: "{count} fallidos" },
    expired: { one: "{count} caducado", other: "{count} caducados" },
    retryLabel: "Reintentar el push de {repository} a {server}",
    retryFailed: "No se pudo reintentar el push",
    caption: "Pushes que no llegaron",
    column: { repository: "Repositorio", server: "Servidor", attempts: "Intentos", state: "Estado" },
  },
  deployKeys: {
    nav: {
      label: "Claves de despliegue",
      keywords: "claves ssh repositorio privado clonar claves de host fijadas huella deploy keys",
      command: "Ajustes de claves de despliegue",
    },
  },
  allowed: {
    title: "De dónde puede venir el código",
    description: "Este servidor nombra los orígenes de los que acepta código. Cualquier otro se rechaza.",
    listLabel: "Orígenes permitidos",
    recipesAllowed: "Las recetas del catálogo se pueden desplegar.",
    recipesBlocked: "Las recetas del catálogo no están en la lista, así que Noust se negaría a desplegar una.",
    allowRecipes: "Permitir las recetas del catálogo",
    confirmTitle: "¿Permitir las recetas del catálogo?",
    confirmBody: "Se añade la palabra recipes a la lista y no cambia nada más. El código de cualquier otro sitio sigue rechazado.",
  },
};
