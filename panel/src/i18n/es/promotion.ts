import type { promotion as en } from "../en/promotion";
import type { Catalog } from "../types";

export const promotion: Catalog<typeof en> = {
  promote: {
    menu: "Promover a otro entorno",
    menuDescription: "Pone la release que sirve aquí en la app gemela de otro entorno, sin volver a compilarla",
    title: "Promover {domain}",
    description:
      "La release que sirve {domain} se copia a la misma app en otro entorno de su proyecto y se activa allí, sin volver a compilarse. Debe pasar allí la comprobación de arranque; si no, vuelve lo que servía.",
    loading: "Leyendo adónde se puede promover {domain}",
    loadFailed: "No se pudo leer adónde se puede promover {domain}",
    release: "Release",
    commit: "Commit",
    noRelease: "{domain} no tiene ninguna release que promover",
    noReleaseHint: "Solo una app con reversión instantánea tiene releases. Actívala en los ajustes de despliegue de la app, despliega una vez y promueve.",
    noTargetsTitle: "Ningún otro entorno tiene esta app",
    noTargetsHint:
      "Una release se promueve a la misma app (mismo repositorio y tipo) en otro entorno de este proyecto. Clona primero esta app en ese entorno y luego promueve.",
    targetLabel: "Promover a",
    targetDescription: "Aplicaciones de este proyecto en otros entornos, compiladas desde el mismo repositorio.",
    targetRuns: "ejecuta {release}",
    targetEmpty: "aún sin release",
    moreOptions: "Más opciones",
    schemaLabel: "Permitir volver atrás de un cambio de esquema",
    schemaDescription: "Solo hace falta cuando el destino ya ejecutó una release posterior que cambió el esquema de su base de datos. Noust devuelve el código, nunca una base de datos.",
    submit: "Promover release",
    queued: "Promoviendo la release {release} a {target}",
    failed: "No se promovió la release",
  },
  clone: {
    menu: "Clonar aplicación",
    menuDescription: "Crea otra app con estos ajustes, en este o en otro entorno",
    title: "Clonar {domain}",
    description:
      "Una app nueva con los ajustes, variables, hooks, tags, tareas programadas y vistas previas de esta, desplegada desde el mismo origen y compilada, comprobada y registrada como cualquier otra. Sus datos no se copian, ni los nombres en los que esta app también responde.",
    domainLabel: "Dominio del clon",
    domainDescription: "Donde responde el clon, como staging.example.com.",
    domainRequired: "Indica el dominio en el que responde el clon.",
    dataLegend: "Su base de datos",
    dataEmpty: "Una base de datos propia",
    dataEmptyDescription: "Una base de datos vacía creada para el clon, que no recibe ningún ajuste de conexión de esta app.",
    dataShare: "La base de datos de esta app",
    dataShareDescription: "El clon se conecta a la misma base de datos, así que lo que escribe uno lo lee el otro.",
    dataNeeded: "Elige qué hace el clon con la base de datos.",
    moreOptions: "Más opciones",
    sourceLabel: "Desplegar desde otro origen",
    sourceDescription: "Un repositorio o un directorio desde el que se compila el clon en lugar del de esta app.",
    submit: "Clonar aplicación",
    queued: "Clonando {domain} como {clone}",
    failed: "No se clonó {domain}",
  },
};
