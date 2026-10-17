import type { complianceSetup as en } from "../en/complianceSetup";
import type { Catalog } from "../types";

export const complianceSetup: Catalog<typeof en> = {
  title: "Cumplimiento",
  description:
    "Los marcos activan los controles que ayudan con una norma. Nada está activo hasta que lo eliges, y pueden estar varios a la vez: cada control toma el valor más estricto que pida cualquiera de ellos.",
  honest:
    "Un marco activa controles técnicos. No hace que un servidor cumpla: lo organizativo es tuyo, y solo un auditor puede decir el resto.",
  loadFailed: "No se pudo leer la configuración de cumplimiento",
  loading: "Leyendo la configuración de cumplimiento",
  frameworks: {
    title: "Marcos",
    description:
      "Activa uno para encender los controles que nombra. Antes de confirmar ves qué cambia y qué cláusula cumple cada control.",
    on: "Activo",
    off: "Inactivo",
    viaProfile: "Activo por security.profile",
    controlsCount: { one: "{count} control", other: "{count} controles" },
    changesCount: {
      one: "activa o endurece {count} control",
      other: "activa o endurece {count} controles",
    },
    nothingNew: "Todo lo que pide ya está en vigor",
    show: "Qué activa",
    enable: "Activar…",
    disable: "Desactivar…",
    clauses: "Cláusula",
    organisational: "Sigue siendo organizativo",
    notCovered: "Técnico, y no se aplica aquí",
    forcedHere: "Fuerza {count} de los controles de abajo",
    summary: {
      ensBasica:
        "Esquema Nacional de Seguridad, categoría BÁSICA: las medidas del anexo II que se aplican a la categoría más baja.",
      ensMedia:
        "Esquema Nacional de Seguridad, categoría MEDIA: lo que Noust llama perfil ens-medium desde la 3.1.",
      ensAlta: "Esquema Nacional de Seguridad, categoría ALTA: MEDIA más una copia guardada aparte.",
      iso27001:
        "La norma internacional de gestión de la seguridad de la información: los controles del anexo A que una herramienta de despliegue puede aplicar.",
      nis2: "La directiva europea de seguridad de redes y sistemas de información: las medidas de gestión de riesgos del artículo 21 con las que un servidor puede ayudar.",
      cisIg1:
        "CIS Controls v8, Grupo de implementación 1: higiene cibernética esencial, las salvaguardas que una herramienta de despliegue puede aplicar.",
      cisIg2:
        "CIS Controls v8, Grupo de implementación 2: IG1 más las salvaguardas de IG2 que una herramienta de despliegue puede aplicar.",
      soc2Security:
        "Criterios de Seguridad de SOC 2: los criterios de acceso lógico, monitorización y gestión de cambios con los que un servidor puede ayudar.",
      gdpr32:
        "Artículo 32 del RGPD: las medidas técnicas de seguridad del tratamiento con las que un servidor puede ayudar.",
    },
  },
  dialog: {
    enableTitle: "Activar {name}",
    disableTitle: "Desactivar {name}",
    enableLead: "Esto es lo que cambia en este servidor.",
    disableLead:
      "La protección baja. Lo que este marco forzaba vuelve al valor por defecto, o a lo que otro marco o tu propio ajuste sigan pidiendo.",
    nothing: "Ningún control cambia de valor.",
    planning: "Calculando qué cambia",
    planFailed: "No se pudo calcular qué cambia",
    control: "Control",
    now: "Ahora",
    then: "Después",
    because: "Por qué",
    understood: "Lo he leído y quiero continuar",
    enableAction: "Activar",
    disableAction: "Desactivar",
    failed: "No se pudo hacer el cambio",
    turnedOn: "{name} está activo",
    turnedOff: "{name} está inactivo",
  },
  warning: {
    heading: "Mira esto primero",
    no_security_officer:
      "Ninguna cuenta activa tiene el rol de seguridad, y los administradores dejarán de gestionar cuentas, ajustes de seguridad y el registro de auditoría. Crea una antes, desde un terminal: noust user create NOMBRE --role security.",
    separation_conflicts: {
      one: "{count} persona tiene cuentas que no pueden ir juntas. Sepáralas o registra una excepción documentada, o serán rechazadas.",
      other:
        "{count} personas tienen cuentas que no pueden ir juntas. Sepáralas o registra una excepción documentada, o serán rechazadas.",
    },
  },
  controls: {
    title: "Controles",
    description:
      "Cada control tiene su propio ajuste. Un marco activo bloquea los controles que pide en su valor o en uno más estricto; siempre puedes endurecer uno más.",
    lockedBy: "Lo exige {names}",
    ownSetting: "Tu propio ajuste",
    change: "Cambiar…",
    forced: "Forzado",
    checkOnly: "Lo juzga la comprobación de cumplimiento: nada lo rechaza",
    changed: "{name} cambiado",
    group: {
      access: "Acceso y cuentas",
      accountability: "Trazabilidad y registro de auditoría",
      backup: "Copias de seguridad",
      network: "Red y servidor",
    },
    dialog: {
      title: "Cambiar {name}",
      value: "Valor",
      noLimit: "Sin límite",
      stricter: "Un marco pide {floor} o más estricto.",
      save: "Guardar",
      failed: "No se pudo cambiar el control",
    },
  },
  value: {
    on: "Activado",
    off: "Desactivado",
    none: "Sin límite",
  },
  unit: {
    minutes: { one: "{count} minuto", other: "{count} minutos" },
    hours: { one: "{count} hora", other: "{count} horas" },
    days: { one: "{count} día", other: "{count} días" },
    characters: { one: "{count} carácter", other: "{count} caracteres" },
    attempts: { one: "{count} intento", other: "{count} intentos" },
  },
  choice: {
    optional: "Opcional",
    required: "Obligatorio",
    any: "Cualquier certificado",
    operator: "El certificado de la organización",
    allowed: "Permitido",
    refused: "Rechazado",
    break_glass: "Acceso de emergencia",
    recovery: "Solo recuperar cuentas",
  },
  control: {
    roles_split: {
      title: "Separar las funciones del administrador",
      summary:
        "Los administradores no gestionan cuentas, ajustes de seguridad ni el registro de auditoría; lo hacen los responsables de seguridad y los auditores.",
    },
    separation_of_duties: {
      title: "Separación de funciones",
      summary:
        "Una persona no puede tener la cuenta que opera el servidor y la que lo gobierna, salvo que se registre una excepción documentada.",
    },
    single_role_per_account: {
      title: "Un rol por cuenta",
      summary:
        "Los roles sobre todo el servidor no se suman al propio de la cuenta; una persona con dos funciones tiene dos cuentas.",
    },
    sso_no_admin: {
      title: "Los proveedores de identidad nunca dan admin",
      summary: "El rol admin lo concede una persona, nunca el rol por defecto ni un grupo del proveedor.",
    },
    approvals: {
      title: "Aprobación de una segunda persona",
      summary:
        "Las acciones equivalentes a root, los servidores y los cambios de rol esperan a que una segunda persona los apruebe.",
    },
    restricted_tokens: {
      title: "Tokens de API restringidos",
      summary:
        "Un token pertenece a una cuenta, no puede actuar en modo sudo y nunca tiene permisos equivalentes a root, de cuentas ni de seguridad.",
    },
    token_max_days: {
      title: "Vida máxima de un token de API",
      summary: "Pasa a ser obligatoria una caducidad, como mucho de esta duración.",
    },
    master_token: {
      title: "Token maestro",
      summary: "Qué puede hacer el token maestro cuando ya existen cuentas.",
    },
    sudo_password: {
      title: "Contraseña para el modo sudo",
      summary: "Confirmar que eres tú pide también la contraseña de la cuenta.",
    },
    sudo_idle_minutes: {
      title: "Inactividad del modo sudo",
      summary: "Cuánto sigue abierto el modo sudo sin una acción elevada.",
    },
    sudo_max_minutes: {
      title: "Duración máxima del modo sudo",
      summary: "Lo máximo que dura el modo sudo desde que se confirma, por activa que esté la sesión.",
    },
    session_idle_minutes: {
      title: "Inactividad de la sesión",
      summary: "Una sesión de la consola sin uso durante este tiempo termina.",
    },
    session_absolute_hours: {
      title: "Duración máxima de la sesión",
      summary: "Una sesión termina pasado este tiempo desde el inicio, por activa que esté.",
    },
    lockout_threshold: {
      title: "Intentos fallidos antes del bloqueo",
      summary: "Inicios de sesión rechazados seguidos que bloquean una cuenta.",
    },
    lockout_minutes: {
      title: "Duración del bloqueo",
      summary: "Lo mínimo que dura ese bloqueo.",
    },
    password_min_length: {
      title: "Contraseña más corta",
      summary: "El menor número de caracteres que puede tener una contraseña.",
    },
    access_review_days: {
      title: "Revisión de accesos",
      summary: "Las cuentas y los roles se revisan al menos con esta frecuencia.",
    },
    stale_account_days: {
      title: "Cuentas inactivas",
      summary: "Una cuenta sin uso durante este tiempo se señala.",
    },
    change_reasons: {
      title: "Un motivo para cada cambio",
      summary: "Cada cambio desde la línea de órdenes, y cada solicitud de aprobación, dice por qué.",
    },
    exception_reasons: {
      title: "Un motivo para cada excepción",
      summary:
        "Compilar como root, aceptar un riesgo, un contenedor privilegiado o congelar por un incidente dice por qué.",
    },
    audit_retention_days: {
      title: "Retención de la auditoría",
      summary: "El menor número de días que se conservan los eventos de auditoría.",
    },
    audit_fail_closed: {
      title: "Rechazar cambios si falla la auditoría",
      summary: "Se rechaza un cambio mientras no se pueda escribir el registro de auditoría.",
    },
    external_audit_sink: {
      title: "Auditoría fuera de la máquina",
      summary: "El registro de auditoría se envía a un destino fuera de la máquina.",
    },
    audit_review_days: {
      title: "Revisión de la auditoría",
      summary: "El registro de auditoría se revisa al menos con esta frecuencia.",
    },
    log_shipping_tls: {
      title: "Registros solo por TLS",
      summary: "Los registros salen del servidor solo por TLS.",
    },
    backup_encryption: {
      title: "Copias cifradas",
      summary: "Las copias de seguridad salen del servidor solo cifradas.",
    },
    backup_verify_days: {
      title: "Verificación de copias",
      summary: "Se demuestra que una copia se puede restaurar al menos con esta frecuencia.",
    },
    backup_max_age_hours: {
      title: "Antigüedad de la última copia",
      summary: "La última copia de una aplicación debe ser más reciente que esto.",
    },
    backup_offsite: {
      title: "Una copia guardada aparte",
      summary: "Al menos una copia se guarda en un lugar distinto del servidor.",
    },
    tls_certificate: {
      title: "Certificado de la consola",
      summary: "La consola sirve un certificado de la PKI de la organización, no uno autofirmado.",
    },
    certificate_min_days: {
      title: "Aviso de certificado",
      summary: "Un certificado que caduca antes de esto es un hallazgo.",
    },
    explicit_allowlist: {
      title: "Indicar quién puede conectarse",
      summary: "Una consola alcanzable fuera de loopback indica quién puede conectarse.",
    },
    console_exposure: {
      title: "Publicar la consola",
      summary: "Si la consola puede publicarse en un nombre público.",
    },
    database_remote_listen: {
      title: "Bases de datos fuera de loopback",
      summary: "Si un motor de bases de datos puede escuchar fuera de loopback.",
    },
    server_terminal: {
      title: "Terminal en el servidor",
      summary: "Si Noust puede abrir una shell de root en el servidor desde el navegador.",
    },
    terminal_idle_minutes: {
      title: "Inactividad del terminal",
      summary: "Un terminal de la consola sin teclear nada durante este tiempo termina.",
    },
    terminal_max_minutes: {
      title: "Duración máxima del terminal",
      summary: "Lo máximo que dura en total un terminal de la consola.",
    },
    mounts_read_only: {
      title: "Rutas del servidor de solo lectura",
      summary: "Una ruta del servidor montada en una aplicación es de solo lectura.",
    },
    db_write_root_equivalent: {
      title: "Escribir SQL exige equivalente a root",
      summary: "Escribir SQL desde la consola necesita el permiso equivalente a root.",
    },
  },
};
