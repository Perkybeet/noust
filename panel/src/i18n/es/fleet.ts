import type { fleet as en } from "../en/fleet";
import type { Catalog } from "../types";

export const fleet: Catalog<typeof en> = {
  selector: {
    label: "Servidor",
    trigger: "Servidor: {name}",
    triggerWithStatus: "Servidor: {name}, {status}",
    thisServer: "Este servidor",
    version: "Noust {version}",
    noVersion: "Versión aún desconocida",
    status: {
      reachable: "Accesible",
      unreachable: "Inaccesible",
      refused: "Rechazado",
      unknown: "Sin comprobar",
    },
    listFailed: "No se pudieron listar los servidores",
    switched: "Ahora en {name}",
    switchTo: "Cambiar al servidor {name}",
    switchToThisServer: "Cambiar a este servidor, {name}",
    keywords: "servidor nodo flota máquina cambiar",
  },
  shell: {
    noticeLabel: "Sobre {node}",
    olderNode:
      "{node} ejecuta Noust {nodeVersion}, anterior a la {version} de este servidor. Una página que necesite algo que {node} no tiene lo indica.",
    newerNode:
      "{node} ejecuta Noust {nodeVersion}, posterior a la {version} de este servidor. Esta consola muestra solo lo que conoce; actualiza este servidor para usar todo lo que ofrece {node}.",
    unknownNode: "Este servidor no tiene ningún nodo llamado {node}.",
    backToThisServer: "Ir a este servidor",
  },
  capability: {
    notAvailable: "No disponible en {node} (Noust {version})",
    notAvailableNoVersion: "No disponible en {node}",
    explanation: "El Noust de {node} no ofrece esto. Actualiza Noust en {node} para usarlo aquí.",
    checking: "Comprobando qué ofrece {node}",
  },
  errors: {
    unreachableTitle: "{node} no responde",
    refusedTitle: "{node} rechazó a este servidor",
    unreachableHint: "Este servidor no pudo llegar a {node} a través de su túnel.",
    refusedHint: "{node} ya no acepta el token de flota de este servidor.",
  },
};
