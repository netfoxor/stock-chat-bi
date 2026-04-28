import { useCallback, useEffect, useState } from "react";

export function useElementSize<T extends HTMLElement>() {
  const [el, setEl] = useState<T | null>(null);
  const [size, setSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });

  useEffect(() => {
    if (!el) return;

    // 先同步读一次，避免首次渲染 size=0
    const rect = el.getBoundingClientRect();
    setSize({ width: rect.width, height: rect.height });

    const ro = new ResizeObserver((entries) => {
      const cr = entries[0]?.contentRect;
      if (!cr) return;
      setSize({ width: cr.width, height: cr.height });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);

  const ref = useCallback((node: T | null) => {
    setEl(node);
  }, []);

  return { ref, size };
}

