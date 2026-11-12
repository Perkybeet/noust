import type { SessionInfo } from "../../api/queries/auth";
import { SecondFactor } from "./GatePage";

/**
 * Setting up a second factor from inside "Confirm it's you", for an account that left it for later (item 131): the very
 * `SecondFactor` the first sign-in and the first account use, so there is one way to do it. Its own module so the
 * dialog, which every page carries, does not carry the QR code and the passkey ceremony until it is needed.
 */
export default function ElevateEnrol({ session, onDone }: { session: SessionInfo; onDone: () => Promise<void> }) {
  return <SecondFactor session={session} onDone={onDone} />;
}
