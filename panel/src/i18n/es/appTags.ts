import type { appTags as en } from "../en/appTags";
import type { Catalog } from "../types";

export const appTags: Catalog<typeof en> = {
  row: {
    label: "Etiquetas de la app",
    none: "Ninguno",
    hint: "Agrupa apps para filtrar la lista y actualizarlas juntas.",
    edit: "Editar etiquetas",
  },
  dialog: {
    title: "Etiquetas de {domain}",
    description: "Nombres que agrupan apps, como web o cliente-acme. Una app lleva hasta {max}.",
    label: "Etiquetas",
    fieldDescription: "Separados por comas o espacios. De 1 a 32 letras, dígitos, puntos, guiones bajos o guiones, empezando y terminando por una letra o un dígito.",
    placeholder: "web, cliente-acme",
    submit: "Guardar etiquetas",
    saved: "Etiquetas de {domain} guardadas",
    tooMany: "Como máximo {max} etiquetas.",
    failed: "No se guardaron las etiquetas de {domain}",
    invalid: {
      one:
        "No es una etiqueta: {list}. Una etiqueta tiene de 1 a 32 letras, dígitos, puntos, guiones bajos o guiones, y empieza y termina por una letra o un dígito.",
      other:
        "No son etiquetas: {list}. Una etiqueta tiene de 1 a 32 letras, dígitos, puntos, guiones bajos o guiones, y empieza y termina por una letra o un dígito.",
    },
  },
  filter: {
    label: "Etiqueta",
    every: "Todas las etiquetas",
  },
  /** Las bases de datos y los servicios llevan tags también: las mismas reglas, editados desde sus listas. */
  resource: {
    chips: "Etiquetas",
    title: "Etiquetas de {noun} {name}",
    description: "Nombres que agrupan cosas, como web o client-acme. Una lleva hasta {max}. Guardarlos no reinicia nada.",
    saved: "Etiquetas de {name} guardadas",
    failed: "No se guardaron las etiquetas de {name}",
  },
  restart: {
    action: "Reiniciar con etiqueta",
    title: "¿Reiniciar todo lo que lleva la etiqueta {tag}?",
    description: {
      one: "{count} app lleva {tag}, y también pueden llevarlo bases de datos y servicios del servidor. Cada servicio se reinicia, uno a uno, y se interrumpe mientras reinicia. Una base de datos se deja: reinicia con su motor.",
      other: "{count} apps llevan {tag}, y también pueden llevarlo bases de datos y servicios del servidor. Cada servicio se reinicia, uno a uno, y se interrumpe mientras reinicia. Una base de datos se deja: reinicia con su motor.",
    },
    confirm: "Reiniciarlos",
    done: {
      one: "Se reinició {count} cosa con la etiqueta {tag}",
      other: "Se reiniciaron {count} cosas con la etiqueta {tag}",
    },
    left: {
      one: "{count} se dejó como estaba",
      other: "{count} se dejaron como estaban",
    },
    failedTitle: {
      one: "No se pudo reiniciar {count}",
      other: "No se pudieron reiniciar {count}",
    },
    nothing: "No se reinició nada con la etiqueta {tag}",
  },
  update: {
    action: "Actualizar apps con etiqueta",
    title: "¿Actualizar las apps con la etiqueta {tag}?",
    description: {
      one: "{count} app lleva {tag}. Obtiene su origen y compila una release nueva, como Actualizar, y se omite si no hay nada nuevo.",
      other: "{count} apps llevan {tag}. Cada una obtiene su origen y compila una release nueva, como Actualizar, y se omite si no hay nada nuevo.",
    },
    force: "Recompilar aunque no haya nada nuevo",
    confirm: "Actualizar apps",
    queued: {
      one: "Actualizando {count} app con la etiqueta {tag}",
      other: "Actualizando {count} apps con la etiqueta {tag}",
    },
    skipped: {
      one: "{count} app no tenía nada nuevo y se omitió",
      other: "{count} apps no tenían nada nuevo y se omitieron",
    },
    failedTitle: {
      one: "No se pudo actualizar {count} app",
      other: "No se pudieron actualizar {count} apps",
    },
    nothing: "No se actualizó ninguna app con la etiqueta {tag}",
  },
};
