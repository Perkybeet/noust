import type { manifestPreview as en } from "../en/manifestPreview";
import type { Catalog } from "../types";

export const manifestPreview: Catalog<typeof en> = {
  nav: {
    label: "Manifiesto",
    keywords: "manifiesto declarativo yaml plan aplicar infraestructura como código gitops vista previa diferencias",
    command: "Vista previa de un manifiesto",
  },
  documentTitle: "Manifiesto",
  intro: {
    title: "Ver un manifiesto antes de aplicarlo",
    description:
      "Pega el manifiesto de las aplicaciones de este servidor para ver qué cambiaría aplicarlo aquí y aplica lo que has leído. La vista previa no cambia nada, y aplicarlo nunca elimina nada.",
  },
  form: {
    label: "Manifiesto",
    help:
      "YAML o JSON. Escribe en él el valor de cada variable: una leída de un entorno o de un archivo la resuelve la máquina que aplica el manifiesto, así que ese manifiesto se aplica desde un terminal.",
    placeholder: "version: 1\napplications:\n  - domain: shop.example.com\n    source: https://github.com/acme/shop\n    branch: main",
    preview: "Ver los cambios",
    errorTitle: "No se aceptó el manifiesto",
  },
  result: {
    title: "Qué cambiaría aplicarlo",
    nothing: "No hay nada que cambiar",
    nothingBody: "Este servidor ya coincide con el manifiesto.",
    summary: { one: "{count} cambio, en este orden", other: "{count} cambios, en este orden" },
    tableCaption: "Cambios que haría un manifiesto",
    column: { change: "Cambio", target: "Objetivo", what: "Qué ocurre" },
    needsSudo: "Pide el modo sudo",
    unchecked: "No se comprueba aquí",
    warningsTitle: "Diferencias que no se concilian",
    unmanagedTitle: "Aplicaciones que el manifiesto no nombra",
    unmanagedBody: "Aplicarlo nunca las elimina: {names}",
    applyHint: "Aplicarlo hace estos cambios como tú, uno a uno, y un cambio que no puedas hacer detiene la ejecución ahí. Desde un pipeline, aplícalo con un token que se pueda revocar.",
  },
  apply: {
    action: "Aplicar estos cambios",
    changedSince: "El manifiesto ha cambiado desde esta vista previa. Vuelve a verla para poder aplicarlo.",
    confirm: {
      title: "¿Aplicar el manifiesto?",
      description: {
        one:
          "Se hará {count} cambio en este servidor como tú, y no se eliminará nada. Si se rechaza, la ejecución se detiene ahí y lo que falte queda para la próxima vez.",
        other:
          "Se harán {count} cambios en este servidor como tú, uno tras otro, y no se eliminará nada. Si se rechaza alguno, la ejecución se detiene ahí y lo que falte queda para la próxima vez.",
      },
      action: "Aplicar",
    },
    running: { one: "Aplicando {count} cambio", other: "Aplicando {count} cambios" },
    succeeded: "El manifiesto está aplicado",
    failed: "Se detuvo la aplicación del manifiesto",
  },
  kind: {
    projectCreate: "Crear proyecto",
    environmentCreate: "Crear entorno",
    appCreate: "Crear aplicación",
    appMove: "Mover aplicación",
    appBranch: "Cambiar la rama",
    appLimits: "Cambiar los límites",
    appHealth: "Cambiar la comprobación de arranque",
    appDomain: "Añadir dominio",
    appDatabase: "Añadir base de datos",
    appEnv: "Fijar variables",
    appBackups: "Programar copias",
  },
  command: "Desde un terminal",
  commandPlan: "noust --context prod manifest plan noust-manifest.yaml",
  commandExport: "noust --context prod manifest export > noust-manifest.yaml",
};
