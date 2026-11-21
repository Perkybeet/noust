/**
 * Where an application answers: the one reading every screen that says an address of an application asks (its page, the card a
 * deploy ends on). There are two ways to be reached and the machine decides which one this is: on a server with Noust's web server
 * an application answers on its names (the first alias a certificate covers, else the name it has); on a central that deploys on its
 * machine's own Docker (a NAS) it has no name and no certificate of Noust's and answers at a port of the machine, which the server
 * says (`GET /api/apps/{domain}/address`). Each screen used to ask for itself, and the card after a deploy wrote
 * `http://paperless`, an address that does not exist, under a page that said `http://nas-casa:8000`.
 */

import { useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";

import { appAddressQuery } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import { appDomainsQuery } from "../../api/queries/domains";
import type { AppDomainList } from "../../api/queries/domains";
import { useContainerHost, useFeature } from "../central/features";
import { displayAddress } from "./displayAddress";
import type { Address } from "./displayAddress";

/** What the machine says of where an application answers. */
export type AppAnswer =
  /** The server has not said yet: nothing is drawn for an address that may be another. */
  | { kind: "loading" }
  /** A port of the machine, with no HTTPS: `http://nas-casa:8000`. */
  | { kind: "machine"; url: string }
  /** The application serves no web, so there is nothing to open. */
  | { kind: "nothing" }
  /** The names of Noust's web server: the address to say, and whether a certificate covers it. */
  | { kind: "names"; address: Address };

export interface AppAnswerReading {
  answer: AppAnswer;
  /** The names the application answers on, as asked (the page lists them and their failure). */
  names: UseQueryResult<AppDomainList>;
  /** The machine has names and certificates of Noust's to offer: where it does not, nothing says "add your domain". */
  offersNames: boolean;
  offersCertificates: boolean;
}

/**
 * Reads where an application answers.
 *
 * @param domain The application's own name.
 */
export function useAppAnswer(domain: string): AppAnswerReading {
  const containerHost = useContainerHost();
  const offersNames = useFeature("domains");
  const offersCertificates = useFeature("certificates");
  const names = useQuery({ ...appDomainsQuery(domain), enabled: offersNames });
  const certs = useQuery({ ...certsQuery(), enabled: offersCertificates });
  const machine = useQuery({ ...appAddressQuery(domain), enabled: containerHost });

  let answer: AppAnswer;
  if (containerHost) {
    const url = machine.data?.url ?? null;
    answer = machine.data === undefined ? { kind: "loading" } : url !== null ? { kind: "machine", url } : { kind: "nothing" };
  } else {
    answer = { kind: "names", address: displayAddress(domain, names.data?.domains, certs.data) };
  }
  return { answer, names, offersNames, offersCertificates };
}
