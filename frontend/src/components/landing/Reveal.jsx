import React, { useEffect, useRef, useState } from 'react';

const prefersReducedMotion = () => {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
};

/**
 * Fades a section in as it scrolls into view.
 *
 * The hidden state is opt-in, not the default: `visible` starts true whenever
 * the animation should not run - reduced motion, or a browser without
 * IntersectionObserver - so the content is never hidden by a mechanism that
 * will not fire to reveal it again. Anything that cannot animate simply shows.
 *
 * `delay` staggers siblings; it is applied as an inline transition-delay
 * rather than a class so arbitrary values do not each need a Tailwind rule.
 */
export const Reveal = ({ children, delay = 0, className = '', as: Tag = 'div' }) => {
  const ref = useRef(null);
  const [visible, setVisible] = useState(() => prefersReducedMotion() || typeof IntersectionObserver === 'undefined');

  useEffect(() => {
    if (visible) return undefined;
    const node = ref.current;
    if (!node) return undefined;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      // A little early, so a section is already settled by the time it is
      // properly on screen rather than animating under the reader's eye.
      { rootMargin: '0px 0px -10% 0px' }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  return (
    <Tag
      ref={ref}
      style={visible ? undefined : { transitionDelay: `${delay}ms` }}
      className={`transition-[opacity,transform] duration-700 ease-out motion-reduce:transition-none ${
        visible ? 'translate-y-0 opacity-100' : 'translate-y-4 opacity-0'
      } ${className}`}
    >
      {children}
    </Tag>
  );
};
