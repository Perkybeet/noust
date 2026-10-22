import type { appTags as en } from "../en/appTags";
import type { Catalog } from "../types";

export const appTags: Catalog<typeof en> = {
  row: {
    label: "Tags de la app",
    none: "Ninguno",
    hint: "Agrupa apps para filtrar la lista y actualizarlas juntas.",
    edit: "Editar tags",
  },
  dialog: {
    title: "Tags de {domain}",
    description: "Nombres que agrupan apps, como web o cliente-acme. Una app lleva hasta {max}.",
    label: "Tags",
    fieldDescription: "Separados por comas o espacios. De 1 a 32 letras, dígitos, puntos, guiones bajos o guiones, empezando y terminando por una letra o un dígito.",
    placeholder: "web, cliente-acme",
    submit: "Guardar tags",
    saved: "Tags de {domain} guardados",
    tooMany: "Como máximo {max} tags.",
    failed: "No se guardaron los tags de {domain}",
    invalid: {
      one: "No es un tag: {list}. Un tag tiene de 1 a 32 letras, dígitos, puntos, guiones bajos o guiones, y empieza y termina por una letra o un dígito.",
      other: "No son tags: {list}. Un tag tiene de 1 a 32 letras, dígitos, puntos, guiones bajos o guiones, y empieza y termina por una letra o un dígito.",
    },
  },
  filter: {
    label: "Tag",
    every: "Todos los tags",
  },
  /** Las bases de datos y los servicios llevan tags también: las mismas reglas, editados desde sus listas. */
  resource: {
    chips: "Tags",
    title: "Tags de {noun} {name}",
    description: "Nombres que agrupan cosas, como web o client-acme. Una lleva hasta {max}. Guardarlos no reinicia nada.",
    saved: "Tags de {name} guardados",
    failed: "No se guardaron los tags de {name}",
  },
  restart: {
    action: "Reiniciar con tag",
    title: "¿Reiniciar todo lo que lleva el tag {tag}?",
    description: {
      one: "{count} app lleva {tag}, y también pueden llevarlo bases de datos y servicios del servidor. Cada servicio se reinicia, uno a uno, y se interrumpe mientras reinicia. Una base de datos se deja: reinicia con su motor.",
      other: "{count} apps llevan {tag}, y también pueden llevarlo bases de datos y servicios del servidor. Cada servicio se reinicia, uno a uno, y se interrumpe mientras reinicia. Una base de datos se deja: reinicia con su motor.",
    },
    confirm: "Reiniciarlos",
    done: {
      one: "Se reinició {count} cosa con el tag {tag}",
      other: "Se reiniciaron {count} cosas con el tag {tag}",
    },
    left: {
      one: "{count} se dejó como estaba",
      other: "{count} se dejaron como estaban",
    },
    failedTitle: {
      one: "No se pudo reiniciar {count}",
      other: "No se pudieron reiniciar {count}",
    },
    nothing: "No se reinició nada con el tag {tag}",
  },
  update: {
    action: "Actualizar apps con tag",
    title: "¿Actualizar las apps con el tag {tag}?",
    description: {
      one: "{count} app lleva {tag}. Obtiene su origen y compila una release nueva, como Actualizar, y se omite si no hay nada nuevo.",
      other: "{count} apps llevan {tag}. Cada una obtiene su origen y compila una release nueva, como Actualizar, y se omite si no hay nada nuevo.",
    },
    force: "Recompilar aunque no haya nada nuevo",
    confirm: "Actualizar apps",
    queued: {
      one: "Actualizando {count} app con el tag {tag}",
      other: "Actualizando {count} apps con el tag {tag}",
    },
    skipped: {
      one: "{count} app no tenía nada nuevo y se omitió",
      other: "{count} apps no tenían nada nuevo y se omitieron",
    },
    failedTitle: {
      one: "No se pudo actualizar {count} app",
      other: "No se pudieron actualizar {count} apps",
    },
    nothing: "No se actualizó ninguna app con el tag {tag}",
  },
};
