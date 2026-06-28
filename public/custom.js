(function () {
  'use strict';

  var LOGO = '/public/JobHuntAI_logo.svg';

  // ── Logo swap ─────────────────────────────────────────────────────────────
  function swapLogo() {
    document.querySelectorAll('#jhai-sidebar-logo, img[alt="logo"]').forEach(function (img) {
      if (img.getAttribute('src') !== LOGO) img.setAttribute('src', LOGO);
    });
  }

  // ── Username (cached once) ─────────────────────────────────────────────────
  var _user = null;
  function getUser(cb) {
    if (_user) { cb(_user); return; }
    fetch('/user', { credentials: 'include' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        _user = d
          ? { name: d.display_name || d.identifier || '', id: d.identifier || '' }
          : { name: '?', id: '' };
        cb(_user);
      })
      .catch(function () { _user = { name: '?', id: '' }; cb(_user); });
  }

  // ── Icons ─────────────────────────────────────────────────────────────────
  var ICON = {
    dash:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    chats:  '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
    logout: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>',
  };

  // ── Dashboard overlay (appended to body — safe from React) ────────────────
  function showDashboard() {
    var ol = document.getElementById('jhai-dash-overlay');
    if (ol) { ol.style.display = 'flex'; setActive('dash'); return; }
    ol = document.createElement('div');
    ol.id = 'jhai-dash-overlay';
    ol.innerHTML = [
      '<div id="jhai-dash-panel">',
      '<div id="jhai-dash-header"><h2>Dashboard</h2>',
      '<button id="jhai-dash-close">&#x2715; Back to Chat</button></div>',
      '<div id="jhai-dash-body"><p class="jhai-dash-loading">Loading…</p></div>',
      '</div>',
    ].join('');
    document.body.appendChild(ol);
    document.getElementById('jhai-dash-close').addEventListener('click', hideDashboard);
    loadDashData();
    setActive('dash');
  }

  function hideDashboard() {
    var ol = document.getElementById('jhai-dash-overlay');
    if (ol) ol.style.display = 'none';
    setActive('chats');
    var c = document.querySelector('[data-sidebar="content"]');
    if (c) c.style.display = '';
  }

  function loadDashData() {
    getUser(function (u) {
      fetch('/public/dashboard_' + encodeURIComponent(u.id) + '.json')
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(renderDash).catch(function () { renderDash(null); });
    });
  }

  function renderDash(data) {
    var body = document.getElementById('jhai-dash-body');
    if (!body) return;
    if (!data) { body.innerHTML = '<p class="jhai-dash-error">Could not load data.</p>'; return; }
    var apps = data.applications || [], vers = data.resume_versions || [];
    var h = '<section><h3>Applications</h3>';
    if (apps.length) {
      h += '<table class="jhai-dash-table"><thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Applied</th></tr></thead><tbody>';
      apps.forEach(function (a) { h += '<tr><td>' + e(a.company) + '</td><td>' + e(a.role) + '</td><td>' + e(a.status) + '</td><td>' + e((a.applied_at||'').slice(0,10)) + '</td></tr>'; });
      h += '</tbody></table>';
    } else h += '<p class="jhai-dash-empty">No applications tracked yet.</p>';
    h += '</section><section><h3>Resume Versions</h3>';
    if (vers.length) {
      h += '<table class="jhai-dash-table"><thead><tr><th>Version</th><th>File</th><th>Uploaded</th></tr></thead><tbody>';
      vers.forEach(function (v) { h += '<tr><td>' + e(v.version) + '</td><td>' + e(v.filename) + '</td><td>' + e((v.uploaded_at||'').slice(0,10)) + '</td></tr>'; });
      h += '</tbody></table>';
    } else h += '<p class="jhai-dash-empty">No resume versions yet.</p>';
    h += '</section>';
    body.innerHTML = h;
  }

  function e(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  function setActive(which) {
    var d = document.getElementById('jhai-nav-dash');
    var c = document.getElementById('jhai-nav-chats');
    if (d) d.setAttribute('data-active', which === 'dash' ? 'true' : 'false');
    if (c) c.setAttribute('data-active', which === 'chats' ? 'true' : 'false');
  }

  // ── Inject: logo into sidebar header ──────────────────────────────────────
  // React does NOT remove unknown DOM children during reconciliation — it only
  // updates fiber-tracked nodes. So this element persists through re-renders.
  function injectLogo() {
    if (document.getElementById('jhai-sidebar-logo-wrap')) return;
    var header = document.querySelector('[data-sidebar="sidebar"]:not([data-mobile]) [data-sidebar="header"]');
    if (!header) return;
    var wrap = document.createElement('div');
    wrap.id = 'jhai-sidebar-logo-wrap';
    wrap.innerHTML = '<img id="jhai-sidebar-logo" src="' + LOGO + '" alt="jhai-logo" />';
    header.insertBefore(wrap, header.firstChild);
  }

  // ── Inject: nav between header and content ────────────────────────────────
  function injectNav() {
    if (document.getElementById('jhai-sidebar-nav')) return;
    var content = document.querySelector('[data-sidebar="sidebar"]:not([data-mobile]) [data-sidebar="content"]');
    if (!content) return;

    var nav = document.createElement('div');
    nav.id = 'jhai-sidebar-nav';
    nav.innerHTML = [
      '<button id="jhai-nav-dash" class="jhai-nav-item" data-active="false">',
      '<span class="jhai-nav-icon">' + ICON.dash + '</span><span>Dashboard</span></button>',
      '<button id="jhai-nav-chats" class="jhai-nav-item" data-active="true">',
      '<span class="jhai-nav-icon">' + ICON.chats + '</span><span>Chats</span></button>',
    ].join('');

    content.parentNode.insertBefore(nav, content);

    document.getElementById('jhai-nav-dash').addEventListener('click', function () {
      showDashboard();
      var c = document.querySelector('[data-sidebar="content"]');
      if (c) c.style.display = 'none';
    });
    document.getElementById('jhai-nav-chats').addEventListener('click', hideDashboard);
  }

  // ── Inject: user + logout footer ──────────────────────────────────────────
  function injectFooter() {
    if (document.getElementById('jhai-sidebar-bottom')) return;
    var sidebar = document.querySelector('[data-sidebar="sidebar"]:not([data-mobile])');
    if (!sidebar) return;

    var bar = document.createElement('div');
    bar.id = 'jhai-sidebar-bottom';
    bar.setAttribute('data-sidebar', 'footer');
    bar.innerHTML = [
      '<div class="jhai-sb-user">',
      '<div class="jhai-sb-avatar" id="jhai-avatar">?</div>',
      '<span class="jhai-sb-name" id="jhai-name">…</span></div>',
      '<div class="jhai-sb-actions">',
      '<button id="jhai-logout-btn" title="Log out">' + ICON.logout + '</button></div>',
    ].join('');
    sidebar.appendChild(bar);

    getUser(function (u) {
      var a = document.getElementById('jhai-avatar');
      var n = document.getElementById('jhai-name');
      if (a) a.textContent = u.name ? u.name[0].toUpperCase() : '?';
      if (n) n.textContent = u.name || 'Account';
    });
    document.getElementById('jhai-logout-btn').addEventListener('click', function () {
      fetch('/logout', { method: 'POST', credentials: 'include' })
        .finally(function () { window.location.href = '/'; });
    });
  }

  // ── Run all injections ────────────────────────────────────────────────────
  function runAll() {
    swapLogo();
    injectLogo();
    injectNav();
    injectFooter();
  }

  // ── MutationObserver: re-inject immediately if elements are missing ────────
  new MutationObserver(function () {
    // If any custom element is missing, re-inject right away (no debounce).
    // Body-level appends (dashboard overlay) are unaffected by React.
    // Sidebar-internal elements: React does NOT remove foreign DOM nodes during
    // reconciliation, but just in case — we react within one micro-task.
    var missing =
      !document.getElementById('jhai-sidebar-logo-wrap') ||
      !document.getElementById('jhai-sidebar-nav') ||
      !document.getElementById('jhai-sidebar-bottom');
    if (missing) runAll();
    else swapLogo(); // always keep logo src in sync with theme
  }).observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class'], // catch dark-mode class toggle
  });

  // ── Safety interval: re-inject even if MutationObserver missed something ──
  setInterval(runAll, 500);

  runAll();
})();
