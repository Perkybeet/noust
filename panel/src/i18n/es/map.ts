import type { map as en } from "../en/map";
import type { Catalog } from "../types";

export const map: Catalog<typeof en> = {
  title: "Mapa",
  queryLabel: "el mapa de este servidor",
  label: "Cómo encaja este servidor",
  layers: {
    names: "Nombres",
    applications: "Aplicaciones",
    databases: "Bases de datos",
    scheduled: "Tareas programadas",
    data: "Datos",
  },
  relation: {
    serves: "responde en",
    uses: "usa",
    runs: "ejecuta",
    keeps: "conserva",
  },
  disabled: "Desactivada",
  legend: "Cada línea es una relación, no tráfico: un nombre lleva a la aplicación que responde en él, y una aplicación a las bases de datos que usa, las tareas programadas que ejecuta y los datos que conserva.",
  summary: "Aplicaciones: {applications}. Bases de datos: {databases}. Tareas programadas: {scheduled}.",
  truncated: "El mapa es más grande de lo que dibuja: el resto queda fuera. Redúcelo a un equipo o un proyecto con el selector de arriba.",
  emptyTitle: "Todavía no hay nada que mapear",
  emptyDescription: "Despliega una aplicación y sus nombres, bases de datos, tareas programadas y datos aparecen aquí, conectados.",
  fromTerminal: "Léelo desde un terminal",
};
