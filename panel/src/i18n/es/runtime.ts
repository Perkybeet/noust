import type { runtime as en } from "../en/runtime";
import type { Catalog } from "../types";

export const runtime: Catalog<typeof en> = {
  deployLog: {
    levelLabel: "Nivel del registro",
    levelAll: "Todo",
    levelWarn: "Avisos y errores",
    levelError: "Errores",
    timesLabel: "Mostrar las horas",
    share: "Copiar para compartir",
    shareDescription:
      "Copia lo que se ve sin códigos de color y con lo que parezca una credencial oculto: seguro para pegarlo en una incidencia.",
    shareCopied: "Registro copiado, listo para compartir",
    shareFailed: "No se pudo preparar el registro para compartirlo",
    nothingAtLevel: "Ninguna línea de este registro es un aviso o un error.",
    filteredNote: "Se muestran {shown} de {total} líneas.",
  },
  rollback: {
    instantWords: "Vuelve en segundos: lo que ejecutó el despliegue {id} sigue en el servidor, así que no se construye nada.",
    rebuildWords: "Construye otra vez el commit {commit} donde corre la aplicación, y tarda lo que un despliegue.",
    instantBadge: "Al instante",
    rebuildBadge: "Reconstruye",
  },
};
