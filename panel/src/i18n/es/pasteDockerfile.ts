import type { pasteDockerfile as en } from "../en/pasteDockerfile";
import type { Catalog } from "../types";

export const pasteDockerfile: Catalog<typeof en> = {
  open: "O pega un Dockerfile",
  label: "Dockerfile",
  help:
    "La compilación no tiene más que este archivo, así que un COPY no encuentra nada: el Dockerfile trae lo que necesita (ADD de una URL, RUN git clone, o una imagen base que ya tenga el programa).",
  use: "Usar este Dockerfile",
  failed: "No se aceptó ese Dockerfile",
  stored: "El Dockerfile está listo",
  storedBody: "Se guarda en este servidor como todo el proyecto. Continúa para inspeccionarlo.",
  edit: "Editarlo",
};
