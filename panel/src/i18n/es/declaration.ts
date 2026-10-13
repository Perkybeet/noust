import type { declaration as en } from "../en/declaration";
import type { Catalog } from "../types";

export const declaration: Catalog<typeof en> = {
  title: "Archivo de declaración",
  description: "Cómo se ejecuta esta aplicación (comprobación de salud, límites, opciones de origen, comandos de compilación, hooks, variables no secretas, dominios y tareas) con las claves de noust.yaml. Se escribe desde esta aplicación o se aplica a ella.",
  download: "Descargar la declaración",
  downloaded: "Se descargó la declaración de {domain}",
  downloadFailed: "No se pudo escribir la declaración",
  secrets: "Las variables secretas nunca están en ella: sus nombres se listan en un comentario.",
  apply: "Aplicar una declaración…",
  dialog: {
    title: "Aplicar una declaración a {domain}",
    description: "Pega un archivo de declaración. Solo cambian las secciones que tiene, y no cambia nada hasta que la apliques.",
    label: "Declaración (YAML)",
    placeholder: "app:\n  health:\n    path: /up\nenv:\n  LOG_LEVEL: info",
    check: "Comprobar qué cambia",
    failed: "No se pudo comprobar ni aplicar la declaración",
    prune: "Quitar también los alias, redirecciones y tareas que no lista",
    pruneDescription: "Sin esto, los dominios y las tareas solo se añaden. Las variables nunca se quitan.",
    close: "Cerrar",
    applyNow: "Aplicarla",
    applied: "Se aplicó la declaración a {domain}",
    partly: "{count} parte(s) no se aplicaron",
  },
  plan: {
    title: "Qué cambia",
    nothing: "Nada: la aplicación ya está como se declara.",
    already: "Ya como se declara: {sections}",
    runsCommands: "Ejecuta comandos",
    confirm: "He leído lo que ejecuta como la aplicación o en esta máquina, y estoy de acuerdo",
    notApplied: "No aplicado",
  },
  fromTerminal: "Lo mismo, desde un terminal",
};
