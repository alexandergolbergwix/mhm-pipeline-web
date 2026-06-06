/**
 * useDebounce — returns a debounced copy of ``value`` that only updates
 * after ``delayMs`` has elapsed without a further change. Used to throttle
 * expensive reactions to fast-changing inputs (search keystrokes, slider
 * drags) without firing on every intermediate value.
 */

import { useEffect, useState } from "react";

export function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState<T>(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);

  return debounced;
}
