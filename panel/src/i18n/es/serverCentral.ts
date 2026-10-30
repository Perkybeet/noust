import type { serverCentral as en } from "../en/serverCentral";
import type { Catalog } from "../types";

export const serverCentral: Catalog<typeof en> = {
  label: "los permisos de la central",
  title: "Lo que puede hacer la central",
  description: "Es lo máximo que la central puede hacer en este servidor, sea lo que sea lo que puedan hacer sus propias personas.",
  descriptionOf: "Es lo máximo que la central {central} puede hacer en este servidor, sea lo que sea lo que puedan hacer sus propias personas.",
  summary: "{node} deja a la central: {level}.",
  summaryUnknown: "{node} no dice qué deja hacer a la central.",
  check: "Comprobar de nuevo",
  choose: "Lo que {node} deja hacer a la central",
  now: "Ahora",
  hostAccess: "Cambiar también cómo se llega al servidor",
  hostAccessHelp: "Claves SSH, sshd, el cortafuegos y las cuentas del sistema, y los cambios que se ejecutan como root.",
  hostAccessOnlyAdmin: "Solo con el nivel que lo permite todo.",
  changeTitle: "Cámbialo en {node}",
  changeDescription: "Solo el propio servidor puede cambiarlo: nada de lo que haga la central puede subirlo. Ejecuta esto en el servidor.",
  copy: "Copiar el comando",
  afterIt: "Surte efecto en la siguiente petición. Después, vuelve a comprobar aquí.",
  inForce: "Esto es lo que {node} deja hacer ahora a la central.",
  never: "Sea cual sea el nivel, una central nunca cambia las cuentas, los tokens ni el segundo factor del propio servidor. {command} en el servidor lo muestra y lo cambia.",
  thisCentral: "Esta es la central. Lo que puede hacer en cada servidor está en la página de ese servidor.",
  level: {
    read: {
      description: "Lo lee todo y no cambia nada.",
    },
    deploy: {
      description: "Además arranca, para, reinicia, actualiza y devuelve a una versión anterior las aplicaciones que ya están, y hace copias.",
    },
    admin: {
      description: "Todo salvo las cuentas, los ajustes de seguridad y el registro de auditoría del servidor.",
    },
  },
};
