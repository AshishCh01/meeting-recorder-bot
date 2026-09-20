/* ===================================================================
   Shared shell and interactions for the app prototype.

   Plain script, no modules and no build - these pages open straight from
   the filesystem. The shell is rendered here rather than copied into
   seven HTML files, so the menu sections exist in one place; in the React
   port this is the Layout component.
   =================================================================== */

/* --------------------------------------------------------- icons */
// The mark is drawn more than once per page (sidebar and top bar), so each
// copy needs its own gradient id - duplicate ids leave every copy after the
// first with an unresolved paint server and no arc.
let markSeq = 0;
function logoMark(size = 24) {
  const id = `mk${++markSeq}`;
  return `<svg width="${size}" height="${size}" viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <circle class="mark-dot" cx="16" cy="6.6" r="3.6"/>
    <path d="M5.6 20.2a10.4 10.4 0 0 1 20.8 0v3.4a4.8 4.8 0 0 1-4.8 4.8h-7.9" stroke="url(#${id})" stroke-width="4" stroke-linecap="round"/>
    <defs><linearGradient id="${id}" x1="5" y1="10" x2="27" y2="29" gradientUnits="userSpaceOnUse">
      <stop class="mark-from"/><stop class="mark-to" offset="1"/>
    </linearGradient></defs></svg>`;
}

