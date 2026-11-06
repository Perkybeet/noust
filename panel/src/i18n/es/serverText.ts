import type { serverText as en } from "../en/serverText";
import type { Catalog } from "../types";

/** Las frases que el servidor dice con un código, en español. Ver `en/serverText.ts`. */
export const serverText: Catalog<typeof en> = {
  actors: {
    through_central: {
      text: "{person} (desde la central {central})",
    },
  },
  approvals: {
    asked_approved: {
      text: "Esto necesita la aprobación de una segunda persona, seas quien seas. La solicitud {request} está aprobada: envía la llamada otra vez con ella ahora.",
      hint: "Una vez aprobada, envía exactamente la misma llamada otra vez con la cabecera {header}: {request}, en menos de {minutes} minutos.",
    },
    asked_waiting: {
      text: "Esto necesita la aprobación de una segunda persona, seas quien seas. La solicitud {request} espera a que la decida una cuenta con uno de estos roles: {approvers}.",
      hint: "Una vez aprobada, envía exactamente la misma llamada otra vez con la cabecera {header}: {request}, en menos de {minutes} minutos.",
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
  authdns: {
    check: {
      ds_dangling: {
        text: "La zona superior tiene un registro DS para {zone}, y la zona no está firmada",
        hint: "Los resolutores que validan rechazan el dominio. Quita el DS en tu registrador, o firma la zona.",
      },
      ds_mismatch: {
        text: "El registro DS que la zona superior tiene para {zone} no es el de la clave de esta zona",
        hint: "Sustitúyelo en tu registrador por el de los pasos.",
      },
      ds_missing: {
        text: "La zona superior no tiene registro DS para {zone}, que está firmada",
        hint: "Añádelo en tu registrador, después de que hayan cambiado los servidores de nombres.",
      },
      no_public_answer: {
        text: "Ningún resolutor público respondió, así que no se pudo comprobar la delegación de {zone}",
        hint: "Comprueba la salida de este servidor al puerto 53 de los resolutores públicos.",
      },
      not_authoritative: {
        text: "{server} en {address} respondió, pero no como autoridad de {zone}",
        hint: "La zona aún no está cargada allí, o responde otro programa en el puerto 53.",
      },
      not_delegated: {
        text: "Los resolutores encuentran {found} para {zone}, no tus servidores de nombres",
        hint: "Cambia los servidores de nombres en tu registrador y espera a que pase su TTL.",
      },
      out_of_sync: {
        text: "{server} sirve el número de serie {serial} de {zone} y {primary} sirve {primary_serial}",
        hint: "Un secundario copia una zona unos segundos después de que el primario la cambie. Si dura, comprueba que el primario llega a él por el puerto 53.",
      },
      recurses: {
        text: "{server} en {address} responde preguntas sobre otros dominios",
        hint: "Un servidor de nombres abierto a la recursión puede usarse para atacar a otros. Noust DNS nunca lo hace: en esa dirección responde otro programa.",
      },
      silent: {
        text: "{server} en {address} no respondió por {zone}",
        hint: "El puerto 53 por UDP y TCP debe estar abierto a internet en {address}, en el cortafuegos y en el panel de tu proveedor.",
      },
    },
    delegation: {
      check: {
        text: "Comprueba que los resolutores encuentran tus servidores de nombres",
        hint: "Ejecuta: {command}",
      },
      ds: {
        text: "Cuando hayan cambiado los servidores de nombres, añade este registro DS a {zone} en tu registrador: {ds}",
        hint: "Escribe la etiqueta de la clave, el algoritmo, el tipo de resumen y el resumen en el orden que pida el formulario de tu registrador. Añadir el DS antes de que cambien los servidores de nombres hace que los resolutores que validan rechacen el dominio.",
      },
      glue: {
        text: "En tu registrador, da de alta {hosts} como servidores de nombres con estas direcciones: {addresses}",
        hint: "Un servidor de nombres dentro del dominio que sirve solo se encuentra por direcciones que guarda el propio registrador (registros glue). Tu registrador puede llamarlos registros de host o servidores de nombres hijos.",
      },
      nameservers: {
        text: "En tu registrador, pon como servidores de nombres de {zone}: {nameservers}",
        hint: "Busca servidores de nombres personalizados o delegación DNS en los ajustes del dominio. El cambio llega a los resolutores tras el TTL del registrador, a menudo unas horas y como mucho dos días.",
      },
      one_server: {
        text: "Solo un servidor de nombres responde por {zone}",
        hint: "Si cae, {zone} cae. Añade un secundario en otro servidor, en otra red, y nómbralo en la zona.",
      },
      same_network: {
        text: "{first} y {second} están en la misma red",
        hint: "Una caída allí se lleva a los dos. Pon uno de ellos en otra red.",
      },
    },
    warning: {
      amplification: {
        text: "Un servidor de nombres en internet puede usarse para inundar de tráfico a otras personas",
        hint: "Noust DNS nunca recursa, limita la velocidad con que responde a una dirección y entrega transferencias de zona solo a los servidores que nombras, lo que lo hace una mala herramienta para eso. Aun así responde en el puerto 53 a cualquiera, así que mantenlo actualizado.",
      },
      availability: {
        text: "Un servidor de nombres es un servicio del que depende todo internet: si cae, caen todos los nombres que sirve",
        hint: "Pon al menos dos, en servidores de redes distintas, y mantén actualizados sus sistemas operativos.",
      },
    },
  },
  controls: {
    asked_by_framework: {
      text: "El control {control} lo pide, y el marco de cumplimiento en vigor ({frameworks}) pide ese control, así que sigue activo mientras el marco lo esté y no se puede desactivar por sí solo.",
      hint: "Para desactivar el marco (Ajustes > Cumplimiento):\n  {command}",
    },
    asked_by_setting: {
      text: "El control {control} lo pide, y se activó en Ajustes > Cumplimiento (o con la línea de comandos). Se aplica a todo el mundo, sea cual sea su rol.",
      hint: "Para desactivarlo allí, o:\n  {command}",
    },
  },
  database_engines: {
    conflict_mysql: {
      text: "{installed} está instalado y {requested} no puede funcionar a su lado: comparten el puerto 3306, sus paquetes y /var/lib/mysql.",
      hint: "Quita {installed} primero si quieres sustituirlo.",
    },
    conflict_redis: {
      text: "{installed} está instalado y {requested} no puede funcionar a su lado: comparten el puerto 6379 y sus paquetes.",
      hint: "Quita {installed} primero si quieres sustituirlo.",
    },
    no_package_manager: {
      text: "Noust instala {engine} con apt, dnf o zypper, y este sistema no tiene ninguno.",
      hint: "Ejecútalo como instancia, un contenedor que no necesita gestor de paquetes ({command}), o instálalo con el gestor de paquetes de la distribución: Noust lo gestiona desde ahí.",
    },
    not_available: {
      text: "Noust no puede instalar {engine} en {system}",
      hint: "Ejecútalo como instancia, un contenedor que no necesita gestor de paquetes ({command}), o instálalo con el gestor de paquetes del sistema: Noust lo gestiona desde ahí.",
    },
    redis_not_shipped: {
      text: "{system} no incluye Redis",
      hint: "Instala Valkey en su lugar: ejecuta '{command}'.",
    },
    valkey_not_shipped: {
      text: "{system} no incluye Valkey",
      hint: "Valkey viene empaquetado desde Debian 13 y Ubuntu 24.04. Instala Redis en su lugar: ejecuta '{command}'.",
    },
  },
  database_servers: {
    default_app: {
      text: "{key} es la base de datos de la pila de una aplicación, así que no puede ser la predeterminada",
      hint: "La predeterminada es el motor del sistema o una instancia que Noust ejecuta. Crea una con: {command}",
    },
    default_not_ready: {
      text: "{key} está en estado {state}, así que todavía no puede ser la predeterminada",
      hint: "Espera a que termine y vuelve a elegirla.",
    },
    unknown: {
      text: "No existe ningún servidor de bases de datos {key}",
      hint: "Lístalos con: {command}",
    },
    unsupported_ended: {
      text: "{name} {version} no recibe correcciones de seguridad desde {end_of_life}",
      hint: "Úsala solo para una aplicación que la necesite y no la dejes expuesta a internet. Para continuar, confirma que aceptas ejecutarla: unsupported_ok en la API, --accept-unsupported en la línea de comandos.",
    },
    unsupported_ending: {
      text: "{name} {version} dejará de recibir correcciones de seguridad el {end_of_life}",
      hint: "Un servidor creado hoy funcionaría mucho tiempo sin ellas. Para continuar, confirma que lo aceptas: unsupported_ok en la API, --accept-unsupported en la línea de comandos.",
    },
  },
  databases: {
    already_added: {
      text: "La base de datos {name} de {engine} ya está añadida a Noust",
    },
  },
  deploy_failure: {
    app_unhealthy: { text: "{domain} no superó la comprobación de arranque" },
    build_failed: { text: "No se pudo compilar la aplicación" },
    build_oom: { text: "La compilación se cerró por falta de memoria (código de salida 137)" },
    build_timeout: { text: "La compilación tardó más de {seconds} segundos y se detuvo" },
    install_failed: { text: "No se pudieron instalar las dependencias" },
    install_oom: { text: "La instalación de dependencias se quedó sin memoria y se cerró" },
    install_timeout: { text: "La instalación de dependencias tardó más de {seconds} segundos y se detuvo" },
    release_unhealthy: { text: "La versión {release} no superó la comprobación de arranque" },
    release_unhealthy_back: { text: "La versión {release} no superó la comprobación de arranque; la versión {previous} está activa de nuevo" },
    release_unhealthy_down: { text: "La versión {release} no superó la comprobación de arranque; la versión {previous} está activa de nuevo, pero tampoco responde" },
    rollback_unhealthy: { text: "{domain} no superó la comprobación de arranque tras volver atrás" },
    stack_back: { text: "La actualización de {domain} no superó la comprobación de arranque; los contenedores que estaban sirviendo vuelven a funcionar" },
    stack_back_down: { text: "La actualización de {domain} no superó la comprobación de arranque; los contenedores anteriores están de vuelta, pero tampoco responden" },
    stack_nothing_before: { text: "La actualización de {domain} no superó la comprobación de arranque; antes no había nada en marcha, así que no hay nada que recuperar" },
    stack_not_restored: { text: "La actualización de {domain} no superó la comprobación de arranque; lo que estaba sirviendo no se pudo recuperar por completo" },
    stack_unhealthy: { text: "La actualización de {domain} no superó la comprobación de arranque" },
  },
  diagnose: {
    blue_green: {
      idle_failed: {
        text: "{serving} sirve en el puerto {port}; {idle} está {idle_state}; su último arranque falló, así que {serving} siguió sirviendo",
      },
      idle_failed_release: {
        text: "{serving} sirve la versión {release} en el puerto {port}; {idle} está {idle_state}; su último arranque falló, así que {serving} siguió sirviendo",
      },
      leftover: {
        text: "{unit}.service sigue en marcha junto a las instancias; la próxima activación se negará a arrancar blue en el puerto {port}",
      },
      no_instance: {
        text: "El modo sin cortes está activado, pero ninguna instancia consta como la que sirve",
      },
      off: {
        text: "No está en modo sin cortes",
      },
      serving: {
        text: "{serving} sirve en el puerto {port}; {idle} está {idle_state}",
      },
      serving_release: {
        text: "{serving} sirve la versión {release} en el puerto {port}; {idle} está {idle_state}",
      },
      upstream_mismatch: {
        text: "nginx envía al {upstream_port}, pero {serving} sirve en el {port}",
      },
      upstream_none: {
        text: "nginx no envía a ningún servidor, pero {serving} sirve en el {port}",
      },
    },
    cause: {
      certificate_expired: {
        text: "El certificado TLS de {domain} caducó el {expiry}.",
      },
      crash_loop: {
        text: "El proceso termina con el estado {status} al arrancar; mira las últimas líneas del diario más abajo.",
      },
      disk_full: {
        text: "El disco que contiene el directorio de aplicaciones está al {percent}%.",
      },
      nginx_status: {
        text: "La aplicación responde directamente, pero nginx devuelve {status} para {domain}; nginx no llega a la aplicación.",
      },
      nginx_unreachable: {
        text: "La aplicación responde directamente, pero nginx no puede conectar ({error}) para {domain}; nginx no llega a la aplicación.",
      },
      no_unit: {
        text: "No hay unidad de systemd para {unit}; nunca se arrancó o se quitó su unidad.",
      },
      oom: {
        text: "El kernel la mató por quedarse sin memoria (por el límite o por la máquina).",
      },
      port_elsewhere: {
        text: "Escucha en el {actual_port}, pero Noust enruta al {recorded_port}.",
      },
      port_silent: {
        text: "El proceso está en marcha, pero no escucha en el puerto {port}.",
      },
      unit_failed: {
        text: "La unidad no pudo arrancar (Result={result}, estado de salida {status}); mira las últimas líneas del diario más abajo.",
      },
      upstream_mismatch: {
        text: "nginx envía {domain} al {upstream_port}, pero la instancia {serving} escucha en el {port}.",
      },
      upstream_none: {
        text: "nginx no envía {domain} a ningún servidor, pero la instancia {serving} escucha en el {port}.",
      },
      worker: {
        text: "El worker: {summary}; mira su salida más abajo.",
      },
      worker_port: {
        text: "{domain} no publica ningún puerto, pero tiene registrado el {port}: quítalo con {command}.",
      },
    },
    certificate: {
      bad_expiry: {
        text: "La fecha de caducidad de {label} no se puede leer",
      },
      expired: {
        text: "El certificado de {label} caducó el {expiry}",
      },
      expires_one: {
        text: "El certificado de {label} caduca en 1 día",
      },
      expires_several: {
        text: "El certificado de {label} caduca en {days} días",
      },
      missing: {
        text: "TLS está activado para {domain}, pero no se encontró ningún certificado",
      },
      no_expiry: {
        text: "No se pudo leer la fecha de caducidad de {label}",
      },
      none: {
        text: "No se encontró ningún certificado para {domain}",
      },
      valid_one: {
        text: "El certificado de {label} es válido 1 día más",
      },
      valid_several: {
        text: "El certificado de {label} es válido {days} días más",
      },
    },
    check: {
      incomplete: {
        text: "No se pudo completar esta comprobación",
      },
    },
    containers: {
      no_app: {
        text: "No hay ninguna aplicación registrada",
      },
      publishes_port: {
        text: "El stack publica un puerto: se juzga como una web, más abajo",
      },
      worker: {
        text: "Un worker: {summary}",
      },
      worker_with_port: {
        text: "Un worker: {summary}; tiene registrado el puerto {port} y no publica ninguno. Quítalo con: {command}",
      },
    },
    disk: {
      usage: {
        text: "{free} libres ({percent}% usado) en el sistema de archivos que contiene {path}",
      },
    },
    http_direct: {
      no_port: {
        text: "No hay puerto de servicio que sondear directamente",
      },
      status: {
        text: "HTTP {status} desde 127.0.0.1:{port}",
      },
      unreachable: {
        text: "No se pudo llegar a la aplicación directamente, en el puerto {port}",
      },
    },
    http_nginx: {
      status: {
        text: "HTTP {status} desde nginx para Host: {domain}",
      },
      unreachable: {
        text: "No se pudo llegar a nginx para {domain}",
      },
    },
    journal: {
      empty: {
        text: "No hay entradas en el diario de {unit_file}",
      },
      lines_one: {
        text: "Última línea del diario de {unit_file}",
      },
      lines_several: {
        text: "Últimas {count} líneas del diario de {unit_file}",
      },
      no_unit: {
        text: "No hay ninguna unidad llamada {unit} de la que leer el registro",
      },
      skipped_php: {
        text: "Aplicación PHP: se ejecuta en su pool de PHP-FPM, que se comprueba aparte; no hay unidad de la que leer el registro",
      },
      skipped_static: {
        text: "Sitio estático: lo sirve directamente el servidor web; no hay unidad de la que leer el registro",
      },
      unreadable: {
        text: "No se pudo leer el diario de {unit_file}",
      },
    },
    last_deployment: {
      cancelled: {
        text: "El último despliegue se canceló; lo que servía antes sigue sirviendo",
      },
      cancelled_by: {
        text: "El último despliegue lo canceló {actor}; lo que servía antes sigue sirviendo",
      },
      failed: {
        text: "El último despliegue falló",
      },
      failed_with: {
        text: "El último despliegue falló: {error}",
      },
      none: {
        text: "No hay ningún despliegue registrado para este dominio",
      },
      state: {
        text: "El último despliegue está {status}",
      },
      succeeded: {
        text: "El último despliegue salió bien",
      },
    },
    nginx_log: {
      clean: {
        text: "Ninguna línea del registro de errores de nginx menciona este dominio",
      },
      lines_one: {
        text: "Una línea del registro de errores de nginx menciona este dominio o un servidor de destino",
      },
      lines_several: {
        text: "{count} líneas del registro de errores de nginx mencionan este dominio o un servidor de destino",
      },
      unreadable: {
        text: "No se pudo leer {path}",
      },
    },
    oom: {
      found: {
        text: "El registro del kernel muestra actividad por falta de memoria en los últimos 7 días",
      },
      none: {
        text: "No hay procesos matados por falta de memoria en el registro del kernel en los últimos 7 días",
      },
      unreadable: {
        text: "No se pudo leer el registro del kernel",
      },
    },
    php_fpm: {
      config_refused: {
        text: "PHP-FPM rechaza su configuración; mira {command} más abajo.",
      },
      no_answer: {
        text: "El pool de PHP-FPM no responde a la comprobación de arranque; mira los errores de PHP más abajo.",
      },
      no_record: {
        text: "No hay registro de la aplicación",
      },
      ok: {
        text: "El pool de PHP-FPM responde ({service} {state})",
      },
      pool_disabled: {
        text: "El pool de PHP-FPM está desactivado (parado); arráncalo con {command}.",
      },
      pool_missing: {
        text: "Falta el archivo del pool de PHP-FPM {pool_file}; vuelve a desplegar con {command}.",
      },
      service_down: {
        text: "{service}, que ejecuta todos los pools de PHP, está {state}.",
      },
    },
    port: {
      elsewhere: {
        text: "La aplicación escucha en el {actual_port}, no en el puerto registrado, el {recorded_port}",
      },
      listening: {
        text: "El puerto {port} está a la escucha",
      },
      listening_process: {
        text: "El puerto {port} está a la escucha ({processes})",
      },
      not_recorded: {
        text: "No hay ningún puerto registrado para esta aplicación",
      },
      silent: {
        text: "Nada escucha en el puerto {port}",
      },
      skipped_php: {
        text: "Aplicación PHP: se ejecuta en su pool de PHP-FPM, que se comprueba aparte; no hay puerto que comprobar",
      },
      skipped_static: {
        text: "Sitio estático: lo sirve directamente el servidor web; no hay puerto que comprobar",
      },
    },
    unit: {
      active: {
        text: "{unit_file} está activa (en marcha)",
      },
      active_unmanaged: {
        text: "{unit_file} está activa (en marcha) (no la gestiona Noust)",
      },
      failed: {
        text: "{unit_file} ha fallado (Result={result})",
      },
      failed_unmanaged: {
        text: "{unit_file} ha fallado (no la gestiona Noust) (Result={result})",
      },
      missing: {
        text: "No se encontró ninguna unidad de systemd para {unit}",
      },
      restarting: {
        text: "{unit_file} se está reiniciando ({restarts} reinicios hasta ahora)",
      },
      restarting_unmanaged: {
        text: "{unit_file} se está reiniciando (no la gestiona Noust) ({restarts} reinicios hasta ahora)",
      },
      skipped_php: {
        text: "Aplicación PHP: se ejecuta en su pool de PHP-FPM, que se comprueba aparte; no hay unidad",
      },
      skipped_static: {
        text: "Sitio estático: lo sirve directamente el servidor web; no hay unidad",
      },
      state: {
        text: "{unit_file} está en {active_state}/{sub_state}",
      },
      state_unmanaged: {
        text: "{unit_file} está en {active_state}/{sub_state} (no la gestiona Noust)",
      },
    },
    zero_downtime: {
      could: {
        text: "Sus actualizaciones la reinician en el sitio; podría actualizarse sin cortes",
      },
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
        text: "'noust server cleanup' elimina lo que Noust puede demostrar que no se usa; cada release en disco conserva su código y sus dependencias.",
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
        text: "Las compilaciones son lo que necesita memoria; un archivo de swap deja que una que necesita más de la que tiene el servidor termine despacio en lugar de que la maten.",
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
        text: "Nada de esto deja de funcionar: Noust acepta los nombres antiguos mientras se sigan usando. 'noust setup rename' termina lo que se puede terminar sin reiniciar nada.",
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
        text: "Un certificado se pide por el puerto 80 y los sitios se sirven por el 443: el servidor web no puede arrancar mientras otro programa los ocupe.",
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
  fleet: {
    refused: {
      permission: {
        text: "Tu rol {role} no tiene el permiso {permission} en estos servidores",
        hint: "Pide a un responsable de seguridad de cada uno una cuenta con un rol que lo tenga, o que añadan ese permiso a tu rol allí.",
      },
    },
  },
  fleet_job: {
    apps_restart: {
      single: { text: "Reiniciar aplicaciones en 1 servidor" },
      several: { text: "Reiniciar aplicaciones en {count} servidores" },
    },
    apps_update: {
      single: { text: "Actualizar aplicaciones en 1 servidor" },
      several: { text: "Actualizar aplicaciones en {count} servidores" },
    },
    backups_run: {
      single: { text: "Hacer copia de las aplicaciones ahora en 1 servidor" },
      several: { text: "Hacer copia de las aplicaciones ahora en {count} servidores" },
    },
    backups_verify: {
      single: { text: "Verificar las copias más recientes en 1 servidor" },
      several: { text: "Verificar las copias más recientes en {count} servidores" },
    },
    certs_renew: {
      single: { text: "Renovar certificados en 1 servidor" },
      several: { text: "Renovar certificados en {count} servidores" },
    },
    database_transfer: {
      single: { text: "Mover una base de datos a otro servidor" },
      several: { text: "Mover una base de datos a {count} servidores" },
    },
    dns_cluster: {
      single: { text: "Configurar el DNS de Noust en 1 servidor" },
      several: { text: "Configurar el DNS de Noust en {count} servidores" },
    },
    index_refresh: {
      single: { text: "Recargar las listas de paquetes en 1 servidor" },
      several: { text: "Recargar las listas de paquetes en {count} servidores" },
    },
    noust_update: {
      single: { text: "Actualizar Noust en 1 servidor" },
      several: { text: "Actualizar Noust en {count} servidores" },
    },
    os_updates: {
      single: { text: "Aplicar actualizaciones del sistema en 1 servidor" },
      several: { text: "Aplicar actualizaciones del sistema en {count} servidores" },
    },
    placement_copy: {
      single: { text: "Copiar una aplicación a 1 servidor" },
      several: { text: "Copiar una aplicación a {count} servidores" },
    },
    placement_rollout: {
      single: { text: "Desplegar una aplicación en 1 servidor" },
      several: { text: "Desplegar una aplicación en {count} servidores" },
    },
    placement_ship: {
      single: { text: "Compilar en un servidor y ejecutar en otros, en 1 servidor" },
      several: { text: "Compilar en un servidor y ejecutar en otros, en {count} servidores" },
    },
    settings_apply: {
      single: { text: "Aplicar los ajustes de la flota en 1 servidor" },
      several: { text: "Aplicar los ajustes de la flota en {count} servidores" },
    },
  },
  fleet_settings: {
    backend_changed: {
      text: "El destino de copias {name} es {have} en este servidor y la flota lo quiere {want}",
      hint: "Ninguna ruta cambia el tipo de un destino: bórralo en ese servidor o dale otro nombre al de la flota.",
    },
    bad_mode: {
      text: "{mode} no es una forma en que un servidor pueda seguir los ajustes de la flota",
      hint: "Usa 'managed' para seguirlos o 'independent' para quedar fuera.",
    },
    bad_scope: {
      text: "{scope} no es un lugar donde se pueda fijar un ajuste de la flota",
      hint: "Usa 'fleet' para todos los servidores o 'node:<servidor>' para uno.",
    },
    field_not_text: {
      text: "El campo {field} de un {kind} debe ser texto",
      hint: "Envíalo como cadena de texto, por ejemplo https://central.example.com.",
    },
    item_conflict: {
      text: "El {kind} {name} ya existe en este servidor y no es de la flota",
      hint: "Aplica de nuevo dejando que la flota lo asuma, o ponle otro nombre al ajuste de la flota.",
    },
    item_unsupported: {
      text: "Este servidor no puede recibir un {kind} de la flota: no ofrece {operation}",
      hint: "Tiene un Noust más antiguo: actualízalo primero.",
    },
    kind_not_yet: {
      text: "La central todavía no puede gestionar {kind} para la flota",
      hint: "Por ahora, fíjalo en cada servidor, en sus propios ajustes.",
    },
    managed_missing: {
      text: "Este servidor no tiene ningún {kind} llamado {name}",
      hint: "Un ajuste se anota como de la flota cuando la central lo ha escrito: escríbelo primero.",
    },
    missing_field: {
      text: "Un {kind} necesita {field}",
      hint: "Un servidor no puede crearlo sin eso.",
    },
    no_such_setting: {
      text: "La flota no tiene ningún {kind} llamado {name}",
      hint: "Mira lo que tiene con: GET /api/fleet/settings",
    },
    not_managed: {
      text: "Ninguna central gestiona el {kind} {name} en este servidor",
      hint: "Solo se puede pedir que vuelva un ajuste que una central haya empujado.",
    },
    out_of_reach: {
      domain: {
        text: "La ruta del {kind} {name} nombra dominios que ninguna aplicación de este servidor tiene, así que no se envía aquí",
        hint: "Se envía aquí en cuanto una aplicación de este servidor tenga un dominio que la ruta nombra: aplica de nuevo tras desplegarla.",
      },
      server: {
        text: "La ruta del {kind} {name} no nombra este servidor, así que no se envía aquí",
        hint: "Añade este servidor a los servidores de la ruta, o quita la lista, para enviarlo aquí.",
      },
      tag: {
        text: "La ruta del {kind} {name} nombra etiquetas que ninguna aplicación de este servidor lleva, así que no se envía aquí",
        hint: "Se envía aquí en cuanto una aplicación de este servidor lleve una etiqueta que la ruta nombra (junto con un dominio que nombre, si la ruta tiene los dos): aplica de nuevo tras etiquetarla.",
      },
    },
    permission_missing: {
      text: "Fijar un {kind} para la flota necesita el permiso {permission}",
      hint: "Es el permiso que pide fijarlo en un solo servidor: pide a un administrador un rol que lo tenga.",
    },
    reach_unknown: {
      text: "No se pudieron leer las aplicaciones de este servidor, así que no se sabe si el {kind} {name} puede coincidir con algo en él",
      hint: "No se envió nada. Aplica de nuevo cuando el servidor responda sus aplicaciones.",
    },
    route_invalid: {
      text: "La ruta de este canal no es válida: {problem}",
      hint: "Una ruta de flota admite apps (dominios, con * y ?), tags, min_severity, server_events y servers (nombres y patrones de los servidores de esta central).",
    },
    route_local: {
      text: "Un canal de la flota no se puede enrutar por {list}",
      hint: "Los proyectos y los entornos son de cada servidor. Enruta por dominio, etiqueta, gravedad, evento o servidor, o crea un canal de equipo en el servidor que tiene el proyecto.",
    },
    secret_missing: {
      text: "{field} está oculto y todavía no hay nada guardado",
      hint: "Escribe el secreto otra vez: solo se muestra como {redacted} cuando hay uno guardado.",
    },
    single_name: {
      text: "Solo hay un {kind} para la flota y se llama {name}",
      hint: "Fíjalo como {name}.",
    },
    sync_ceiling: {
      text: "Este servidor deja a su central hacer como mucho '{level}', y aplicar los ajustes de la flota necesita 'admin'",
      hint: "Súbelo en este servidor, como root:\n  {command}",
    },
    sync_required: {
      text: "Solo la central que gestiona este servidor puede anotar un ajuste como de la flota",
      hint: "La central lo hace cuando aplica un ajuste; nadie lo anota a mano.",
    },
    sync_route: {
      text: "Un empuje de los ajustes de la flota no puede hacer {method} {path}",
      hint: "Solo puede escribir los ajustes que gestiona la flota: canales, reglas de alerta, destinos de copia, accesos a registros, variables compartidas y la dirección de la consola de la central.",
    },
    unknown_field: {
      text: "Un {kind} no tiene ningún campo llamado {field}",
      hint: "Un {kind} admite: {fields}.",
    },
    unknown_kind: {
      text: "{kind} no es algo que gestione la flota",
      hint: "La flota gestiona: {kinds}.",
    },
    unknown_node: {
      text: "No hay ningún servidor llamado {node} en esta flota",
      hint: "Lista los servidores con: {command}",
    },
    value_not_object: {
      text: "El valor de un {kind} debe ser un objeto de campos",
      hint: "Envía los campos que los ajustes de un servidor admiten para un {kind}.",
    },
    value_too_large: {
      text: "Este {kind} es mayor que los {limit} bytes que puede ocupar un ajuste",
    },
  },
  host: {
    updates_unsupported: {
      no_manager: {
        text: "Noust maneja apt, dnf y zypper, y aquí no hay ninguno instalado. Actualiza este sistema con su propio gestor de paquetes.",
      },
      transactional: {
        text: "Este sistema instala los paquetes mediante transacciones y un reinicio (transactional-update o un sistema basado en imágenes), así que Noust solo informa de su estado. Actualízalo con su propia herramienta.",
      },
    },
  },
  inspect: {
    can_deploy: {
      text: "Noust puede desplegar esto como {type}.",
    },
    can_deploy_also: {
      text: "Noust puede desplegar esto como {type}. También se parece a {others}; elige ese tipo en su lugar para desplegarlo así.",
    },
    container_dockerfile: {
      text: "Noust puede desplegar esto como un contenedor, construido con su Dockerfile.",
    },
    container_dockerfile_also: {
      text: "Noust puede desplegar esto como un contenedor, construido con su Dockerfile. También se parece a {others}; elige ese tipo en su lugar para desplegarlo así.",
    },
    container_railpack: {
      text: "Noust puede desplegar esto como un contenedor, construido con Railpack: no tiene Dockerfile.",
    },
    container_railpack_also: {
      text: "Noust puede desplegar esto como un contenedor, construido con Railpack: no tiene Dockerfile. También se parece a {others}; elige ese tipo en su lugar para desplegarlo así.",
    },
    dockerfile: {
      unreadable: {
        text: "El repositorio tiene un {dockerfile}, pero no se pudo leer como tal.",
        hint: "Despliégalo como contenedor: {command}. Comprueba que el {dockerfile} es un archivo, no un enlace, y que está confirmado en esta rama.",
      },
    },
    dotnet: {
      no_web_project: {
        text: "Es un proyecto .NET ({marker}), pero Noust no encontró ningún proyecto web que ejecutar en él.",
        hint: "Despliégalo como contenedor: añade un Dockerfile al repositorio y despliégalo con --type container. Sin Dockerfile, instala Railpack ({setup_command}), que lo construye como contenedor por sí solo: --type container --builder railpack.",
      },
      no_web_project_with_railpack: {
        text: "Es un proyecto .NET ({marker}), pero Noust no encontró ningún proyecto web que ejecutar en él.",
        hint: "Despliégalo como contenedor, que Railpack construye sin Dockerfile (está instalado aquí): {command}. O añade un Dockerfile y despliégalo con --type container.",
      },
    },
    install_deno: {
      text: "Instala Deno 2 para todo el sistema, donde la cuenta de compilación pueda ejecutarlo ({command}, o el instalador de {url} con {env_command}). Después despliégalo.",
    },
    install_setup_init: {
      text: "Instala lo que necesita con {command} y después despliégalo.",
    },
    install_toolchain: {
      text: "Instala las herramientas con: {command}. Después despliégalo.",
    },
    missing_programs: {
      text: "Es un proyecto {type}, pero este servidor no tiene {programs}.",
    },
    no_match: {
      text: "Nada del repositorio coincide con un tipo de aplicación que Noust despliegue.",
      hint: "Noust reconoce un proyecto por los archivos de la raíz del repositorio ({examples}, o un package.json con un script start). Revisa la rama, o elige un tipo explícitamente en vez de fiarte de la detección automática.",
    },
    package_json: {
      no_start: {
        text: "package.json no tiene un script start, ni una entrada main, ni ningún framework que Noust reconozca (Next.js, Vite, Nuxt, SvelteKit, Astro, Remix, Angular).",
        hint: "Añade un script start a package.json ({example_command}) o una entrada \"main\", para que Noust sepa cómo ejecutarlo; o elige el tipo explícitamente.",
      },
      unreadable: {
        text: "package.json no se puede leer como JSON.",
        hint: "Arregla package.json y vuelve a inspeccionar ({error}).",
      },
    },
    stack: {
      no_deployer: {
        text: "Es un proyecto {stack} ({marker}); Noust no tiene un desplegador de {stack}.",
        hint: "Despliégalo como contenedor: añade un Dockerfile al repositorio y despliégalo con --type container. Sin Dockerfile, instala Railpack ({setup_command}), que lo construye como contenedor por sí solo: --type container --builder railpack.",
      },
      no_deployer_with_railpack: {
        text: "Es un proyecto {stack} ({marker}); Noust no tiene un desplegador de {stack}.",
        hint: "Despliégalo como contenedor, que Railpack construye sin Dockerfile (está instalado aquí): {command}. O añade un Dockerfile y despliégalo con --type container.",
      },
      no_program: {
        text: "Es un proyecto {stack} ({marker}), pero Noust no encontró ningún programa que ejecutar en él.",
        hint: "Despliégalo como contenedor: añade un Dockerfile al repositorio y despliégalo con --type container. Sin Dockerfile, instala Railpack ({setup_command}), que lo construye como contenedor por sí solo: --type container --builder railpack.",
      },
      no_program_with_railpack: {
        text: "Es un proyecto {stack} ({marker}), pero Noust no encontró ningún programa que ejecutar en él.",
        hint: "Despliégalo como contenedor, que Railpack construye sin Dockerfile (está instalado aquí): {command}. O añade un Dockerfile y despliégalo con --type container.",
      },
    },
    subprojects: {
      text: "En la raíz del repositorio no hay nada que Noust despliegue; se encontró {found}.",
      hint: "Noust despliega un repositorio desde su raíz, o una aplicación por proyecto: un monorepo de JavaScript (workspaces de npm, Yarn, pnpm, Lerna, Nx o Turborepo) se lee con '{detect_command}' y cada aplicación se crea con '{create_command}', con versiones y vigilando solo sus propios archivos. Si no, despliega cada proyecto desde un repositorio propio, o añade en la raíz un compose.yaml que los construya y despliégalo como Docker Compose.",
    },
    turbo: {
      no_apps: {
        text: "El repositorio usa Turborepo (turbo.json), pero no es un monorepo que Noust despliegue: ninguna app de apps/ tiene un package.json.",
        hint: "El tipo monorepo necesita turbo.json, una configuración de workspace y al menos dos apps en apps/, cada una con su propio package.json. Con una sola app, llévala a la raíz del repositorio; o añade en la raíz un compose.yaml que construya las apps y despliégalo como Docker Compose.",
      },
      no_workspace: {
        text: "El repositorio usa Turborepo (turbo.json), pero no es un monorepo que Noust despliegue: no hay workspace (pnpm-workspace.yaml, o \"workspaces\" en package.json).",
        hint: "El tipo monorepo necesita turbo.json, una configuración de workspace y al menos dos apps en apps/, cada una con su propio package.json. Con una sola app, llévala a la raíz del repositorio; o añade en la raíz un compose.yaml que construya las apps y despliégalo como Docker Compose.",
      },
      no_workspace_no_apps: {
        text: "El repositorio usa Turborepo (turbo.json), pero no es un monorepo que Noust despliegue: no hay workspace (pnpm-workspace.yaml, o \"workspaces\" en package.json); ninguna app de apps/ tiene un package.json.",
        hint: "El tipo monorepo necesita turbo.json, una configuración de workspace y al menos dos apps en apps/, cada una con su propio package.json. Con una sola app, llévala a la raíz del repositorio; o añade en la raíz un compose.yaml que construya las apps y despliégalo como Docker Compose.",
      },
      no_workspace_one_app: {
        text: "El repositorio usa Turborepo (turbo.json), pero no es un monorepo que Noust despliegue: no hay workspace (pnpm-workspace.yaml, o \"workspaces\" en package.json); solo apps/{app}/ tiene un package.json en apps/.",
        hint: "El tipo monorepo necesita turbo.json, una configuración de workspace y al menos dos apps en apps/, cada una con su propio package.json. Con una sola app, llévala a la raíz del repositorio; o añade en la raíz un compose.yaml que construya las apps y despliégalo como Docker Compose.",
      },
      one_app: {
        text: "El repositorio usa Turborepo (turbo.json), pero no es un monorepo que Noust despliegue: solo apps/{app}/ tiene un package.json en apps/.",
        hint: "El tipo monorepo necesita turbo.json, una configuración de workspace y al menos dos apps en apps/, cada una con su propio package.json. Con una sola app, llévala a la raíz del repositorio; o añade en la raíz un compose.yaml que construya las apps y despliégalo como Docker Compose.",
      },
      other: {
        text: "El repositorio usa Turborepo (turbo.json), pero no es un monorepo que Noust despliegue.",
        hint: "El tipo monorepo necesita turbo.json, una configuración de workspace y al menos dos apps en apps/, cada una con su propio package.json. Con una sola app, llévala a la raíz del repositorio; o añade en la raíz un compose.yaml que construya las apps y despliégalo como Docker Compose.",
      },
    },
  },
  invitation: {
    mail_failed: {
      text: "No se pudo enviar la invitación por correo: {reason}",
      hint: "La invitación existe: entrega su enlace tú mismo.",
    },
  },
  jobs: {
    step: {
      backup: {
        complete: {
          text: "Copia de seguridad completada",
        },
        creating: {
          text: "Creando la copia de seguridad",
        },
        upload_complete: {
          text: "Subida completada",
        },
        uploading: {
          text: "Subiendo a {destination}",
        },
      },
      bundle: {
        active: {
          text: "Versión activa",
        },
        building: {
          text: "Compilando para enviar",
        },
        packed: {
          text: "Empaquetada, a la espera de que la recojan",
        },
        receiving: {
          text: "Recibiendo la versión",
        },
      },
      certificate: {
        created: {
          text: "Certificado creado",
        },
        requesting: {
          text: "Pidiendo el certificado",
        },
        serving: {
          text: "Sirviéndolo",
        },
      },
      clone: {
        complete: {
          text: "Clonado completado",
        },
      },
      complete: {
        text: "Completado",
      },
      delete: {
        complete: {
          text: "Eliminación completada",
        },
      },
      deploy: {
        complete: {
          text: "Despliegue completado",
        },
        copying_data: {
          text: "Copiando los datos",
        },
        database: {
          text: "Creando la base de datos {engine}",
        },
        deploying: {
          text: "Desplegando la aplicación",
        },
        preparing: {
          text: "Preparando el despliegue",
        },
        recipe: {
          text: "Preparando la receta {recipe}",
        },
      },
      disk: {
        giving_limit: {
          text: "Pasando el límite al kernel",
        },
        measuring: {
          text: "Midiendo el directorio de la aplicación",
        },
        taking_limit: {
          text: "Quitando el límite al kernel",
        },
      },
      domains: {
        deleted: {
          text: "El certificado está borrado",
        },
        deleting: {
          text: "Borrando el certificado de {name}",
        },
        extended: {
          text: "El certificado cubre todos los dominios",
        },
        extending: {
          text: "Ampliando el certificado a todos los dominios",
        },
        giving_back: {
          text: "Devolviéndolo",
        },
        in_place: {
          text: "El certificado está en su sitio",
        },
        ordering: {
          text: "Pidiendo un certificado propio",
        },
        removed: {
          text: "El certificado cubre solo los dominios que quedan",
        },
        removing: {
          text: "Quitando el dominio del certificado",
        },
      },
      done: {
        text: "Todo hecho",
      },
      engine: {
        installing: {
          text: "Instalando {engine}",
        },
        uninstalling: {
          text: "Desinstalando {engine}",
        },
      },
      exec: {
        finished: {
          text: "Comando terminado",
        },
        running: {
          text: "Ejecutando un comando en {domain}",
        },
      },
      hardening: {
        changing: {
          text: "Cambiando el aislamiento de la unidad de la aplicación",
        },
      },
      identity: {
        moving: {
          text: "Pasando la aplicación a su propia cuenta",
        },
      },
      import: {
        complete: {
          text: "Importación completada",
        },
      },
      migrate: {
        complete: {
          text: "Migración completada",
        },
        moving: {
          text: "Pasando el árbol a versiones",
        },
        planning: {
          text: "Planificando la migración",
        },
      },
      packages: {
        installing: {
          text: "Instalando los paquetes del sistema",
        },
      },
      pitr: {
        recovering: {
          text: "Recuperación a un momento dado: {action}",
        },
      },
      promote: {
        complete: {
          text: "Promoción completada",
        },
      },
      renewal: {
        complete: {
          text: "Renovación completada",
        },
        none_due: {
          text: "Ningún certificado tocaba renovarse",
        },
        renewing: {
          text: "Renovando certificados",
        },
        unreadable: {
          text: "No se pudo leer la lista de certificados de certbot, así que no se sabe cuáles se renovaron",
        },
      },
      replicas: {
        starting: {
          text: "Arrancando las copias y preguntando a cada una si responde",
        },
      },
      restore: {
        complete: {
          text: "Restauración completada",
        },
        downloading: {
          text: "Descargando y restaurando la copia de seguridad",
        },
        restoring: {
          text: "Restaurando la copia de seguridad",
        },
      },
      rollback: {
        complete: {
          text: "Vuelta atrás completada",
        },
        rolling_back: {
          text: "Volviendo atrás",
        },
        to_deployment: {
          text: "Volviendo al despliegue {deployment}",
        },
      },
      sandbox: {
        building: {
          text: "Compilando el commit actual en el entorno aislado",
        },
      },
      staged: {
        building: {
          text: "Compilando sin activar",
        },
        built: {
          text: "Compilada, a la espera de promoverla",
        },
        promoted: {
          text: "Promovida",
        },
        promoting: {
          text: "Poniendo la versión en servicio",
        },
      },
      tuning: {
        tuning: {
          text: "Ajustando la unidad de la aplicación",
        },
      },
      update: {
        complete: {
          text: "Actualización completada",
        },
        unit_not_running: {
          text: "Se reinició, pero la unidad no está en marcha: mira sus registros",
        },
      },
      zero_downtime: {
        off: {
          text: "Desactivando las actualizaciones sin cortes",
        },
        on: {
          text: "Activando las actualizaciones sin cortes",
        },
      },
    },
    title: {
      app_exec: { text: "Ejecutar un comando en {domain}" },
      app_exec_any: { text: "Ejecutar un comando en una aplicación" },
      backup: { text: "Hacer una copia de {domain}" },
      backup_any: { text: "Hacer una copia de una aplicación" },
      cert_acme_account: { text: "Registrar la cuenta ACME" },
      cert_create: { text: "Conseguir un certificado para {domain}" },
      cert_create_any: { text: "Conseguir un certificado" },
      cert_renew: { text: "Renovar el certificado de {domain}" },
      cert_renew_all: { text: "Renovar los certificados que toca renovar" },
      cleanup: { text: "Limpiar el servidor" },
      cloudflared_install: { text: "Instalar cloudflared" },
      custom: { text: "Hacer una tarea" },
      custom_of_app: { text: "Hacer una tarea para {domain}" },
      database: { text: "Hacer una tarea de bases de datos" },
      database_of_app: { text: "Trabajar en una base de datos de {domain}" },
      delete: { text: "Eliminar {domain}" },
      delete_any: { text: "Eliminar una aplicación" },
      deploy: { text: "Desplegar {domain}" },
      deploy_any: { text: "Desplegar una aplicación" },
      disk: { text: "Medir el disco que usa {domain}" },
      disk_any: { text: "Medir el disco que usa una aplicación" },
      disk_scan: { text: "Medir qué ocupa el disco" },
      fleet: { text: "Ejecutar una acción en toda la flota" },
      hardening: { text: "Cambiar el sandbox de {domain}" },
      hardening_any: { text: "Cambiar el sandbox de una aplicación" },
      identity_migrate: { text: "Dar a {domain} su propia cuenta de sistema" },
      identity_migrate_any: { text: "Dar a una aplicación su propia cuenta de sistema" },
      manifest: { text: "Aplicar un manifiesto" },
      migrate: { text: "Pasar {domain} a la disposición por releases" },
      migrate_any: { text: "Pasar una aplicación a la disposición por releases" },
      node_enroll: { text: "Añadir {node}" },
      node_enroll_any: { text: "Añadir un servidor" },
      node_provision: { text: "Crear el servidor {node}" },
      node_provision_any: { text: "Crear un servidor" },
      os_refresh: { text: "Actualizar las listas de paquetes" },
      os_update: { text: "Actualizar el sistema" },
      push: { text: "Enviar una copia a {destination}" },
      push_any: { text: "Enviar una copia a su destino" },
      replicas: { text: "Cambiar las copias en marcha de {domain}" },
      replicas_any: { text: "Cambiar las copias en marcha de una aplicación" },
      restore: { text: "Restaurar una copia en {domain}" },
      restore_any: { text: "Restaurar una copia" },
      rollback: { text: "Volver atrás en {domain}" },
      rollback_any: { text: "Volver atrás en una aplicación" },
      sandbox_test: { text: "Probar {domain} en el sandbox de compilación" },
      sandbox_test_any: { text: "Probar una aplicación en el sandbox de compilación" },
      self_update: { text: "Actualizar Noust" },
      server_action: { text: "Hacer una tarea del servidor" },
      server_action_dns_install: { text: "Instalar Knot DNS" },
      server_action_dns_setup: { text: "Configurar Noust DNS como {role}" },
      server_action_software_install: { text: "Instalar {component}" },
      server_action_software_remove: { text: "Quitar {component}" },
      server_security: { text: "Hacer una tarea de seguridad" },
      service_action: { text: "Reiniciar servicios" },
      site_action: { text: "Cambiar un sitio" },
      site_console_expose: { text: "Publicar la consola en {domain}" },
      site_console_unexpose: { text: "Dejar de publicar la consola" },
      site_status_page_publish: { text: "Publicar la página de estado en {domain}" },
      site_status_page_unpublish: { text: "Dejar de publicar la página de estado" },
      site_webserver_move: { text: "Pasar los sitios a {webserver}" },
      swap: { text: "Cambiar el archivo de intercambio" },
      swap_create: { text: "Crear un archivo de intercambio" },
      swap_remove: { text: "Quitar el archivo de intercambio" },
      tuning: { text: "Ajustar {domain}" },
      tuning_any: { text: "Ajustar una aplicación" },
      update: { text: "Actualizar {domain}" },
      update_any: { text: "Actualizar una aplicación" },
      zero_downtime: { text: "Cambiar la activación sin cortes de {domain}" },
      zero_downtime_any: { text: "Cambiar la activación sin cortes" },
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
  networks: {
    finding: {
      default_bridge: {
        text: "Es el puente por defecto de Docker: todo contenedor que se arranca sin una red está en él, y se alcanzan entre sí por la dirección.",
      },
      host: {
        text: "Es la red propia del anfitrión: los contenedores no tienen una red propia.",
      },
      internal: {
        text: "Es interna: los contenedores que están en ella no pueden salir al exterior.",
      },
      made_elsewhere: {
        text: "No la creó Compose: se hizo a mano o con otra herramienta.",
      },
      owned_by_another: {
        text: "Pertenece a la pila {owner}, que la creó.",
      },
      shared_many: {
        text: "Está compartida: {members} y {more} más también están en ella.",
      },
      shared_one: {
        text: "Está compartida: {members} también está en ella.",
      },
      shared_several: {
        text: "Está compartida: {members} también están en ella.",
      },
    },
    not_applicable: {
      text: "Esta aplicación no se ejecuta en Docker, así que no tiene red de Docker.",
    },
  },
  operations: {
    app_apply: { text: "Aplicar una declaración a {target}" },
    app_move: { text: "Mover {target} a otro proyecto" },
    app_restart: { text: "Reiniciar {target}" },
    app_start: { text: "Iniciar {target}" },
    app_stop: { text: "Detener {target}" },
    backup_verify: { text: "Verificar la copia {target}" },
    build_apply: { text: "Aplicar los ajustes de compilación de {target}" },
    build_cache_clear: { text: "Vaciar la caché de compilación de {target}" },
    compose_import: { text: "Importar un proyecto de Compose" },
    cron_run: { text: "Ejecutar la tarea programada {target}" },
    encryption_enable: { text: "Activar el cifrado de las copias" },
    encryption_rotate: { text: "Rotar la clave de cifrado de las copias" },
    engine_restart: { text: "Reiniciar el motor de bases de datos {target}" },
    engine_start: { text: "Iniciar el motor de bases de datos {target}" },
    engine_stop: { text: "Detener el motor de bases de datos {target}" },
    env_sync: { text: "Sincronizar el entorno de {target}" },
    fleet_settings_apply: { text: "Aplicar los ajustes de la flota" },
    monitor_install: { text: "Instalar el monitor" },
    monitor_scan: { text: "Analizar el servidor con el monitor" },
    node_test: { text: "Probar la conexión con {target}" },
    power_reboot: { text: "Reiniciar el servidor" },
    power_shutdown: { text: "Apagar el servidor" },
    probe_check: { text: "Comprobar la sonda {target}" },
    service_restart: { text: "Reiniciar el servicio {target}" },
    service_start: { text: "Iniciar el servicio {target}" },
    service_stop: { text: "Detener el servicio {target}" },
    sites_reload: { text: "Recargar los sitios del servidor web" },
    waf_install: { text: "Instalar el cortafuegos de aplicaciones web" },
  },
  power_checks: {
    jobs_running: { text: "En marcha ahora: {jobs}" },
    jobs_idle: { text: "No hay nada en marcha" },
    console_returns: { text: "La consola arranca con el servidor" },
    console_stays_down: {
      text: "La consola no es una unidad de systemd activada: no volverá a arrancar tras el reinicio. Ejecuta antes 'noust web enable'.",
    },
    apps_stay_down: { text: "Estas aplicaciones no están activadas y no volverán a arrancar: {apps}" },
    apps_return: { text: "Todas las aplicaciones arrancan con el servidor" },
    fstab_correct: { text: "fstab es correcto" },
    fstab_problem: {
      text: "findmnt --verify ha encontrado un problema, y un fstab erróneo puede dejar la máquina en modo de emergencia: {output}",
    },
    ramdisk_missing: { text: "El kernel más nuevo, {release}, no tiene disco RAM inicial en /boot" },
    ramdisk_present: { text: "El kernel más nuevo está completo" },
  },
  recipes: {
    root_equivalent: {
      agent: {
        text: "{title} es un agente de IA que ejecuta órdenes, y quien pueda manejarlo puede hacerle hacer todo lo que él puede: entrar en él es como entrar en una terminal",
        hint: "Su propia política de seguridad dice que no separa a unos usuarios de otros. Da su acceso solo a quien darías una terminal en este servidor, y no le pongas ninguna clave de modelo ni cuenta que no les darías a ellos.",
      },
      confirm_required: {
        text: "{title} tiene el poder de root en este servidor, y para instalarlo hace falta que lo confirmes",
        hint: "Si lo aceptas, confírmalo y vuelve a instalarlo: {command}",
      },
      docker_socket: {
        text: "{title} controla el Docker de este servidor, y eso equivale a tener root en él",
        hint: "Puede arrancar cualquier contenedor con cualquier parte del servidor montada, también las aplicaciones de Noust y sus datos. Instálalo solo si le darías root en el servidor a esta aplicación.",
      },
    },
    unavailable: {
      archived: {
        text: "El proyecto está archivado y ya no se mantiene",
        hint: "Usa la receta que lo sustituye, si el catálogo nombra una.",
      },
      host_ports: {
        text: "Necesita puertos que ya ocupan el servidor web y el correo del servidor",
        hint: "Ponlo en un servidor solo para él.",
      },
      licence: {
        text: "Su licencia no permite que alguien lo ponga en marcha para otros",
        hint: "Si tienes una licencia que lo permita, despliégalo desde su repositorio con 'noust create'.",
      },
      no_image: {
        text: "El proyecto no publica ninguna imagen de contenedor que se pueda fijar",
        hint: "Despliégalo desde el repositorio del proyecto con 'noust create', o añade un Dockerfile propio.",
      },
      no_sign_in: {
        text: "Su interfaz web no tiene inicio de sesión propio y guarda las claves con las que trabaja",
        hint: "Entra por un túnel SSH al servidor, como explica su proyecto, nunca por una dirección pública.",
      },
      not_an_application: {
        text: "Es un framework para construir aplicaciones, no una aplicación que instalar",
        hint: "Despliega tu propio proyecto desde su repositorio con 'noust create'.",
      },
      not_for_production: {
        text: "Su propio proyecto dice que esta edición no es para producción",
        hint: "Usa la edición que el proyecto mantiene para producción, desplegada desde su repositorio.",
      },
      platform_stack: {
        text: "Es una plataforma de decenas de servicios con su propio proxy",
        hint: "Ponla en un servidor solo para ella, con el instalador del proyecto.",
      },
      root_equivalent: {
        text: "Controla el Docker del servidor, y eso equivale a ser root en él",
        hint: "Despliégalo tú con 'noust create' si lo aceptas, en un servidor que sea tuyo.",
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
      hint: "Un servidor siempre conserva a alguien que pueda hacerlo todo. Da primero el rol de propietario a otra cuenta que pueda entrar y este cambio podrá seguir.",
    },
    name_invalid: {
      text: "Nombre de rol no válido: {name}",
      hint: "Usa de 2 a 40 letras minúsculas, cifras o '-', empezando y acabando por letra o cifra, como 'release-manager'.",
    },
    name_system: {
      text: "{name} es el nombre de un rol integrado",
      hint: "Elige otro nombre. Los roles que trae Noust se pueden editar y devolver a como vienen, pero no redefinir con su nombre.",
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
      hint: "Tiene todos los permisos que existen, para que el servidor siempre conserve a alguien que pueda hacerlo todo. Para dar todo eso a una persona, dale el rol de propietario.",
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
      hint: "Sus roles juntos tienen {governing} (quién puede ejecutar el servidor) con {running} (ejecutarlo). La separación de funciones las mantiene en manos distintas: da a la persona una segunda cuenta para la otra función.",
    },
    separation_role: {
      text: "{subject} gobernaría el acceso y ejecutaría el servidor a la vez",
      hint: "Tiene {governing} (quién puede ejecutar el servidor) junto con {running} (ejecutarlo). La separación de funciones las mantiene en manos distintas: divídelo en dos roles.",
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
  sign_in_image: {
    alt_control: {
      text: "La descripción debe ser una sola línea de texto sin formato",
      hint: "Quita los saltos de línea y los caracteres de control.",
    },
    alt_too_long: {
      text: "La descripción tiene más de {limit} caracteres",
      hint: "Acórtala: un lector de pantalla la lee entera.",
    },
    animated: {
      text: "Una imagen animada no se admite como imagen del inicio de sesión",
      hint: "Usa una imagen fija: una animación no se puede pausar, y esta pantalla se lee.",
    },
    bad_fit: {
      text: "El ajuste debe ser cover o contain",
      hint: "Usa cover para rellenar el espacio (recortando lo que no quepa) o contain para mostrarla entera.",
    },
    damaged: {
      text: "El archivo {kind} está dañado o incompleto",
      hint: "Exporta la imagen otra vez y envía el archivo nuevo.",
    },
    no_image_yet: {
      text: "No hay una imagen del inicio de sesión a la que cambiar el ajuste o la descripción",
      hint: "Envía también la imagen.",
    },
    not_an_image: {
      text: "El archivo no es una imagen PNG, JPEG ni WebP",
      hint: "El tipo se lee del propio archivo, no de su nombre: exporta la imagen como PNG, JPEG o WebP.",
    },
    not_base64: {
      text: "La imagen debe enviarse como texto base64",
      hint: "Envía los bytes del archivo codificados en base64, sin prefijo data: y sin saltos de línea.",
    },
    svg_refused: {
      text: "Un SVG no se admite como imagen del inicio de sesión",
      hint: "Un SVG es código, y esta imagen la ve cualquiera que abra la consola antes de entrar. Expórtala como PNG, JPEG o WebP.",
    },
    too_large: {
      text: "La imagen pesa más de {limit}",
      hint: "Expórtala otra vez más pequeña o con más compresión y vuelve a enviarla.",
    },
    too_many_pixels: {
      text: "La imagen mide {width} por {height} píxeles, y el máximo es {limit} por cada lado",
      hint: "Redúcela y vuelve a enviarla.",
    },
    unsupported: {
      text: "Este archivo {kind} usa una codificación que no todos los navegadores dibujan",
      hint: "Guárdalo otra vez como imagen estándar, de 8 bits y no jerárquica, y vuelve a enviarlo.",
    },
  },
  sites: {
    config_updated: {
      text: "Configuración actualizada de {domain}. Recarga {webserver} para aplicarla.",
    },
    created: {
      not_loaded: {
        text: "Sitio creado: {domain}, pero {webserver} no lo cargó: {error}",
      },
      reload: {
        text: "Sitio creado: {domain}. Recarga {webserver} para servirlo.",
      },
      with_ssl: {
        text: "Sitio creado con SSL: {domain}.",
      },
      without_tls: {
        text: "Sitio creado: {domain}. Se pidió TLS, pero no se activó.",
      },
      without_tls_because: {
        text: "Sitio creado: {domain}. Se pidió TLS, pero no se activó: {error}.",
      },
    },
    deleted: {
      text: "Sitio eliminado: {domain}",
    },
    disabled: {
      text: "Sitio desactivado: {domain}",
    },
    enabled: {
      text: "Sitio activado: {domain}",
    },
    reloaded: {
      text: "{webserver} recargado",
    },
  },
  software: {
    apache: {
      purpose: {
        text: "El servidor web Apache, para los sitios que corren detrás de él",
      },
    },
    auto_updates: {
      purpose: {
        text: "Instala por sí solo las actualizaciones de seguridad del sistema",
      },
    },
    certbot: {
      purpose: {
        text: "Obtiene y renueva los certificados HTTPS de Let's Encrypt y de otras autoridades",
      },
    },
    chrony: {
      purpose: {
        text: "Mantiene el reloj en hora por red, de lo que dependen los certificados y los registros",
      },
    },
    cloudflared: {
      purpose: {
        text: "El conector por el que pasa un túnel de Cloudflare, para llegar a este servidor sin abrir un puerto",
      },
    },
    curl: {
      never: {
        text: "Noust descarga con él las claves y los instaladores que comprueba",
      },
      purpose: {
        text: "Descarga las claves de firma y los instaladores que Noust comprueba antes de fiarse de ellos",
      },
    },
    docker: {
      purpose: {
        text: "Ejecuta las aplicaciones de contenedor y de Compose, y los motores de bases de datos que Noust corre en contenedores",
      },
    },
    fail2ban: {
      purpose: {
        text: "Bloquea las direcciones que prueban contraseñas o sondean el servidor web",
      },
    },
    firewall: {
      never: {
        text: "Noust abre y cierra puertos con él, y es lo que mantiene cerrado el servidor",
      },
      purpose: {
        text: "El cortafuegos por el que Noust abre y cierra puertos, para los puertos publicados y para las bases de datos que expone",
      },
    },
    git: {
      never: {
        text: "Todo despliegue desde un repositorio lo necesita",
      },
      purpose: {
        text: "Descarga el código de toda aplicación que se despliega desde un repositorio",
      },
    },
    gnupg: {
      never: {
        text: "Noust comprueba con él la clave de firma de cada repositorio que añade",
      },
      purpose: {
        text: "Comprueba la clave de firma de cada repositorio que Noust añade",
      },
    },
    hub: {
      text: "Esta central es un hub: no tiene software propio",
    },
    knot: {
      purpose: {
        text: "Knot DNS, el servidor de nombres de las zonas DNS propias de Noust",
      },
    },
    mongodb: {
      purpose: {
        text: "MongoDB, una base de datos de documentos para las aplicaciones",
      },
    },
    mysql: {
      purpose: {
        text: "MySQL o MariaDB, un motor de bases de datos para las aplicaciones",
      },
    },
    nginx: {
      purpose: {
        text: "El servidor web que sirve las aplicaciones y termina su HTTPS",
      },
    },
    nginx_stream_module: {
      purpose: {
        text: "Permite que nginx publique un puerto TCP o UDP, como el de una base de datos o el de un servidor de juegos",
      },
    },
    nodejs: {
      purpose: {
        text: "Compila y ejecuta aplicaciones Node.js",
      },
    },
    noust: {
      never: {
        text: "Quitarlo sacaría a Noust, y la consola que estás usando, de este servidor",
      },
      purpose: {
        text: "Noust mismo, con las bibliotecas de Python sobre las que está hecho",
      },
    },
    php: {
      purpose: {
        text: "PHP-FPM, que ejecuta las aplicaciones PHP",
      },
    },
    postgresql: {
      purpose: {
        text: "PostgreSQL, un motor de bases de datos para las aplicaciones",
      },
    },
    rclone: {
      purpose: {
        text: "Sube las copias de seguridad a destinos remotos",
      },
    },
    redis: {
      purpose: {
        text: "Redis o Valkey, un almacén de clave-valor y caché para las aplicaciones",
      },
    },
    sshd: {
      never: {
        text: "Quitarlo te dejaría sin acceso a esta máquina",
      },
      purpose: {
        text: "El servidor SSH: por donde entras tú, y una central, a esta máquina",
      },
    },
    systemd: {
      never: {
        text: "Todo lo de este servidor, Noust incluido, corre bajo él",
      },
      purpose: {
        text: "El gestor de servicios bajo el que corren todas las aplicaciones, los sitios y las unidades de Noust",
      },
    },
    waf: {
      purpose: {
        text: "ModSecurity y las reglas OWASP Core Rule Set: el cortafuegos de aplicaciones web de un sitio",
      },
    },
    refused: {
      cannot_tell: {
        text: "Noust no puede saber qué depende de {component}",
        hint: "{reason} Quitarlo sin saberlo podría romper lo que dependa de él.",
      },
      dependents: {
        text: "{component} no se puede quitar mientras algo dependa de él: {dependents}",
        hint: "Deja de usarlo primero (quita o mueve lo que sale en la lista) y luego quítalo.",
      },
      never: {
        text: "{component} no se quita nunca",
      },
      not_installed: {
        text: "{component} no está instalado",
      },
      takes_in_use: {
        text: "Quitar {component} quitaría también {other}, que está en uso: {dependents}",
        hint: "No se ha quitado nada. Deja de usar {other} primero.",
      },
      takes_never: {
        text: "Quitar {component} quitaría también {other}, que no se quita nunca",
        hint: "No se ha quitado nada.",
      },
      unknown_origin: {
        text: "Noust no instaló {component}",
        hint: "Otras cosas del sistema pueden usarlo. Repite la petición confirmándolo para quitarlo igualmente.",
      },
    },
  },
  sources: {
    central_only: {
      text: "Solo la central que gestiona este servidor puede cederle acceso a GitHub",
      hint: "La central hace y renueva la cesión por su cuenta; no hay nada que hacer en este servidor. Para clonar con una credencial propia de este servidor, dale a la aplicación una clave de despliegue.",
    },
    delivery_not_retryable: {
      text: "Ese evento está en estado {state}, y solo se puede entregar otra vez uno que se dio por perdido",
      hint: "Un servidor que ya tomó el evento no lo recibe dos veces; uno que aún espera lo vuelve a intentar solo.",
    },
    forward_gave_up: {
      text: "{node} no aceptó el {kind} de {repository} en {minutes} minutos, y la central lo dejó",
      hint: "No se desplegó nada en {node} por ello. Cuando vuelva a responder, actualiza allí la aplicación, o haz push otra vez.",
    },
    forward_refused: {
      text: "{node} no ha querido aceptar el {kind} de {repository}: {reason}",
      hint: "Las palabras del propio servidor van al lado. No se desplegó nada en {node} por ello.",
    },
    github_refused: {
      text: "GitHub no ha querido dar un token para la instalación {installation}",
      hint: "Las palabras de GitHub van al lado. Si la App se eliminó o cambiaron sus permisos, acepta los permisos nuevos en los ajustes de la App en GitHub, o instálala otra vez.",
    },
    lease_expired: {
      text: "El acceso a GitHub que {central} cedió a este servidor para {repository} terminó el {ended_at} y no se ha renovado",
      hint: "{central} llamó por última vez el {last_call} y no cedió uno nuevo: puede que no llegue a GitHub, o que su GitHub App ya no cubra {repository}. Hasta que lo haga, todo lo que arranque en este servidor y lea {repository} falla. Una clave de despliegue no depende de la central; mira: {command}",
    },
    lease_expired_quiet: {
      text: "El acceso a GitHub que {central} cedió a este servidor para {repository} terminó el {ended_at}, y {central} no ha llamado desde el {last_call}",
      hint: "Todo lo que arranque en este servidor y lea {repository} (una actualización programada, una preview) falla hasta que {central} vuelva a llamar y ceda uno nuevo: comprueba que la central está en marcha y llega a este servidor. Una clave de despliegue no depende de la central; mira: {command}",
    },
    no_app: {
      text: "Esta central no tiene una GitHub App con la que ceder acceso",
      hint: "Créala en la consola (Integraciones, GitHub) o con: noust github setup",
    },
    no_installation: {
      text: "Ninguna instalación de la GitHub App llega a {repository}",
      hint: "Instala la App en la cuenta {owner} y deja que lea {repository}: {url}",
    },
    node_ceiling: {
      text: "{node} deja a esta central solo leer, así que no se le puede ceder acceso a GitHub",
      hint: "Ceder acceso necesita el techo deploy o superior. En {node}, como root: {command}",
    },
    node_independent: {
      text: "{node} es independiente, así que esta central no le pasa nada de GitHub",
      hint: "Haz que vuelva a seguir la flota y le llegarán el acceso y los eventos de sus repositorios. Hasta entonces despliega con sus propias credenciales.",
    },
    node_refused: {
      text: "{node} no ha querido aceptar el acceso a GitHub: {reason}",
      hint: "Las palabras del propio servidor van al lado. Se intenta otra vez en unos minutos.",
    },
    node_unreachable: {
      text: "No se ha podido llegar a {node} para cederle acceso a GitHub",
      hint: "Se intenta otra vez en menos de un minuto, y el acceso que tiene termina solo. Compruébalo con: {command}",
    },
    node_unsupported: {
      text: "{node} ejecuta un Noust que no puede guardar el acceso a GitHub que esta central cede",
      hint: "Actualiza Noust en {node} a la 4.1 o posterior; el acceso se cede en menos de un minuto después.",
    },
    not_reachable: {
      text: "La GitHub App no puede leer {repository}",
      hint: "Añade {repository} a los repositorios a los que llega la instalación de la App: {url}",
    },
    webhook_unreachable: {
      text: "GitHub no llega a esta central, así que un push a un repositorio que sus servidores despliegan no despliega nada",
      hint: "Los servidores que gestiona no tienen una dirección a la que GitHub pueda llamar: el webhook de la App es de esta central. En un servidor con servidor web, dale un nombre propio: {command}. Detrás de un proxy tuyo, di dónde responde: {config_command}",
    },
  },
  storage: {
    effect: {
      docker_build_cache: {
        text: "Borra la caché de compilación de más de una semana. La próxima compilación será más lenta por ello.",
      },
      docker_dangling_images: {
        text: "Borra las imágenes que no tienen nombre ni contenedor. Las imágenes con nombre se conservan, igual que todas las noust_previous.",
      },
      docker_image: {
        text: "Borra la imagen {image}, sin --force: Docker se niega si un contenedor la usa.",
      },
      journal: {
        text: "Rota el diario y borra los archivos archivados que pasen del límite. El archivo activo no se toca.",
      },
      pkg_cache: {
        text: "Borra los archivos de paquetes descargados.",
      },
      releases: {
        text: "Borra las versiones que cada aplicación guarda por encima de su propio límite. La versión activa y a la que llevaría una vuelta atrás se conservan siempre.",
      },
    },
    run: {
      below_threshold: {
        text: "{mount} está al {percent}%, por debajo del {threshold}% que espera la política",
      },
      policy_off: {
        text: "la política de limpieza está desactivada",
      },
    },
  },
  streams: {
    firewall: {
      allowed_one: {
        text: "{backend} ahora permite 1 regla para este puerto",
      },
      allowed_several: {
        text: "{backend} ahora permite {count} reglas para este puerto",
      },
      unchanged: {
        text: "No se ha cambiado el cortafuegos",
      },
      unchanged_because: {
        text: "No se ha cambiado el cortafuegos: {note}",
      },
    },
    plan: {
      nothing_listens: {
        text: "Todavía no escucha nada en {target}: los visitantes serán rechazados hasta que el servicio esté en marcha",
      },
      open_to_everyone: {
        text: "{protocol} {port} estará abierto a todo el mundo",
      },
      open_to_everyone_without_login: {
        text: "{protocol} {port} estará abierto a todo el mundo; un servicio sin autenticación propia debería limitarse a direcciones conocidas",
      },
    },
    support: {
      config_unparsable: {
        text: "La configuración de nginx no se puede leer: {reason}",
      },
      config_unreadable: {
        text: "nginx no puede leer su propia configuración",
      },
      foreign_block: {
        text: "nginx.conf ya tiene un bloque stream que no incluye los puertos de Noust",
        hint: "Añade esta línea dentro y vuelve a intentarlo: {line_command}",
      },
      module_not_loaded: {
        text: "El módulo stream está compilado como módulo y nada lo carga",
        hint: "Instala el módulo con: {command}",
      },
      module_not_loaded_suse: {
        text: "El módulo stream está compilado como módulo y nada lo carga",
        hint: "Añade {line_command} al principio de nginx.conf.",
      },
      no_nginx: {
        text: "nginx no está instalado",
        hint: "Instálalo con: {command}",
      },
      no_stream_block: {
        text: "nginx.conf no tiene un bloque stream para los puertos de Noust",
        hint: "Añade esto en el nivel superior de nginx.conf (junto a http) y vuelve a intentarlo: {block_command}",
      },
      no_stream_module: {
        text: "Este nginx se compiló sin el módulo stream",
        hint: "Instala el módulo con: {command}",
      },
      no_stream_module_suse: {
        text: "Este nginx se compiló sin el módulo stream",
        hint: "openSUSE incluye nginx con stream; instala el paquete nginx de la distribución o de nginx.org.",
      },
    },
  },
  swap: {
    cloud_init: {
      text: "cloud-init está configurado con una sección swap:, así que vuelve a crear el swap en cada arranque. Quita esa sección o deja el swap a cloud-init.",
    },
    unsupported_container: {
      text: "Esto es un contenedor ({container}), donde el swap es del anfitrión: swapon se rechaza dentro de él.",
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
      hint: "Ningún marco de cumplimiento lo pide: se fijó en Ajustes > Cumplimiento o con la línea de comandos. Para permitirlo: {command}",
    },
    server_framework: {
      text: "El marco de cumplimiento en vigor ({frameworks}) mantiene desactivado el terminal del propio servidor (el control {control} está en refused)",
      hint: "Una shell de root en el navegador es lo más cercano a root que tiene la consola. Para permitirlo de todos modos, lo cual queda registrado: {command}. O desactiva el marco: {turn_off_command}",
    },
    server_off: {
      text: "El terminal del propio servidor está desactivado (terminal.server.enabled)",
      hint: "Es una shell de root, así que sigue desactivado hasta que un administrador lo active: {command}",
    },
  },
  tunnels: {
    cloudflared_missing: {
      text: "cloudflared no está instalado",
      hint: "Noust lo instala desde el repositorio de paquetes de Cloudflare, {url}, con la clave con la que Cloudflare lo firma: ejecuta 'noust tunnel install-cloudflared' y después este comando otra vez.",
    },
    no_account: {
      text: "No hay ninguna cuenta de Cloudflare con la que gestionar un túnel",
      hint: "Añade una con Account > Cloudflare Tunnel:Edit: noust dns provider add NAME --kind cloudflare --token-stdin",
    },
    store_sealed: {
      text: "El almacén de secretos está sellado",
      hint: "El conector de un túnel lee su token al arrancar, sin nadie que escriba una frase de paso. Usa los túneles en un servidor, no en una central sellada.",
    },
  },
  webserver_move: {
    approximate: {
      text: "{directive} (línea {line}) pasa como {result}",
    },
    compose_only: {
      text: "{domain} es una aplicación de Docker Compose, y esas solo las sirve nginx",
      hint: "Déjala detrás de nginx.",
    },
    confirm_handover: {
      text: "{target} tomará los puertos 80 y 443 de {source} para {count} sitio(s)",
      hint: "Todos los sitios quedan sin servicio los segundos que dura el cambio, y todo se deja como estaba si alguno no responde. Para seguir: confirma en la API, --yes en la línea de órdenes.",
    },
    confirm_live: {
      text: "{count} sitio(s) pasarán de {source} a {target}, que está sirviendo",
      hint: "{target} prueba cada sitio antes de activarlo, y se deja como estaba si no responde. Para seguir: confirma en la API, --yes en la línea de órdenes.",
    },
    console_site: {
      text: "{domain} es el nombre público de la consola, que Noust solo monta detrás de nginx",
      hint: "Quítalo antes con {command}, o deja el servidor web como está.",
    },
    included: {
      text: "La línea {line} incluye {path}, que no se traslada",
      hint: "La traducción no lee los ficheros que un sitio incluye. Copia a mano lo que contiene ese fichero al sitio nuevo, o deja la aplicación donde está.",
    },
    left_behind: {
      text: "{name} es un sitio de {source} que Noust no puede mover",
      hint: "{source} dejaría de servirlo. Escribe tú su equivalente para {target} en {target_path}, o deja el servidor web como está.",
    },
    losses_to_accept: {
      text: "Los sitios nuevos no harán {count} cosa(s) que hacían los antiguos",
      hint: "Léelas en el plan. Para seguir de todos modos, acéptalas: accept_losses en la API, --accept-losses en la línea de órdenes.",
    },
    no_site: {
      text: "{domain} no tiene fichero de sitio en {source}",
      hint: "No hay nada que traducir. Despliega la aplicación otra vez para que tenga un sitio y planifica el cambio de nuevo.",
    },
    not_restored: {
      text: "No se pudo dejar todo como estaba",
      hint: "Lo que sigue dice qué quedó y dónde está. El registro de auditoría dice qué se movió.",
    },
    not_serving: {
      text: "{target} no arrancó",
      hint: "Su propio mensaje se muestra tal cual. {source} volvió a arrancar y los sitios están como estaban.",
    },
    operator_site: {
      text: "El sitio de {domain} es tuyo, no de Noust ({path})",
      hint: "Noust nunca reescribe un sitio que escribiste tú. Escribe tú su equivalente para {target} en {target_path} y planifica el cambio de nuevo: los dos ficheros se dejan como están y solo se mueve la aplicación.",
    },
    partial_handover: {
      text: "{target} no está sirviendo: mover {count} de los {total} sitios dejaría a los demás sin servidor web",
      hint: "Los puertos 80 y 443 son de un solo servidor web a la vez. Mueve todos los sitios juntos: {command}",
    },
    protection: {
      text: "{directive} (línea {line}) protege el sitio, y {target} no tiene nada equivalente",
      hint: "El sitio nuevo no quedaría protegido así. Pon antes otra protección delante, o deja la aplicación donde está.",
    },
    rejected: {
      text: "{target} no aceptó el sitio traducido de {domain}",
      hint: "Su propio mensaje se muestra tal cual. No se cambió nada: corrige lo que nombra en el sitio de {source}, o deja la aplicación donde está.",
    },
    status_site: {
      text: "{domain} es el nombre público de la página de estado, que Noust solo monta detrás de nginx",
      hint: "Quítalo antes con {command}, o deja el servidor web como está.",
    },
    target_missing: {
      text: "{target} no está instalado en este servidor",
      hint: "Instálalo primero y planifica el cambio de nuevo: {command}",
    },
    tuning: {
      text: "{directive} (línea {line}) ajusta {source} y se deja fuera",
    },
    unanswered: {
      text: "{domain} no respondió a través de {target}",
      hint: "Lo que se probó y lo que dijo la unidad de la aplicación se muestra tal cual. Todo se dejó como estaba: {source} vuelve a servir los sitios.",
    },
    unknown: {
      text: "{domain} no es ni una aplicación ni un sitio detrás de {source}",
      hint: "Mira qué hay detrás de cada servidor web con: {command}",
    },
    unreadable: {
      text: "{source} no leería este sitio: {problem} (línea {line})",
      hint: "Corrige el sitio donde está, con el editor de su página, y planifica el cambio de nuevo.",
    },
    unsupported: {
      text: "{directive} (línea {line}) no tiene equivalente en {target}",
      hint: "El sitio nuevo no hará lo que hacía esa línea. Revisa el sitio después del cambio, o deja la aplicación donde está.",
    },
    variable: {
      text: "{directive} (línea {line}) lee {variable}, que {target} no puede dar",
      hint: "El sitio nuevo enviaría otra cosa en su lugar. Revísalo después del cambio, o deja la aplicación donde está.",
    },
    zero_downtime: {
      text: "{domain} funciona en modo sin cortes, que solo admite nginx",
      hint: "Apaga antes el modo y planifica el cambio de nuevo: {command}",
    },
  },
};
