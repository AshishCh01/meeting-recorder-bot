import { LayoutDashboard, CalendarClock, Sparkles, Settings } from 'lucide-react';

/**
 * The one definition of the app's menu. The sidebar, the phone bottom nav and
 * the command palette all read this, so an item can never appear in one and
 * not the others.
 *
 * The grouping is the point: four ungrouped links read as a list of leftovers,
 * three named sections read as a product. Keep `Account` last.
 */
export const NAV = [
  {
    label: 'Workspace',
    items: [
      { id: 'meetings', label: 'Meetings', href: '/dashboard', icon: LayoutDashboard },
      { id: 'upcoming', label: 'Upcoming', href: '/upcoming', icon: CalendarClock },
    ],
  },
  {
    label: 'Intelligence',
    items: [{ id: 'ask', label: 'Ask AI', href: '/ask', icon: Sparkles }],
  },
  {
    label: 'Account',
    items: [{ id: 'settings', label: 'Settings', href: '/settings', icon: Settings }],
  },
];

export const NAV_ITEMS = NAV.flatMap((group) => group.items);

// A section's own sub-pages count as the section: /ask/<id> is still Ask AI.
export const isCurrentPath = (pathname, href) =>
  pathname === href || pathname.startsWith(`${href}/`);