const I = {
  meetings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="13" height="16" rx="2"/><path d="m16 10 5-3v10l-5-3"/></svg>`,
  upcoming: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18M12 15v3l2 1"/></svg>`,
  ask: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v2M18.4 5.6l-1.4 1.4M21 12h-2M5 12H3M7 7 5.6 5.6"/><circle cx="12" cy="14" r="5"/></svg>`,
  settings: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 7 19.4a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0-1.2-2.9H1a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 2.6 7a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H7a1.7 1.7 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V7a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" transform="translate(1.5 1.5) scale(0.86)"/></svg>`,
  search: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`,
  plus: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  chevron: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>`,
  collapse: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></svg>`,
  sun: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`,
  moon: `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>`,
  out: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/></svg>`,
  trash: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>`,
  more: `<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>`,
  check: `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 13 4 4L19 7"/></svg>`,
  play: `<svg width="14" height="14" viewBox="0 0 12 12" fill="currentColor"><path d="M3 1.5v9l7-4.5z"/></svg>`,
  pause: `<svg width="14" height="14" viewBox="0 0 12 12" fill="currentColor"><rect x="2.5" y="2" width="2.6" height="8" rx="1"/><rect x="7" y="2" width="2.6" height="8" rx="1"/></svg>`,
  back: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>`,
  download: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12M8 11l4 4 4-4M4 20h16"/></svg>`,
  send: `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m4 12 16-8-6 16-2.5-6.5z"/></svg>`,
  close: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>`,
  spark: `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v3M12 18v3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M3 12h3M18 12h3M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/><circle cx="12" cy="12" r="2.5"/></svg>`,
};

/* ------------------------------------------------- menu sections */
const NAV = [
  {
    label: 'Workspace',
    items: [
      { id: 'meetings', label: 'Meetings', href: 'dashboard.html', icon: 'meetings', count: 8 },
      { id: 'upcoming', label: 'Upcoming', href: 'upcoming.html', icon: 'upcoming', count: 3 },
    ],
  },
  {
    label: 'Intelligence',
    items: [{ id: 'ask', label: 'Ask AI', href: 'ask.html', icon: 'ask' }],
  },
  {
    label: 'Account',
    items: [{ id: 'settings', label: 'Settings', href: 'settings.html', icon: 'settings' }],
  },
];

const USER = { name: 'Ashish Choudhary', email: 'ashishchoudhary5430@gmail.com', initial: 'A' };

/* ---------------------------------------------------------- theme */
function setTheme(mode) {
  document.documentElement.classList.toggle('dark', mode === 'dark');
  try {
    localStorage.setItem('theme', mode);
  } catch (e) {
    /* storage blocked - the choice just won't survive a reload */
  }
  document.querySelectorAll('[data-theme-icon]').forEach((el) => {
    el.innerHTML = mode === 'dark' ? I.sun : I.moon;
  });
}

function currentTheme() {
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

/* ---------------------------------------------------------- shell */
function renderShell(active) {
  const groups = NAV.map(
    (g) => `
    <div class="nav-group">
      <span class="nav-label eyebrow">${g.label}</span>
      ${g.items
        .map(
          // title and aria-label carry the name in the rail, where the
          // visible label is hidden.
          (it) => `<a class="nav-item ${it.id === active ? 'is-active' : ''}" href="${it.href}"
             title="${it.label}" aria-label="${it.label}" ${it.id === active ? 'aria-current="page"' : ''}>
            ${I[it.icon]}<span>${it.label}</span>
            ${it.count ? `<span class="nav-count">${it.count}</span>` : ''}
          </a>`
        )
        .join('')}
    </div>`
  ).join('');

  const aside = document.createElement('aside');
  aside.className = 'sidebar';
  aside.innerHTML = `
    <div class="side-top">
      <a class="brand" href="dashboard.html">${logoMark()}<span>MeetIQ</span></a>
      <button class="icon-btn side-collapse" id="railBtn" aria-label="Collapse sidebar">${I.collapse}</button>
    </div>

    <button class="side-search" id="paletteBtn" aria-label="Search meetings">
      ${I.search}<span>Search</span><kbd>⌘K</kbd>
    </button>

    <div class="side-cta">
      <button class="btn btn-primary btn-block" data-record aria-label="New recording">
        ${I.plus}<span>New recording</span>
      </button>
    </div>

    <div class="side-scroll">
      ${groups}
      <div class="plan">
        <div class="plan-row"><span>Free plan</span><span>3 / 5</span></div>
        <div class="plan-bar"><i style="width:60%"></i></div>
        <a href="../index.html#pricing">Upgrade to Pro →</a>
      </div>
    </div>

    <div class="side-foot">
      <button class="account" id="accountBtn" aria-haspopup="menu" aria-expanded="false">
        <span class="avatar">${USER.initial}</span>
        <span class="account-who">
          <span class="account-name truncate" style="display:block">${USER.name}</span>
          <span class="account-mail truncate" style="display:block">${USER.email}</span>
        </span>
        ${I.chevron}
      </button>
    </div>`;

  const topbar = document.createElement('header');
  topbar.className = 'topbar';
  topbar.innerHTML = `
    <a class="brand" href="dashboard.html">${logoMark()}<span>MeetIQ</span></a>
    <button class="icon-btn" id="paletteBtnM" style="margin-left:auto" aria-label="Search">${I.search}</button>
    <button class="icon-btn" data-theme-toggle aria-label="Switch theme"><span data-theme-icon></span></button>
    <button class="icon-btn" id="accountBtnM" aria-haspopup="menu" aria-expanded="false" aria-label="Account">
      <span class="avatar" style="width:28px;height:28px">${USER.initial}</span>
    </button>`;

  const bottom = document.createElement('nav');
  bottom.className = 'bottom-nav';
  bottom.innerHTML = NAV.flatMap((g) => g.items)
    .map(
      (it) =>
        `<a href="${it.href}" class="${it.id === active ? 'is-active' : ''}">${I[it.icon]}<span>${it.label}</span></a>`
    )
    .join('');

  document.body.prepend(aside);
  const main = document.querySelector('.main');
  main.prepend(topbar);
  document.body.append(bottom);

  wireShell();
}

function wireShell() {
  // Rail toggle, remembered between pages.
  const railBtn = document.getElementById('railBtn');
  if (railBtn) {
    railBtn.addEventListener('click', () => {
      const on = document.body.classList.toggle('rail');
      try {
        localStorage.setItem('rail', on ? '1' : '0');
      } catch (e) {
        /* ignore */
      }
    });
  }

  const openAccount = (btn) => {
    closeLayers();
    const menu = document.createElement('div');
    menu.className = 'menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <div class="mail">${USER.email}</div>
      <hr>
      <a href="settings.html">${I.settings}Settings</a>
      <button data-theme-toggle><span data-theme-icon></span>Switch theme</button>
      <hr>
      <a class="danger" href="login.html">${I.out}Sign out</a>`;
    document.body.append(menu);
    const r = btn.getBoundingClientRect();
    const w = menu.offsetWidth;
    const top = r.top < window.innerHeight / 2 ? r.bottom + 8 : r.top - menu.offsetHeight - 8;
    menu.style.top = `${Math.max(8, top)}px`;
    menu.style.left = `${Math.min(Math.max(8, r.left), window.innerWidth - w - 8)}px`;
    btn.setAttribute('aria-expanded', 'true');
    setTheme(currentTheme()); // paints the icon inside the fresh menu
  };

  ['accountBtn', 'accountBtnM'].forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener('click', (e) => (e.stopPropagation(), openAccount(btn)));
  });

  ['paletteBtn', 'paletteBtnM'].forEach((id) => {
    const btn = document.getElementById(id);
    if (btn) btn.addEventListener('click', openPalette);
  });

  // stopPropagation matters: the document-level click handler below closes
  // open layers, and without it the modal would be removed in the same
  // click that opened it.
  document.querySelectorAll('[data-record]').forEach((b) =>
    b.addEventListener('click', (e) => {
      e.stopPropagation();
      openRecord();
    })
  );
}

