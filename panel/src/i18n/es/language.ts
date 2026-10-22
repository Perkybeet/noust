import type { language as en } from "../en/language";
import type { Catalog } from "../types";

export const language: Catalog<typeof en> = {
  label: "Idioma",
  loadFailed: "No se pudo cambiar el idioma",
  loadFailedHint: "La traducción no se descargó. Comprueba la conexión con la consola y vuelve a intentarlo.",
};
