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
      "Pega el manifiesto de las aplicaciones de este servidor para ver qué cambiaría aplicarlo aquí. La vista previa no cambia nada, y al aplicarlo nunca se borra nada.",
  },
  form: {
    label: "Manifiesto",
    help: "YAML o JSON. Una variable leída del entorno o de un fichero la resuelve la máquina que aplica el manifiesto, nunca este servidor.",
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
    unmanagedBody: "Aplicarlo nunca las borra: {names}",
    applyHint: "Aplícalo desde el repositorio donde vive el manifiesto, con un token que se pueda revocar.",
  },
  kind: {
    projectCreate: "Crear proyecto",
    environmentCreate: "Crear entorno",
    appCreate: "Crear aplicación",
    appMove: "Mover aplicación",
    appBranch: "Cambiar la rama",
    appLimits: "Cambiar los límites",
    appHealth: "Cambiar la comprobación de salud",
    appDomain: "Añadir dominio",
    appDatabase: "Añadir base de datos",
    appEnv: "Fijar variables",
    appBackups: "Programar copias",
  },
  command: "Desde una terminal",
  commandPlan: "noust --context prod manifest plan noust-manifest.yaml",
  commandExport: "noust --context prod manifest export > noust-manifest.yaml",
};
