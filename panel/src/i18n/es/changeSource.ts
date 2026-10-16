/**
 * Cambiar de dónde viene el código de una aplicación (sus ajustes de Origen): un repositorio que
 * se mudó, o un equipo que cambió de plataforma. Lo que git responde cuando la nueva dirección no
 * se puede leer se muestra tal cual y no está aquí.
 */
export const changeSource = {
  title: "De dónde viene el código",
  description: "Apunta esta aplicación a otro repositorio o directorio sin crearla de nuevo. Sus versiones, variables, dominio e historial se quedan.",
  current: "Se despliega desde",
  branch: "Rama",
  none: "Nada registrado",
  open: "Cambiar el origen",
  fromTerminal: "Desde una terminal",
  changed: "{domain} se desplegará desde {source}",
  unsupportedInPlace: "El origen se puede cambiar con la disposición por versiones, y esta aplicación compila en su sitio. Pásala antes a versiones.",
  unsupportedType: "Una aplicación {type} se compila con sus propios ficheros o imagen, así que aquí no hay un origen que cambiar.",
  unsupportedPreview: "Una vista previa se compila desde su pull request. Cambia el origen de la aplicación a la que pertenece.",
  unsupportedPush: "Esta aplicación se despliega con git push. Desactívalo antes en Desplegar al hacer push y luego cambia su origen.",
  dialog: {
    title: "Cambiar el origen de {domain}",
    description: "Antes se comprueba la nueva dirección: tiene que poder leerse y tener la rama. No se compila nada hasta que actualices.",
    source: "Nuevo origen",
    sourceDescription: "Una dirección git, la URL de un archivo o un directorio de este servidor. Sin contraseña ni token dentro.",
    required: "Indica el nuevo origen.",
    branch: "Rama",
    branchDescription: "Vacío mantiene {branch} si es el mismo repositorio, y toma la rama por defecto del remoto si es otro.",
    ask: "Preguntar antes al remoto",
    askDescription: "Desactívalo solo para un remoto al que este servidor aún no llega. Entonces la rama es obligatoria.",
    check: "Comprobar el nuevo origen",
    apply: "Cambiar el origen",
    cancel: "Cancelar",
    notChecked: "No se pudo comprobar el nuevo origen",
    notChanged: "No se cambió el origen de {domain}",
  },
  plan: {
    from: "Ahora",
    to: "Nuevo origen",
    branch: "Rama",
    head: "Cabeza de la rama",
    warnings: "Lo que esto no traslada",
    unchanged: "Esta aplicación ya se despliega desde ahí.",
  },
  done: {
    title: "{domain} se despliega desde el nuevo origen",
    body: "Lo que sirve ahora sigue sirviendo. La próxima actualización compila el nuevo origen.",
    build: "Actualizar ahora",
    queued: "Actualización de {domain} en cola",
    queueFailed: "No se pudo poner en cola la actualización de {domain}",
  },
} as const;
