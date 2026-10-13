import type { sourceOptions as en } from "../en/sourceOptions";
import type { Catalog } from "../types";

export const sourceOptions: Catalog<typeof en> = {
  title: "Obtención y despliegue",
  description: "Lo que trae el clon y qué pushes se convierten en despliegues.",
  loading: "Cargando cómo se obtiene esta aplicación",
  readFailed: "No se pudo leer cómo se obtiene esta aplicación",
  submodules: {
    label: "Obtener submódulos",
    description: "Pone en la release los archivos de los submódulos del repositorio. Una release suele ser un archivo del commit, que los deja vacíos.",
  },
  lfs: {
    label: "Archivos de Git LFS",
    description: "Sin ellos una release contiene archivos puntero, que es un despliegue roto.",
    auto: "Cuando el repositorio usa LFS",
    on: "Siempre",
    off: "Nunca",
  },
  shallow: {
    label: "Clon superficial",
    description: "Obtiene solo el último commit. Desactívalo cuando una compilación lea el historial, como git describe.",
  },
  skip: {
    label: "Omitir pushes marcados con [skip ci] o [skip cd]",
    description: "Un push no se despliega cuando todos sus commits lo dicen. Un pull request no se compila cuando su título o su commit más reciente lo dice.",
  },
  paths: {
    label: "Rutas vigiladas",
    description: "Un patrón por línea. Un push solo despliega si toca alguna; una línea que empieza por ! excluye y gana la última coincidencia. Vacío significa que todo push despliega.",
    placeholder: "apps/web/**\n!**/*.md",
    note: "GitHub, Gitea y GitLab listan los archivos de un push. Un servicio que no lo hace (Bitbucket) no se puede filtrar, y sus pushes despliegan.",
  },
  save: "Guardar",
  saved: "{domain} se obtendrá y desplegará así",
  notSaved: "No se guardaron los ajustes de origen de {domain}",
  reset: "Volver a los valores por defecto",
  resetTitle: "¿Usar los valores por defecto en {domain}?",
  resetDescription: "Los submódulos y LFS se obtienen según lo que el repositorio necesite, no se conserva el historial y todo push despliega.",
  resetAction: "Usar los valores por defecto",
  resetDone: "{domain} usa los ajustes de origen por defecto",
  fromTerminal: "Lo mismo, desde un terminal",
  deployUrl: {
    label: "URL de despliegue",
    description: "Un pipeline despliega esta aplicación con un POST a esta dirección y un token que pueda desplegar.",
  },
  outcome: {
    skip: "Marcado para omitir, ignorado",
    paths: "Ninguna ruta vigilada tocada, ignorado",
  },
};
