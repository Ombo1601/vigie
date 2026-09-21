/* Progressive enhancement only. All source reading works without JavaScript. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const all = (selector) => [...document.querySelectorAll(selector)];
  const on = (el, event, fn) => { if (el && el.addEventListener) el.addEventListener(event, fn); };
  const KEY = 'vigie.resident.v1';
  const LKEY = 'vigie.lenses.v1';
  // Folding must match resident_brief.folded(): ligatures and typographic
  // apostrophes are mapped before diacritics are stripped, or searching
  // "oeuvre" would never match a stored "œuvre".
  // Python str.casefold() expands ß and the fi/fl ligatures; NFD + toLowerCase
  // does not. Map them first so a search for "strasse" or "fichier" hits the
  // stored form. The character class must list every key.
  const LIGATURES = {
    'œ': 'oe', 'Œ': 'oe', 'æ': 'ae', 'Æ': 'ae',
    '’': "'", '‘': "'", '`': "'", '´': "'",
    'ß': 'ss', 'ẞ': 'ss', 'ﬁ': 'fi', 'ﬂ': 'fl', 'ﬀ': 'ff', 'ﬃ': 'ffi', 'ﬄ': 'ffl',
  };
  const fold = (text) => String(text || '').replace(/[œŒæÆ’‘`´ßẞﬁﬂﬀﬃﬄ]/g, ch => LIGATURES[ch])
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const rows = all('.story');
  // Per-row refs and parsed facets are cached once: render() runs on every
  // interaction over 300+ rows and up to 1200 saved marks, so it must stay
  // linear with hash lookups — never a nested scan.
  const cards = rows.map(row => {
    const d = row.dataset;
    const h3 = row.querySelector('h3');
    return {
      row,
      id: d.id,
      geo: d.geo,
      areas: new Set(String(d.areas || '').split(' ')),
      topics: new Set(String(d.topics || '').split(' ')),
      search: String(d.search || ''),
      url: String(d.url || (row.querySelector('h3 a') || {}).href || ''),
      inDossier: d.inDossier === '1',
      official: d.official === '1',
      newLabel: row.querySelector('.new-label'),
      saveBtn: row.querySelector('[data-save]'),
      title: h3 ? h3.textContent.trim() : '',
    };
  });
  const idSet = new Set(cards.map(c => c.id));
  let view = 'brief', topic = 'all', limit = 12, timer, searchTimer, pinId = '';
  let state = { saved: [], seen: null, visited: null };
  let savedSet = new Set(), seenSet = null, newCount = 0;
  const syncSets = () => {
    savedSet = new Set(state.saved);
    seenSet = state.seen === null ? null : new Set(state.seen);
    newCount = seenSet === null ? 0 : cards.reduce((n, c) => n + (seenSet.has(c.id) ? 0 : 1), 0);
  };
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || 'null');
    const validIds = (ids) => Array.isArray(ids) ? [...new Set(ids.filter(x => typeof x === 'string' && /^[a-f0-9]{20}$/.test(x)))].slice(0, 1200) : [];
    if (raw && typeof raw === 'object') state = { saved: validIds(raw.saved), seen: Array.isArray(raw.seen) ? validIds(raw.seen) : null, visited: typeof raw.visited === 'string' && Number.isFinite(Date.parse(raw.visited)) ? raw.visited : null };
  } catch { /* Reading remains usable when browser storage is unavailable. */ }
  syncSets();
  let lenses = { titleOnly: false, focus: false, muted: [] };
  try {
    const raw = JSON.parse(localStorage.getItem(LKEY) || 'null');
    if (raw && typeof raw === 'object') {
      const muted = Array.isArray(raw.muted)
        ? [...new Set(raw.muted.filter(x => typeof x === 'string').map(x => fold(x.trim())).filter(x => x.length >= 2 && x.length <= 40))].slice(0, 20)
        : [];
      lenses = { titleOnly: raw.titleOnly === true, focus: raw.focus === true, muted };
    }
  } catch { /* Lenses stay at defaults when storage is unreadable. */ }
  const persistLenses = () => { try { localStorage.setItem(LKEY, JSON.stringify(lenses)); } catch { /* ignore */ } };
  const drawMute = () => {
    const list = $('#mute-list');
    if (!list) return;
    list.textContent = '';
    list.hidden = lenses.muted.length === 0;
    lenses.muted.forEach(token => {
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = token + ' ×';
      btn.setAttribute('aria-label', 'Ne plus masquer : ' + token);
      btn.addEventListener('click', () => {
        lenses.muted = lenses.muted.filter(m => m !== token);
        persistLenses(); drawMute(); render();
      });
      li.appendChild(btn);
      list.appendChild(li);
    });
  };
  const toastEl = $('#toast');
  const toast = (text) => { toastEl.textContent = text; clearTimeout(timer); timer = setTimeout(() => { toastEl.textContent = ''; }, 5000); };
  const persist = () => {
    try { localStorage.setItem(KEY, JSON.stringify(state)); return true; }
    catch { toast('Stockage indisponible : vos repères resteront seulement pendant cette visite.'); return false; }
  };
  const dateText = (value) => new Intl.DateTimeFormat('fr-CA', { timeZone:'America/Toronto', day:'numeric', month:'short', hour:'2-digit', minute:'2-digit' }).format(new Date(value));
  all('time[datetime]').forEach(el => { if (Number.isFinite(Date.parse(el.dateTime))) { el.textContent = dateText(el.dateTime); el.title = 'Heure de Québec · ' + el.dateTime; } });
  const freshnessEl = $('#freshness-label');
  const freshness = () => {
    const label = freshnessEl;
    if (!label) return;
    const date = Date.parse(label.dataset.fetched);
    const age = Date.now() - date;
    const stale = !Number.isFinite(date) || age > 6 * 3600 * 1000 || age < -300000;
    label.classList.toggle('warning', stale || label.dataset.partial === 'true');
    label.textContent = !Number(label.dataset.total) ? 'État des sources inconnu' : !Number(label.dataset.ok) ? 'Collecte indisponible' : stale ? 'Collecte à actualiser' : label.dataset.partial === 'true' ? 'Collecte partielle' : 'Dernière collecte';
  };
  freshness();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) freshness(); });
  setInterval(freshness, 60000);
  const isNew = card => seenSet !== null && !seenSet.has(card.id);
  const searchEl = $('#search'), scopeEl = $('#scope'), areaEl = $('#area');
  const resultCountEl = $('#result-count'), noResultsEl = $('#no-results'), showMoreEl = $('#show-more');
  const endNoteEl = $('#end-note'), savedCountEl = $('#saved-count'), newCountEl = $('#new-count'), visitStatusEl = $('#visit-status');
  const visitListEl = $('#visit-list');
  const viewBtns = all('[data-view]'), topicBtns = all('[data-topic]');
  // Progressive disclosure: the secondary theme chips live behind one toggle.
  const moreTopicsBtn = $('#more-topics'), moreTopicsList = $('#more-topics-list');
  const secondaryTopicKeys = moreTopicsList ? all('#more-topics-list [data-topic]').map(b => b.dataset.topic) : [];
  const setMoreTopics = (open) => {
    if (!moreTopicsBtn || !moreTopicsList) return;
    moreTopicsList.hidden = !open;
    moreTopicsBtn.setAttribute('aria-expanded', String(open));
    moreTopicsBtn.textContent = open ? 'Moins de thèmes' : 'Plus de thèmes';
  };
  on(moreTopicsBtn, 'click', () => setMoreTopics(moreTopicsList && moreTopicsList.hidden));
  function render() {
    const terms = fold(searchEl.value.trim()).split(/\s+/).filter(Boolean);
    const scope = scopeEl.value, area = areaEl.value;
    const matches = cards.filter(card => {
      const scopeMatch = scope === 'all' || card.geo === 'quebec-city' || (scope === 'province' && card.geo === 'quebec');
      return scopeMatch && (area === 'all' || card.areas.has(area))
        && (topic === 'all' || card.topics.has(topic))
        && terms.every(t => card.search.includes(t))
        && (view !== 'saved' || savedSet.has(card.id)) && (view !== 'new' || isNew(card))
        && (card.id === pinId || ((!lenses.focus || card.inDossier || card.official)
        && !lenses.muted.some(m => m && card.search.includes(m))));
    });
    const visible = new Set(matches.slice(0, limit));
    if (pinId) {
      const pinned = cards.find(c => c.id === pinId);
      if (pinned) visible.add(pinned);
      pinId = '';
    }
    cards.forEach(card => {
      card.row.hidden = !visible.has(card);
      card.newLabel.hidden = !isNew(card);
      const saved = savedSet.has(card.id);
      card.saveBtn.setAttribute('aria-pressed', String(saved));
      card.saveBtn.textContent = saved ? 'Gardé ✓' : 'Garder ＋';
      // Keep the visible label inside the accessible name (WCAG 2.5.3): a
      // voice-control user who says "Gardé" must be able to target it.
      card.saveBtn.setAttribute('aria-label', (saved ? 'Gardé ✓ — retirer : ' : 'Garder : ') + card.title);
    });
    viewBtns.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.view === view)));
    topicBtns.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.topic === topic)));
    if (secondaryTopicKeys.includes(topic)) setMoreTopics(true);
    resultCountEl.textContent = `${Math.min(limit, matches.length)} sur ${matches.length} articles dans cette vue`;
    noResultsEl.hidden = matches.length > 0;
    showMoreEl.hidden = matches.length <= limit;
    endNoteEl.textContent = matches.length > limit ? 'Un premier point. La suite, si vous en avez besoin.' : matches.length ? 'Vous avez fait le tour de cette sélection.' : 'Élargissez votre regard.';
    savedCountEl.textContent = state.saved.length ? `(${state.saved.length})` : '';
    newCountEl.textContent = state.seen !== null ? `(${newCount})` : '';
    const missingSaved = state.saved.reduce((n, id) => n + (idSet.has(id) ? 0 : 1), 0);
    visitStatusEl.textContent = view === 'saved' && missingSaved
      ? `${missingSaved} article(s) gardé(s) ne figurent plus dans cette collecte. Les repères sont conservés.`
      : state.visited ? `${newCount} article(s) apparu(s) dans les flux depuis votre repère du ${dateText(state.visited)}. Ce n’est pas un suivi des modifications.`
      : 'Mémorisez votre point de lecture pour voir les nouveaux articles à votre prochaine visite.';
    if (view === 'new' && state.seen === null) visitStatusEl.textContent = 'Créez d’abord un repère avec « Mémoriser ce point de lecture ».';
    document.body.classList.toggle('lens-title-only', lenses.titleOnly);
    const titleBtn = $('#lens-title'), focusBtn = $('#lens-focus');
    if (titleBtn) titleBtn.setAttribute('aria-pressed', String(lenses.titleOnly));
    if (focusBtn) focusBtn.setAttribute('aria-pressed', String(lenses.focus));
    // Continuity (on-device only): name the newest articles since the marker,
    // so "what appeared since you left" is concrete, not just a number.
    if (visitListEl) {
      const fresh = state.seen === null ? [] : cards.filter(c => !seenSet.has(c.id));
      const shown = fresh.slice(0, 4);
      visitListEl.hidden = shown.length === 0;
      visitListEl.textContent = '';
      shown.forEach(c => {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.href = '#article-' + c.id;
        a.textContent = c.title.replace(/\s*↗\s*$/, '');
        li.appendChild(a);
        visitListEl.appendChild(li);
      });
      if (fresh.length > shown.length) {
        const li = document.createElement('li');
        li.className = 'visit-more';
        li.textContent = `et ${fresh.length - shown.length} autre(s), dans la vue « Depuis mon repère ».`;
        visitListEl.appendChild(li);
      }
    }
  }
  function reset() { topic = 'all'; view = 'brief'; limit = 12; searchEl.value = ''; scopeEl.value = 'local'; areaEl.value = 'all'; render(); }
  viewBtns.forEach(b => on(b, 'click', () => { view = b.dataset.view; limit = 12; if (view === 'saved' || view === 'new') { scopeEl.value = 'all'; areaEl.value = 'all'; searchEl.value = ''; topic = 'all'; } else { scopeEl.value = 'local'; } render(); }));
  topicBtns.forEach(b => on(b, 'click', () => { topic = b.dataset.topic; limit = 12; render(); }));
  // Debounced: filtering every row per keystroke wastes battery on phones;
  // 150 ms still feels instant and the final render is always correct.
  on(searchEl, 'input', () => { limit = 12; clearTimeout(searchTimer); searchTimer = setTimeout(render, 150); });
  [scopeEl, areaEl].forEach(el => on(el, 'change', () => { limit = 12; render(); }));
  ['#reset-filters', '#empty-reset'].forEach(s => on($(s), 'click', reset));
  on(showMoreEl, 'click', () => { limit += 12; render(); });
  on($('#lens-title'), 'click', () => { lenses.titleOnly = !lenses.titleOnly; persistLenses(); render(); });
  on($('#lens-focus'), 'click', () => { lenses.focus = !lenses.focus; persistLenses(); render(); });
  const muteAdd = $('#mute-add');
  const addMute = () => {
    if (!muteAdd) return;
    const token = fold(muteAdd.value.trim());
    if (token.length < 2 || token.length > 40) return;
    if (!lenses.muted.includes(token) && lenses.muted.length < 20) lenses.muted.push(token);
    muteAdd.value = '';
    persistLenses(); drawMute(); render();
  };
  on(muteAdd, 'keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addMute(); } });
  const sortDossiers = () => {
    const mode = (($('#dossier-sort') || {}).value) || 'official';
    all('.dossier').forEach(d => {
      ['ol.dossier-headlines', 'ul.dossier-sources'].forEach(sel => {
        const list = d.querySelector(sel);
        if (!list) return;
        const items = [...list.children];
        items.sort((a, b) => {
          if (mode === 'date') return String(b.dataset.date || '').localeCompare(String(a.dataset.date || ''));
          if (mode === 'nest') return Number(a.dataset.nestRank || 9) - Number(b.dataset.nestRank || 9);
          const ao = Number(a.dataset.official || 0), bo = Number(b.dataset.official || 0);
          return (bo - ao) || String(b.dataset.date || '').localeCompare(String(a.dataset.date || ''));
        });
        items.forEach(li => list.appendChild(li));
      });
    });
  };
  on($('#dossier-sort'), 'change', sortDossiers);
  on($('#dossier-find'), 'input', () => {
    const q = fold((($('#dossier-find') || {}).value || '').trim());
    all('.dossier').forEach(d => {
      d.hidden = Boolean(q) && !String(d.dataset.search || '').includes(q);
    });
  });
  drawMute();
  all('[data-save]').forEach(b => on(b, 'click', () => {
    const id = b.dataset.save;
    const wasSaved = savedSet.has(id);
    if (wasSaved) state.saved = state.saved.filter(x => x !== id);
    else { if (state.saved.length >= 1200) { toast('La limite de 1 200 repères est atteinte. Retirez-en pour en garder de nouveaux.'); return; } state.saved.push(id); }
    syncSets(); persist(); render();
    // Un-saving inside the saved view hides the row under the focused button;
    // move focus to a stable status line instead of dropping it to <body>.
    if (view === 'saved' && wasSaved && document.activeElement === document.body) {
      visitStatusEl.setAttribute('tabindex', '-1');
      visitStatusEl.focus();
    }
  }));
  on($('#remember'), 'click', () => {
    state.seen = cards.map(c => c.id).slice(0, 1200); state.visited = new Date().toISOString();
    syncSets();
    const stored = persist(); render(); if (stored) toast('Point de lecture mémorisé sur cet appareil.');
  });
  on($('#clear-local'), 'click', () => {
    let cleared = true;
    try { [KEY, LKEY, 'vigie_facets_v1', 'vigie_visit_v1', 'vigie.corridors.v1'].forEach(k => localStorage.removeItem(k)); } catch { cleared = false; }
    state = { saved: [], seen: null, visited: null }; syncSets();
    lenses = { titleOnly: false, focus: false, muted: [] };
    drawMute(); reset();
    const ps = $('#privacy-status');
    if (ps) ps.textContent = cleared ? 'Vos repères Vigie ont été effacés de cet appareil.' : 'L’accès au stockage est bloqué. Les repères de cette visite ont été effacés.';
  });
  // Hash links to disclosures must open the disclosure, including direct URLs.
  const revealHash = () => { if (location.hash === '#couverture' && $('#couverture')) $('#couverture').open = true; };
  window.addEventListener('hashchange', revealHash); revealHash();
  // Command palette (Ctrl/Cmd-K) + section scout. Progressive: without JS the
  // page stays fully readable and the masthead links still work.
  const cmdk = $('#cmdk'), cmdkInput = $('#cmdk-input'), cmdkList = $('#cmdk-list'), cmdkOpenBtn = $('#cmdk-open');
  const navLinks = all('.masthead nav a');
  const sections = navLinks.map(a => ({ label: a.textContent.trim(), target: a.getAttribute('href') })).filter(s => s.target);
  const seenTargets = new Set(sections.map(s => s.target));
  all('[data-cmdk][id]').forEach(el => {
    const target = '#' + el.id;
    const label = (el.getAttribute('data-cmdk') || '').trim();
    if (label && !seenTargets.has(target)) {
      sections.push({ label, target });
      seenTargets.add(target);
    }
  });
  const dossierCards = all('.dossier').map(el => ({
    id: el.id,
    search: String(el.dataset.search || ''),
    title: ((el.querySelector('.dossier-q') || {}).textContent || 'Dossier').trim(),
    urls: [...el.querySelectorAll('[data-url], a[href]')].map(node => node.dataset.url || node.href).filter(Boolean),
  }));
  const looksLikeUrl = q => {
    const t = String(q || '').trim();
    return /^https?:\/\//i.test(t) || /^[a-z0-9.-]+\.[a-z]{2,}([/:?#].*)?$/i.test(t);
  };
  const canonUrl = raw => {
    try {
      const t = String(raw || '').trim();
      const u = new URL(/^[a-z][a-z0-9+.-]*:/i.test(t) ? t : 'https://' + t);
      if (u.protocol !== 'http:' && u.protocol !== 'https:') return '';
      const host = u.hostname.replace(/^www\./i, '').toLowerCase();
      const path = (u.pathname.replace(/\/+$/, '') || '/').toLowerCase();
      const params = new URLSearchParams(u.search);
      [...params.keys()].filter(k => /^utm_/i.test(k) || k === 'fbclid' || k === 'gclid').forEach(k => params.delete(k));
      const query = params.toString();
      return host + path + (query ? '?' + query : '');
    } catch { return ''; }
  };
  let cmdkItems = [], cmdkActive = 0, cmdkOpener = null;
  const cmdkDraw = () => {
    const query = cmdkInput ? cmdkInput.value.trim() : '';
    const hits = [];
    if (looksLikeUrl(query)) {
      const needle = canonUrl(query);
      let found = false;
      if (needle) {
        for (const c of cards) {
          if (canonUrl(c.url) === needle) {
            hits.push({ kind: 'URL', title: c.title.replace(/\s*↗\s*$/, ''), target: '', id: c.id });
            found = true;
            break;
          }
        }
        if (!found) {
          for (const d of dossierCards) {
            if (d.urls.some(u => canonUrl(u) === needle)) {
              hits.push({ kind: 'Dossier', title: d.title, target: '#' + d.id, id: '' });
              found = true;
              break;
            }
          }
        }
      }
      cmdkItems = hits.slice(0, 9);
      if (cmdkActive >= cmdkItems.length) cmdkActive = 0;
      cmdkList.textContent = '';
      if (!cmdkItems.length) {
        const empty = document.createElement('li');
        empty.className = 'cmdk-empty';
        empty.textContent = 'Cette URL n’est pas dans cette édition.';
        cmdkList.appendChild(empty);
        if (cmdkInput) cmdkInput.setAttribute('aria-activedescendant', '');
        return;
      }
    } else {
      const terms = fold(query).split(/\s+/).filter(Boolean);
      sections.forEach(s => {
        if (!terms.length || terms.every(t => fold(s.label).includes(t))) hits.push({ kind: 'Section', title: s.label, target: s.target, id: '' });
      });
      if (terms.length) {
        for (const d of dossierCards) {
          if (hits.length >= 10) break;
          if (terms.every(t => d.search.includes(t) || fold(d.title).includes(t))) {
            hits.push({ kind: 'Dossier', title: d.title, target: '#' + d.id, id: '' });
          }
        }
        for (const c of cards) {
          if (hits.length >= 10) break;
          if (terms.every(t => c.search.includes(t))) hits.push({ kind: 'Article', title: c.title.replace(/\s*↗\s*$/, ''), target: '', id: c.id });
        }
      }
      cmdkItems = hits.slice(0, 9);
      if (cmdkActive >= cmdkItems.length) cmdkActive = 0;
      cmdkList.textContent = '';
      if (!cmdkItems.length) {
        const empty = document.createElement('li');
        empty.className = 'cmdk-empty';
        empty.textContent = terms.length ? 'Aucun résultat. Élargissez le terme.' : 'Tapez pour chercher un article, coller une URL, ou une section.';
        cmdkList.appendChild(empty);
        if (cmdkInput) cmdkInput.setAttribute('aria-activedescendant', '');
        return;
      }
    }
    cmdkItems.forEach((it, i) => {
      const li = document.createElement('li');
      li.className = 'cmdk-opt';
      li.id = 'cmdk-opt-' + i;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', String(i === cmdkActive));
      const kind = document.createElement('span'); kind.className = 'cmdk-kind'; kind.textContent = it.kind;
      const title = document.createElement('span'); title.className = 'cmdk-title'; title.textContent = it.title;
      li.append(kind, title);
      li.addEventListener('mousedown', e => { e.preventDefault(); cmdkChoose(i); });
      cmdkList.appendChild(li);
    });
    if (cmdkInput) cmdkInput.setAttribute('aria-activedescendant', 'cmdk-opt-' + cmdkActive);
  };
  const cmdkClose = () => {
    if (!cmdk) return;
    cmdk.hidden = true;
    if (cmdkOpener && cmdkOpener.focus) cmdkOpener.focus();
    cmdkOpener = null;
  };
  const cmdkChoose = i => {
    const it = cmdkItems[i];
    if (!it) return;
    cmdkClose();
    const target = String(it.target || '');
    // A masthead link may point at another page (e.g. /registre.html): that is
    // a navigation, not a fragment, and querySelector on a path throws.
    if (!it.id && target && target.charAt(0) !== '#') {
      location.href = target;
      return;
    }
    if (it.id) {
      // Reach the article whatever the current filters, mute or focus are.
      // pinId unhides that one card for this render; lenses stay as the
      // reader left them and never rewrite the public rank.
      pinId = it.id;
      view = 'brief'; topic = 'all'; scopeEl.value = 'all'; areaEl.value = 'all'; searchEl.value = ''; limit = cards.length;
      render();
    }
    if (target.indexOf('#dossier') === 0) {
      const find = $('#dossier-find');
      if (find) find.value = '';
      all('.dossier').forEach(d => { d.hidden = false; });
    }
    const selector = it.id ? '#article-' + it.id : (target.charAt(0) === '#' ? target : '');
    const node = selector ? document.querySelector(selector) : null;
    if (node) {
      node.scrollIntoView({ behavior: 'smooth', block: 'start' });
      node.setAttribute('tabindex', '-1');
      node.focus();
    } else if (target) {
      location.hash = target;
    }
  };
  const cmdkOpen = opener => {
    if (!cmdk) return;
    cmdkOpener = opener || document.activeElement;
    cmdk.hidden = false;
    cmdkActive = 0;
    if (cmdkInput) { cmdkInput.value = ''; cmdkInput.focus(); }
    cmdkDraw();
  };
  if (cmdk && cmdkOpenBtn) on(cmdkOpenBtn, 'click', () => cmdkOpen(cmdkOpenBtn));
  if (cmdk) {
    on(document, 'keydown', e => {
      const key = (e.key || '').toLowerCase();
      if ((e.metaKey || e.ctrlKey) && key === 'k') {
        e.preventDefault();
        if (cmdk.hidden) cmdkOpen(document.activeElement); else cmdkClose();
        return;
      }
      if (cmdk.hidden) return;
      if (e.key === 'Escape') { e.preventDefault(); cmdkClose(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); cmdkActive = Math.min(cmdkActive + 1, cmdkItems.length - 1); cmdkDraw(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); cmdkActive = Math.max(cmdkActive - 1, 0); cmdkDraw(); }
      else if (e.key === 'Enter') { e.preventDefault(); cmdkChoose(cmdkActive); }
    });
    on(cmdkInput, 'input', () => { cmdkActive = 0; cmdkDraw(); });
    on(cmdk, 'mousedown', e => { if (e.target === cmdk) cmdkClose(); });
  }
  // Section scout: highlight the section you are actually reading.
  const spyIds = ['essentiel', 'travaux', 'participation', 'changements', 'dossiers', 'agir', 'methode'];
  if ('IntersectionObserver' in window && navLinks.length) {
    const byHref = new Map(navLinks.map(a => [a.getAttribute('href'), a]));
    const spy = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const link = byHref.get('#' + entry.target.id);
        if (!link) return;
        navLinks.forEach(a => a.removeAttribute('aria-current'));
        link.setAttribute('aria-current', 'true');
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    spyIds.map(id => document.getElementById(id)).filter(Boolean).forEach(node => spy.observe(node));
  }
  // Saved corridors (Travaux): opt-in, on-device, literal street matching only.
  const CKEY = 'vigie.corridors.v1';
  const corridorBox = $('#rw-corridors');
  if (corridorBox) {
    const island = $('#vigie-streets');
    const byKey = new Map();
    const names = [];
    try {
      const doc = JSON.parse(island ? island.textContent : 'null');
      const rows = doc && Array.isArray(doc.streets) ? doc.streets : [];
      rows.forEach(r => {
        if (r && typeof r.key === 'string' && typeof r.name === 'string') {
          byKey.set(r.key, { name: r.name, n: Number(r.n) || 0 });
          names.push(r.name);
        }
      });
    } catch { /* unreadable island: the control stays unavailable */ }
    if (byKey.size) {
      const input = $('#rw-corridor-input'), addBtn = $('#rw-corridor-add');
      const list = $('#rw-corridor-list'), status = $('#rw-corridor-status');
      const datalist = $('#rw-street-options');
      if (datalist) {
        names.slice(0, 400).forEach(name => {
          const opt = document.createElement('option'); opt.value = name; datalist.appendChild(opt);
        });
      }
      let corridors = [];
      try {
        const raw = JSON.parse(localStorage.getItem(CKEY) || '[]');
        corridors = Array.isArray(raw)
          ? [...new Set(raw.filter(x => typeof x === 'string' && x.trim()))].slice(0, 12)
          : [];
      } catch { corridors = []; }
      const keyOf = name => fold(name).replace(/\s+/g, ' ').trim();
      const persist = () => { try { localStorage.setItem(CKEY, JSON.stringify(corridors)); } catch { /* ignore */ } };
      const mark = () => {
        const keys = new Set(corridors.map(keyOf));
        all('#travaux .rw-item').forEach(item => {
          const roads = String(item.dataset.roads || '').split(' ').filter(Boolean);
          item.classList.toggle('rw-hit', keys.size > 0 && roads.some(r => keys.has(r)));
        });
      };
      const draw = () => {
        corridorBox.hidden = false;
        list.textContent = '';
        corridors.forEach((name, i) => {
          const hit = byKey.get(keyOf(name));
          const n = hit ? hit.n : 0;
          const li = document.createElement('li');
          li.className = 'rw-corridor';
          const label = document.createElement('span'); label.className = 'rw-corridor-name'; label.textContent = name;
          const count = document.createElement('span'); count.className = 'rw-corridor-count';
          count.textContent = n === 0
            ? 'aucune entrave déclarée'
            : n + ' entrave' + (n !== 1 ? 's' : '') + ' déclarée' + (n !== 1 ? 's' : '');
          const rm = document.createElement('button'); rm.type = 'button'; rm.className = 'rw-corridor-remove';
          rm.textContent = 'Retirer'; rm.setAttribute('aria-label', 'Retirer ' + name);
          rm.addEventListener('click', () => { corridors.splice(i, 1); persist(); draw(); });
          li.append(label, count, rm);
          list.appendChild(li);
        });
        if (status) {
          status.textContent = corridors.length
            ? 'Correspondance littérale sur le nom déclaré. Sur cet appareil seulement.'
            : 'Aucun corridor suivi. Les rues proposées sont celles déclarées par la Ville.';
        }
        mark();
      };
      const add = () => {
        const raw = (input && input.value || '').trim();
        if (!raw) return;
        const key = keyOf(raw);
        if (!byKey.has(key)) {
          if (status) status.textContent = 'Cette rue n’est pas déclarée dans la collecte actuelle.';
          return;
        }
        const display = byKey.get(key).name;
        if (!corridors.some(c => keyOf(c) === key)) {
          if (corridors.length >= 12) { if (status) status.textContent = 'Douze corridors au maximum.'; return; }
          corridors.push(display); persist();
        }
        if (input) input.value = '';
        draw();
      };
      if (addBtn) on(addBtn, 'click', add);
      on(input, 'keydown', e => { if (e.key === 'Enter') { e.preventDefault(); add(); } });
      draw();
    }
  }
  // Reveal controls first, then render: a missing shell element in a future
  // edition must leave the static article reading intact, never a dead page.
  document.documentElement.classList.add('js');
  try { render(); } catch { /* static reading remains intact */ }
})();
