import type { githubHost as en } from "../en/githubHost";
import type { Catalog } from "../types";

export const githubHost: Catalog<typeof en> = {
  title: "Qué GitHub",
  description: "github.com, o la dirección de tu GitHub Enterprise Server o Cloud. Decide dónde se crea la App y a qué dirección pide Noust los tokens, los estados y los clones.",
  label: "Dirección de GitHub",
  fieldDescription: "Déjala vacía para github.com. Para Enterprise, el host sin https://, como ghe.example.com.",
  placeholder: "github.com",
  save: "Usar este GitHub",
  saved: "La integración ahora habla con {host}",
  failed: "No se pudo guardar la dirección",
  enterprise: "GitHub Enterprise en {host}",
  fact: "GitHub",
  factDescription: "El host donde vive la App. No puede cambiar mientras la App exista.",
  fromTerminal: "Lo mismo, desde un terminal",
};
