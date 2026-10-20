import { Checkbox } from "../../../components/ui/Checkbox";

export interface RemoteOnlyWords {
  label: string;
  /** What ticking it does, in one sentence. */
  help: string;
  /** Said instead of the help while there is no destination to keep the copies in. */
  needsDestination: string;
}

export interface RemoteOnlyFieldProps {
  words: RemoteOnlyWords;
  /** What the form holds: the stored value, then whatever was ticked since. */
  value: boolean;
  onChange: (value: boolean) => void;
  /** How many destinations the policy sends to. */
  destinations: number;
}

/**
 * "Keep nothing on this server": each backup is deleted here once every destination holds it. It
 * means nothing without a destination, and the server refuses it, so with none the box is shown
 * off and disabled with the reason; the form's `value` is kept, so a destination added back
 * brings the tick back, and `remoteOnlyBody` is what is sent.
 */
export function RemoteOnlyField({ words, value, onChange, destinations }: RemoteOnlyFieldProps) {
  const available = destinations > 0;
  return (
    <Checkbox
      checked={value && available}
      onCheckedChange={onChange}
      disabled={!available}
      label={words.label}
      description={available ? words.help : words.needsDestination}
    />
  );
}
