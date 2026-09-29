import type { language as en } from "../en/language";
import type { Catalog } from "../types";

export const language: Catalog<typeof en> = {
  label: "Idioma",
  description:
    "El idioma de esta consola en este navegador. Se guarda aquí, no en el archivo de configuración. Lo que escriben Noust, nginx y systemd se muestra tal cual.",
  loadFailed: "No se pudo cambiar el idioma",
  loadFailedHint: "La traducción no se descargó. Comprueba la conexión con la consola y vuelve a intentarlo.",
};