/* --------------------------------------------------------- layers */
function closeLayers() {
  document.querySelectorAll('.menu, .scrim, .palette, .modal, .sheet').forEach((el) => el.remove());
  document.querySelectorAll('[aria-expanded="true"]').forEach((el) =>
    el.setAttribute('aria-expanded', 'false')
  );
  document.body.style.overflow = '';
}

document.addEventListener('click', (e) => {
  if (e.target.closest('.menu, .palette, .modal, .sheet')) return;
  if (e.target.closest('[aria-haspopup], #paletteBtn, #paletteBtnM')) return;
  closeLayers();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeLayers();
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault();
    openPalette();
  }
});

// Theme toggles can live anywhere, including inside a menu opened later.
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-theme-toggle]');
  if (!t) return;
  e.stopPropagation();
  setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
});

/* ------------------------------------------------------- palette */
const PALETTE = [
  { sec: 'Go to', items: NAV.flatMap((g) => g.items).map((i) => ({ label: i.label, href: i.href, icon: i.icon })) },
  {
    sec: 'Recent meetings',
    items: [
      { label: 'Meeting Recorder Bot Architecture and Lifecycle Overview', href: 'meeting.html', icon: 'meetings' },
      { label: 'Testing and Debugging Google Meet Bot', href: 'meeting.html', icon: 'meetings' },
      { label: 'Discussion on AI Legal Assistant', href: 'meeting.html', icon: 'meetings' },
    ],
  },
];

function openPalette() {
  closeLayers();
  const scrim = document.createElement('div');
  scrim.className = 'scrim';

  const box = document.createElement('div');
  box.className = 'palette';
  box.innerHTML = `
    <div class="palette-input">${I.search}
      <input type="text" placeholder="Search meetings and pages…" aria-label="Search" />
      <kbd class="eyebrow">esc</kbd>
    </div>
    <div class="palette-list"></div>`;

  document.body.append(scrim, box);

  const list = box.querySelector('.palette-list');
  const input = box.querySelector('input');

  const paint = (q) => {
    const needle = q.trim().toLowerCase();
    list.innerHTML = PALETTE.map((group) => {
      const hits = group.items.filter((i) => i.label.toLowerCase().includes(needle));
      if (!hits.length) return '';
      return `<div class="sec eyebrow">${group.sec}</div>${hits
        .map((i) => `<a href="${i.href}">${I[i.icon]}<span class="truncate">${i.label}</span></a>`)
        .join('')}`;
    }).join('');
    if (!list.innerHTML) list.innerHTML = `<div class="sec eyebrow">No matches</div>`;
  };

  paint('');
  input.addEventListener('input', () => paint(input.value));
  input.focus();
}

/* -------------------------------------------------- record modal */
function openRecord() {
  closeLayers();
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  const box = document.createElement('div');
  box.className = 'palette modal';
  box.style.top = '16vh';
  box.innerHTML = `
    <form style="padding:20px">
      <h3 style="font-size:17px">Record a meeting</h3>
      <p style="margin-top:6px;font-size:13.5px;color:var(--text-muted)">
        Paste a Google Meet or Zoom link. The bot joins under your chosen name and records audio only.
      </p>
      <input class="input" style="margin-top:14px" type="url" required
             placeholder="https://meet.google.com/abc-defg-hij" aria-label="Meeting link" />
      <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:16px">
        <button type="button" class="btn btn-bare" data-close>Cancel</button>
        <button type="submit" class="btn btn-primary">Start recording</button>
      </div>
    </form>`;
  document.body.append(scrim, box);
  box.querySelector('input').focus();
  box.querySelector('[data-close]').addEventListener('click', closeLayers);
  box.querySelector('form').addEventListener('submit', (e) => {
    e.preventDefault();
    closeLayers();
    toast('The bot is joining your meeting');
  });
}

/* --------------------------------------------------------- toasts */
function toast(msg) {
  let host = document.querySelector('.toasts');
  if (!host) {
    host = document.createElement('div');
    host.className = 'toasts';
    document.body.append(host);
  }
  const el = document.createElement('div');
  el.className = 'toast';
  el.setAttribute('role', 'status');
  el.innerHTML = `${I.check}<span>${msg}</span>`;
  host.append(el);
  setTimeout(() => el.remove(), 3200);
}

