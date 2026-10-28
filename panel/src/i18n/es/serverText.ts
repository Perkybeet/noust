import type { serverText as en } from "../en/serverText";
import type { Catalog } from "../types";

/** Las frases que el servidor dice con un código, en español. Ver `en/serverText.ts`. */
export const serverText: Catalog<typeof en> = {
  approvals: {
    asked_approved: {
      text:
        "Esto necesita la aprobación de una segunda persona, seas quien seas. La solicitud {request} está aprobada: envía la llamada otra vez con ella ahora.",
      hint:
        "Una vez aprobada, envía exactamente la misma llamada otra vez con la cabecera {header}: {request}, en menos de {minutes} minutos.",
    },
    asked_waiting: {
      text:
        "Esto necesita la aprobación de una segunda persona, seas quien seas. La solicitud {request} espera a que la decida una cuenta con uno de estos roles: {approvers}.",
      hint:
        "Una vez aprobada, envía exactamente la misma llamada otra vez con la cabecera {header}: {request}, en menos de {minutes} minutos.",
    },
    central_missing: {
      text: "Esto necesita la aprobación de una segunda persona en la central.",
      hint: "Pídela en la central y repite la llamada a través de ella cuando esté aprobada.",
    },
    reason_required: {
      text: "Di por qué: esta llamada necesita la aprobación de una segunda persona y un motivo.",
      hint: "Envía el motivo en la cabecera {header} (codificado en porcentaje).",
    },
  },
  controls: {
    asked_by_framework: {
      text:
        "El control {control} lo pide, y el marco de cumplimiento en vigor ({frameworks}) pide ese control, así que sigue activo mientras el marco lo esté y no se puede desactivar por sí solo.",
      hint: "Para desactivar el marco (Ajustes > Controles):\n  {command}",
    },
    asked_by_setting: {
      text:
        "El control {control} lo pide, y se activó en Ajustes > Controles (o con la línea de comandos). Se aplica a todo el mundo, sea cual sea su rol.",
      hint: "Para desactivarlo allí, o:\n  {command}",
    },
  },
  doctor: {
    config: {
      apps_dir_missing: {
        text: "Directorio de aplicaciones: no se encontró {path}",
      },
      apps_dir_ok: {
        text: "Directorio de aplicaciones: {path}",
      },
      file_missing: {
        text: "Archivo de configuración: no se encontró {path}",
      },
      file_ok: {
        text: "Archivo de configuración: {path}",
      },
      log_dir_missing: {
        text: "Directorio de registros: no se encontró {path}",
      },
      log_dir_ok: {
        text: "Directorio de registros: {path}",
      },
    },
    cpu: {
      below_floor: {
        text: "Procesadores: {amount} (Noust necesita al menos {floor} para funcionar)",
      },
      below_minimum: {
        text: "Procesadores: {amount} (un servidor se dimensiona para al menos {minimum})",
      },
      note: {
        text: "Noust y una compilación necesitan al menos uno.",
      },
      ok: {
        text: "Procesadores: {amount} (mínimo {minimum})",
      },
      unreadable: {
        text: "Procesadores: no se pudieron leer",
      },
    },
    disk: {
      below_floor: {
        text: "Disco libre: {free} libres de {total} en {path}, por debajo de los {floor} que Noust necesita para funcionar",
      },
      below_minimum: {
        text: "Disco libre: {free} libres de {total} en {path}, por debajo de los {minimum} para los que se dimensiona un servidor",
      },
      note: {
        text:
          "'noust server cleanup' elimina lo que Noust puede demostrar que no se usa; cada release en disco conserva su código y sus dependencias.",
      },
      ok: {
        text: "Disco libre: {free} libres de {total} en {path} (mínimo {minimum})",
      },
      unreadable: {
        text: "Disco libre: no se pudo leer",
      },
    },
    memory: {
      below_floor: {
        text: "Memoria: {amount}, por debajo de los {floor} que Noust necesita para funcionar",
      },
      below_floor_swap: {
        text: "Memoria: {amount}, swap {swap}, por debajo de los {floor} que Noust necesita para funcionar",
      },
      below_minimum: {
        text: "Memoria: {amount}, por debajo de los {minimum} para los que se dimensiona un servidor",
      },
      below_minimum_swap: {
        text: "Memoria: {amount}, swap {swap}, por debajo de los {minimum} para los que se dimensiona un servidor",
      },
      note: {
        text:
          "Las compilaciones son lo que necesita memoria; un archivo de swap deja que una que necesita más de la que tiene el servidor termine despacio en lugar de que la maten.",
      },
      ok: {
        text: "Memoria: {amount} (mínimo {minimum})",
      },
      ok_swap: {
        text: "Memoria: {amount}, swap {swap} (mínimo {minimum})",
      },
      unreadable: {
        text: "Memoria: no se pudo leer",
      },
    },
    names: {
      explain: {
        text:
          "Nada de esto deja de funcionar: Noust acepta los nombres antiguos mientras se sigan usando. 'noust setup rename' termina lo que se puede terminar sin reiniciar nada.",
      },
    },
    node: {
      not_needed: {
        text: "node: no instalado (solo lo necesitan las aplicaciones Node.js)",
      },
      npm_missing: {
        text: "npm: no instalado, aunque viene con Node.js",
      },
    },
    platform: {
      best_effort: {
        text: "{label}: con el mejor esfuerzo, no es una plataforma probada",
      },
      eol: {
        text: "{release} deja de recibir actualizaciones de seguridad el {date}",
      },
      explain: {
        text: "Las plataformas en las que Noust se instala están en docs/PLATFORMS.md",
      },
      pip_only: {
        text: "{label}: instalado con pip, no hay paquete para él",
      },
      supported: {
        text: "{label}: compatible",
      },
      unsupported: {
        text: "{label}: no es una plataforma para la que Noust declare soporte",
      },
    },
    port: {
      container: {
        text: "Puerto {port}: publicado por un contenedor ({holders})",
      },
      container_note: {
        text: "El proxy de otra plataforma o un stack de Compose lo ocupa, así que nginx no puede escuchar en él hasta que eso se detenga.",
      },
      free: {
        text: "Puerto {port}: libre (el servidor web aún no escucha en él)",
      },
      held: {
        text: "Puerto {port}: ocupado por {holders}, no por el servidor web",
      },
      held_note: {
        text:
          "Un certificado se pide por el puerto 80 y los sitios se sirven por el 443: el servidor web no puede arrancar mientras otro programa los ocupe.",
      },
      web_server: {
        text: "Puerto {port}: {holders}",
      },
    },
    program: {
      installed: {
        text: "{command}: instalado",
      },
      missing: {
        text: "{command}: no instalado",
      },
      optional_missing: {
        text: "{command}: no instalado (opcional)",
      },
      version: {
        text: "{command}: {version}",
      },
    },
    python: {
      missing: {
        text: "python3: no instalado, necesario para las aplicaciones Python",
      },
    },
    relays: {
      left: {
        text: "{domain}: {what}",
      },
      unread: {
        text: "Sin comprobar: {reason}",
      },
    },
    ssh: {
      github_connected: {
        text: "SSH de GitHub: conectado",
      },
      github_note: {
        text: "Añade tu clave a GitHub: https://github.com/settings/keys",
      },
      github_refused: {
        text: "SSH de GitHub: no configurado",
      },
      github_untested: {
        text: "SSH de GitHub: no comprobado aquí",
      },
      key_ok: {
        text: "Clave SSH: {path}",
      },
      no_key: {
        text: "Clave SSH: no encontrada",
      },
    },
    web: {
      none: {
        text: "Servidor web: no instalado",
      },
      running: {
        text: "  Servicio {unit}: en marcha",
      },
      stopped: {
        text: "  Servicio {unit}: parado",
      },
    },
  },
  invitation: {
    mail_failed: {
      text: "No se pudo enviar la invitación por correo: {reason}",
      hint: "La invitación existe: entrega su enlace tú mismo.",
    },
  },
  mail: {
    no_public_url: {
      text: "La consola no tiene una dirección pública que poner en el enlace",
      hint: "Define web.public_url con la dirección en la que las personas abren la consola.",
    },
    no_transport: {
      text: "Este servidor no tiene ninguna forma de enviar correo",
      hint: "Define monitor.smtp.* o monitor.email_api.* (Ajustes > Canales) y vuelve a pedirlo.",
    },
  },
  names: {
    rule: {
      dashes: {
        text: "Usa letras minúsculas, dígitos y guiones sueltos, empezando por una letra, con un máximo de {max} caracteres.",
      },
      underscores: {
        text: "Usa letras minúsculas, dígitos y guiones bajos sueltos, empezando por una letra, con un máximo de {max} caracteres.",
      },
      words: {
        text: "Usa letras minúsculas, dígitos, '-' y '_', empezando por una letra, con un máximo de {max} caracteres.",
      },
    },
  },
  roles: {
    auditor_exclusive: {
      text: "{username} es auditor: revisa a todos y no tiene nada más",
      hint: "Usa otra cuenta para lo que deba poder hacer.",
    },
    in_use: {
      text: "El rol {name} sigue dado a {count} cuenta(s) o lugar(es)",
      hint: "Mira quién lo tiene con: {command}, quítaselo y después elimínalo.",
    },
    last_owner: {
      text: "{username} es la última cuenta que puede usar el rol de propietario",
      hint:
        "Un servidor siempre conserva a alguien que pueda hacerlo todo. Da primero el rol de propietario a otra cuenta que pueda entrar y este cambio podrá seguir.",
    },
    name_invalid: {
      text: "Nombre de rol no válido: {name}",
      hint: "Usa de 2 a 40 letras minúsculas, cifras o '-', empezando y acabando por letra o cifra, como 'release-manager'.",
    },
    name_system: {
      text: "{name} es el nombre de un rol integrado",
      hint:
        "Elige otro nombre. Los roles que trae Noust se pueden editar y devolver a como vienen, pero no redefinir con su nombre.",
    },
    name_taken: {
      text: "Ya existe un rol llamado {name}",
      hint: "Elige otro nombre, o mira ese con: {command}",
    },
    no_roles: {
      text: "Una cuenta necesita al menos un rol",
      hint: "Dale uno, como viewer.",
    },
    offer_unknown: {
      text: "{permission} no se ofrece al rol {name}",
      hint: "Mira lo que se ofrece con: {command}",
    },
    owner_by_owner: {
      text: "Solo un propietario puede dar o quitar el rol de propietario",
      hint: "Pídeselo a una cuenta que lo tenga. En el propio servidor puede hacerlo root: noust user roles.",
    },
    owner_protected: {
      text: "El rol de propietario no se puede cambiar ni eliminar",
      hint:
        "Tiene todos los permisos que existen, para que el servidor siempre conserve a alguien que pueda hacerlo todo. Para dar todo eso a una persona, dale el rol de propietario.",
    },
    permissions_many: {
      text: "Un rol tiene como máximo {max} permisos",
    },
    permissions_none: {
      text: "Un rol necesita al menos un permiso",
      hint: "Elige lo que puede hacer, como apps.read y apps.deploy.",
    },
    permissions_unknown: {
      text: "Permisos desconocidos: {names}",
      hint: "Lista todos los permisos que existen con: {command}",
    },
    reset_custom: {
      text: "{name} es un rol tuyo, así que no hay una versión de Noust a la que volver",
      hint: "Cámbialo, o elimínalo con: {command}",
    },
    seeded_kept: {
      text: "{name} viene con Noust, así que no se puede eliminar",
      hint: "Cambia lo que tiene, o devuélvelo a como viene con: {command}",
    },
    separation_account: {
      text: "{username} gobernaría el acceso y ejecutaría el servidor a la vez",
      hint:
        "Sus roles juntos tienen {governing} (quién puede ejecutar el servidor) con {running} (ejecutarlo). La separación de funciones las mantiene en manos distintas: da a la persona una segunda cuenta para la otra función.",
    },
    separation_role: {
      text: "{subject} gobernaría el acceso y ejecutaría el servidor a la vez",
      hint:
        "Tiene {governing} (quién puede ejecutar el servidor) junto con {running} (ejecutarlo). La separación de funciones las mantiene en manos distintas: divídelo en dos roles.",
    },
    too_many: {
      text: "Un servidor guarda como máximo {max} roles propios",
      hint: "Elimina uno que nadie tenga. Lístalos con: {command}",
    },
    unknown: {
      text: "No existe ningún rol llamado {name}",
      hint: "Lista los roles con: {command}",
    },
  },
  terminal: {
    disabled: {
      text: "Los terminales están desactivados en este servidor",
      hint: "Un administrador los activa con: {command}",
    },
    limit_total: {
      text: "Hay {open} terminales abiertos, el máximo que permite este servidor",
      hint: "Cierra uno o sube terminal.max_sessions.",
    },
    limit_user: {
      text: "Ya tienes {open} terminales abiertos, el máximo que puede tener una persona",
      hint: "Cierra uno o sube terminal.max_sessions_per_user.",
    },
    server_control: {
      text: "El control {control} está en refused en este servidor, así que el terminal del propio servidor sigue desactivado",
      hint: "Ningún marco de cumplimiento lo pide: se fijó en Ajustes > Controles o con la línea de comandos. Para permitirlo: {command}",
    },
    server_framework: {
      text:
        "El marco de cumplimiento en vigor ({frameworks}) mantiene desactivado el terminal del propio servidor (el control {control} está en refused)",
      hint:
        "Una shell de root en el navegador es lo más cercano a root que tiene la consola. Para permitirlo de todos modos, lo cual queda registrado: {command}. O desactiva el marco: {turn_off_command}",
    },
    server_off: {
      text: "El terminal del propio servidor está desactivado (terminal.server.enabled)",
      hint: "Es una shell de root, así que sigue desactivado hasta que un administrador lo active: {command}",
    },
  },
  tunnels: {
    cloudflared_missing: {
      text: "cloudflared no está instalado",
      hint:
        "Noust lo instala desde el repositorio de paquetes de Cloudflare, {url}, con la clave con la que Cloudflare lo firma: ejecuta 'noust tunnel install-cloudflared' y después este comando otra vez.",
    },
    no_account: {
      text: "No hay ninguna cuenta de Cloudflare con la que gestionar un túnel",
      hint: "Añade una con Account > Cloudflare Tunnel:Edit: noust dns provider add NAME --kind cloudflare --token-stdin",
    },
    store_sealed: {
      text: "El almacén de secretos está sellado",
      hint:
        "El conector de un túnel lee su token al arrancar, sin nadie que escriba una frase de paso. Usa los túneles en un servidor, no en una central sellada.",
    },
  },
};
