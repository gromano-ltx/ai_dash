/**
 * Renders a number like a running ledger total: fixed-width tabular digits
 * that roll in on change, instead of a flat static figure. The token count
 * is the one number in this app that's actually earned trust (see AI-54) —
 * this is the visual acknowledgment of that.
 */
export function Odometer({ value }: { value: string }) {
  return (
    <span key={value} className="inline-block font-mono tabular-nums animate-ledger-roll">
      {value}
    </span>
  );
}
