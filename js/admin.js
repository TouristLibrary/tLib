// Version 3.16 - 24.06.2026
// Admin Dashboard JS для TlibWebApp
// Описание: Аутентификация через Magic Link + цифровой код + управление правами администраторов.
//           Неавторизованные видят только хедер с формой входа и общим статусом.
//           Авторизованные админы видят все 5 панелей + секции управления (6 — права, 7 — пользователи).
// Изменения v3.1: гибридная авторизация — поле кода в header-форме.
// Изменения v3.3: настройка времени дайджеста и тестовое письмо.
// Изменения v3.4: переведён на authService; admin.html -> type=module.
// Изменения v3.5: убран no-op 'use strict' (ESM-модуль — strict включён всегда).
// Изменения v3.6: экранирование данных из БД/логов через escapeHtml (renderGrowth/Disks/Security/Traffic/AdminList).
// Изменения v3.7: кнопка «Выйти» заменена dropdown-меню (выход / выход со всех устройств).
// Изменения v3.8: плитка Email Quota в renderSecurity (event_type=EMAIL_QUOTA, level=medium).
// Изменения v3.9: панель 7 — список пользователей, сессии, блокировка/разблокировка, удаление.
// Изменения v3.10: обработка 401/403 в loadUsers/loadSessions (showAuthForm, как в loadData).
// Изменения v3.11: удалена кнопка «Удалить» (только CLI); заблокированные в отдельной таблице.
// Изменения v3.12: кнопка «Выйти» в таблице сессий; текущая сессия защищена (серая кнопка + 403).
// Изменения v3.13: убран столбец «Статус» из renderUsers (избыточен после разделения на две таблицы).
// Изменения v3.14: активные делятся на «входившие» (сортировка по last_login_at DESC) и «без входов»; колонка «Последний вход».
// Изменения v3.15: панель 7 — единые таблицы: Email·Имя·IP·Последний вход + кнопки Заблокировать/Разблокировать и Выход.
//   Убрана отдельная таблица «Активные сессии»; добавлен last_login_ip; кнопка «Выход» завершает сессии пользователя
//   (для своей строки — все, кроме текущей); is_root/is_self делают «Заблокировать» серой;
//   «без входов» спрятаны под <details>.
// Изменения v3.16: убран висячий вызов loadSessions() в _userAction() (функция удалена в v3.15, вызов не удалили).

import { getCurrentUser, requestLink, verifyCode as authVerifyCode, logout as authLogout } from './services/authService.js';
import { escapeHtml } from './utils/sanitize.js';
import { attachLogoutMenu } from './modules/logoutMenu.js';

const API_STATUS           = '/api/admin/status';
const API_HEALTH           = '/api/admin/health-brief';
const API_ADMINS           = '/api/admin/admins';
const API_GRANT            = '/api/admin/grant';
const API_REVOKE           = '/api/admin/revoke';
const API_PAUSE            = '/api/admin/pause';
const API_REINDEX          = '/api/admin/reindex';
const API_REINDEX_ST       = '/api/admin/reindex-status';
const API_SETTINGS         = '/api/admin/settings';
const API_TEST_EMAIL       = '/api/admin/test-email';
const API_USERS            = '/api/admin/users';
const API_USER_ACTIVATE    = '/api/admin/users/activate';
const API_USER_DEACTIVATE  = '/api/admin/users/deactivate';
const API_USER_LOGOUT      = '/api/admin/users/logout';

// ---- helpers ---------------------------------------------------------------

function fmtSize(mb) {
  if (mb == null) return '—';
  if (mb >= 1024) return Math.round(mb / 1024) + ' ГБ';
  if (mb >= 1)    return Math.round(mb) + ' МБ';
  return Math.round(mb * 1024) + ' КБ';
}

function fmtGb(gb) {
  if (gb == null) return '—';
  return Math.round(gb) + ' ГБ';
}

function fmtUptime(sec) {
  if (sec == null) return '—';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const parts = [];
  if (d) parts.push(d + ' д');
  if (h) parts.push(h + ' ч');
  parts.push(m + ' мин');
  return parts.join(' ');
}

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString('ru-RU', { timeZone: 'UTC', hour12: false });
  } catch { return iso; }
}

function badgeOk(ok, labelOk, labelFail) {
  return `<span class="badge ${ok ? 'badge-ok' : 'badge-error'}">${ok ? labelOk : labelFail}</span>`;
}

function badgeWarn(val, labelTrue, labelFalse) {
  return `<span class="badge ${val ? 'badge-warn' : 'badge-ok'}">${val ? labelTrue : labelFalse}</span>`;
}

function kvItem(label, value) {
  return `<div class="kv-item"><span class="kv-label">${label}</span><span class="kv-value">${value}</span></div>`;
}

function num(v) { return v != null ? v.toLocaleString('ru-RU') : '—'; }

function fractionBadge(dbVal, fsVal) {
  const dbStr = dbVal != null ? dbVal.toLocaleString('ru-RU') : '—';
  const fsStr = fsVal != null ? fsVal.toLocaleString('ru-RU') : '—';
  const equal = dbVal != null && fsVal != null && dbVal === fsVal;
  const cls = equal ? 'badge-ok' : 'badge-warn';
  return `<span class="badge ${cls}" title="Записей в БД / Файлов в data">${dbStr} / ${fsStr}</span>`;
}