/* ------------------------------------------------ small helpers */
// Switches (Upcoming, Settings).
document.addEventListener('click', (e) => {
  const sw = e.target.closest('.switch');
  if (!sw) return;
  const on = sw.getAttribute('aria-checked') === 'true';
  sw.setAttribute('aria-checked', String(!on));
  if (sw.dataset.on) toast(!on ? sw.dataset.on : sw.dataset.off || 'Turned off');
});

// Single-select chip rows and tab strips.
document.addEventListener('click', (e) => {
  const chip = e.target.closest('.chip');
  if (chip && chip.parentElement.classList.contains('chips')) {
    chip.parentElement.querySelectorAll('.chip').forEach((c) => c.classList.remove('is-on'));
    chip.classList.add('is-on');
  }
});

// Row overflow menus.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-row-menu]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  closeLayers();
  const menu = document.createElement('div');
  menu.className = 'menu';
  menu.innerHTML = `
    <a href="meeting.html">${I.meetings}Open</a>
    <button>${I.download}Export PDF</button>
    <hr>
    <button class="danger" data-delete>${I.trash}Delete</button>`;
  document.body.append(menu);
  const r = btn.getBoundingClientRect();
  menu.style.top = `${Math.min(r.bottom + 6, window.innerHeight - menu.offsetHeight - 8)}px`;
  menu.style.left = `${Math.max(8, r.right - menu.offsetWidth)}px`;
  menu.querySelector('[data-delete]').addEventListener('click', () => {
    const row = btn.closest('.row');
    closeLayers();
    if (row) row.remove();
    toast('Meeting deleted');
  });
});

// Tabs: [data-tab="id"] buttons switch [data-panel="id"] sections.
document.addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (!tab) return;
  const scope = tab.closest('[data-tabs]') || document;
  scope.querySelectorAll('[data-tab]').forEach((t) => t.classList.toggle('is-on', t === tab));
  document
    .querySelectorAll('[data-panel]')
    .forEach((p) => (p.hidden = p.dataset.panel !== tab.dataset.tab));
});

// Audio player: enough for a prototype to feel alive.
document.addEventListener('click', (e) => {
  const play = e.target.closest('.play');
  if (play) {
    const on = play.getAttribute('aria-pressed') === 'true';
    play.setAttribute('aria-pressed', String(!on));
    play.innerHTML = on ? I.play : I.pause;
    play.setAttribute('aria-label', on ? 'Play' : 'Pause');
  }
  const scrub = e.target.closest('.scrub');
  if (scrub) {
    const r = scrub.getBoundingClientRect();
    const pct = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100));
    scrub.querySelector('.scrub-fill').style.width = `${pct}%`;
  }
  const rate = e.target.closest('.rate');
  if (rate) {
    const rates = ['1×', '1.25×', '1.5×', '2×'];
    rate.textContent = rates[(rates.indexOf(rate.textContent) + 1) % rates.length];
  }
});

// Bottom sheet, used by the meeting page's Ask AI button on phones.
function openSheet(sourceSelector) {
  const src = document.querySelector(sourceSelector);
  if (!src) return;
  closeLayers();
  const scrim = document.createElement('div');
  scrim.className = 'scrim';
  const sheet = document.createElement('div');
  sheet.className = 'sheet';
  sheet.innerHTML = `<div class="sheet-grab"></div>${src.innerHTML}`;
  const head = sheet.querySelector('.ai-head');
  if (head) {
    const close = document.createElement('button');
    close.className = 'icon-btn';
    close.style.marginLeft = 'auto';
    close.setAttribute('aria-label', 'Close');
    close.innerHTML = I.close;
    close.addEventListener('click', closeLayers);
    head.append(close);
  }
  document.body.append(scrim, sheet);
  document.body.style.overflow = 'hidden';
}

document.addEventListener('click', (e) => {
  const opener = e.target.closest('[data-sheet]');
  if (!opener) return;
  e.stopPropagation(); // same reason as the record modal
  openSheet(opener.dataset.sheet);
});

// Chat column toggle on the meeting page, remembered between visits.
document.addEventListener('click', (e) => {
  const t = e.target.closest('[data-chat-toggle]');
  if (!t) return;
  const off = document.body.classList.toggle('chat-off');
  t.setAttribute('aria-pressed', String(!off));
  try {
    localStorage.setItem('chat-off', off ? '1' : '0');
  } catch (err) {
    /* ignore */
  }
});

/* ------------------------------------------------------- startup */
document.addEventListener('DOMContentLoaded', () => {
  try {
    if (localStorage.getItem('rail') === '1') document.body.classList.add('rail');
    if (localStorage.getItem('chat-off') === '1') document.body.classList.add('chat-off');
  } catch (e) {
    /* ignore */
  }
  const active = document.body.dataset.page;
  if (active) renderShell(active);
  setTheme(currentTheme());
});
