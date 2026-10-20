import type { inbox as en } from "../en/inbox";
import type { Catalog } from "../types";

export const inbox: Catalog<typeof en> = {
  forServer: "Requiere tu atención en {name}",
  tabs: {
    label: "Bandeja",
    needs: "Requiere tu atención {count}",
    needsNone: "Requiere tu atención",
    recent: "Recientes",
  },
  needs: {
    checking: "Comprobando qué requiere tu atención",
    emptyTitle: "Nada requiere tu atención.",
    emptyDescription:
      "Una aplicación caída, un certificado a punto de caducar, una alerta que salta o una solicitud que espera tu decisión aparece aquí hasta que se resuelve.",
    count: { one: "{count} elemento requiere tu atención", other: "{count} elementos requieren tu atención" },
    couldNotLoad: "No se pudo leer qué requiere tu atención",
  },
  recent: {
    emptyTitle: "Aún no hay avisos.",
    emptyDescription: "Lo que el servidor te cuenta mientras esta pestaña está abierta, como un despliegue que terminó o una copia que falló, se guarda aquí.",
    footer: "Desde que abriste la consola",
    busy: "En curso",
    active: "Hecho",
    failed: "Fallido",
    idle: "Aviso",
  },
  history: "Abrir Actividad",
  reasons: {
    alertFiring: "Esta alerta ha saltado.",
    alertTaken: "Esta alerta ha saltado. La atiende {by}.",
    probeDown: "Esta sonda está caída.",
    approvalWaiting: "{by} espera tu decisión.",
    noustUpdate: "Noust {version} está disponible. Este servidor usa la {current}.",
    noustSecurityUpdate: "Noust {version} corrige un problema de seguridad. Este servidor usa la {current}.",
    recipeUpdate: "{recipe} tiene una versión más reciente, la {version}. Ahora usa la {current}.",
  },
  actions: {
    acknowledge: "Reconocer",
    acknowledgeAria: "Reconocer {subject}",
    reopen: "Reabrir",
    reopenAria: "Reabrir {subject}",
    acknowledgeFailed: "No se pudo reconocer la alerta",
    reopenFailed: "No se pudo reabrir la alerta",
    openProbes: "Abrir sondas",
    openProbesAria: "Abrir sondas, {name}",
    review: "Revisar",
    reviewAria: "Revisar la solicitud {id}",
    openUpdates: "Abrir actualizaciones",
    openUpdatesAria: "Abrir las actualizaciones de Noust",
    reviewRecipe: "Revisar",
    reviewRecipeAria: "Revisar la actualización de {domain}",
  },
};
