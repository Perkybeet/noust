import type { remoteImport as en } from "../en/remoteImport";
import type { Catalog } from "../types";

export const remoteImport: Catalog<typeof en> = {
  disclosure: "Leerla desde Coolify o Dokploy",
  title: "Desde otra plataforma",
  description: "Noust lee una aplicación por la API del otro servidor con un token de solo lectura y propone cómo se ejecuta aquí. No se escribe nada en el otro servidor. El token se usa en esta petición y no se guarda.",
  platform: "Plataforma",
  platforms: {
    coolify: "Coolify",
    dokploy: "Dokploy",
  },
  url: "Dirección del servidor",
  urlDescription: "Donde responde su API, como https://coolify.example.com.",
  token: "Token de la API",
  tokenDescription: "Un token de solo lectura creado en ese servidor.",
  list: "Listar las aplicaciones",
  listFailed: "No se pudieron listar las aplicaciones",
  applications: "Aplicación",
  applicationsDescription: "{count} encontradas en {url}.",
  none: "Ese servidor no tiene aplicaciones.",
  choose: "Elige una aplicación",
  domain: "Dominio con el que crearla aquí",
  domainDescription: "Déjalo vacío para usar el primero con el que respondía allí.",
  read: "Leerla",
  readFailed: "No se pudo leer la aplicación",
  loadedTitle: "Se leyó {name}",
  loadedBody: "Revísala en el paso siguiente: no se crea nada hasta que despliegues.",
  notDeployable: "{name} no se puede importar tal cual",
  notDeployableBody: "Ejecutaba una imagen, un Dockerfile o un archivo Compose, que una exportación no lleva. Este comando crea el equivalente aquí.",
  warnings: "Lo que no tiene equivalente aquí",
  label: "{platform}: {name}",
  fromTerminal: "Lo mismo, desde un terminal",
};
