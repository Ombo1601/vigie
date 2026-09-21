/* Avant de partir — on-device corridors. Progressive enhancement only: the
   departure screen is complete and readable without JavaScript. No account, no
   position, no server round-trip; corridors stay in this browser's storage.
   Mirrors brief.js fold(): ligatures and typographic apostrophes first, then
   diacritics, then case — the same key the street island is built with. */
(function () {
  var KEY = 'vigie.corridors.v1';
  var box = document.getElementById('depart-corridors');
  if (!box) return;
  var island = document.getElementById('vigie-streets');
  var byKey = {};
  try {
    var doc = JSON.parse(island ? island.textContent : 'null');
    var rows = doc && Array.isArray(doc.streets) ? doc.streets : [];
    rows.forEach(function (r) {
      if (r && typeof r.key === 'string') {
        byKey[r.key] = { name: r.name, n: Number(r.n) || 0, severity: Number(r.severity), open: r.open === true, impact: String(r.impact_label || ''), until: String(r.until || '') };
      }
    });
  } catch (e) { /* unreadable island: counts stay unknown, marks stay off */ }
  // Same fold as brief.js / resident_brief.folded(), including ß and the
  // fi/fl ligatures that casefold expands and toLowerCase does not.
  var foldMap = {
    '\u0153': 'oe', '\u0152': 'oe', '\u00e6': 'ae', '\u00c6': 'ae',
    '\u2019': "'", '\u2018': "'", '`': "'", '\u00b4': "'",
    '\u00df': 'ss', '\u1e9e': 'ss', '\ufb01': 'fi', '\ufb02': 'fl',
    '\ufb00': 'ff', '\ufb03': 'ffi', '\ufb04': 'ffl'
  };
  var fold = function (text) {
    return String(text || '').replace(/[\u0153\u0152\u00e6\u00c6\u2019\u2018`\u00b4\u00df\u1e9e\ufb01\ufb02\ufb00\ufb03\ufb04]/g, function (ch) { return foldMap[ch]; })
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  };
  var keyOf = function (name) { return fold(name).replace(/\s+/g, ' ').trim(); };
  var list = document.getElementById('depart-corridor-list');
  var hint = document.getElementById('depart-hint');
  var status = document.getElementById('depart-corridor-status');
  var names = [];
  try {
    var raw = JSON.parse(localStorage.getItem(KEY) || '[]');
    names = Array.isArray(raw) ? raw.filter(function (x) { return typeof x === 'string' && x.trim(); }) : [];
  } catch (e) { names = []; }
  if (!names.length) {
    if (status) status.textContent = 'Aucune rue suivie pour l\u2019instant — elles restent sur cet appareil.';
    return;
  }
  if (hint) hint.hidden = true;
  var keys = {};
  names.forEach(function (name) {
    var key = keyOf(name);
    if (!key) return;
    keys[key] = true;
    var hit = byKey[key];
    var n = hit ? hit.n : 0;
    var li = document.createElement('li');
    li.className = 'depart-corridor';
    var label = document.createElement('span');
    label.className = 'depart-corridor-name';
    label.textContent = name;
    var count = document.createElement('span');
    count.className = 'depart-corridor-count';
    count.textContent = n === 0 ? 'aucune entrave déclarée' : n + ' entrave' + (n !== 1 ? 's' : '') + ' déclarée' + (n !== 1 ? 's' : '');
    li.append(label, count);
    if (n > 0 && hit) {
      var worst = document.createElement('span');
      worst.className = 'depart-corridor-worst';
      var sev = Number(hit.severity);
      if (hit.open === true) {
        worst.textContent = 'aucune restriction de voie active — travaux déclarés';
      } else if (!isFinite(sev) || sev >= 6) {
        // An unmapped vehicle_impact is not an open road: say the City did not
        // specify, never claim « aucune restriction ».
        worst.textContent = 'impact non précisé par la Ville — entrave déclarée';
      } else {
        worst.textContent = 'la plus restrictive : ' + hit.impact + (hit.until ? ' — jusqu\u2019au ' + hit.until : '');
      }
      li.appendChild(worst);
    }
    if (list) list.appendChild(li);
  });
  if (list) list.hidden = false;
  Array.prototype.forEach.call(document.querySelectorAll('.depart-item[data-roads]'), function (item) {
    var roads = String(item.getAttribute('data-roads') || '').split(' ').filter(Boolean);
    if (roads.some(function (r) { return keys[r]; })) {
      item.classList.add('depart-hit');
      var tag = document.createElement('span');
      tag.className = 'depart-hit-tag';
      tag.textContent = 'votre rue';
      item.appendChild(tag);
    }
  });
})();
