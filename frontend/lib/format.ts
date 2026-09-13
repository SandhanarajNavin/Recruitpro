/**
 * Display formatting shared across screens.
 */

/** Words that stay lowercase inside a name, and prefixes that keep their casing. */
const PARTICLES = new Set(["van", "von", "der", "den", "de", "del", "della", "di", "da", "du", "la", "le", "bin", "binte", "ibn", "al"]);

/**
 * Human-readable version of a name that arrived in block capitals.
 *
 * Resumes are frequently typeset with the name in caps, and "SANDHANARAJ NAVIN C"
 * shouts in a table. Only all-caps input is touched: a name that already carries
 * meaningful case ("McDonald", "van der Berg", "O'Brien") is left exactly as the
 * candidate wrote it, because re-casing it would be a guess that loses information.
 */
export function displayName(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return trimmed;

  // Any lowercase letter means the writer made a casing choice worth keeping.
  if (/[a-z]/.test(trimmed)) return trimmed;

  return trimmed
    .toLocaleLowerCase()
    .split(/(\s+)/)
    .map((token, index) => {
      if (!token.trim()) return token;
      if (index > 0 && PARTICLES.has(token)) return token;
      return capitaliseParts(token);
    })
    .join("");
}

/** Capitalises across the separators that appear inside names. */
function capitaliseParts(word: string): string {
  return word
    .split(/([-'’.])/)
    .map((part) =>
      /[-'’.]/.test(part) ? part : part.charAt(0).toLocaleUpperCase() + part.slice(1),
    )
    .join("");
}
