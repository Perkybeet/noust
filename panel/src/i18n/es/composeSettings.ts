import type { composeSettings as en } from "../en/composeSettings";
import type { Catalog } from "../types";

export const composeSettings: Catalog<typeof en> = {
  title: "Archivo Compose, perfiles y argumentos",
  description: "Con qué se construye y se inicia la pila. Un archivo o unos perfiles nuevos se escriben también en su unidad, así que un arranque con la máquina levanta la misma pila.",
  loading: "Cargando los ajustes de Compose",
  readFailed: "No se pudieron leer los ajustes de Compose",
  file: {
    label: "Archivo Compose",
    description: "Relativo al proyecto. Si se deja vacío se usa el archivo que registró la unidad, o los nombres habituales.",
    placeholder: "deploy/compose.prod.yml",
  },
  profiles: {
    label: "Perfiles",
    description: "Perfiles que activar, separados por espacios o comas. Un servicio bajo un perfil que no figura queda fuera de cada compilación e inicio.",
  },
  buildArgs: {
    label: "Argumentos de docker compose build",
    description: "Solo se aceptan estos: {allowed}.",
  },
  upArgs: {
    label: "Argumentos de docker compose up",
    description: "Solo se aceptan estos: {allowed}. Noust decide cuándo compilar, así que --build y --no-build no lo están.",
  },
  services: {
    label: "Servicios",
    description: "Compila e inicia solo estos (y de lo que dependen). Vacío significa todos los servicios.",
  },
  save: "Guardar",
  saved: "Se guardaron los ajustes de Compose de {domain}. Se aplican en la próxima actualización.",
  notSaved: "No se guardaron los ajustes de Compose de {domain}",
  reset: "Olvidar estas elecciones",
  resetTitle: "¿Olvidar las elecciones de Compose de {domain}?",
  resetDescription: "Vuelven a aplicarse el archivo y los perfiles que registró la unidad, y no se añaden argumentos.",
  resetAction: "Olvidarlas",
  resetDone: "{domain} usa lo que registró su unidad",
  networks: {
    title: "Redes de Docker",
    description: "Las redes en las que está la pila y quién más está en ellas, tal como las informa Docker. Solo lectura.",
    loading: "Leyendo las redes de la pila",
    readFailed: "No se pudieron leer las redes de la pila",
    none: "La pila no está en ninguna red de Docker ahora. Sus contenedores no están en marcha.",
    isolated: "Aislada: todas las redes son de la pila y no hay nada más en ellas.",
    shared: "No aislada: una red se comparte con algo que no es parte de esta pila.",
    list: "Redes de la pila",
    members: "Contenedores en {name}",
    ranges: "Rangos de direcciones: {ranges}",
    internal: "Interna",
    sharedBadge: "Compartida",
    other: "Otra pila",
  },
  fromTerminal: "Lo mismo, desde un terminal",
};