function setOverall(overall) {
  const cls   = overall === 'healthy' ? 'badge-ok' : overall === 'degraded' ? 'badge-warn' : 'badge-error';
  const label = overall === 'healthy' ? 'Исправно' : overall === 'degraded' ? 'Деградация' : 'Неисправно';
  document.getElementById('overallStatus').innerHTML = `<span class="badge ${cls}">${label}</span>`;
}

// ---- section 1: Health -----------------------------------------------------

function renderHealth(h) {
  setOverall(h.overall || 'unhealthy');

  const paused = h.processing_paused;
  const pauseLabel = paused ? 'Закачка выключена' : 'Закачка включена';
  const pauseCls   = paused ? 'mgmt-btn mgmt-btn-danger' : 'mgmt-btn';

  return `<div class="kv-grid">
    ${kvItem('База данных',     badgeOk(h.db_accessible,       'Доступна',   'Недоступна'))}
    ${kvItem('Директория data', badgeOk(h.data_dir_accessible, 'Доступна',   'Недоступна'))}
    ${kvItem('DB Watcher',      badgeOk(h.db_watcher_running,  'Работает',   'Остановлен'))}
    ${kvItem('File Watcher',    badgeOk(h.file_watcher_running,'Работает',   'Остановлен'))}
    ${kvItem('Обработка',       badgeWarn(h.processing_paused, 'Пауза',      'Активна'))}
    ${kvItem('Uptime',          fmtUptime(h.uptime_seconds))}
    ${kvItem('Запущен',         fmtDate(h.started_at))}
  </div>
  <div class="mgmt-row" style="margin-top:14px">
    <button class="${pauseCls}" id="pauseBtn">${pauseLabel}</button>
    <button class="mgmt-btn" id="reindexBtn">Реиндексировать</button>
    <span id="reindex-status" style="font-size:13px;color:#555;margin-left:4px"></span>
  </div>
  <div id="processing-msg" class="mgmt-msg"></div>`;
}

// ---- section 2: Disks ------------------------------------------------------

function renderDisks(d) {
  const disks = d.disks || [];
  if (!disks.length) {
    return '<span class="badge badge-neutral">Нет данных о дисках</span>';
  }

  return disks.map(disk => {
    const usedBarWidth = Math.min(disk.used_pct, 100);
    const barColor = disk.used_pct >= 90 ? '#b91c1c'
      : disk.used_pct >= 75 ? '#d97706' : '#2563eb';

    const dirsRows = (disk.dirs || []).map(dir => {
      let sizeStr = fmtSize(dir.size_mb);
      if (dir.cache_limit_mb) {
        const limitStr = fmtSize(dir.cache_limit_mb);
        const overFree = dir.cache_limit_mb > disk.free_gb * 1024;
        const fraction = overFree
          ? `${sizeStr} / <strong style="color:#b91c1c">${limitStr}</strong>`
          : `${sizeStr} / ${limitStr}`;
        sizeStr = `<span title="Занято / Максимум">${fraction}</span>`;
      }
      const pctStr  = Math.round(dir.pct) + '%';
      const dirPathEsc = escapeHtml(dir.path);
      const dirNameEsc = escapeHtml(dir.name);
      const pathCell = dir.path.length > 40
        ? `<span title="${dirPathEsc}">${dirNameEsc}/</span>`
        : `${dirNameEsc}/`;
      return `<tr>
        <td class="td-dir">${pathCell}</td>
        <td class="td-path muted" title="${dirPathEsc}">${dirPathEsc}</td>
        <td class="td-size">${sizeStr}</td>
        <td class="td-pct">${pctStr}</td>
      </tr>`;
    }).join('');

    return `
      <div class="disk-block">
        <div class="disk-header">
          <span class="disk-mount">${escapeHtml(disk.mountpoint)}</span>
          <span class="disk-stats">
            Всего: <strong>${fmtGb(disk.total_gb)}</strong>
            &nbsp;·&nbsp; Занято: <strong>${fmtGb(disk.used_gb)}</strong>
            <span class="disk-pct ${disk.used_pct >= 90 ? 'pct-high' : disk.used_pct >= 75 ? 'pct-med' : ''}">(${disk.used_pct}%)</span>
            &nbsp;·&nbsp; Свободно: <strong>${fmtGb(disk.free_gb)}</strong>
            <span class="disk-pct">(${disk.free_pct}%)</span>
          </span>
        </div>
        <div class="disk-bar-track">
          <div class="disk-bar-fill" style="width:${usedBarWidth}%;background:${barColor}"></div>
        </div>
        <table>
          <thead><tr>
            <th>Директория</th><th>Путь</th><th style="text-align:right">Размер</th><th style="text-align:right">% диска</th>
          </tr></thead>
          <tbody>${dirsRows}</tbody>
        </table>
      </div>`;
  }).join('');
}

// ---- section 3: Growth -----------------------------------------------------

