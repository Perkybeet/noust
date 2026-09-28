import type { cron as en } from "../en/cron";
import type { Catalog } from "../types";

export const cron: Catalog<typeof en> = {
  area: "Tareas programadas",

  common: {
    cancel: "Cancelar",
    edit: "Editar",
    clearFilters: "Borrar filtros",
    fromTerminal: "Desde una terminal",
  },

  status: {
    succeeded: "Correcta",
    failed: "Fallida",
    unknown: "Desconocido",
    neverRun: "Nunca se ejecutó",
  },

  presets: {
    hourly: "Cada hora",
    daily: "Diaria",
    weekly: "Semanal",
    monthly: "Mensual",
    custom: "Personalizada",
    dailyAt: "Cada día a las {hours}:{minutes}",
  },

  page: {
    title: "Cron",
    description: "Comandos que se ejecutan con una programación, como temporizadores de systemd.",
    newJob: "Nueva tarea",
    loadError: "No se pudieron cargar las tareas programadas",
    filterAria: "Filtrar tareas programadas",
    searchAria: "Buscar tareas programadas",
    searchPlaceholder: "Buscar por nombre o comando",
    jobsCount: { one: "{count} tarea", other: "{count} tareas" },
    jobsCountFiltered: "{shown} de {total} tareas",
    empty: {
      title: "Programa tu primera tarea",
      description: "Una tarea programada ejecuta un comando con una programación: cada hora, diaria, semanal, mensual, o una expresión de calendario de systemd.",
    },
    noMatch: {
      title: "Ninguna tarea coincide",
      description: "Nada en esta máquina coincide con estos filtros.",
    },
  },

  table: {
    captionAll: "Tareas programadas",
    captionFiltered: "Tareas programadas que coinciden con los filtros",
    actionsFor: "Acciones de {name}",
    disabledReason: "Desactivada",
    enabled: "Activada",
    disabled: "Desactivada",
    columns: {
      state: "Estado",
      job: "Tarea",
      schedule: "Programación",
      nextRun: "Próxima ejecución",
      lastResult: "Último resultado",
    },
  },

  actions: {
    runNow: "Ejecutar ahora",
    viewRuns: "Ver ejecuciones",
    disable: "Desactivar",
    enable: "Activar",
    deleteJob: "Eliminar tarea",
  },

  deleteDialog: {
    title: "Eliminar {name}",
    description: "Elimina el temporizador y su unidad de servicio. Esto no se puede deshacer.",
  },

  dialog: {
    titleNew: "Nueva tarea programada",
    titleEdit: "Editar {name}",
    description: "Ejecuta un comando con una programación, como un temporizador de systemd.",
    createJob: "Crear tarea",
    saveJob: "Guardar tarea",
    previewCheckError: "No se pudo comprobar la programación.",
    checkingSchedule: "Comprobando la programación",
    noFutureRun: "Esta programación no tiene ninguna ejecución futura.",
    systemdOutputLabel: "Lo que dijo systemd",
    errorCreate: "La tarea no se creó",
    errorSave: "La tarea no se guardó",
  },

  fields: {
    name: "Nombre",
    command: "Comando",
    commandDescription: "Se ejecuta como un argv, sin un shell.",
    schedule: "Programación",
    calendarLabel: "Expresión de calendario",
    calendarDescription: "Una expresión OnCalendar de systemd.",
    user: "Usuario",
    userDescription: "Usa de forma predeterminada el usuario de servicio configurado.",
    workingDirectory: "Directorio de trabajo",
  },

  runsDrawer: {
    titleFor: "Ejecuciones de {name}",
    titleDefault: "Ejecuciones",
    description: "Las más recientes primero, leídas del journal de la unidad.",
    loadError: "No se pudo cargar el historial de ejecuciones",
    loading: "Cargando el historial de ejecuciones",
    empty: "Esta tarea aún no se ha ejecutado.",
    noExitCode: "Sin código de salida",
    exitCode: "Salida {code}",
    output: "Salida",
    outputLabel: "Salida de esta ejecución de {name}",
  },

  toast: {
    theJob: "la tarea",
    created: "Se creó {name}",
    nextRun: "Próxima ejecución: {value}.",
    deleted: "Se eliminó {name}",
    deleteError: "No se pudo eliminar {name}",
    started: "Se inició {name}",
    startedDescription: "Su resultado aparecerá en su historial de ejecuciones en breve.",
    startError: "No se pudo iniciar {name}",
    enabled: "Se activó {name}",
    enableError: "No se pudo activar {name}",
    disabled: "Se desactivó {name}",
    disableError: "No se pudo desactivar {name}",
  },
};
