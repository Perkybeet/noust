import type { nav as en } from "../en/nav";
import type { Catalog } from "../types";

// Search words keep the English terms operators type anyway (deploy, ssl, cron) next to the
// Spanish ones, so the palette finds a page in either language.
export const nav: Catalog<typeof en> = {
  landmarks: {
    main: "Principal",
    appSections: "Secciones de la aplicación",
    settingsSections: "Secciones de los ajustes",
  },
  failed: { one: "{count} con fallo", other: "{count} con fallos" },
  overview: { label: "Resumen", keywords: "inicio panel salud home dashboard health", goTo: "Ir al resumen" },
  apps: { label: "Aplicaciones", keywords: "apps sitios desplegar despliegue deploy", goTo: "Ir a aplicaciones" },
  databases: {
    label: "Bases de datos",
    keywords: "mysql postgres redis mongodb sql databases",
    goTo: "Ir a bases de datos",
  },
  services: { label: "Servicios", keywords: "systemd unidades demonios services units" },
  cron: { label: "Cron", keywords: "programar temporizadores tareas schedule timers jobs" },
  domains: {
    label: "Dominios y certificados",
    keywords: "ssl tls https certbot nginx apache sitios domains certificates",
  },
  backups: { label: "Copias de seguridad", keywords: "restaurar instantáneas backups restore" },
  activity: { label: "Actividad", keywords: "auditoría registro tareas historial despliegues audit log" },
  server: { label: "Servidor", keywords: "máquina salud cpu memoria disco procesos monitor server" },
  settings: { label: "Ajustes", keywords: "preferencias configuración settings", goTo: "Ir a ajustes" },
  appTabs: {
    overview: { label: "Resumen" },
    deployments: { label: "Despliegues", keywords: "desplegar compilaciones historial revertir deploys rollback" },
    logs: { label: "Registros", keywords: "journal salida logs" },
    metrics: { label: "Métricas", keywords: "cpu memoria gráficas metrics" },
    environment: { label: "Entorno", keywords: "env variables secretos environment" },
    domains: { label: "Dominios", keywords: "certificado ssl www domains" },
    diagnose: { label: "Diagnóstico", keywords: "caída por qué rota salud diagnose" },
    settings: { label: "Ajustes", keywords: "webhook origen compilación puerto eliminar settings" },
  },
  settingsTabs: {
    general: {
      label: "General",
      keywords: "directorio de aplicaciones servidor web correo idioma language",
      command: "Ajustes generales",
    },
    security: {
      label: "Seguridad",
      keywords: "verificación en dos pasos 2fa totp sesiones bloqueo security",
      command: "Ajustes de seguridad",
    },
    notifications: {
      label: "Notificaciones",
      keywords: "alertas correo slack webhook canales notifications",
      command: "Ajustes de notificaciones",
    },
    integrations: {
      label: "Integraciones",
      keywords: "github app repositorios push pull request vistas previas integrations",
      command: "Ajustes de integraciones",
    },
    tokens: { label: "Tokens de API", keywords: "automatización ci alcance tokens", command: "Tokens de API" },
    about: { label: "Acerca de", keywords: "versión actualización about", command: "Acerca de Noust" },
  },
};
