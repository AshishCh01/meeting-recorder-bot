import { useCallback, useEffect, useState } from 'react';

/**
 * A boolean that outlives the component and the page.
 *
 * Panel preferences are the kind of thing a user sets once and expects to
 * stay set - collapsing the Ask AI column on one meeting and finding it back
 * on the next one is the same complaint as the sidebar rail springing open on
 * navigation. Reading storage in the initialiser rather than an effect means
 * the first paint is already correct, with no flash of the other state.
 */
export function usePersistentToggle(key, fallback = false) {
  const [value, setValue] = useState(() => {
    try {
      const stored = localStorage.getItem(key);
      return stored === null ? fallback : stored === '1';
    } catch {
      // Private mode or blocked site data; the preference just won't persist.
      return fallback;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, value ? '1' : '0');
    } catch {
      /* not fatal */
    }
  }, [key, value]);

  const toggle = useCallback(() => setValue((v) => !v), []);

  return [value, toggle, setValue];
}
