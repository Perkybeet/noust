import type { scimAdmin as en } from "../en/scimAdmin";
import type { Catalog } from "../types";

export const scimAdmin: Catalog<typeof en> = {
  title: "Aprovisionamiento (SCIM)",
  description:
    "Un proveedor de identidad que lleva la lista: crea la cuenta de una persona nueva, la apaga cuando se va y da roles por grupo a medida que los envía. No inicia sesión a nadie; eso lo hacen los proveedores de inicio de sesión de arriba.",
  loadingLabel: "fuentes de aprovisionamiento",
  tableCaption: "Fuentes de aprovisionamiento",
  add: "Añadir una fuente",
  emptyTitle: "Ninguna fuente de aprovisionamiento",
  endpoint: "Dirección SCIM",
  endpointHelp: "Dale al proveedor esta dirección y el token de la fuente.",
  column: {
    source: "Origen",
    state: "Estado",
    people: "Personas",
    starts: "Cuentas nuevas",
    lastUsed: "Último uso",
  },
  state: {
    enabled: "Activado",
    disabled: "Desactivada",
  },
  neverUsed: "Nunca",
  editText: "Editar",
  removeText: "Quitar",
  form: {
    createTitle: "Añadir una fuente de aprovisionamiento",
    editTitle: "Editar {name}",
    description:
      "El proveedor se autentica con un token propio, que se muestra una vez. Una cuenta nueva espera al primer inicio de sesión de la persona con uno de los proveedores de arriba.",
    name: "Nombre",
    nameHelp: "Un nombre corto en minúsculas, como entra u okta. No se puede cambiar después.",
    label: "Etiqueta",
    labelHelp: "Lo que lee la gente sobre ella.",
    enabled: "Aceptar su token",
    enabledHelp: "Desactivada, el proveedor es rechazado; las cuentas que creó se quedan como están.",
    rotate: "Emitir un token nuevo",
    rotateHelp: "El token anterior deja de funcionar al instante, así que hay que dar el nuevo al proveedor.",
    mappingsLine: "La línea {line} no es una asignación. Escribe GRUPO=ROL, GRUPO=EQUIPO:ROL o GRUPO=ROL@EQUIPO/PROYECTO.",
    create: "Añadir fuente",
    save: "Guardar fuente",
    cancel: "Cancelar",
    errorTitle: "No se guardó la fuente",
  },
  token: {
    title: "Dale este token al proveedor",
    description: "{name} se autentica con él. Es la única vez que se muestra; solo se guardan sus últimos caracteres.",
    warning: "Cópialo ahora. Si se pierde, emite otro: el anterior deja de funcionar.",
    tokenLabel: "Token de {name}",
    copy: "Copiar token",
    endpointLabel: "Dirección SCIM",
    done: "Hecho",
  },
  remove: {
    title: "¿Quitar {name}?",
    description: "Su token deja de funcionar y el proveedor ya no puede crear ni apagar cuentas. Las cuentas que creó se quedan como están.",
    action: "Quitar fuente",
    toast: "Se quitó la fuente {name}",
  },
  savedToast: "Se guardó la fuente {name}",
} as const;
