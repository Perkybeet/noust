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
    daily: "Cada día a las 02:00",
    weekly: "Cada lunes a las 02:00",
    monthly: "El día 1 de cada mes a las 02:00",
    custom: "Expresión de calendario propia",
  },

  words: {
    everyHour: "Cada hora, en punto",
    everyMinutes: { one: "Cada minuto", other: "Cada {count} minutos" },
    everyDayAt: "Cada día a las {time}",
    workdaysAt: "De lunes a viernes a las {time}",
    everyWeekdayAt: "Cada {weekday} a las {time}",
    monthlyAt: "El día {day} de cada mes a las {time}",
    custom: "Programación personalizada",
  },

  page: {
    title: "Cron",
    description: "Comandos que el servidor ejecuta según un horario.",
    newJob: "Nueva tarea",
    loadError: "No se pudieron cargar las tareas programadas",
    filterAria: "Filtrar tareas programadas",
    searchAria: "Buscar tareas programadas",
    searchPlaceholder: "Buscar por nombre o comando",
    jobsCount: { one: "{count} tarea", other: "{count} tareas" },
    jobsCountFiltered: "{shown} de {total} tareas",
    empty: {
      title: "Programa tu primera tarea",
      description: "Una tarea programada ejecuta un comando por sí sola: cada hora, cada noche o a las horas que escribas.",
    },
    noMatch: "Ninguna tarea coincide con esta búsqueda.",
  },

  table: {
    captionAll: "Tareas programadas",
    captionFiltered: "Tareas programadas que coinciden con los filtros",
    actionsFor: "Acciones de {name}",
    disabled: "Desactivada",
    columns: {
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
    description: "Ejecuta un comando en este servidor por sí solo, según un horario.",
    createJob: "Crear tarea",
    saveJob: "Guardar tarea",
    previewCheckError: "No se pudo comprobar la programación.",
    checkingSchedule: "Comprobando la programación",
    noFutureRun: "Esta programación no tiene ninguna ejecución futura.",
    systemdOutputLabel: "Lo que dijo systemd",
    nextRuns: "Próximas ejecuciones",
    serverClock: "En el reloj del servidor ({zone}), el de la programación.",
    serverTime: "{time} {zone}",
    yourTime: "{time} en tu hora",
    errorCreate: "La tarea no se creó",
    errorSave: "La tarea no se guardó",
  },

  fields: {
    name: "Nombre",
    nameDescription: "Da nombre a la tarea y a su temporizador, como nightly-report.",
    nameMissing: "Escribe un nombre para la tarea.",
    command: "Comando",
    commandDescription: "Se ejecuta directamente, sin shell: no funcionan tuberías, && ni $VARIABLES.",
    commandMissing: "Escribe el comando que se ejecutará.",
    schedule: "Programación",
    calendarLabel: "Expresión de calendario",
    calendarDescription: "Con la sintaxis de calendario de systemd, en el reloj del servidor: Mon..Fri *-*-* 09:00:00 es de lunes a viernes a las 09:00; *-*-* *:0/15 es cada 15 minutos.",
    calendarMissing: "Escribe una expresión de calendario, como *-*-* 03:30:00.",
    user: "Usuario",
    userDescription: "En blanco se ejecuta con el usuario de servicio de los ajustes.",
    workingDirectory: "Directorio de trabajo",
    workingDirectoryDescription: "En blanco usa la carpeta de su aplicación, si tiene una.",
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
