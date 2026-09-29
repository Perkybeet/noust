import type { servers as en } from "../en/servers";
import type { Catalog } from "../types";

export const servers: Catalog<typeof en> = {
  nav: {
    fleet: { label: "Flota", keywords: "servidores nodos central vps máquinas", goTo: "Ir a la flota" },
    settingsTab: {
      label: "Servidores",
      keywords: "flota nodos central ssh túnel añadir servidor código de unión",
      command: "Ajustes de servidores",
    },
  },

  thisServer: "Este servidor",
  hubBadge: "Hub",

  reachability: {
    locked: "Bloqueado",
  },

  // -------------------------------------------------------------------------------------
  // La página Flota

  fleet: {
    title: "Flota",
    description: "Todos los servidores que gestiona esta central, uno junto a otro: si responden, qué ejecutan y qué necesita tu atención.",
    addServer: "Añadir un servidor",
    manageServers: "Gestionar servidores",
    serversTitle: "Servidores",
    serversDescription: "Las lecturas se actualizan cada 15 segundos; las aplicaciones, los certificados y las unidades, cada minuto.",
    tableCaption: "Servidores de esta flota",
    loadingLabel: "la flota",
    column: {
      server: "Servidor",
      reachability: "Alcance",
      version: "Versión",
      cpu: "CPU",
      memory: "Memoria",
      disk: "Disco",
      apps: "Aplicaciones",
      units: "Unidades",
      certificates: "Certificados",
      lastSeen: "Visto por última vez",
    },
    versionMismatch: "Otra versión",
    versionMismatchLabel: "Ejecuta {version}; esta central ejecuta {central}",
    appsRunning: { one: "{count} en marcha", other: "{count} en marcha" },
    appsFailed: { one: "{count} fallida", other: "{count} fallidas" },
    unitsFailed: { one: "{count} fallida", other: "{count} fallidas" },
    unitsNoneFailed: "Ninguna fallida",
    certsExpiring: { one: "{count} a punto de caducar", other: "{count} a punto de caducar" },
    certsNoneExpiring: "Ninguno a punto de caducar",
    notRead: "Sin leer",
    now: "Ahora",
    never: "Nunca",
    hubNoApps: "No despliega nada",
    open: "Abrir {name}",
    empty: {
      title: "Aún no hay servidores en esta flota",
      description:
        "Una flota son varios servidores Noust gestionados desde una sola consola, la de esta central. La central llega a cada servidor por un túnel SSH hasta su consola, con una clave que solo puede reenviar ese puerto, y lo maneja a través de su propia API.",
      action: "Añadir un servidor",
    },
    hub: {
      title: "Esta central no despliega nada",
      description:
        "Es un hub: las aplicaciones, los sitios, los certificados, las bases de datos y las copias de seguridad viven en los servidores que gestiona. Elige un servidor abajo para abrirlos allí.",
      noServers: "Añade primero un servidor: todas las aplicaciones de un hub viven en sus servidores.",
    },
    attention: {
      title: "Requiere atención",
      description: "Los problemas de todos los servidores, los más graves primero. Cada uno se abre en el servidor al que pertenece.",
      allClear: {
        one: "Nada requiere atención en el único servidor.",
        other: "Nada requiere atención en ninguno de los {count} servidores.",
      },
      onServer: "En {server}",
      unreachable: "La central no puede llegar a este servidor",
      refused: "Este servidor rechazó el token de la central",
      unreachableFix:
        "Comprueba que el servidor está en marcha y responde por SSH en {address}, y que su clave de host no ha cambiado. Lo que dijo ssh:",
      refusedFix:
        "El servidor ya no acepta el token de flota de esta central. Vuelve a ejecutar noust fleet authorize en él y, después, quítalo y añádelo aquí con el nuevo código de unión.",
      machineFailed: "No se pudo leer la máquina de este servidor",
      unchecked: "No se pudo comprobar {source}:",
      sources: {
        apps: "sus aplicaciones",
        deploys: "sus despliegues",
        certificates: "sus certificados",
        units: "sus unidades",
      },
      testAgain: "Probar de nuevo",
    },
  },

  // -------------------------------------------------------------------------------------
  // Ajustes > Servidores

  settings: {
    documentTitle: "Ajustes de servidores",
    title: "Servidores",
    description:
      "Los servidores que gestiona esta central. Llega a cada uno por un túnel SSH hasta su consola, con una clave que solo puede reenviar ese puerto y nunca ejecutar una orden.",
    loadingLabel: "los servidores",
    tableCaption: "Servidores que gestiona esta central",
    column: {
      name: "Nombre",
      address: "Dirección SSH",
      status: "Estado",
      version: "Versión",
      lastSeen: "Visto por última vez",
    },
    addServer: "Añadir un servidor",
    test: "Probar",
    testLabel: "Probar {name}",
    testedToast: "{name} respondió en {latency} ms",
    testedDescription: "Ejecuta Noust {version}.",
    testFailedToast: "{name} no respondió",
    open: "Abrir",
    openLabel: "Abrir {name}",
    remove: "Quitar",
    removeLabel: "Quitar {name}",
    never: "Nunca",
    empty: {
      title: "Aún no hay servidores",
      description:
        "Añade un servidor para gestionarlo desde esta consola. Autorizas a la central en el propio servidor, donde ya eres root; la central nunca obtiene una shell allí.",
    },
    twoFactor: {
      title: "Activa primero la verificación en dos pasos",
      description:
        "Esta central se niega a añadir su primer servidor sin ella: quien inicia sesión aquí llega a todos los servidores que gestiona.",
      link: "Configurar la verificación en dos pasos",
    },
    fromTerminal: "Desde una terminal",
    removeDialog: {
      title: "¿Quitar {name}?",
      description:
        "La central cierra su túnel a {name} y olvida su clave, su token y su clave de host fijada. Si {name} responde, la central revoca antes su token allí, para que la clave que queda no abra nada.",
      unreachable: "Si no se puede llegar a {name}, ejecuta esto en él, como root, para quitar la clave de la central y revocar su token:",
      unreachableUnknown: "Si no se puede llegar a {name}, Noust te dirá qué ejecutar en él para terminar.",
      action: "Quitar servidor",
      removedToast: "Se quitó {name}",
      resultTitle: "Se quitó {name}",
      resultDescription: "Lo que hizo la central, en sus propias palabras:",
    },
  },

  // -------------------------------------------------------------------------------------
  // Añadir un servidor

  add: {
    title: "Añadir un servidor",
    stepOf: "Paso {step} de {total}",
    steps: {
      authorize: "Autorizar",
      join: "Unir",
      result: "Resultado",
    },
    progressLabel: "Progreso",
    stepDone: "{name}, hecho",
    stepCurrent: "{name}, paso actual",
    stepTodo: "{name}, pendiente",
    cancel: "Cancelar",
    back: "Atrás",
    next: "Ya lo ejecuté: siguiente",
    done: "Hecho",
    name: {
      label: "Nombre",
      description: "Cómo llamará esta consola al servidor: de 1 a 32 letras minúsculas, dígitos y guiones, como web-2.",
      invalid: "Usa de 1 a 32 letras minúsculas, dígitos y guiones, empezando y terminando por una letra o un dígito.",
      showCommand: "Mostrar la orden",
      keyFailed: "No se pudo preparar la clave de la central",
    },
    authorize: {
      intro: "En {name}, como root, ejecuta esta orden. Imprime un código de unión en una línea: cópialo para el paso siguiente.",
      commandLabel: "Orden para ejecutar en {name}",
      copy: "Copiar orden",
      safeTitle: "Por qué es seguro",
      safeBody:
        "La clave de esta orden solo puede reenviar el puerto de la consola de {name} a esta central. No puede abrir una shell ni ejecutar nada en {name}, y {name} puede revocarla cuando quiera con noust fleet deauthorize. La central solo habla con {name} a través de su API, con un token que {name} emite y puede revocar.",
    },
    join: {
      intro: "Pega lo que imprimió la orden en {name} y dónde llega la central a su SSH.",
      codeLabel: "Código de unión",
      codeDescription: "Lleva un token para la consola de {name}: trátalo como una contraseña. Se envía una vez y no se vuelve a mostrar.",
      addressLabel: "Dirección SSH",
      addressDescription:
        "Como usuario@host o usuario@host:puerto, por ejemplo root@web2.example.com. El usuario y el puerto son por defecto los del código de unión.",
      addressInvalid: "Escríbela como host, usuario@host o usuario@host:puerto.",
      submit: "Añadir servidor",
      elevationNote: "Añadir un servidor te pide confirmar que eres tú.",
    },
    result: {
      addedTitle: "{name} ya forma parte de la flota",
      addedBody: "La central fijó su clave de host, abrió el túnel y comprobó el token.",
      status: "Estado",
      version: "Versión",
      address: "Dirección SSH",
      notReported: "Aún no la ha comunicado",
      open: "Abrir {name}",
      failedTitle: "No se pudo añadir {name}",
      twoFactorLink: "Configurar la verificación en dos pasos",
      tryAgain: "Volver al código de unión",
    },
    addedToast: "Se añadió {name}",
  },

  // -------------------------------------------------------------------------------------
  // Los secretos sellados de la central

  lock: {
    title: "Esta central está bloqueada",
    description:
      "Sus secretos están sellados en reposo: las claves y los tokens que llegan a tus servidores están cifrados con una frase de paso que no se guarda en ningún sitio. Hasta que los desbloquees, nada llega a los servidores.",
    passphraseLabel: "Frase de paso",
    unlock: "Desbloquear",
    wrongPassphrase: "Esa frase de paso no abre los secretos de esta central. Revísala y vuelve a intentarlo.",
    failedTitle: "No se pudo desbloquear la central",
    continueLocked: "Continuar sin desbloquear",
    continueNote: "La consola sigue funcionando en esta máquina; sus servidores quedan fuera de alcance hasta que la desbloquees.",
    lostPassphrase: "Nadie puede recuperar una frase de paso perdida. Sin ella, hay que volver a autorizar todos los servidores.",
    unlockedToast: "Desbloqueada: los servidores vuelven a estar al alcance",
    fromTerminal: "O desde una terminal en la central",
    bannerTitle: "Esta central está bloqueada",
    bannerDescription: "Sus servidores están fuera de alcance hasta que desbloquees sus secretos sellados.",
    bannerAction: "Desbloquear",
    dialogTitle: "Desbloquear esta central",
  },

  // -------------------------------------------------------------------------------------
  // Un hub

  hub: {
    refusedTitle: "No disponible en un hub",
    refusedHint: "Esta central es un hub: no despliega nada. Abre un servidor de la flota y hazlo allí.",
  },
};
