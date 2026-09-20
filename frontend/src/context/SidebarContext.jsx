import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

const SidebarContext = createContext({});

const STORAGE_KEY = 'sidebar-rail';

// Read once, at module load, so the first paint already has the saved width
// and the sidebar does not visibly snap from 264px to 72px after mount.
const getInitialRail = () => {
  try {
    return localStorage.getItem(STORAGE_KEY) === '1';
  } catch {
    // Private mode, or site data blocked. The rail just won't be remembered.
    return false;
  }
};

/**
 * Whether the desktop sidebar is collapsed to a rail.
 *
 * This lives above the router on purpose. It used to be `useState` inside
 * `Layout`, and every page mounts its own `Layout`, so collapsing the sidebar
 * and then navigating unmounted the state and reset the width. Anything the
 * shell must remember across navigation belongs here, not in the component.
 */
export const SidebarProvider = ({ children }) => {
  const [isRail, setIsRail] = useState(getInitialRail);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, isRail ? '1' : '0');
    } catch {
      /* not fatal - the preference just won't survive a reload */
    }
  }, [isRail]);

  const toggleRail = useCallback(() => setIsRail((v) => !v), []);

  const value = useMemo(() => ({ isRail, toggleRail }), [isRail, toggleRail]);

  return <SidebarContext.Provider value={value}>{children}</SidebarContext.Provider>;
};

export const useSidebar = () => useContext(SidebarContext);
