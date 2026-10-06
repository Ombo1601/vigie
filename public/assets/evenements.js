/* Vigie — event pages: progressive enhancement only (ES2017, no dependency).
   Every page is complete and readable without this script. It never sends
   anything anywhere: no network call, no cookie, no tracking. It does four
   small things on the reader's device:
     1. shared words: each chip becomes a toggle that highlights the word in
        the voices (accent- and case-insensitive, same fold as the server);
     2. "Chez moi": street / neighbourhood matching against the page itself;
        the typed names are kept, optionally, in this browser's localStorage
        under the key the departure screen already uses (vigie.corridors.v1),
        inside try/catch: a blocked store only means nothing is remembered;
     3. timeline dot -> voice: smooth scroll, focus and a short flash (the
        dots are real links, so the jump also works without this script);
     4. relative age ("3 h ago") next to the roadworks collection time.
   Texts come from the JSON island #vigie-i18n written by the server. */
(function () {
  'use strict';
  var doc = document;
  var KEY = 'vigie.corridors.v1';
  var S = {};
  try {
    var island = doc.getElementById('vigie-i18n');
    if (island) S = JSON.parse(island.textContent) || {};
  } catch (e) { S = {}; }
  var lang = String(doc.documentElement.lang || 'fr').slice(0, 2).toLowerCase();

  function text(key, vars) {
    var tpl = typeof S[key] === 'string' ? S[key] : '';
    return tpl.replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] !== undefined ? String(vars[k]) : m; });
  }
  function count(base, n) {
    var one = lang === 'fr' ? n < 2 : n === 1;
    return text(base + (one ? '.one' : '.other'), { n: n });
  }
  function reduced() {
    try { return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches); } catch (e) { return false; }
  }

  /* ---- fold: lower-case, ligatures and typographic apostrophes, no diacritics.
     Same key as scripts/composants.py fold() and depart.js. Returns the folded
     text with, for every folded unit, the span of the original character it
     came from, so a match can be mapped back for highlighting. */
  var LIG = { 'œ': 'oe', 'Œ': 'oe', 'æ': 'ae', 'Æ': 'ae', '’': "'", '‘': "'", '`': "'", '´': "'" };
  function foldOne(ch) {
    var s = LIG[ch] || ch;
    return s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
  }
  function fold(str) {
    var out = '';
    String(str || '').split('').forEach(function (ch) { out += foldOne(ch); });
    return out;
  }
  function foldMap(str) {
    var folded = '', starts = [], ends = [];
    var s = String(str || '');
    for (var i = 0; i < s.length; i++) {
      var f = foldOne(s.charAt(i));
      for (var j = 0; j < f.length; j++) { folded += f.charAt(j); starts.push(i); ends.push(i + 1); }
    }
    return { folded: folded, starts: starts, ends: ends };
  }
  function isAlnum(c) { return !!c && (/[0-9]/.test(c) || c.toLowerCase() !== c.toUpperCase()); }

  /* ---- 1. shared words ---- */
  function wordsInit() {
    var voices = doc.getElementById('voices');
    var chips = doc.querySelectorAll('.word[data-forms]');
    if (!voices || !chips.length) return;
    var targets = [];
    Array.prototype.forEach.call(voices.querySelectorAll('[data-hl]'), function (el) {
      targets.push({ el: el, text: el.textContent });
    });
    var active = [];
    function render() {
      var forms = [];
      active.forEach(function (btn) { (btn.getAttribute('data-forms') || '').split('|').forEach(function (f) { if (f) forms.push(fold(f)); }); });
      targets.forEach(function (t) {
        if (!forms.length) { if (t.el.firstChild !== null && t.el.children.length) t.el.textContent = t.text; return; }
        var m = foldMap(t.text), ranges = [];
        forms.forEach(function (f) {
          if (!f) return;
          var from = 0, at;
          while ((at = m.folded.indexOf(f, from)) !== -1) {
            var before = at > 0 ? m.folded.charAt(at - 1) : '';
            var after = at + f.length < m.folded.length ? m.folded.charAt(at + f.length) : '';
            if (!isAlnum(before) && !isAlnum(after)) ranges.push([m.starts[at], m.ends[at + f.length - 1]]);
            from = at + 1;
          }
        });
        ranges.sort(function (a, b) { return a[0] - b[0] || b[1] - a[1]; });
        var merged = [];
        ranges.forEach(function (r) {
          var last = merged[merged.length - 1];
          if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]); else merged.push([r[0], r[1]]);
        });
        var frag = doc.createDocumentFragment(), pos = 0;
        merged.forEach(function (r) {
          if (r[0] > pos) frag.appendChild(doc.createTextNode(t.text.slice(pos, r[0])));
          var mk = doc.createElement('mark');
          mk.appendChild(doc.createTextNode(t.text.slice(r[0], r[1])));
          frag.appendChild(mk);
          pos = r[1];
        });
        if (pos < t.text.length) frag.appendChild(doc.createTextNode(t.text.slice(pos)));
        t.el.textContent = '';
        t.el.appendChild(frag);
      });
    }
    Array.prototype.forEach.call(chips, function (span) {
      var btn = doc.createElement('button');
      btn.type = 'button';
      btn.className = span.className;
      btn.setAttribute('aria-pressed', 'false');
      btn.setAttribute('data-forms', span.getAttribute('data-forms') || '');
      if (span.title) btn.title = span.title;
      while (span.firstChild) btn.appendChild(span.firstChild);
      span.parentNode.replaceChild(btn, span);
      btn.addEventListener('click', function () {
        var i = active.indexOf(btn);
        if (i === -1) active.push(btn); else active.splice(i, 1);
        btn.setAttribute('aria-pressed', i === -1 ? 'true' : 'false');
        render();
      });
    });
  }

  /* ---- 2. Chez moi ---- */
  function readNames() {
    try {
      var raw = JSON.parse(localStorage.getItem(KEY) || '[]');
      return Array.isArray(raw) ? raw.filter(function (x) { return typeof x === 'string' && x.trim(); }) : [];
    } catch (e) { return []; }
  }
  function saveNames(names) {
    try { localStorage.setItem(KEY, JSON.stringify(names)); } catch (e) { /* not remembered: still works this visit */ }
  }
  function mineInit() {
    var root = doc.querySelector('[data-mine]');
    if (!root) return;
    var area = root.querySelector('.js-only');
    if (!area) return;
    area.hidden = false;
    var input = root.querySelector('#mine-in');
    var form = root.querySelector('form');
    var chipsBox = root.querySelector('[data-mine-chips]');
    var sugg = root.querySelector('[data-mine-sugg]');
    var status = root.querySelector('[data-mine-status]');
    var hitsBox = root.querySelector('[data-mine-hits]');
    var cards = Array.prototype.slice.call(doc.querySelectorAll('[data-mine-card]'));
    var roadList = doc.querySelector('[data-rw-list]');
    var roads = roadList ? Array.prototype.slice.call(roadList.querySelectorAll('[data-rw]')) : [];
    roads.forEach(function (li, i) { li.__i = i; });
    var cardText = cards.map(function (c) {
      var t = '';
      Array.prototype.forEach.call(c.querySelectorAll('[data-hl]'), function (el) { t += ' ' + el.textContent; });
      return fold(t);
    });
    var roadText = roads.map(function (li) { return fold(li.textContent); });
    var names = readNames();

    function matched(hay) {
      return names.filter(function (n) { var f = fold(n).trim(); return f && hay.indexOf(f) !== -1; });
    }
    function refresh() {
      chipsBox.textContent = '';
      names.forEach(function (n, i) {
        var chip = doc.createElement('span');
        chip.className = 'chip';
        chip.appendChild(doc.createTextNode(n));
        var rm = doc.createElement('button');
        rm.type = 'button';
        rm.setAttribute('data-act', 'rm');
        rm.setAttribute('data-i', String(i));
        rm.setAttribute('aria-label', text('remove', { x: n }));
        rm.appendChild(doc.createTextNode('✕'));
        chip.appendChild(rm);
        chipsBox.appendChild(chip);
      });
      Array.prototype.forEach.call(sugg.querySelectorAll('button'), function (b) {
        var v = fold(b.getAttribute('data-v') || '');
        b.parentNode.hidden = names.some(function (n) { return fold(n) === v; });
      });
      var evHits = [], roadHits = 0;
      cards.forEach(function (c, i) {
        var m = matched(cardText[i]);
        var old = c.querySelector('.chip.mine');
        if (old) old.parentNode.removeChild(old);
        c.classList.toggle('mine-hit', m.length > 0);
        if (m.length) {
          var chip = doc.createElement('span');
          chip.className = 'chip mine';
          chip.textContent = text('mine_in', { x: m.join(', ') });
          var top = c.querySelector('.ev-top');
          if (top) top.appendChild(chip);
          var a = c.querySelector('.ev-h a');
          if (a) evHits.push(a);
        }
      });
      roads.forEach(function (li, i) {
        var hit = matched(roadText[i]).length > 0;
        li.classList.toggle('hit', hit);
        if (hit) roadHits++;
      });
      if (roadList) {
        roads.slice().sort(function (a, b) {
          var ha = a.classList.contains('hit') ? 0 : 1, hb = b.classList.contains('hit') ? 0 : 1;
          return ha - hb || a.__i - b.__i;
        }).forEach(function (li) { roadList.appendChild(li); });
      }
      hitsBox.textContent = '';
      if (!names.length) { status.textContent = ''; return; }
      if (!evHits.length && !roadHits) { status.textContent = text('mine_none'); return; }
      status.textContent = count('res_events', evHits.length) + ' · ' + count('res_roads', roadHits);
      evHits.forEach(function (a) {
        var li = doc.createElement('li'), link = doc.createElement('a');
        link.href = a.getAttribute('href') || '#';
        link.setAttribute('lang', a.getAttribute('lang') || lang);
        link.textContent = a.textContent;
        li.appendChild(link);
        hitsBox.appendChild(li);
      });
    }
    function add(v) {
      v = String(v || '').trim();
      if (!v || names.some(function (n) { return fold(n) === fold(v); })) return;
      names.push(v);
      saveNames(names);
      refresh();
    }
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      add(input.value);
      input.value = '';
      input.focus();
    });
    root.addEventListener('click', function (e) {
      var el = e.target && e.target.closest ? e.target.closest('[data-act]') : null;
      if (!el) return;
      var act = el.getAttribute('data-act');
      if (act === 'add') add(el.getAttribute('data-v'));
      else if (act === 'rm') {
        names.splice(Number(el.getAttribute('data-i')), 1);
        saveNames(names);
        refresh();
      }
    });
    refresh();
    if (location.hash === '#chez-moi' && input) {
      try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
    }
  }

  /* ---- 3. timeline dot -> voice ---- */
  function jumpInit() {
    doc.addEventListener('click', function (e) {
      var a = e.target && e.target.closest ? e.target.closest('a[data-jump]') : null;
      if (!a) return;
      var target = doc.getElementById(a.getAttribute('data-jump'));
      if (!target) return;
      e.preventDefault();
      try { target.scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'center' }); } catch (err) { target.scrollIntoView(); }
      target.classList.remove('flash');
      void target.offsetWidth;
      target.classList.add('flash');
      try { target.focus({ preventScroll: true }); } catch (err) { /* focus is a courtesy */ }
      try { history.replaceState(null, '', '#' + target.id); } catch (err) { /* keep the page where it is */ }
    });
  }

  /* ---- 4. relative age of the roadworks collection ---- */
  function ageInit() {
    Array.prototype.forEach.call(doc.querySelectorAll('time[data-age]'), function (t) {
      var at = Date.parse(t.getAttribute('datetime') || '');
      var rel = t.parentNode ? t.parentNode.querySelector('[data-age-rel]') : null;
      if (isNaN(at) || !rel) return;
      var mins = Math.floor((Date.now() - at) / 60000);
      var out = '';
      if (mins < -5) out = '';
      else if (mins < 1) out = text('age_now');
      else if (mins < 60) out = text('age_min', { n: mins });
      else if (mins < 48 * 60) out = text('age_h', { n: Math.floor(mins / 60) });
      else out = text('age_d', { n: Math.floor(mins / 1440) });
      rel.textContent = out ? '· ' + out : '';
    });
  }

  function rulesInit() {
    var d = doc.querySelector('[data-rules]');
    try { if (d && window.matchMedia && !window.matchMedia('(min-width: 720px)').matches) d.open = false; } catch (e) { /* stays open */ }
  }

  function start() {
    [wordsInit, mineInit, jumpInit, ageInit, rulesInit].forEach(function (fn) {
      try { fn(); } catch (e) { /* an enhancement must never break the page */ }
    });
  }
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start); else start();
})();
