import type { samlAdmin as en } from "../en/samlAdmin";
import type { Catalog } from "../types";

export const samlAdmin: Catalog<typeof en> = {
  certificate: {
    expired: "Caducado",
  },
  plainHttpTitle: "Esta consola no se sirve por HTTPS",
  plainHttp:
    "Un proveedor SAML envía un formulario desde su sitio, y el navegador solo manda la cookie de inicio de sesión con él por HTTPS. Sirve la consola con TLS.",
  form: {
    createTitle: "Añadir un proveedor SAML",
    description:
      "Las aserciones deben ir firmadas por uno de los certificados de abajo. Las aserciones cifradas se rechazan: la conexión ya va cifrada.",
    nameHelp: "Un nombre corto en minúsculas, como corp o entra. No se puede cambiar después.",
    label: "Etiqueta",
    labelHelp: "El nombre en su botón de inicio de sesión.",
    enabled: "Ofrecerlo en la página de inicio de sesión",
    mappingsLine: "La línea {line} no es una correspondencia. Escribe GRUPO=ROL, GRUPO=EQUIPO:ROL o GRUPO=ROL@EQUIPO/PROYECTO.",
    create: "Añadir proveedor",
    save: "Guardar proveedor",
    errorTitle: "No se guardó el proveedor",
    register: {
      title: "Registra este servidor en el proveedor",
      description: "Dale al proveedor estos datos, o la dirección de los metadatos, cuando pida un proveedor de servicio.",
      entityId: "Entity ID",
      acs: "URL de respuesta (assertion consumer service)",
      metadata: "Dirección de los metadatos",
    },
    idp: {
      title: "El proveedor de identidad",
      metadata: "Sus metadatos",
      needed: "Indica los metadatos, o el ID de entidad, la dirección de inicio de sesión y un certificado.",
      metadataHelp: "Pega el XML que publica el proveedor: rellena los tres ajustes de abajo, y lo que escribas junto a él prevalece.",
      entityId: "Entity ID",
      entityIdHelp: "Lo que sus aserciones nombran como emisor.",
      ssoUrl: "Dirección de inicio de sesión",
      ssoUrlHelp: "Adonde se envía el navegador para iniciar sesión (el enlace HTTP-Redirect).",
      certificates: "Certificados de firma",
      certificatesHelp: "En PEM, uno detrás de otro. Da el viejo y el nuevo durante un relevo; sustituir la lista sustituye todos.",
    },
    server: {
      title: "Cómo se presenta este servidor",
      entityId: "Entity ID de este servidor",
      entityIdHelp: "Vacío lo deriva de la dirección de esta consola, que es lo que la mayoría de proveedores aceptan.",
    },
    people: {
      title: "Encontrar a las personas",
      subject: "Atributo que identifica a una persona",
      subjectHelp: "Vacío usa el NameID, que debería ser un identificador persistente. Nunca un correo que pueda reasignarse.",
      email: "Atributo del correo",
      displayName: "Atributo del nombre visible",
      username: "Atributo del nombre de usuario",
      groups: "Atributo de los grupos",
      groupsHelp: "Donde la respuesta lista los grupos de una persona.",
      skew: "Diferencia de reloj tolerada (segundos)",
    },
    contexts: "Clases de autenticación exigidas",
    contextsHelp: "Debe informarse una de ellas, como urn:oasis:names:tc:SAML:2.0:ac:classes:TimeSyncToken. Vacío no exige ninguna.",
  },
  removeDialog: {
    description: "Las personas vinculadas a él ya no podrán iniciar sesión con él. Sus cuentas se quedan como están.",
    action: "Quitar proveedor",
  },
  savedToast: "Proveedor SAML {name} guardado",
};
