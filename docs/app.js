const PAGE_SIZE = 25;
let currentPage = 1;
let totalPages = 1;

const contentEl = document.getElementById('content');
const paginationEl = document.getElementById('pagination');
const pageInfoEl = document.getElementById('pageInfo');
const btnPrev = document.getElementById('btnPrev');
const btnNext = document.getElementById('btnNext');
const dateFromEl = document.getElementById('dateFrom');
const dateToEl = document.getElementById('dateTo');

document.getElementById('btnFilter').addEventListener('click', () => { currentPage = 1; loadPage(); });
document.getElementById('btnClear').addEventListener('click', () => {
  dateFromEl.value = '';
  dateToEl.value = '';
  currentPage = 1;
  loadPage();
});
btnPrev.addEventListener('click', () => { if (currentPage > 1) { currentPage--; loadPage(); } });
btnNext.addEventListener('click', () => { if (currentPage < totalPages) { currentPage++; loadPage(); } });

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str ?? '';
  return div.innerHTML;
}

function formatTimestamp(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

async function tursoQuery(statements) {
  const url = CONFIG.TURSO_HTTP_URL.replace(/\/$/, '') + '/v2/pipeline';
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${CONFIG.TURSO_READ_ONLY_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      requests: [
        ...statements.map(stmt => ({ type: 'execute', stmt })),
        { type: 'close' },
      ],
    }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 300)}`);
  }

  const data = await res.json();
  const results = [];
  for (const r of data.results) {
    if (r.type === 'error') {
      throw new Error(r.error?.message || 'Nieznany błąd zapytania Turso');
    }
    if (r.response?.type === 'execute') {
      const { cols, rows } = r.response.result;
      const colNames = cols.map(c => c.name);
      const parsed = rows.map(row => {
        const obj = {};
        row.forEach((cell, i) => { obj[colNames[i]] = cell?.value ?? null; });
        return obj;
      });
      results.push(parsed);
    }
  }
  return results;
}

function buildDateArgs() {
  const from = dateFromEl.value ? `${dateFromEl.value}T00:00:00+00:00` : null;
  const to = dateToEl.value ? `${dateToEl.value}T23:59:59+00:00` : null;
  return { from, to };
}

function whereClause(from, to) {
  if (from && to) return 'WHERE first_seen_at >= ? AND first_seen_at <= ?';
  if (from) return 'WHERE first_seen_at >= ?';
  if (to) return 'WHERE first_seen_at <= ?';
  return '';
}

function argsFor(from, to) {
  const args = [];
  if (from) args.push({ type: 'text', value: from });
  if (to) args.push({ type: 'text', value: to });
  return args;
}

async function loadPage() {
  if (!CONFIG.TURSO_HTTP_URL || CONFIG.TURSO_HTTP_URL.startsWith('WKLEJ')) {
    renderConfigError();
    return;
  }

  contentEl.innerHTML = '<div class="state-msg">Wczytywanie…</div>';
  paginationEl.style.display = 'none';

  const { from, to } = buildDateArgs();
  const where = whereClause(from, to);
  const args = argsFor(from, to);
  const offset = (currentPage - 1) * PAGE_SIZE;

  const countSql = `SELECT COUNT(*) AS total FROM utrudnienia ${where}`;
  const listSql = `
    SELECT linie, utrudnienie, zmiana_sytuacji, first_seen_at, last_seen_at, active, disappeared_at
    FROM utrudnienia
    ${where}
    ORDER BY first_seen_at DESC
    LIMIT ${PAGE_SIZE} OFFSET ${offset}
  `;

  try {
    const [countRows, listRows] = await tursoQuery([
      { sql: countSql, args },
      { sql: listSql, args },
    ]);

    const total = Number(countRows[0]?.total ?? 0);
    totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    renderList(listRows, total);
    renderPagination();
  } catch (err) {
    renderFetchError(err);
  }
}

function renderList(rows, total) {
  if (rows.length === 0) {
    contentEl.innerHTML = `
      <div class="state-msg">
        <strong>Brak utrudnień w wybranym zakresie</strong>
        Spróbuj poszerzyć zakres dat albo wyczyść filtr.
      </div>`;
    return;
  }

  const items = rows.map(r => {
    const isActive = Number(r.active) === 1;
    const statusHtml = isActive
      ? '<span class="status-pill active">AKTYWNE</span>'
      : '<span class="status-pill closed">ZAMKNIĘTE</span>';

    return `
      <div class="entry">
        <div class="linia-badge">${escapeHtml(r.linie || '—')}</div>
        <div class="entry-body">
          <div class="entry-utrudnienie truncatable">${escapeHtml(r.utrudnienie)}</div>
          <span class="expand-hint">▾ pokaż więcej</span>
          ${r.zmiana_sytuacji ? `<div class="entry-zmiana truncatable">${escapeHtml(r.zmiana_sytuacji)}</div><span class="expand-hint">▾ pokaż więcej</span>` : ''}
          <div class="entry-meta">zgłoszono ${formatTimestamp(r.first_seen_at)}${!isActive && r.disappeared_at ? ` · zamknięto ${formatTimestamp(r.disappeared_at)}` : ''}</div>
        </div>
        <div class="entry-side">${statusHtml}</div>
      </div>
    `;
  }).join('');

  contentEl.innerHTML = `<div class="list">${items}</div>`;
  requestAnimationFrame(setupTruncation);
}

function setupTruncation() {
  document.querySelectorAll('.truncatable').forEach(el => {
    const hint = el.nextElementSibling;
    if (!hint || !hint.classList.contains('expand-hint')) return;

    el.classList.remove('expanded', 'has-more');
    hint.textContent = '▾ pokaż więcej';

    if (el.scrollHeight > el.clientHeight + 1) {
      el.classList.add('has-more');
      const toggle = () => {
        const expanded = el.classList.toggle('expanded');
        hint.textContent = expanded ? '▴ zwiń' : '▾ pokaż więcej';
      };
      el.addEventListener('click', toggle);
      hint.addEventListener('click', toggle);
    }
  });
}

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(setupTruncation, 200);
});

function renderPagination() {
  paginationEl.style.display = 'flex';
  pageInfoEl.textContent = `Strona ${currentPage} z ${totalPages}`;
  btnPrev.disabled = currentPage <= 1;
  btnNext.disabled = currentPage >= totalPages;
}

function renderConfigError() {
  contentEl.innerHTML = `
    <div class="state-msg">
      <strong>Strona nie jest jeszcze skonfigurowana</strong>
      Uzupełnij TURSO_HTTP_URL i TURSO_READ_ONLY_TOKEN w sekcji CONFIG na początku pliku index.html.
    </div>`;
}

function renderFetchError(err) {
  contentEl.innerHTML = `
    <div class="state-msg">
      <strong>Nie udało się pobrać danych</strong>
      Sprawdź adres bazy i token w sekcji CONFIG, oraz czy token ma jeszcze ważność.
      <code>${escapeHtml(err.message || String(err))}</code>
    </div>`;
}

loadPage();
