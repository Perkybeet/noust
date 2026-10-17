import type { archiveUpload as en } from "../en/archiveUpload";
import type { Catalog } from "../types";

export const archiveUpload: Catalog<typeof en> = {
  label: "Archivo del proyecto",
  help: "Un .zip, .tar.gz, .tgz, .tar.bz2 o .tar.xz con los ficheros del proyecto. Se extrae una sola vez en el servidor y no se escribe nada fuera del proyecto.",
  extension: "{name} no es un archivo que Noust extraiga. Usa un .zip, .tar, .tar.gz, .tgz, .tar.bz2 o .tar.xz.",
  empty: "{name} está vacío.",
  uploading: "Subiendo el archivo",
  completing: "Extrayendo el archivo",
  progress: "{received} de {size}",
  waiting: "Se cortó la conexión. Se reintenta solo (intento {attempt}).",
  paused: "La subida se detuvo tras {received}. El servidor conserva lo recibido: reintenta para seguir desde ahí.",
  retry: "Reintentar ahora",
  cancel: "Cancelar la subida",
  cancelled: "Se canceló la subida. No se guardó nada.",
  failed: "No se aceptó el archivo",
  done: "Se subió {name}",
  doneBody: "Se extrajeron {files} ficheros en el servidor.",
  another: "Elegir otro archivo",
};
