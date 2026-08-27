/**
 * One query layer. Server Components call this; nothing in the front end talks
 * to PostgreSQL, so the read-only public build can point at a seeded API with
 * no database credentials anywhere near the browser.
 */
const API = process.env.CHAOSPROOF_API ?? 'http://127.0.0.1:8000';

export const READ_ONLY = process.env.READ_ONLY !== 'false';

export interface Fetched<T> {
  data: T | null;
  /** Non-null when the framework or its store was unreachable. */
  error: string | null;
}

/**
 * A dashboard that errors during a live demo is worse than one showing
 * 30-second-old data — so a failed fetch returns an error the caller RENDERS
 * rather than an exception that blanks the page.
 */
export async function fetchJson<T>(path: string, revalidate = 15): Promise<Fetched<T>> {
  try {
    const res = await fetch(`${API}${path}`, { next: { revalidate } });
    if (!res.ok) {
      return { data: null, error: `framework API returned ${res.status} for ${path}` };
    }
    return { data: (await res.json()) as T, error: null };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { data: null, error: `framework API unreachable: ${msg}` };
  }
}
