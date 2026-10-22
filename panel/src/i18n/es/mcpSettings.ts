import type { mcpSettings as en } from "../en/mcpSettings";
import type { Catalog } from "../types";

export const mcpSettings: Catalog<typeof en> = {
  nav: {
    label: "MCP",
    keywords: "mcp model context protocol endpoint herramientas cliente token inspeccionar desplegar controlar llms",
    command: "Ajustes de MCP",
  },
  documentTitle: "MCP",
  loadingLabel: "los ajustes de MCP",
  state: {
    title: "Endpoint MCP",
    description: "Un cliente puede hacer exactamente lo que permite su token, y cada llamada queda en la auditoría.",
    label: "Responder a clientes MCP",
    help: "Deja que un cliente con un token de API gestione aplicaciones.",
    on: "Activado",
    off: "Desactivado",
    turnedOn: "El endpoint MCP responde ahora",
    turnedOff: "El endpoint MCP está desactivado",
    failed: "No se pudo cambiar el endpoint MCP",
  },
  connection: {
    title: "Conectar un cliente",
    description:
      "Dale al cliente esta dirección y un token en la cabecera Authorization. No se acepta una sesión del navegador ni el token de acceso: solo un token de API.",
    address: "Dirección del endpoint",
    copyAddress: "Copiar la dirección del endpoint",
    token: "Un token con solo lo que necesita",
    tokenHelp: "Crea uno que tenga solo los permisos de las herramientas que quieras, y si hace falta ligado a un equipo, un proyecto o un entorno.",
    tokensLink: "Abrir los tokens de API",
    protocols: "Versiones del protocolo",
  },
  groups: {
    title: "Qué ofrece",
    description:
      "Las herramientas vienen en tres grupos. Desactiva un grupo y sus herramientas no se listan ni se pueden llamar, tenga lo que tenga el token.",
    toolCount: { one: "{count} herramienta", other: "{count} herramientas" },
    inspect: { name: "Inspeccionar", description: "Solo lectura: aplicaciones, registros, despliegues, salud." },
    deploy: { name: "Desplegar", description: "Actualizar, volver atrás y copiar una aplicación, y cancelar una tarea." },
    control: { name: "Controlar", description: "Reiniciar, arrancar y parar una aplicación, y renovar un certificado." },
    changed: "Se cambiaron los grupos que se ofrecen",
  },
  tools: {
    title: "Herramientas",
    description:
      "Lo que ve un cliente, con el permiso que pide cada una. Ninguna devuelve los valores de las variables ni los secretos.",
    tableCaption: "Herramientas MCP",
    column: { tool: "Herramienta", group: "Grupo", permission: "Permiso", effect: "Efecto" },
    reads: "Lee",
    changes: "Cambia",
    asksTwice: "Pide confirmar",
    prompts: "Prompts",
    resources: "Recursos",
  },
  commandEnable: "noust mcp enable",
  commandToken: "noust mcp token ci-bot --preset deploy --owner maria",
};
