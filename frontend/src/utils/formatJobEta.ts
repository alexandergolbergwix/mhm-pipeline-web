/** Format a job ETA in seconds for curator-facing progress (W-192). */

export function formatJobEta(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) {
    return "Estimating…";
  }
  const whole = Math.round(seconds);
  if (whole <= 0) return "Done";
  if (whole < 60) return `about ${whole}s left`;
  const minutes = Math.round(whole / 60);
  if (minutes < 60) {
    return minutes === 1 ? "about 1 min left" : `about ${minutes} min left`;
  }
  const hours = Math.round(minutes / 60);
  return hours === 1 ? "about 1 h left" : `about ${hours} h left`;
}

export function formatJobElapsed(seconds: number | null | undefined): string {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return "—";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s elapsed`;
  const minutes = Math.floor(whole / 60);
  const rem = whole % 60;
  if (minutes < 60) return rem ? `${minutes}m ${rem}s elapsed` : `${minutes}m elapsed`;
  const hours = Math.floor(minutes / 60);
  const minRem = minutes % 60;
  return minRem ? `${hours}h ${minRem}m elapsed` : `${hours}h elapsed`;
}

export function formatJobEtaShort(seconds: number | null | undefined): string | null {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return null;
  const whole = Math.round(seconds);
  if (whole <= 0) return null;
  if (whole < 60) return `~${whole}s`;
  const minutes = Math.round(whole / 60);
  if (minutes < 60) return `~${minutes}m`;
  return `~${Math.round(minutes / 60)}h`;
}
