import type { accessSecurity as en } from "../en/accessSecurity";
import type { Catalog } from "../types";

export const accessSecurity: Catalog<typeof en> = {
  loadFailed: "No se pudieron leer los ajustes del acceso",
  loading: "Cargando los ajustes del acceso",
  door: {
    title: "En la puerta",
    description: "Lo que se pide a quien entra, y lo que frena a un extraño que prueba contraseñas.",
    addresses: "Solo desde estas direcciones",
    addressesHelp: "Se fija en la configuración (web.ip_whitelist), no aquí.",
    anyAddress: "Cualquier dirección",
  },
  sessions: {
    title: "Sesiones",
    description: "Cuánto tiempo sigue dentro una persona sin hacer nada, y cuánto como máximo.",
    yours: "Tus sesiones",
    open: "Abiertas ahora",
    openCount: { one: "{count} sesión", other: "{count} sesiones" },
  },
  master: {
    title: "Token de acceso maestro",
    description: "Para cuando nada más funciona: entra sin cuenta. Guárdalo fuera de este servidor.",
    help: "El token que imprimió el instalador entra sin cuenta y sin el segundo factor de una persona. Con «Acceso de emergencia» sigue siendo una forma de entrar; con «Solo recuperar cuentas» sirve únicamente para recuperar una cuenta, y para nada más.",
  },
};
