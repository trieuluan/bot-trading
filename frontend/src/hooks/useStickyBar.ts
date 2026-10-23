import { useEffect, useRef, useState } from 'react';

/**
 * Thanh `position: sticky`: trả `stuck` khi thanh đang dính mép trên (để đổ bóng),
 * và nếu có `heightVar` thì ghi chiều cao thanh vào biến CSS trên :root để thanh
 * sticky phía dưới biết chỗ dừng.
 */
export function useStickyBar<T extends HTMLElement>(heightVar?: string) {
  const ref = useRef<T>(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let frame = 0;
    const check = () => {
      frame = 0;
      const top = parseFloat(getComputedStyle(element).top) || 0;
      setStuck(window.scrollY > 0 && element.getBoundingClientRect().top <= top + 0.5);
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(check);
    };
    const resize = new ResizeObserver(() => {
      if (heightVar)
        document.documentElement.style.setProperty(heightVar, `${element.offsetHeight}px`);
      schedule();
    });
    resize.observe(element);
    window.addEventListener('scroll', schedule, { passive: true });
    window.addEventListener('resize', schedule);
    check();
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      window.removeEventListener('scroll', schedule);
      window.removeEventListener('resize', schedule);
      if (heightVar) document.documentElement.style.removeProperty(heightVar);
    };
  }, [heightVar]);
  return [ref, stuck] as const;
}
