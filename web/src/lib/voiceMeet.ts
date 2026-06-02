const ROLLY_WAKE_NAMES = "(?:rolly|rollie|rowley|rowly|rowy|roley|rally)";
const HEY_ROLLY_RE = new RegExp(`\\bhey\\s+${ROLLY_WAKE_NAMES}\\b`);

export function normalizeMeetTranscript(text: string): string {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function isRollyWakePhrase(text: string): boolean {
  const normalized = normalizeMeetTranscript(text);
  return HEY_ROLLY_RE.test(normalized);
}
