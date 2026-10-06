import type { provisioning as en } from "../en/provisioning";
import type { Catalog } from "../types";

export const provisioning: Catalog<typeof en> = {
  engineLabel: "Motor",
  enginePlaceholder: "Elige un motor",
  versionLabel: "Versión",
  engineState: {
    installed: "Instalado, versión {version}",
    installedUnknown: "Instalado",
    installable: "No instalado: Noust lo instala antes",
  },
  version: {
    defaultRunning: "La versión que corre ({version})",
    defaultRunningUnknown: "La versión que corre",
    defaultInstall: "Por defecto ({version})",
    defaultInstallUnknown: "Por defecto",
    state: {
      installed: "Corre aquí",
      installable: "Se puede instalar",
      unavailable: "No disponible aquí",
    },
    helpInstalled: "En un servidor corre un solo {engine}, así que la base de datos se crea en la versión que corre.",
    helpInstallable: "{engine} no está instalado aquí, así que eliges qué versión instala Noust.",
  },
  unavailable: {
    title: "No disponible en este servidor",
    line: "{engine}: {reason}",
    noReason: "no se puede instalar aquí",
  },
  none: {
    title: "Ningún motor puede alojar una base de datos aquí",
    body: "Instala antes un motor de bases de datos, desde Bases de datos > Motores.",
  },
  install: {
    label: "Instalar {engine} antes",
    description:
      "{engine} {version} se instala desde el repositorio del propio proyecto, con su clave de firma fijada, antes de crear la base de datos. Te pide confirmar que eres tú.",
    descriptionUnknown:
      "{engine} se instala desde el repositorio del propio proyecto, con su clave de firma fijada, antes de crear la base de datos. Te pide confirmar que eres tú.",
    required: "Marca la casilla para instalar {engine}, o elige un motor que esté instalado.",
  },
  plan: {
    willInstall: "{engine} se instala antes.",
    version: "Versión",
  },
};
