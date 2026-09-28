import type { auth as en } from "../en/auth";
import type { Catalog } from "../types";

export const auth: Catalog<typeof en> = {
  area: "Iniciar sesión",
  subtitle: "Usa el token de acceso de este servidor para abrir su consola.",
  sessionExpired: "Tu sesión expiró. Inicia sesión de nuevo para continuar donde lo dejaste.",
  accessToken: "Token de acceso",
  accessTokenHint: "Muéstralo en el servidor con {command}",
  enterToken: "Introduce el token de acceso.",
  enterCode: "Introduce el código.",
  tokenAccepted: "Token de acceso {status}",
  accepted: "aceptado",
  tokenAcceptedAnnounce: "Token aceptado. Introduce tu código de verificación en dos pasos.",
  useDifferentToken: "Usar otro token",
  twoFactorCode: "Código de verificación en dos pasos",
  twoFactorHint: "El código de 6 dígitos de tu app de autenticación, o uno de tus códigos de respaldo.",
  signIn: "Iniciar sesión",
  verify: "Verificar",
  signInFailed: "No se pudo iniciar sesión. El servidor dijo:",
  whatServerSaid: "Lo que dijo el servidor",
  tooManyAttempts: "Demasiados intentos fallidos",
  lockedFor: "El inicio de sesión está bloqueado para esta dirección. Vuelve a intentarlo en {time}.",
  tryAgainIn: "Vuelve a intentarlo en {time}.",
  lockedMinutes: { one: "{count} minuto", other: "{count} minutos" },
  signedIn: "Sesión iniciada",
  elevate: {
    title: "Confirma que eres tú",
    descriptionTotp:
      "Esta acción necesita una confirmación reciente. Introduce un código de tu app de autenticación o uno de tus códigos de respaldo. Cubre los próximos 10 minutos.",
    descriptionToken:
      "Esta acción necesita una confirmación reciente. Introduce el token de acceso de este servidor. Cubre los próximos 10 minutos.",
    authenticationCode: "Código de autenticación",
    cancel: "Cancelar",
    confirm: "Confirmar",
    confirmFailed: "La confirmación falló. El sistema dijo:",
    whatSystemSaid: "Lo que dijo el sistema",
  },
  signOutFailed: "Error al cerrar sesión",
  signedOut: "Sesión cerrada",
};
