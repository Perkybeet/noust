import type { settingsHome as en } from "../en/settingsHome";
import type { Catalog } from "../types";

export const settingsHome: Catalog<typeof en> = {
  documentTitle: "Ajustes",
  lead: "Todo lo que puedes configurar, según para qué sirve.",
  doors: "Ajustes de {group}",
  summary: {
    accounts: "Quién tiene cuenta y qué puede hacer cada persona.",
    access: "Los roles que lo deciden y qué permite cada uno.",
    sso: "Entrar con el directorio de tu empresa.",
    approvals: "Quién debe aprobar los cambios más delicados.",
    console: "La dirección que abren los enlaces de los avisos.",
    "sign-in-image": "Una imagen de tu empresa en la pantalla de entrada.",
    notifications: "A dónde llegan los mensajes y cómo silenciarlos un rato.",
    "log-shipping": "Enviar registros y métricas a otro servicio.",
    integrations: "GitHub, GitLab y dónde vive tu código.",
    "deploy-keys": "Una clave SSH para cada repositorio y las claves de host fijadas.",
    registries: "Cuentas para descargar imágenes privadas.",
    "secret-managers": "Cajas fuertes de las que leer variables.",
    cloud: "Cuentas en la nube que crean servidores por ti.",
    compliance: "Las normativas que sigues, como el ENS, y cómo vas.",
    "access-security": "Segundo factor, sesiones y el token para emergencias.",
    audit: "Quién hizo qué y cuándo.",
    tokens: "Claves para scripts e integración continua.",
    api: "Todas las llamadas que acepta la API.",
    mcp: "Conectar programas externos mediante MCP.",
    servers: "Añadir, enlazar o quitar servidores.",
    central: "Cómo se guarda y se sella esta central.",
    general: "Lo que Noust decide por defecto: versiones, certificados, copias y despliegues.",
    variables: "Variables que puede usar cualquier app del servidor.",
    about: "Versión, actualizaciones y novedades.",
  },
  facts: {
    people: { one: "{count} persona", other: "{count} personas" },
    roles: { one: "{count} rol", other: "{count} roles" },
    version: "Versión {version}",
  },
};
