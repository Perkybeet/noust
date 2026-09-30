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

  reachability: {
    locked: "Bloqueado",
  },

  fleet: {
    notRead: "Sin leer",
    hub: {
      title: "Esta central no despliega nada por sí misma",
      description:
        "Es un hub: las aplicaciones, los sitios, los certificados, las bases de datos y las copias están en los servidores que gestiona. Abre uno desde su fila, o elígelo arriba.",
      noServers: "Añade primero un servidor: todas las aplicaciones de un hub están en sus servidores.",
    },
  },

  settings: {
    documentTitle: "Ajustes de servidores",
    title: "Servidores",
    description:
      "Los servidores que gestiona esta central. Llega a cada uno por un túnel SSH hasta su consola, con una clave que solo puede reenviar ese puerto y nunca ejecutar una orden.",
    loadingLabel: "los servidores",
    tableCaption: "Servidores que gestiona esta central",
    column: {
      name: "Nombre",
      address: "Cuenta y dirección del túnel",
      status: "Estado",
      version: "Versión",
      access: "Esta central puede",
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
    description: "Esta central nunca inicia sesión en él: el servidor autoriza a esta central, una vez, desde su propia terminal.",
    steps: {
      authorize: "Autorizar",
      join: "Unir",
      result: "Resultado",
    },
    cancel: "Cancelar",
    back: "Atrás",
    next: "Ya lo ejecuté: continuar",
    done: "Hecho",
    name: {
      label: "Nombre",
      description: "Cómo llamará esta consola al servidor: de 1 a 32 letras minúsculas, dígitos y guiones, como web-2.",
      invalid: "Usa de 1 a 32 letras minúsculas, dígitos y guiones, empezando y terminando por una letra o un dígito.",
      showCommand: "Mostrar la orden",
      again: "Mostrarla de nuevo",
      keyFailed: "No se pudo preparar la clave de la central",
    },
    access: {
      label: "Esta central puede",
      description: "Lo máximo que esta central puede hacer en él. Lo aplica el servidor, pida lo que pida esta central.",
      adminHint: "Desplegar, configurar y borrar, como en el propio servidor",
      deployHint: "Operar, actualizar y volver atrás sus aplicaciones",
      readHint: "Verlo todo, no cambiar nada",
    },
    authorize: {
      whereTitle: "Ejecútala en el otro servidor: el que estás añadiendo",
      where: "No aquí en {central}. Abre una terminal en el servidor que añades, como root, y ejecuta allí la orden de abajo.",
      intro: "En {name}, como root, ejecuta esta orden. Imprime un código de unión en una línea: cópialo para el paso siguiente.",
      commandLabel: "Orden para ejecutar en {name}",
      copy: "Copiar la orden",
      missing: "Primero ponle nombre al servidor y muestra su orden.",
      safeTitle: "Por qué es seguro",
      safeBody:
        "La clave de esta orden solo puede reenviar el puerto de la consola de {name} a esta central, desde una cuenta sin shell (noust-tunnel). No puede ejecutar nada en {name}, y {name} puede revocarla cuando quiera con noust fleet deauthorize. La central habla con {name} solo a través de su API, con un token que {name} emite y puede revocar.",
    },
    join: {
      intro: "Pega lo que imprimió la orden en {name} y dónde llega la central a su SSH.",
      codeLabel: "Código de unión",
      codeDescription:
        "Empieza por noust-join:v1: y lleva un token para la consola de {name}: trátalo como una contraseña. Se envía una vez y no se vuelve a mostrar.",
      problem: {
        empty: "Pega el código de unión que imprimió la orden en el servidor.",
        apiToken:
          "Eso es un token de API, no un código de unión. El código de unión es la línea que imprimió noust fleet authorize en el servidor y empieza por noust-join:v1:.",
        consoleToken:
          "Eso parece un token de acceso a la consola (noust_…), no un código de unión. El código de unión es la línea que imprimió noust fleet authorize en el servidor y empieza por noust-join:v1:.",
        newer: "Este código de unión es de un Noust más nuevo que esta central. Actualiza Noust en esta central y vuelve a pegarlo.",
        wrongPrefix: "Un código de unión empieza por noust-join:v1:. Copia la línea entera que imprimió noust fleet authorize en el servidor.",
        unreadable: "Empieza como un código de unión pero no se lee como tal: vuelve a copiar la línea entera, sin cortarla.",
      },
      summaryTitle: "Código de unión leído",
      summary: "Para {node}: su cuenta de túnel {user} en el puerto SSH {port}, su consola en el puerto {console}, Noust {version}.",
      otherCentral: "Este código se hizo para la central {central}, no para esta ({name}). El servidor lo rechazaría.",
      otherKey: "Este código se hizo para otra clave distinta de la que mostró esta central en el primer paso. Vuelve a ejecutar la orden del primer paso.",
      rootAccount:
        "Este servidor deja entrar a la central como root. Una cuenta de túnel propia (noust-tunnel, la predeterminada) es más segura: vuelve a ejecutar la orden sin --ssh-user root.",
      addressLabel: "Dirección SSH",
      addressDescription:
        "El nombre o la dirección del servidor. La cuenta y el puerto salen del código de unión; añádelos como usuario@host:puerto solo para cambiarlos.",
      addressDescriptionFrom: "El nombre o la dirección del servidor. La central se conecta como {user} en el puerto {port}, según el código de unión.",
      addressInvalid: "Escríbela como host, usuario@host o usuario@host:puerto.",
      submit: "Añadir servidor",
      elevationNote: "Añadir un servidor te pide confirmar que eres tú.",
    },
    result: {
      addedTitle: "{name} ya es parte de la flota",
      addedBody: "La central fijó su clave de host, abrió el túnel y comprobó el token.",
      status: "Estado",
      version: "Versión",
      tunnel: "Cuenta y dirección del túnel",
      access: "Esta central puede",
      accessUnknown: "Aún sin publicar",
      notReported: "Aún sin informar",
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

  central: {
    documentTitle: "Ajustes de la central",
    fromTerminal: "Desde una terminal en la central",
    identity: {
      title: "Esta central",
      description: "El Noust que gestiona la flota, y lo que hace por sí mismo.",
      name: "Nombre",
      role: "Rol",
      roleServer: "Gestiona servidores y despliega aplicaciones por sí misma",
      roleHub: "Un hub: gestiona servidores, no despliega nada por sí mismo",
      servers: "Servidores",
      serverCount: { one: "{count} servidor", other: "{count} servidores" },
    },
    seal: {
      title: "Secretos sellados",
      description: "Las claves y los tokens que llegan a tus servidores, cifrados con una frase de paso que no se guarda en ningún sitio.",
      locked: "Bloqueada",
      unlocked: "Desbloqueada",
      notSealed: "Sin sellar",
      unlockedBody:
        "Sellados y desbloqueados desde el último arranque de esta central: sus servidores están a su alcance. Tras reiniciarse pide de nuevo la frase de paso.",
      notSealedBody:
        "Las claves y los tokens solo los protegen los permisos de archivo de la máquina. Séllalos para que no se puedan leer si alguien copia el disco de esta máquina.",
    },
  },

  // -------------------------------------------------------------------------------------
  // Un hub

  hub: {
    refusedTitle: "No disponible en un hub",
    refusedHint: "Esta central es un hub: no despliega nada. Abre un servidor de la flota y hazlo allí.",
  },
};
