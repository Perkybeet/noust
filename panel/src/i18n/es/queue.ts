import type { queue as en } from "../en/queue";
import type { Catalog } from "../types";

export const queue: Catalog<typeof en> = {
  title: "Cola de despliegues",
  titleApp: "Cola de {domain}",
  description: "Las compilaciones de aplicaciones distintas corren a la vez, hasta el límite del servidor. El resto espera su turno, en orden, y no se pierde nada.",
  descriptionApp: "Una aplicación compila una sola cosa a la vez: lo que se pide mientras tanto espera aquí, en orden.",
  caption: "Tareas que compilan o esperan, en el orden en que corren",
  captionApp: "Tareas de {domain} que compilan o esperan, en el orden en que corren",
  readFailed: "No se pudo leer la cola de despliegues",
  limits: {
    builds: "{running} de {max} compilaciones en curso",
    waiting: "{waiting} de {limit} esperando",
  },
  columns: {
    job: "Tarea",
    state: "Estado",
    place: "Lugar",
  },
  place: {
    next: "Siguiente en la cola",
    position: "Lugar {position} de la cola",
    since: "En curso desde {time}",
    none: "Aún sin lugar en la cola",
  },
  state: {
    stopping: "Deteniéndose",
  },
  merged: {
    one: "1 petición unida a esta",
    other: "{count} peticiones unidas a esta",
  },
  waitingForLine: "Esperando a {what}",
  cancel: {
    button: "Cancelar",
    label: "Cancelar {name}",
    stopping: "Deteniendo…",
    stoppingNote: "Se le pidió que se detenga y está terminando. La cola avanza cuando acabe.",
    reasons: {
      activating: "Su release se está activando, algo que no puede detenerse a medias. Termina, o devuelve lo que servía antes, por sí sola.",
      buildsInPlace: "Esta aplicación compila en la carpeta que sirve, o en Docker si es un stack de Compose. Detener cualquiera de las dos a medias la dejaría rota, así que termina por sí sola.",
      unsupported: "Una tarea de este tipo está a medias de cambiar el sistema y no puede detenerse con seguridad. Espera a que termine.",
    },
    queued: {
      title: "¿Sacar {name} de la cola?",
      description: "Todavía no ha empezado. Cancelarla la quita de la cola y nada cambia en el servidor. Las tareas de detrás avanzan un lugar.",
      action: "Quitar de la cola",
    },
    running: {
      title: "¿Detener la compilación de {domain}?",
      description:
        "La compilación se detiene y se borra la release que estaba haciendo. Lo que sirve ahora no se toca, y empieza la siguiente petición de la cola. Funciona hasta que su release empieza a activarse; después termina por sí sola.",
      action: "Detener compilación",
    },
    done: {
      queued: "{name} salió de la cola",
      stopping: "Se pidió a {name} que se detenga",
    },
    deploymentCancelled: "Cancelado por {who}: la compilación se detuvo y lo que servía antes no se tocó.",
    deploymentCancelledAnonymous: "Cancelado: la compilación se detuvo y lo que servía antes no se tocó.",
    deploymentCancelledTitle: "Este despliegue se canceló",
    announced: "Se canceló el despliegue {id}",
  },
  full: {
    hint: "Las compilaciones corren de pocas en pocas y el resto espera su turno. Inténtalo de nuevo dentro de {seconds} segundos, o cancela lo que ya no necesita esperar.",
    hintNoWait: "Las compilaciones corren de pocas en pocas y el resto espera su turno. Inténtalo de nuevo en un momento, o cancela lo que ya no necesita esperar.",
  },
  joined: {
    title: "{domain} ya esperaba una actualización",
    body: "Esta petición se unió a ella: una sola actualización compila la rama tal como esté cuando le toque, así que no se pierde nada y nada se compila dos veces.",
  },
};
