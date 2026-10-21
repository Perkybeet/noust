import type { account as en } from "../en/account";
import type { Catalog } from "../types";

export const account: Catalog<typeof en> = {
  title: "Cuenta",
  description: "Quién eres en esta consola y cómo lo demuestras. El tema y el idioma están en el menú de la cuenta.",
  documentTitle: "Cuenta",
  securityDocumentTitle: "Seguridad de inicio de sesión",
  tokens: {
    title: "Tokens de API",
    description: "Los tokens para scripts y CI se crean en Ajustes, donde cada uno aparece con su propietario.",
    open: "Abrir los tokens de API",
  },
};