function renderGrowth(g) {
  let rows = '';
  if (g.recent_reports && g.recent_reports.length) {
    for (const r of g.recent_reports) {
      const id = r.dop ? `${escapeHtml(r.id)}-${escapeHtml(r.dop)}` : escapeHtml(r.id);
      const route = escapeHtml(r.route || '—');
      rows += `<tr>
        <td class="td-id">${id}</td>
        <td class="td-route" title="${route}">${route}</td>
        <td>${escapeHtml(r.author || '—')}</td>
        <td class="td-date">${fmtDate(r.uploaded_at)}</td>
      </tr>`;
    }
  } else {
    rows = '<tr><td colspan="4" style="color:#aaa;text-align:center">Нет данных</td></tr>';
  }

  const queueBadge = g.queue_files != null
    ? (g.queue_files === 0
        ? '<span class="badge badge-ok">0</span>'
        : `<span class="badge badge-warn">${g.queue_files}</span>`)
    : '—';

  const errorBadge = g.error_files != null
    ? (g.error_files === 0
        ? '<span class="badge badge-ok">0</span>'
        : `<span class="badge badge-error">${g.error_files}</span>`)
    : '—';

  const processingBadge = g.processing_files != null
    ? (g.processing_files === 0
        ? '<span class="badge badge-ok">0</span>'
        : `<span class="badge badge-warn">${g.processing_files}</span>`)
    : '—';

  return `
    <div class="kv-grid" style="margin-bottom:16px">
      ${kvItem('Карточек', fractionBadge(g.db_total_count, g.fs_json_count))}
      ${kvItem('Отчетов',  fractionBadge(g.db_with_archive_count, g.fs_archive_count))}
      ${kvItem('Объём архивов',     g.total_archives_size_gb != null ? Math.round(g.total_archives_size_gb) + ' ГБ' : '—')}
      ${kvItem('В очереди (data.up/20_go/)',    queueBadge)}
      ${kvItem('В обработке (data.up/30_processing/)', processingBadge)}
      ${kvItem('Ошибки (data.up/40_error/)', errorBadge)}
      ${kvItem('Обработано (data.new/)',  num(g.done_files))}
      ${kvItem('За 24 часа',  num(g.added_24h))}
      ${kvItem('За 7 дней',   num(g.added_7d))}
      ${kvItem('За 30 дней',  num(g.added_30d))}
    </div>
    <p class="section-title">Последние добавленные</p>
    <table>
      <thead><tr><th>Шифр</th><th>Маршрут</th><th>Автор</th><th>Дата</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

// ---- section 4: Security ---------------------------------------------------

function renderSecurity(s) {
  const types = [
    { key: 'PATH_TRAVERSAL_ATTEMPT', label: 'Path Traversal', level: 'high' },
    { key: 'ZIP_BOMB_DETECTED',      label: 'Zip Bomb',        level: 'high' },
    { key: 'RATE_LIMIT_EXCEEDED',    label: 'Rate Limit',      level: 'medium' },
    { key: 'ARCHIVE_SIZE_EXCEEDED',  label: 'Archive Size',    level: 'medium' },
    { key: 'EMAIL_QUOTA',            label: 'Email Quota',     level: 'medium' },
    { key: 'INVALID_REQUEST',        label: 'Invalid Request', level: 'low' },
  ];

  const by_type = s.by_type || {};

  let secItems = types.map(t => {
    const cnt = by_type[t.key] || 0;
    const cls = cnt === 0 ? 'sec-count-zero'
      : t.level === 'high' ? 'sec-count-high'
      : t.level === 'medium' ? 'sec-count-medium'
      : 'sec-count-low';
    return `<div class="sec-item">
      <div class="sec-count ${cls}">${cnt}</div>
      <div class="sec-label">${t.label}</div>
    </div>`;
  }).join('');

  const totalCls = (s.total_events || 0) === 0 ? 'sec-count-zero' : 'sec-count-medium';
  secItems += `<div class="sec-item">
    <div class="sec-count ${totalCls}">${num(s.total_events)}</div>
    <div class="sec-label">Всего за 24ч</div>
  </div>`;

  let topIps = '';
  if (s.top_ips && s.top_ips.length) {
    const items = s.top_ips.map(x =>
      `<li><span class="ip-addr">${escapeHtml(x.ip)}</span><span class="ip-cnt">${x.count}</span></li>`
    ).join('');
    topIps = `<p class="section-title">Топ IP (HIGH + MEDIUM)</p><ul class="ip-list">${items}</ul>`;
  }

  return `<div class="sec-grid">${secItems}</div>${topIps}`;
}

// ---- section 5: Traffic stats ----------------------------------------------

function renderTrafficPeriod(p) {
  if (!p) return '<span class="badge badge-neutral">Нет данных</span>';
  return `
    <div class="traffic-period">
      ${kvItem('Уник. IP',    num(p.unique_ips))}
      ${kvItem('Поиск',       num(p.search))}
      ${kvItem('Отчёты',      num(p.report))}
      ${kvItem('В кэш',       num(p.cached_reports))}
      ${kvItem('Загрузок',    num(p.download))}
      ${kvItem('API',         num(p.api))}
    </div>`;
}

function renderTraffic(t) {
  if (!t || !t.h24) {
    return '<span class="badge badge-neutral">Нет данных (статистика накапливается)</span>';
  }

  const h24 = t.h24 || {};
  const errBlock = `
    <div class="traffic-errors">
      ${kvItem('Ошибок 4xx', h24.errors_4xx != null
        ? `<span class="badge ${h24.errors_4xx > 0 ? 'badge-warn' : 'badge-ok'}">${num(h24.errors_4xx)}</span>`
        : '—')}
      ${kvItem('Ошибок 5xx', h24.errors_5xx != null
        ? `<span class="badge ${h24.errors_5xx > 0 ? 'badge-error' : 'badge-ok'}">${num(h24.errors_5xx)}</span>`
        : '—')}
    </div>`;

  let topViewRows = '';
  if (t.top_report_views && t.top_report_views.length) {
    topViewRows = t.top_report_views.map(r =>
      `<tr>
        <td class="td-id">${escapeHtml(r.report_id)}</td>
        <td class="td-pct">${num(r.unique_ips)}</td>
      </tr>`
    ).join('');
  } else {
    topViewRows = '<tr><td colspan="2" style="color:#aaa;text-align:center">Нет данных</td></tr>';
  }

  let topRows = '';
  if (t.top_reports && t.top_reports.length) {
    topRows = t.top_reports.map(r =>
      `<tr>
        <td class="td-id">${escapeHtml(r.report_id)}</td>
        <td class="td-pct">${num(r.hits)}</td>
      </tr>`
    ).join('');
  } else {
    topRows = '<tr><td colspan="2" style="color:#aaa;text-align:center">Нет данных</td></tr>';
  }

  return `
    <div class="traffic-grid">
      <div class="traffic-col">
        <p class="section-title">За 24 часа</p>
        ${renderTrafficPeriod(t.h24)}
        ${errBlock}
      </div>
      <div class="traffic-col">
        <p class="section-title">За 7 дней</p>
        ${renderTrafficPeriod(t.d7)}
      </div>
      <div class="traffic-col">
        <p class="section-title">За 30 дней</p>
        ${renderTrafficPeriod(t.d30)}
      </div>
    </div>
    <p class="section-title" style="margin-top:16px">Топ отчётов за 7 дней</p>
    <table>
      <thead><tr><th>Шифр</th><th style="text-align:right">Уник. IP</th></tr></thead>
      <tbody>${topViewRows}</tbody>
    </table>
    <p class="section-title" style="margin-top:16px">Топ скачиваний за 7 дней</p>
    <table>
      <thead><tr><th>Шифр</th><th style="text-align:right">Скачиваний</th></tr></thead>
      <tbody>${topRows}</tbody>
    </table>`;
}

// ---- processing controls (pause + reindex) ---------------------------------

let _reindexPollTimer = null;
let _reindexWasProcessing = false;

function _attachProcessingListeners() {
  const pauseBtn   = document.getElementById('pauseBtn');
  const reindexBtn = document.getElementById('reindexBtn');
  if (pauseBtn)   pauseBtn.addEventListener('click', togglePause);
  if (reindexBtn) reindexBtn.addEventListener('click', startReindex);
}

async function togglePause() {
  const btn   = document.getElementById('pauseBtn');
  const msgEl = document.getElementById('processing-msg');
  if (!btn) return;
  btn.disabled = true;
  msgEl.textContent = '';

  try {
    const resp = await fetch(API_PAUSE, { method: 'POST' });
    const data = await resp.json();
    if (resp.ok && data.paused !== undefined) {
      const paused = data.paused;
      btn.textContent = paused ? 'Закачка выключена' : 'Закачка включена';
      btn.className   = paused ? 'mgmt-btn mgmt-btn-danger' : 'mgmt-btn';
      msgEl.textContent = paused ? 'Обработка приостановлена.' : 'Обработка возобновлена.';
      msgEl.className = 'mgmt-msg ok';
    } else {
      msgEl.textContent = data.error || 'Ошибка.';
      msgEl.className = 'mgmt-msg err';
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    msgEl.className = 'mgmt-msg err';
  } finally {
    btn.disabled = false;
  }
}

function _setReindexStatus(text, cls) {
  const el = document.getElementById('reindex-status');
  if (!el) return;
  el.innerHTML = text
    ? `<span class="badge ${cls}">${text}</span>`
    : '';
}

function _stopReindexPoll() {
  if (_reindexPollTimer) {
    clearInterval(_reindexPollTimer);
    _reindexPollTimer = null;
  }
  _reindexWasProcessing = false;
}

function _startReindexPoll() {
  _stopReindexPoll();
  _reindexWasProcessing = false;
  _reindexPollTimer = setInterval(async () => {
    try {
      const resp = await fetch(API_REINDEX_ST);
      if (resp.status === 401 || resp.status === 403) {
        _stopReindexPoll();
        showAuthForm();
        return;
      }
      if (!resp.ok) return;
      const data = await resp.json();
      const status = data.status;

      if (status === 'queued') {
        _setReindexStatus('В очереди…', 'badge-neutral');
      } else if (status === 'processing') {
        _reindexWasProcessing = true;
        _setReindexStatus('Выполняется…', 'badge-warn');
      } else {
        // idle — если до этого было processing, значит завершилось
        if (_reindexWasProcessing) {
          _stopReindexPoll();
          _setReindexStatus('Готово', 'badge-ok');
          const reindexBtn = document.getElementById('reindexBtn');
          if (reindexBtn) reindexBtn.disabled = false;
          loadData();
        } else {
          // idle сразу — уже завершено или не запускалось
          _stopReindexPoll();
          _setReindexStatus('', '');
          const reindexBtn = document.getElementById('reindexBtn');
          if (reindexBtn) reindexBtn.disabled = false;
        }
      }
    } catch { /* silent */ }
  }, 3000);
}

async function startReindex() {
  const btn   = document.getElementById('reindexBtn');
  const msgEl = document.getElementById('processing-msg');
  if (!btn) return;
  btn.disabled = true;
  msgEl.textContent = '';
  _setReindexStatus('', '');

  try {
    const resp = await fetch(API_REINDEX, { method: 'POST' });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      msgEl.textContent = 'Триггер реиндексации создан.';
      msgEl.className = 'mgmt-msg ok';
      _startReindexPoll();
    } else {
      msgEl.textContent = data.error || 'Ошибка запуска реиндексации.';
      msgEl.className = 'mgmt-msg err';
      btn.disabled = false;
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    msgEl.className = 'mgmt-msg err';
    btn.disabled = false;
  }
}

// ---- main render -----------------------------------------------------------

function renderPage(data) {
  const main = document.getElementById('main');

  const sections = [
    { title: '1. Здоровье системы',              content: renderHealth(data.health) },
    { title: '2. Безопасность (последние 24ч)',  content: renderSecurity(data.security) },
    { title: '3. Состояние дисков',              content: renderDisks(data.disks) },
    { title: '4. Динамика пополнения',           content: renderGrowth(data.growth) },
    { title: '5. Статистика посещений',          content: renderTraffic(data.traffic) },
  ];

  main.innerHTML = sections.map(s => `
    <div class="card">
      <div class="card-header">${s.title}</div>
      <div class="card-body">${s.content}</div>
    </div>
  `).join('');

  _attachProcessingListeners();

  document.getElementById('ts').textContent =
    'Обновлено: ' + new Date(data.timestamp).toLocaleTimeString('ru-RU', { hour12: false });
}

// ---- admin management ------------------------------------------------------

function renderAdminList(admins) {
  if (!admins || !admins.length) {
    return '<span class="badge badge-neutral">Нет администраторов</span>';
  }

  const rows = admins.map(a => {
    const nameEsc  = escapeHtml(a.name);
    const emailEsc = escapeHtml(a.email);
    const nameCell = a.name
      ? `${nameEsc} <span style="color:#aaa;font-size:12px">(${emailEsc})</span>`
      : emailEsc;
    const checkCell = a.is_root
      ? `<td><input type="checkbox" disabled title="Суперадмин — нельзя отобрать права"></td>`
      : `<td><input type="checkbox" class="admin-check" data-email="${emailEsc}"></td>`;
    const rootLabel = a.is_root
      ? `<span class="td-root-label">&nbsp;(root)</span>`
      : '';
    return `<tr>
      ${checkCell}
      <td>${nameCell}${rootLabel}</td>
    </tr>`;
  }).join('');

  return `<table>
    <thead><tr><th style="width:36px"></th><th>E-mail / Имя</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadAdmins() {
  const listEl = document.getElementById('admin-list');
  listEl.innerHTML = '<span class="badge badge-neutral">Загрузка…</span>';
  try {
    const resp = await fetch(API_ADMINS);
    if (!resp.ok) { listEl.innerHTML = '<span class="badge badge-error">Ошибка загрузки</span>'; return; }
    const data = await resp.json();
    listEl.innerHTML = renderAdminList(data.admins || []);
  } catch {
    listEl.innerHTML = '<span class="badge badge-error">Ошибка загрузки</span>';
  }
}

async function grantAdmin() {
  const input  = document.getElementById('grantEmailInput');
  const msgEl  = document.getElementById('grant-msg');
  const btn    = document.getElementById('grantBtn');
  const email  = (input.value || '').trim().toLowerCase();

  if (!email || !email.includes('@')) {
    msgEl.textContent = 'Введите корректный e-mail.';
    msgEl.className = 'mgmt-msg err';
    return;
  }

  btn.disabled = true;
  msgEl.textContent = '';

  try {
    const resp = await fetch(API_GRANT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      input.value = '';
      msgEl.textContent = `Права выданы: ${email}`;
      msgEl.className = 'mgmt-msg ok';
      await loadAdmins();
    } else {
      msgEl.textContent = data.error || 'Ошибка выдачи прав.';
      msgEl.className = 'mgmt-msg err';
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    msgEl.className = 'mgmt-msg err';
  } finally {
    btn.disabled = false;
  }
}

async function revokeAdmins() {
  const checks  = document.querySelectorAll('.admin-check:checked');
  const msgEl   = document.getElementById('revoke-msg');
  const btn     = document.getElementById('revokeBtn');

  if (!checks.length) {
    msgEl.textContent = 'Не выбран ни один администратор.';
    msgEl.className = 'mgmt-msg err';
    return;
  }

  const emails = Array.from(checks).map(c => c.dataset.email);
  btn.disabled = true;
  msgEl.textContent = '';

  try {
    const resp = await fetch(API_REVOKE, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ emails }),
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      const parts = [];
      if (data.revoked && data.revoked.length) parts.push(`Отобрано у: ${data.revoked.join(', ')}`);
      if (data.skipped && data.skipped.length) parts.push(`Пропущено (root): ${data.skipped.join(', ')}`);
      msgEl.textContent = parts.join(' | ') || 'Готово.';
      msgEl.className = 'mgmt-msg ok';
      await loadAdmins();
    } else {
      msgEl.textContent = data.error || 'Ошибка отбора прав.';
      msgEl.className = 'mgmt-msg err';
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    msgEl.className = 'mgmt-msg err';
  } finally {
    btn.disabled = false;
  }
}

// ---- users panel -----------------------------------------------------------

function renderUsers(users, emptyMsg = 'Нет пользователей') {
  if (!users || !users.length) {
    return `<span class="badge badge-neutral">${emptyMsg}</span>`;
  }

  const rows = users.map(u => {
    const emailEsc     = escapeHtml(u.email          || '');
    const nameEsc      = escapeHtml(u.name           || '—');
    const ipEsc        = escapeHtml(u.last_login_ip  || '');
    const ipCell       = ipEsc
      ? `<span style="font-family:monospace">${ipEsc}</span>`
      : '<span style="color:#bbb">—</span>';
    const lastLogin    = u.last_login_at
      ? u.last_login_at.slice(0, 16).replace('T', ' ')
      : '—';

    // Кнопка Заблокировать/Разблокировать
    let toggleBtn;
    if (u.is_active) {
      const blockDisabled = (u.is_root || u.is_self) ? ' disabled title="Нельзя заблокировать"' : '';
      toggleBtn = `<button class="mgmt-btn mgmt-btn-danger user-deactivate-btn"
        data-email="${emailEsc}"${blockDisabled}
        style="padding:3px 8px;font-size:12px">Заблокировать</button>`;
    } else {
      toggleBtn = `<button class="mgmt-btn user-activate-btn"
        data-email="${emailEsc}"
        style="padding:3px 8px;font-size:12px">Разблокировать</button>`;
    }

    // Кнопка Выход: для своей строки — активна если есть другие сессии (count > 1),
    // для чужой — если есть хоть одна живая (count > 0)
    const cnt = u.active_session_count || 0;
    const logoutEnabled = u.is_self ? cnt > 1 : cnt > 0;
    const logoutTitle   = u.is_self ? 'Завершить все другие сессии' : 'Завершить все сессии';
    const logoutBtn = logoutEnabled
      ? `<button class="mgmt-btn mgmt-btn-danger user-logout-btn"
           data-email="${emailEsc}"
           title="${logoutTitle}"
           style="padding:3px 8px;font-size:12px">Выход</button>`
      : `<button class="mgmt-btn" disabled
           title="Нет активных сессий"
           style="padding:3px 8px;font-size:12px">Выход</button>`;

    return `<tr>
      <td>${emailEsc}</td>
      <td>${nameEsc}</td>
      <td>${ipCell}</td>
      <td class="td-date">${lastLogin}</td>
      <td style="white-space:nowrap;display:flex;gap:4px">${toggleBtn}${logoutBtn}</td>
    </tr>`;
  }).join('');

  return `<table>
    <thead><tr>
      <th>Email</th><th>Имя</th><th>IP</th><th>Последний вход</th><th></th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

async function loadUsers() {
  const listEl        = document.getElementById('users-list');
  const neverListEl   = document.getElementById('users-never-list');
  const neverCountEl  = document.getElementById('users-never-count');
  const blockedListEl = document.getElementById('blocked-users-list');
  if (!listEl) return;
  listEl.innerHTML = '<span class="badge badge-neutral">Загрузка…</span>';
  if (neverListEl)   neverListEl.innerHTML   = '<span class="badge badge-neutral">Загрузка…</span>';
  if (blockedListEl) blockedListEl.innerHTML = '<span class="badge badge-neutral">Загрузка…</span>';
  try {
    const resp = await fetch(API_USERS);
    if (resp.status === 401 || resp.status === 403) { showAuthForm(); return; }
    if (!resp.ok) {
      listEl.innerHTML = '<span class="badge badge-error">Ошибка загрузки</span>';
      if (neverListEl)   neverListEl.innerHTML   = '';
      if (blockedListEl) blockedListEl.innerHTML = '';
      return;
    }
    const data    = await resp.json();
    const users   = data.users || [];
    const active  = users.filter(u => u.is_active);
    const blocked = users.filter(u => !u.is_active);
    const loggedIn = active.filter(u => u.last_login_at);
    const never    = active.filter(u => !u.last_login_at);
    loggedIn.sort((a, b) => (b.last_login_at || '').localeCompare(a.last_login_at || ''));
    listEl.innerHTML        = renderUsers(loggedIn, 'Нет пользователей с зарегистрированными входами');
    if (neverListEl)   neverListEl.innerHTML   = renderUsers(never, 'Таких пользователей нет');
    if (neverCountEl)  neverCountEl.textContent = never.length ? `(${never.length})` : '';
    if (blockedListEl) blockedListEl.innerHTML  = renderUsers(blocked, 'Заблокированных нет');
  } catch {
    listEl.innerHTML = '<span class="badge badge-error">Ошибка загрузки</span>';
    if (neverListEl)   neverListEl.innerHTML   = '';
    if (blockedListEl) blockedListEl.innerHTML = '';
  }
}

async function _userAction(url, email) {
  const msgEl = document.getElementById('users-msg');
  msgEl.textContent = '';
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      msgEl.className = 'mgmt-msg ok';
      msgEl.textContent = 'Готово.';
      await loadUsers();
    } else {
      msgEl.className = 'mgmt-msg err';
      msgEl.textContent = data.error || 'Ошибка.';
    }
  } catch {
    msgEl.className = 'mgmt-msg err';
    msgEl.textContent = 'Ошибка сети.';
  }
}

async function _handleUserClick(e) {
  const btn   = e.target.closest('button');
  if (!btn) return;
  const email = btn.dataset.email;
  if (!email) return;

  if (btn.classList.contains('user-activate-btn')) {
    await _userAction(API_USER_ACTIVATE, email);
  } else if (btn.classList.contains('user-deactivate-btn')) {
    if (!confirm(`Заблокировать ${email}? Все сессии будут закрыты.`)) return;
    await _userAction(API_USER_DEACTIVATE, email);
  } else if (btn.classList.contains('user-logout-btn')) {
    if (!confirm(`Завершить активные сессии ${email}?`)) return;
    await _userAction(API_USER_LOGOUT, email);
  }
}

function _attachUsersListeners() {
  const listEl        = document.getElementById('users-list');
  const neverListEl   = document.getElementById('users-never-list');
  const blockedListEl = document.getElementById('blocked-users-list');
  if (listEl)        listEl.addEventListener('click', _handleUserClick);
  if (neverListEl)   neverListEl.addEventListener('click', _handleUserClick);
  if (blockedListEl) blockedListEl.addEventListener('click', _handleUserClick);
}

// ---- digest settings -------------------------------------------------------

async function loadDigestSettings() {
  try {
    const resp = await fetch(API_SETTINGS);
    if (!resp.ok) return;
    const data = await resp.json();
    const inp = document.getElementById('digestTimeInput');
    if (inp && data.digest_send_time) inp.value = data.digest_send_time;
  } catch { /* silent */ }
}

async function saveDigestTime() {
  const inp   = document.getElementById('digestTimeInput');
  const msgEl = document.getElementById('digest-msg');
  const btn   = document.getElementById('saveDigestTimeBtn');
  const val   = (inp && inp.value) ? inp.value.trim() : '';

  if (!val) {
    msgEl.textContent = 'Введите время.';
    msgEl.className = 'mgmt-msg err';
    return;
  }

  btn.disabled = true;
  msgEl.textContent = '';

  try {
    const resp = await fetch(API_SETTINGS, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ digest_send_time: val }),
    });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      msgEl.textContent = `Сохранено: дайджест будет отправляться в ${val} МСК.`;
      msgEl.className = 'mgmt-msg ok';
    } else {
      msgEl.textContent = data.error || 'Ошибка сохранения.';
      msgEl.className = 'mgmt-msg err';
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    msgEl.className = 'mgmt-msg err';
  } finally {
    btn.disabled = false;
  }
}

async function sendTestEmail() {
  const msgEl = document.getElementById('digest-msg');
  const btn   = document.getElementById('testEmailBtn');

  btn.disabled = true;
  msgEl.textContent = 'Отправка…';
  msgEl.className = 'mgmt-msg';

  try {
    const resp = await fetch(API_TEST_EMAIL, { method: 'POST' });
    const data = await resp.json();
    if (resp.ok && data.ok) {
      msgEl.textContent = `Тестовое письмо отправлено ${data.recipients} получателям.`;
      msgEl.className = 'mgmt-msg ok';
    } else {
      msgEl.textContent = data.error || 'Ошибка отправки.';
      msgEl.className = 'mgmt-msg err';
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    msgEl.className = 'mgmt-msg err';
  } finally {
    btn.disabled = false;
  }
}

// ---- data loading ----------------------------------------------------------

async function loadData() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  document.getElementById('ts').textContent = 'Загрузка…';

  try {
    const resp = await fetch(API_STATUS);
    if (resp.status === 401 || resp.status === 403) {
      document.getElementById('ts').textContent = '';
      showAuthForm();
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    renderPage(data);
  } catch (err) {
    document.getElementById('main').innerHTML =
      `<div class="state-error">Ошибка загрузки данных: ${err.message}</div>`;
    document.getElementById('ts').textContent = '';
  } finally {
    btn.disabled = false;
  }
}

async function loadHealthBrief() {
  try {
    const resp = await fetch(API_HEALTH);
    if (!resp.ok) return;
    const data = await resp.json();
    setOverall(data.overall || 'unhealthy');
  } catch { /* silent */ }
}

// ---- auth ------------------------------------------------------------------

function showAuthForm() {
  document.getElementById('auth-form').style.display = 'flex';
  document.getElementById('auth-info').style.display = 'none';
  document.getElementById('main').innerHTML = '';
  document.getElementById('no-access-msg').style.display = 'none';
  document.getElementById('admin-mgmt').style.display = 'none';
}

function showNoAccess(email) {
  document.getElementById('auth-form').style.display = 'none';
  const authInfo = document.getElementById('auth-info');
  authInfo.style.display = 'flex';
  document.getElementById('auth-email').textContent = email;
  document.getElementById('main').innerHTML = '';
  document.getElementById('no-access-msg').style.display = 'block';
  document.getElementById('admin-mgmt').style.display = 'none';
}

function showAdminDashboard(email) {
  document.getElementById('auth-form').style.display = 'none';
  const authInfo = document.getElementById('auth-info');
  authInfo.style.display = 'flex';
  document.getElementById('auth-email').textContent = email;
  document.getElementById('no-access-msg').style.display = 'none';
  document.getElementById('main').innerHTML = '<div class="state-loading">Загрузка данных…</div>';
  document.getElementById('admin-mgmt').style.display = 'block';
  loadData();
  loadAdmins();
  loadDigestSettings();
  loadUsers();
  _attachUsersListeners();
}

async function checkAuth() {
  const user = await getCurrentUser();
  if (!user) {
    showAuthForm();
    return;
  }
  if (user.role === 'admin') {
    showAdminDashboard(user.email);
  } else {
    showNoAccess(user.email);
  }
}

let _adminPendingEmail = '';

async function sendMagicLink() {
  const input = document.getElementById('emailInput');
  const msgEl = document.getElementById('auth-msg');
  const btn   = document.getElementById('sendLinkBtn');
  const email = (input.value || '').trim().toLowerCase();

  if (!email || !email.includes('@')) {
    msgEl.textContent = 'Введите корректный e-mail.';
    return;
  }

  btn.disabled = true;
  msgEl.textContent = 'Отправка…';

  try {
    const r = await requestLink({ email, redirect: '/admin' });
    if (r.ok && r.data.ok) {
      _adminPendingEmail = email;
      msgEl.textContent = `Код отправлен на ${email}`;
      input.style.display                                   = 'none';
      btn.style.display                                     = 'none';
      document.getElementById('codeInput').style.display   = '';
      document.getElementById('verifyCodeBtn').style.display = '';
      document.getElementById('codeInput').value = '';
      document.getElementById('codeInput').focus();
    } else if (r.status === 429) {
      msgEl.textContent = r.data.error || 'Подождите минуту перед повторной отправкой.';
      btn.disabled = false;
    } else {
      msgEl.textContent = r.data.error || 'Ошибка. Попробуйте ещё раз.';
      btn.disabled = false;
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    btn.disabled = false;
  }
}

async function verifyCode() {
  const codeEl = document.getElementById('codeInput');
  const msgEl  = document.getElementById('auth-msg');
  const btn    = document.getElementById('verifyCodeBtn');
  const code   = (codeEl.value || '').trim();

  if (!code) { msgEl.textContent = 'Введите код из письма'; return; }

  btn.disabled = true;
  msgEl.textContent = 'Проверяем…';

  try {
    const r = await authVerifyCode(_adminPendingEmail, code);
    if (r.ok && r.data.ok) {
      location.reload();
    } else {
      msgEl.textContent = r.data.error || 'Неверный или устаревший код';
      codeEl.value = '';
      codeEl.focus();
      btn.disabled = false;
    }
  } catch {
    msgEl.textContent = 'Ошибка сети.';
    btn.disabled = false;
  }
}

async function logout(all = false) {
  await authLogout({ all });
  location.reload();
}

// ---- event listeners + init ------------------------------------------------

document.getElementById('sendLinkBtn').addEventListener('click', sendMagicLink);

document.getElementById('emailInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendMagicLink();
});

document.getElementById('verifyCodeBtn').addEventListener('click', verifyCode);

document.getElementById('codeInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') verifyCode();
});

attachLogoutMenu(document.getElementById('logoutBtn'), {
  onLogout:    () => logout(false),
  onLogoutAll: () => logout(true),
});

document.getElementById('refreshBtn').addEventListener('click', async () => {
  const authInfo = document.getElementById('auth-info');
  if (authInfo.style.display === 'flex') {
    const user = await getCurrentUser();
    if (user && user.role === 'admin') {
      loadData();
      loadAdmins();
      loadUsers();
      return;
    }
  }
  loadHealthBrief();
});

document.getElementById('grantBtn').addEventListener('click', grantAdmin);

document.getElementById('grantEmailInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') grantAdmin();
});

document.getElementById('revokeBtn').addEventListener('click', revokeAdmins);

document.getElementById('saveDigestTimeBtn').addEventListener('click', saveDigestTime);
document.getElementById('testEmailBtn').addEventListener('click', sendTestEmail);

// Handle ?error=expired from magic link verification
(function checkUrlError() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('error') === 'expired') {
    const msgEl = document.getElementById('auth-msg');
    if (msgEl) msgEl.textContent = 'Ссылка устарела или уже была использована.';
    window.history.replaceState({}, '', '/admin');
  }
})();

loadHealthBrief();
checkAuth();
