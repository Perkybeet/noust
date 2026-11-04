import type { widgets as en } from "../en/widgets";
import type { Catalog } from "../types";

export const widgets: Catalog<typeof en> = {
  sizes: {
    small: "Pequeño",
    medium: "Mediano",
    large: "Grande",
    tall: "Alto",
    wide: "Ancho",
    xlarge: "Muy grande",
  },
  grid: {
    label: "Widgets de este panel",
    move: "Mover {title}",
    moveHelp: "Pulsa Espacio para levantar el widget, muévelo con las flechas, pulsa Espacio otra vez para dejarlo o Escape para devolverlo a su sitio.",
    lifted: "{title} levantado. Las flechas lo mueven, Espacio lo deja y Escape lo devuelve a su sitio.",
    position: "{title}: posición {position} de {count}.",
    dropped: "{title} colocado en la posición {position}.",
    cancelled: "{title} devuelto a su sitio.",
    sizeButton: "Tamaño y posición de {title}: {size}",
    sizeGroup: "Tamaño",
    current: "tamaño actual",
    earlier: "Mover antes",
    later: "Mover después",
    resized: "{title}: tamaño {size}.",
    remove: "Quitar {title}",
    removed: "{title} quitado del panel.",
    added: "{title} añadido al final del panel.",
  },
};
