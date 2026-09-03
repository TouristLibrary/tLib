// Version 3.5 - 26.07.2026
// Изменения v3.5: удалено поле ИмяФайла — имя архива строится из ID и archive_ext.
// Логика страницы загрузки отчётов (upload.html)
// Единая форма, три режима: 'new' (загрузка), 'edit' (модерация 10_up), 'edit_published' (правка опубликованного).
// Изменения v3.4: раздел «Скрытые отчёты» (только админ) — loadHiddenReports/initHiddenReportsBlock,
//                 GET/POST /api/admin/hidden-reports; кнопка «Сохранить» активна только при изменении текста.
// Изменения v2.9: переведён на authService (getCurrentUser/requestLink/verifyCode/logout).
// Изменения v3.0: кнопка «Выйти» заменена dropdown-меню (выход / выход со всех устройств).
// Изменения v3.1: _checkUploadStatus() — проверка /api/upload/status при показе формы;
//                 баннер #upload-disk-banner + скрытие формы при uploads_enabled=false;
//                 обработка 507 (disk full) в doSubmitNew/doSubmitEdit.
// Изменения v3.2: раздел «Мои отчёты» (loadMyReports/initMyReportsCollapse) — список
//                 опубликованных отчётов текущего пользователя, кнопка «Редактировать»
//                 открывает enterEditPublishedMode; обновляется в resetFormToNew().
// Изменения v3.3: устранено дублирование SVG-иконки скачивания — DL_ICON_SVG + dlIconLink()
//                 вместо трёх идентичных инлайн-блоков в loadStagedList/loadMyReports.

import { getCurrentUser, requestLink, verifyCode, logout } from './services/authService.js';
import { attachLogoutMenu } from './modules/logoutMenu.js';

// ---------------------------------------------------------------------------
// Состояние
// ---------------------------------------------------------------------------

const MIN_YEAR = 1900;
const MAX_YEAR = new Date().getFullYear();
const RAION_CUSTOM = '__custom__';

let currentUser       = null;   // {id, email, name, role}
let formMode          = 'new';  // 'new' | 'edit' | 'edit_published'
let editOrigId        = null;   // ID отчёта в режиме edit (модерация)
let editPublishedId   = null;   // ID опубликованного отчёта в режиме edit_published
let fileRemoved       = false;  // В режиме edit/edit_published: пользователь удалил текущий файл
let codeCheckTimer        = null;
let uploaderCheckTimer    = null;
let codeOk            = false;  // Результат последней проверки уникальности кода
let currentHasFile    = false;  // В режиме edit/edit_published: изначально есть файл
let hiddenReportsOriginal = '';  // Исходный текст textarea «Скрытые отчёты» (для disabled кнопки)

// Состояние блока «Загрузивший отчёт» (только для админов)
let uploaderResolvedId  = null;   // id резолвленного пользователя, null = не резолвлен / очищен
let uploaderCleared     = false;  // пользователь явно очистил оба поля
let uploaderValid       = true;   // false = поля заполнены, но пользователь не найден в auth.db

// ---------------------------------------------------------------------------
// DOM-утилиты
// ---------------------------------------------------------------------------

function $(id) { return document.getElementById(id); }
function show(el)      { if (el) el.style.display = ''; }
function hide(el)      { if (el) el.style.display = 'none'; }
function showBlock(el) { if (el) el.style.display = 'block'; }
function showFlex(el)  { if (el) el.style.display = 'flex'; }

function isAdminUser() { return currentUser && currentUser.role === 'admin'; }

function setEditBtnEnabled(enabled, pendingId) {
  const btn = $('edit-report-btn');
  if (!btn) return;
  btn.classList.toggle('disabled', !enabled);
  btn._pendingId = enabled ? (pendingId || null) : null;
}

function showUploaderInfo(id, name, email) {
  if (!isAdminUser()) return;
  const wrap = $('uploader-info-wrap');
  if (!wrap) return;
  $('f-uploader-id').value    = id    != null ? String(id) : '';
  $('f-uploader-email').value = email || '';
  $('f-uploader-name').value  = name  || '';
  uploaderResolvedId = id != null ? id : null;
  uploaderCleared    = false;
  uploaderValid      = true;
  _clearUploaderErrors();
  showBlock(wrap);
}

function hideUploaderInfo() {
  const wrap = $('uploader-info-wrap');
  if (wrap) hide(wrap);
  uploaderResolvedId = null;
  uploaderCleared    = false;
  uploaderValid      = true;
}

function _clearUploaderErrors() {
  const eem = $('err-uploader-email');
  if (eem) eem.textContent = '';
}

async function _resolveUploader(by, value) {
  if (!value || !value.toString().trim()) return null;
  const param = by === 'id'
    ? `id=${encodeURIComponent(value)}`
    : `email=${encodeURIComponent(value.trim())}`;
  try {
    const res  = await fetch(`/api/upload/lookup-user?${param}`);
    const data = await res.json();
    return data.found ? data : null;
  } catch { return null; }
}

function _setUploaderFromResult(result) {
  if (result) {
    $('f-uploader-id').value    = String(result.id);
    $('f-uploader-email').value = result.email || '';
    $('f-uploader-name').value  = result.name  || '';
    uploaderResolvedId = result.id;
    uploaderCleared    = false;
    uploaderValid      = true;
  } else {
    $('f-uploader-id').value   = '';
    $('f-uploader-name').value = '';
    uploaderResolvedId = null;
    uploaderValid      = false;
  }
}

function initUploaderBlock() {
  const emailEl = $('f-uploader-email');
  if (!emailEl) return;

  emailEl.addEventListener('input', () => {
    clearTimeout(uploaderCheckTimer);
    const val = emailEl.value.trim();
    _clearUploaderErrors();

    if (!val) {
      uploaderCleared    = true;
      uploaderResolvedId = null;
      uploaderValid      = true;
      $('f-uploader-id').value   = '';
      $('f-uploader-name').value = '';
      updateActionBtns();
      return;
    }

    uploaderCleared = false;
    uploaderValid   = false;
    updateActionBtns();

    uploaderCheckTimer = setTimeout(async () => {
      const result = await _resolveUploader('email', val);
      if (emailEl.value.trim() !== val) return;
      _setUploaderFromResult(result);
      if (!result) $('err-uploader-email').textContent = 'E-mail не найден в базе пользователей';
      updateActionBtns();
    }, 600);
  });
}

function showDeleteReason(reason) {
  const wrap = $('delete-reason-wrap');
  if (!wrap) return;
  if (!reason) { hide(wrap); return; }
  $('f-delete-reason').value = reason;
  showBlock(wrap);
}

function hideDeleteReason() {
  const wrap = $('delete-reason-wrap');
  if (wrap) hide(wrap);
}

function showFileMismatchNote(message) {
  const el = $('file-mismatch-note');
  if (!el) return;
  el.textContent = message || '';
  el.style.display = message ? 'block' : 'none';
}

function hideFileMismatchNote() {
  const el = $('file-mismatch-note');
  if (el) { el.textContent = ''; el.style.display = 'none'; }
}

function showNotice(el, msg, type) {
  el.textContent = msg;
  el.className   = `notice ${type}`;
  showBlock(el);
}

function hideNotice(el) { el.textContent = ''; el.style.display = 'none'; }

function formatBytes(bytes) {
  if (!bytes) return '';
  if (bytes >= 1024 ** 3) return (bytes / 1024 ** 3).toFixed(1) + ' ГБ';
  if (bytes >= 1024 ** 2) return (bytes / 1024 ** 2).toFixed(1) + ' МБ';
  if (bytes >= 1024)      return (bytes / 1024).toFixed(0) + ' КБ';
  return bytes + ' Б';
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso);
    return d.toLocaleDateString('ru-RU') + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  } catch { return iso; }
}

const DL_ICON_SVG = `<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
  <path d="M4 19 L20 19" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
  <path d="M12 0 L12 17"  stroke="currentColor" stroke-width="3" stroke-linecap="round" fill="none"/>
  <path d="M7 12 L12 17 L17 12" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" fill="none"/>
</svg>`;

function dlIconLink(href, stopProp = false) {
  const onclick = stopProp ? ' onclick="event.stopPropagation()"' : '';
  return `<a class="dl-icon" href="${href}" download title="Скачать файл"${onclick}>${DL_ICON_SVG}</a>`;
}

function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function normalizeText(value) {
  if (!value) return '';
  return String(value)
    .normalize('NFC')
    // 1. Удалить невидимые/zero-width/format-символы ДО схлопывания пробелов.
    .replace(/[\u00AD\u200B-\u200F\u2060-\u2064\u2066-\u2069\uFEFF]/g, '')
    // 2. Привести «умные» символы Word к ASCII (иначе их удалит белый список):
    .replace(/[\u2010-\u2015\u2212]/g, '-')                   // дефисы/тире/минус -> "-"
    .replace(/[\u201C\u201D\u201E\u201F\u00AB\u00BB]/g, '"')  // двойные кавычки/«» -> "
    .replace(/[\u2018\u2019\u201A\u201B\u2032]/g, "'")         // одинарные кавычки/апостроф -> '
    .replace(/\u2026/g, '...')                                 // многоточие -> "..."
    // 3. Любые юникод-пробелы (NBSP, em/en-space, \u2028/29, \t, \n …) -> обычный пробел.
    .replace(/\s/g, ' ')
    // 4. Белый список: оставить только печатный ASCII + кириллицу + №, остальное удалить.
    .replace(/[^\u0020-\u007E\u00B0\u0400-\u04FF\u2116]/g, '')
    .replace(/ +/g, ' ')
    .trim();
}

function normalizeRoute(value) {
  return normalizeText(value)
    .replace(/(?:\s*=\s*)+/g, ' = ')    // дубли "==", "= =" и пробелы вокруг "=" -> " = "
    .replace(/ +/g, ' ')                // схлопнуть кратные пробелы после замены "="
    .replace(/^\s*=\s*|\s*=\s*$/g, '')  // убрать висячий "=" в начале/конце
    .trim();
}

// ---------------------------------------------------------------------------
// Прогресс выгрузки
// ---------------------------------------------------------------------------

let _progressStartTs  = 0;
let _progressSmoothed = 0; // сглаженная скорость (байт/мс), EMA

function showProgress() {
  _progressStartTs  = performance.now();
  _progressSmoothed = 0;
  const fill = $('progress-fill');
  const text = $('progress-text');
  fill.style.width = '0%';
  text.textContent = 'Загрузка…';
  showBlock($('upload-progress'));
}

function updateProgress(loaded, total) {
  const pct     = total > 0 ? Math.min(loaded / total * 100, 99) : 0;
  const elapsed = performance.now() - _progressStartTs; // мс

  $('progress-fill').style.width = pct.toFixed(1) + '%';

  if (!total) {
    $('progress-text').textContent = 'Загрузка…';
    return;
  }

  // EMA сглаженная скорость (байт/мс). α=0.3 — умеренная инерция
  const instSpeed = elapsed > 0 ? loaded / elapsed : 0;
  _progressSmoothed = _progressSmoothed === 0
    ? instSpeed
    : 0.3 * instSpeed + 0.7 * _progressSmoothed;

  const remaining = total - loaded;
  let etaStr = '';
  if (_progressSmoothed > 0) {
    const etaMs  = remaining / _progressSmoothed;
    const etaSec = Math.round(etaMs / 1000);
    const m = Math.floor(etaSec / 60);
    const s = etaSec % 60;
    etaStr = ` · осталось ~${m > 0 ? m + 'м ' : ''}${s}с`;
  }

  $('progress-text').textContent =
    `${Math.round(pct)}% · ${formatBytes(loaded)} из ${formatBytes(total)}${etaStr}`;
}

function setProcessing() {
  $('progress-fill').style.width = '100%';
  $('progress-text').textContent = 'Обработка…';
}

function hideProgress() {
  hide($('upload-progress'));
  $('progress-fill').style.width = '0%';
  $('progress-text').textContent = '';
}

/**
 * Выгрузка multipart FormData через XHR с поддержкой прогресса.
 * Возвращает Promise<{ok: bool, status: int, data: object}>.
 * Контракт ответа идентичен fetch — обработка в caller не меняется.
 */
function uploadWithProgress(url, formData, onProgress, onUploaded) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded, e.total);
    };
    xhr.upload.onload = () => {
      if (onUploaded) onUploaded();
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* non-JSON response */ }
      resolve({ ok: xhr.status >= 200 && xhr.status < 300, status: xhr.status, data });
    };
    xhr.onerror  = () => reject(new Error('Ошибка сети'));
    xhr.onabort  = () => reject(new Error('Загрузка прервана'));
    xhr.send(formData);
  });
}

// ---------------------------------------------------------------------------
// Аутентификация
// ---------------------------------------------------------------------------

async function checkAuth() {
  return await getCurrentUser();
}

function renderAuthState(user) {
  if (user) {
    hide($('auth-form'));
    showFlex($('auth-info'));
    $('auth-email').textContent = user.email;
    hide($('login-prompt'));
    showBlock($('report-section'));
    _checkUploadStatus();  // проверяем диск без блокировки рендера
    if (user.role === 'admin') {
      showBlock($('admin-section'));
      loadStagedList();
      showBlock($('hidden-reports-section'));
      loadHiddenReports();
    } else {
      hide($('admin-section'));
      hide($('hidden-reports-section'));
    }
    loadMyReports();
  } else {
    showFlex($('auth-form'));
    hide($('auth-info'));
    showBlock($('login-prompt'));
    hide($('report-section'));
    hide($('admin-section'));
    hide($('hidden-reports-section'));
    hide($('my-reports-section'));
  }
}

/**
 * Показывает баннер о нехватке места на диске и скрывает форму загрузки.
 */
function _showDiskBanner() {
  const banner = $('upload-disk-banner');
  const card   = $('report-section') && $('report-section').querySelector('.card');
  if (banner) showBlock(banner);
  if (card)   hide(card);
}

/**
 * Проверяет /api/upload/status и при uploads_enabled=false показывает баннер.
 * Не блокирует основной рендер (ошибки игнорируются).
 */
async function _checkUploadStatus() {
  try {
    const res = await fetch('/api/upload/status');
    if (!res.ok) return;
    const data = await res.json();
    if (data.uploads_enabled === false) {
      _showDiskBanner();
    }
  } catch {
    // Ошибка проверки статуса не блокирует форму
  }
}

function initAuthForm() {
  let _pendingEmail = '';

    $('sendLinkBtn').addEventListener('click', async () => {
    const email   = $('emailInput').value.trim();
    const authMsg = $('auth-msg');
    if (!email || !email.includes('@')) { authMsg.textContent = 'Введите корректный e-mail'; return; }
    $('sendLinkBtn').disabled = true;
    authMsg.textContent = 'Отправка…';
    try {
      const r = await requestLink({ email, redirect: '/upload.html' });
      if (r.ok && r.data.ok) {
        _pendingEmail = email;
        authMsg.textContent = 'Код отправлен на ' + email;
        $('emailInput').style.display    = 'none';
        $('sendLinkBtn').style.display   = 'none';
        $('codeInput').style.display     = '';
        $('verifyCodeBtn').style.display = '';
        $('codeInput').value = '';
        $('codeInput').focus();
      } else if (r.status === 429) {
        authMsg.textContent = r.data.error || 'Подождите минуту перед повторной отправкой.';
        $('sendLinkBtn').disabled = false;
      } else {
        authMsg.textContent = r.data.error || 'Ошибка отправки';
        $('sendLinkBtn').disabled = false;
      }
    } catch {
      $('auth-msg').textContent = 'Ошибка сети';
      $('sendLinkBtn').disabled = false;
    }
  });

  async function submitCode() {
    const code    = $('codeInput').value.trim();
    const authMsg = $('auth-msg');
    if (!code) { authMsg.textContent = 'Введите код из письма'; return; }
    $('verifyCodeBtn').disabled = true;
    authMsg.textContent = 'Проверяем…';
    try {
      const r = await verifyCode(_pendingEmail, code);
      if (r.ok && r.data.ok) {
        location.reload();
      } else {
        authMsg.textContent = r.data.error || 'Неверный или устаревший код';
        $('codeInput').value = '';
        $('codeInput').focus();
        $('verifyCodeBtn').disabled = false;
      }
    } catch {
      authMsg.textContent = 'Ошибка сети';
      $('verifyCodeBtn').disabled = false;
    }
  }

  $('verifyCodeBtn').addEventListener('click', submitCode);
  $('codeInput').addEventListener('keydown', e => { if (e.key === 'Enter') submitCode(); });

  attachLogoutMenu($('logoutBtn'), {
    onLogout: async () => {
      await logout();
      currentUser = null;
      renderAuthState(null);
    },
    onLogoutAll: async () => {
      await logout({ all: true });
      currentUser = null;
      renderAuthState(null);
    },
  });
}

// ---------------------------------------------------------------------------
// Справочники
// ---------------------------------------------------------------------------

const MONTHS = ['январь','февраль','март','апрель','май','июнь','июль','август','сентябрь','октябрь','ноябрь','декабрь'];

function populateMonthSelect(sel) {
  MONTHS.forEach((m, i) => {
    const opt = document.createElement('option');
    opt.value = i + 1;
    opt.textContent = m;
    sel.appendChild(opt);
  });
}

async function loadReferenceLists() {
  const [raionRes, tipRes, katRes] = await Promise.all([
    fetch('/api/raion-obshiy-list'),
    fetch('/api/tip-list'),
    fetch('/api/kategoria-s-list'),
  ]).catch(() => [null, null, null]);

  async function getList(res) {
    if (!res || !res.ok) return [];
    const d = await res.json();
    return d.success ? (d.data || []) : [];
  }

  const raionList = await getList(raionRes);
  const tipList   = await getList(tipRes);
  const katList   = await getList(katRes);

  fillSelect('f-raion-obshiy', raionList);
  const customOpt = document.createElement('option');
  customOpt.value = RAION_CUSTOM;
  customOpt.textContent = 'Нет в списке…';
  $('f-raion-obshiy').appendChild(customOpt);
  fillSelect('f-tip',           tipList);
  fillSelect('f-kat-s',         katList);
  fillSelect('f-kat-po',        katList);

  ['f-mes-s', 'f-mes-po'].forEach(id => populateMonthSelect($(id)));
}

function fillSelect(selectId, items) {
  const sel = $(selectId);
  if (!sel) return;
  items.forEach(item => {
    if (!item) return;
    const opt = document.createElement('option');
    opt.value = item;
    opt.textContent = item;
    sel.appendChild(opt);
  });
}

function setRaionObshiyValue(val) {
  const sel = $('f-raion-obshiy');
  const custom = $('f-raion-obshiy-custom');
  const strVal = String(val || '');
  const opt = [...sel.options].find(o => o.value === strVal && o.value !== RAION_CUSTOM);
  if (opt) {
    sel.value = strVal;
    custom.style.display = 'none';
    custom.value = '';
  } else if (strVal) {
    sel.value = RAION_CUSTOM;
    custom.value = strVal;
    custom.style.display = '';
  } else {
    sel.value = '';
    custom.style.display = 'none';
    custom.value = '';
  }
}

function getRaionObshiyValue() {
  const sel = $('f-raion-obshiy');
  if (sel.value === RAION_CUSTOM) {
    return normalizeText($('f-raion-obshiy-custom').value);
  }
  return sel.value;
}

function onRaionObshiyChange() {
  const custom = $('f-raion-obshiy-custom');
  if ($('f-raion-obshiy').value === RAION_CUSTOM) {
    custom.style.display = '';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = '';
  }
  updateActionBtns();
}

// ---------------------------------------------------------------------------
// Дефолтный шифр + live-проверка уникальности
// ---------------------------------------------------------------------------

async function loadDefaultCode() {
  try {
    const res = await fetch('/api/upload/next-code');
    if (!res.ok) return;
    const d = await res.json();
    $('f-shifr').value    = d.shifr;
    $('f-dopshifr').value = d.dopshifr || 'TLIB';
    codeOk = true;
    const hint = $('code-hint');
    hint.textContent = '✓ свободен';
    hint.style.color = '#15803d';
    setEditBtnEnabled(false);
    updateActionBtns();
  } catch { /* ignore */ }
}

function scheduleCodeCheck() {
  const shifr = $('f-shifr').value;
  const dop   = $('f-dopshifr').value.toUpperCase();
  const hint  = $('code-hint');
  const err   = $('err-code');

  clearTimeout(codeCheckTimer);
  hint.textContent = '';
  err.textContent  = '';
  $('f-shifr').classList.remove('error');
  $('f-dopshifr').classList.remove('error');
  setEditBtnEnabled(false);
  codeOk = false;
  updateActionBtns();

  if (!shifr) return;

  codeCheckTimer = setTimeout(async () => {
    try {
      const params = new URLSearchParams({ shifr, dopshifr: dop });
      if (formMode === 'edit' && editOrigId) params.set('exclude', editOrigId);
      if (formMode === 'edit_published' && editPublishedId) params.set('exclude', editPublishedId);
      const res = await fetch('/api/upload/check-code?' + params);
      if (!res.ok) return;
      const d = await res.json();
      if (d.taken) {
        hint.textContent = '⚠ занят';
        hint.style.color = '#e53e3e';
        $('f-shifr').classList.add('error');
        $('f-dopshifr').classList.add('error');
        err.textContent = 'Этот Шифр-ДопШифр уже занят';
        if (formMode === 'new' && d.in_library && d.can_edit) {
          setEditBtnEnabled(true, d.normalized);
        } else {
          setEditBtnEnabled(false);
        }
        codeOk = false;
      } else {
        hint.textContent = '✓ свободен';
        hint.style.color = '#15803d';
        setEditBtnEnabled(false);
        codeOk = true;
      }
    } catch { /* ignore */ }
    updateActionBtns();
  }, 600);
}

function initCodeFields() {
  const dop = $('f-dopshifr');
  $('f-shifr').addEventListener('input', scheduleCodeCheck);
  dop.addEventListener('input', () => { dop.value = dop.value.toUpperCase(); scheduleCodeCheck(); });
  dop.addEventListener('blur', () => {
    dop.value = normalizeText(dop.value).toUpperCase();
    scheduleCodeCheck();
  });
}

// ---------------------------------------------------------------------------
// Виджет файла
// ---------------------------------------------------------------------------

function initFileWidget() {
  // Кнопка «Удалить» текущий файл (только в режиме edit)
  $('file-current-delete').addEventListener('click', () => {
    fileRemoved = true;
    hide($('file-current'));
    show($('file-pick'));
    $('file-info').textContent = '';
    $('f-file').value = '';
    $('err-file').textContent = '';
    updateActionBtns();
  });

  // Кнопка «× убрать» выбранный новый файл
  $('file-pick-clear').addEventListener('click', () => {
    $('f-file').value = '';
    $('file-info').textContent = '';
    hide($('file-pick-clear'));
    $('err-file').textContent = '';
    updateActionBtns();
  });

  // Выбор нового файла
  $('f-file').addEventListener('change', () => {
    const f = $('f-file').files[0];
    if (f) {
      $('file-info').textContent = `${f.name} (${formatBytes(f.size)})`;
      show($('file-pick-clear'));
    } else {
      $('file-info').textContent = '';
      hide($('file-pick-clear'));
    }
    $('err-file').textContent = '';
    updateActionBtns();
  });
}

// Определяет, есть ли файл для публикации (в любом из режимов)
function hasFileForSubmit() {
  if (formMode === 'new') {
    return !!$('f-file').files[0];
  }
  // режим edit / edit_published: существующий файл (не удалён) ИЛИ новый выбран
  const newPicked = !!$('f-file').files[0];
  const existingKept = currentHasFile && !fileRemoved;
  return existingKept || newPicked;
}

// ---------------------------------------------------------------------------
// Активация кнопок
// ---------------------------------------------------------------------------

function updateActionBtns() {
  const marshrut  = ($('f-marshrut').value || '').trim();
  const god       = parseInt($('f-god').value);
  const godOk     = god >= MIN_YEAR && god <= MAX_YEAR;
  const uploaderOk = uploaderValid; // false = поле заполнено, но пользователь не найден

  if (formMode === 'new') {
    const file = !!$('f-file').files[0];
    $('saveBtn').disabled = !(marshrut && godOk && file && codeOk && uploaderOk);
  } else if (formMode === 'edit_published') {
    $('saveBtn').disabled = !(marshrut && godOk && codeOk && hasFileForSubmit() && uploaderOk);
    // deleteReportBtn всегда активна в edit_published
  } else {
    $('publishBtn').disabled = !(marshrut && godOk && codeOk && hasFileForSubmit() && uploaderOk);
    // rejectBtn всегда активна в edit
  }
}

// ---------------------------------------------------------------------------
// Режим «new» — сброс формы
// ---------------------------------------------------------------------------

function resetFormToNew() {
  formMode        = 'new';
  editOrigId      = null;
  editPublishedId = null;
  fileRemoved     = false;
  currentHasFile  = false;
  codeOk          = false;

  $('form-title').textContent = 'Загрузка и изменение отчёта';
  $('edit-orig-id').value     = '';

  // Очищаем поля
  const fieldIds = [
    'f-marshrut','f-shifr','f-dopshifr','f-raion-obshiy',
    'f-raion','f-avtor','f-tip','f-kat-s','f-kat-po',
    'f-god','f-mes-s','f-mes-po','f-tip-sudna','f-gorod',
    'f-kommentarii','f-zagruzil','f-admin-comment',
  ];
  fieldIds.forEach(id => {
    const el = $(id);
    if (!el) return;
    el.value = '';
    el.classList.remove('error');
  });
  $('f-mes-s').value  = '0';
  $('f-mes-po').value = '0';
  const customEl = $('f-raion-obshiy-custom');
  if (customEl) { customEl.value = ''; customEl.style.display = 'none'; }

  // Предзаполняем «Загрузил» из сессии
  if (currentUser) $('f-zagruzil').value = currentUser.name || '';

  // Сброс ошибок
  ['err-marshrut','err-code','err-god','err-file','err-kat','err-mes'].forEach(id => {
    const el = $(id); if (el) el.textContent = '';
  });
  ['f-kat-s','f-kat-po','f-mes-s','f-mes-po'].forEach(id => {
    const el = $(id); if (el) el.classList.remove('error');
  });
  $('code-hint').textContent = '';

  // Файловый виджет
  $('f-file').value = '';
  $('file-info').textContent = '';
  hide($('file-pick-clear'));
  hide($('file-current'));
  show($('file-pick'));

  // Кнопки
  show($('saveBtn'));
  hide($('publishBtn'));
  hide($('rejectBtn'));
  hide($('deleteReportBtn'));
  hide($('confirmDeleteBtn'));
  hide($('rejectDeleteBtn'));
  $('saveBtn').disabled = true;
  $('publishBtn').textContent = 'Опубликовать';
  $('rejectBtn').textContent  = 'Отклонить';
  hide($('admin-comment-wrap'));
  hide($('no-email-wrap'));

  // Деактивировать кнопку-карандаш
  setEditBtnEnabled(false);

  // Блок «Загрузивший отчёт»: для администратора показываем и предзаполняем текущим пользователем
  if (isAdminUser() && currentUser) {
    showUploaderInfo(currentUser.id ?? null, currentUser.name || '', currentUser.email || '');
  } else {
    hideUploaderInfo();
  }
  hideDeleteReason();
  hideFileMismatchNote();
  hideNotice($('form-notice'));
  const _rn = $('replace-notice');
  if (_rn) { _rn.textContent = ''; _rn.style.display = ''; }

  // Снять подсветку строки таблицы
  document.querySelectorAll('.active-row').forEach(r => r.classList.remove('active-row'));

  // Загрузить дефолтный шифр
  loadDefaultCode();

  // Обновить список «Мои отчёты» (мог измениться после публикации/удаления/правки)
  loadMyReports();
}

// ---------------------------------------------------------------------------
// Режим «edit» — заполнить форму данными из 10_up
// ---------------------------------------------------------------------------

async function enterEditMode(id, opts = {}) {
  hideNotice($('form-notice'));

  let data, has_archive, archive_ext, uploader_id, uploader_name, uploader_email, orig_id;
  try {
    const res = await fetch(`/api/upload/item?id=${encodeURIComponent(id)}`);
    if (!res.ok) { showNotice($('form-notice'), 'Ошибка загрузки отчёта', 'error'); return; }
    ({ data, has_archive, archive_ext, uploader_id, uploader_name, uploader_email, orig_id } = await res.json());
  } catch (e) {
    showNotice($('form-notice'), 'Ошибка сети: ' + e.message, 'error');
    return;
  }

  const isEditVariant = !!opts.isEdit;

  formMode       = 'edit';
  editOrigId     = id;
  fileRemoved    = false;
  currentHasFile = !!has_archive;
  codeOk         = true; // существующий код изначально свободен (exclude сработает)

  $('form-title').textContent = `Редактирование: ${id}`;
  $('edit-orig-id').value     = id;

  // Предупреждение о замене опубликованного отчёта при смене шифра
  const replaceNotice = $('replace-notice');
  if (replaceNotice) {
    if (orig_id) {
      replaceNotice.textContent =
        `⚠ При публикации отчёт «${orig_id}» будет автоматически удалён из библиотеки и заменён этим.`;
      replaceNotice.style.display = 'block';
    } else {
      replaceNotice.textContent = '';
      replaceNotice.style.display = '';
    }
  }

  // Заполняем поля
  setFormFields(data);

  // Файловый виджет
  $('f-file').value = '';
  $('file-info').textContent = '';
  hide($('file-pick-clear'));
  if (has_archive) {
    const archiveName = `${id}.${archive_ext || ''}`;
    $('file-current-name').textContent = archiveName;
    $('file-current-dl').href = `/api/upload/file?id=${encodeURIComponent(id)}`;
    $('file-current-dl').download = archiveName;
    showFlex($('file-current'));
    hide($('file-pick'));
  } else {
    hide($('file-current'));
    show($('file-pick'));
  }

  // Кнопки
  hide($('saveBtn'));
  show($('publishBtn'));
  show($('rejectBtn'));
  hide($('confirmDeleteBtn'));
  hide($('rejectDeleteBtn'));
  showBlock($('admin-comment-wrap'));
  $('f-admin-comment').value = '';
  showBlock($('no-email-wrap'));
  $('f-no-email').checked = false;
  $('publishBtn').textContent = isEditVariant ? 'Опубликовать изменения' : 'Опубликовать';
  $('rejectBtn').textContent  = isEditVariant ? 'Отклонить изменения'   : 'Отклонить';
  showUploaderInfo(uploader_id ?? data['ЗагрузилID'] ?? null, uploader_name, uploader_email);

  updateActionBtns();

  // Подсветить строку таблицы
  document.querySelectorAll('.active-row').forEach(r => r.classList.remove('active-row'));
  const row = document.querySelector(`[data-id="${CSS.escape(id)}"]`);
  if (row) row.classList.add('active-row');

  // Прокрутить к форме
  $('report-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function setFormFields(data) {
  const set = (id, val) => { const el = $(id); if (el) el.value = val ?? ''; };

  set('f-marshrut',  data['Маршрут']     || '');
  set('f-shifr',     data['Шифр']        || '');
  set('f-dopshifr',  (data['ДопШифр']   || '').toUpperCase());
  set('f-raion',     data['Район']       || '');
  set('f-avtor',     data['Автор']       || '');
  set('f-god',       data['Год']         || '');
  set('f-tip-sudna', data['ТипСудна']    || '');
  set('f-gorod',     data['Город']       || '');
  set('f-kommentarii', data['Комментарии'] || '');
  set('f-zagruzil',  data['ЗагрузилИмя'] || '');

  setRaionObshiyValue(data['РайонОбщий'] || '');
  setSelectVal('f-tip',          data['Тип']         || '');
  setSelectVal('f-kat-s',        data['КатегорияС']  || '');
  setSelectVal('f-kat-po',       data['КатегорияПо'] || '');
  setSelectVal('f-mes-s',        data['МесяцС']      ?? 0);
  setSelectVal('f-mes-po',       data['МесяцПо']     ?? 0);

  // Сброс ошибок
  ['err-marshrut','err-code','err-god','err-file','err-kat','err-mes'].forEach(id => {
    const el = $(id); if (el) el.textContent = '';
  });
  ['f-marshrut','f-shifr','f-dopshifr','f-god','f-kat-s','f-kat-po','f-mes-s','f-mes-po'].forEach(id => {
    const el = $(id); if (el) el.classList.remove('error');
  });
  $('code-hint').textContent = '✓ текущий';
  $('code-hint').style.color = '#888';
}

function setSelectVal(id, val) {
  const el = $(id);
  if (!el) return;
  const strVal = String(val);
  const opt = [...el.options].find(o => o.value === strVal);
  el.value = opt ? strVal : (el.options[0] ? el.options[0].value : '');
}

// ---------------------------------------------------------------------------
// Форма — инициализация и submit
// ---------------------------------------------------------------------------

function initReportForm() {
  $('f-god').min = String(MIN_YEAR);
  $('f-god').max = String(MAX_YEAR);

  $('f-marshrut').addEventListener('input', () => {
    const el = $('f-marshrut');
    if (/[\r\n]/.test(el.value)) {
      const pos = el.selectionStart;
      el.value = el.value.replace(/[\r\n]+/g, ' ');
      el.setSelectionRange(pos, pos);
    }
    if (formMode === 'new') hideNotice($('form-notice'));
    updateActionBtns();
  });
  $('f-marshrut').addEventListener('blur', () => validateMarshrut(false));
  $('f-god').addEventListener('input', updateActionBtns);
  $('f-god').addEventListener('blur',  validateGod);
  ['f-kat-s', 'f-kat-po'].forEach(id =>
    $(id).addEventListener('change', () => validateRange('f-kat-s', 'f-kat-po', 'err-kat', 'Категория'))
  );
  $('f-raion-obshiy').addEventListener('change', onRaionObshiyChange);

  // Нормализация текстовых полей на blur
  ['f-raion','f-avtor','f-tip-sudna','f-gorod','f-kommentarii','f-zagruzil','f-raion-obshiy-custom']
    .forEach(id => {
      const el = $(id);
      if (el) el.addEventListener('blur', () => { el.value = normalizeText(el.value); });
    });

  // Submit (режим new или edit_published)
  $('reportForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (formMode === 'new') {
      await doSubmitNew();
    } else if (formMode === 'edit_published') {
      await doSubmitEdit();
    }
  });

  // Publish (режим edit)
  $('publishBtn').addEventListener('click', async () => {
    if (formMode !== 'edit') return;
    await doPublish();
  });

  // Reject (режим edit)
  $('rejectBtn').addEventListener('click', async () => {
    if (formMode !== 'edit') return;
    await doReject();
  });

  // Запрос удаления (режим edit_published)
  $('deleteReportBtn').addEventListener('click', () => {
    if (formMode !== 'edit_published') return;
    openDeleteConfirmDialog();
  });

  // Подтверждение удаления (admin, режим ревью через таблицу)
  $('confirmDeleteBtn').addEventListener('click', async () => {
    if (!editPublishedId) return;
    await doConfirmDelete(editPublishedId);
  });

  // Отклонение запроса удаления (admin, режим ревью через таблицу)
  $('rejectDeleteBtn').addEventListener('click', async () => {
    if (!editPublishedId) return;
    await doRejectDelete(editPublishedId);
  });

  // Кнопка-карандаш «Редактировать отчёт»
  const editBtn = $('edit-report-btn');
  if (editBtn) {
    editBtn.addEventListener('click', () => {
      if (!editBtn._pendingId) return;
      enterEditPublishedMode(editBtn._pendingId);
    });
  }

  // Отмена редактирования
  $('cancelEditBtn').addEventListener('click', () => resetFormToNew());

  // Диалог подтверждения удаления
  initDeleteConfirmDialog();
}

function validateRange(fromId, toId, errId, label) {
  const from = $(fromId), to = $(toId), errEl = $(errId);
  if (from.selectedIndex > 0 && to.selectedIndex > 0 &&
      from.selectedIndex > to.selectedIndex) {
    errEl.textContent = `«${label} с» не может быть позже «${label} по».`;
    from.classList.add('error');
    to.classList.add('error');
    return true;
  }
  errEl.textContent = '';
  from.classList.remove('error');
  to.classList.remove('error');
  return false;
}

function validateMarshrut(requireFilled) {
  const el = $('f-marshrut');
  el.value = normalizeRoute(el.value);
  const marshrut = el.value;
  if (!marshrut) {
    if (requireFilled) {
      $('err-marshrut').textContent = 'Заполните маршрут';
      $('f-marshrut').classList.add('error');
      return true;
    }
    $('err-marshrut').textContent = '';
    $('f-marshrut').classList.remove('error');
    return false;
  }
  if (/\s/.test(marshrut) && !marshrut.includes('=')) {
    $('err-marshrut').textContent = 'Пункты маршрута должны быть разделены знаком "=".';
    $('f-marshrut').classList.add('error');
    return true;
  }
  $('err-marshrut').textContent = '';
  $('f-marshrut').classList.remove('error');
  return false;
}

function validateGod() {
  const god = parseInt($('f-god').value);
  if (!god || god < MIN_YEAR || god > MAX_YEAR) {
    $('err-god').textContent = `Укажите корректный год (${MIN_YEAR}–${MAX_YEAR})`;
    $('f-god').classList.add('error');
    return true;
  }
  $('err-god').textContent = '';
  $('f-god').classList.remove('error');
  return false;
}

function validateRequiredFields() {
  let hasError = false;
  if (validateMarshrut(true)) hasError = true;
  if (validateGod()) hasError = true;
  if (validateRange('f-kat-s', 'f-kat-po', 'err-kat', 'Категория')) hasError = true;
  return hasError;
}

function buildFormData(extra = {}) {
  const fd = new FormData();
  fd.append('shifr',         $('f-shifr').value);
  fd.append('dopshifr',      normalizeText($('f-dopshifr').value).toUpperCase());
  fd.append('marshrut',      normalizeRoute($('f-marshrut').value));
  fd.append('raion_obshiy',  getRaionObshiyValue());
  fd.append('raion',         normalizeText($('f-raion').value));
  fd.append('avtor',         normalizeText($('f-avtor').value));
  fd.append('tip',           $('f-tip').value);
  fd.append('kategoriya_s',  $('f-kat-s').value);
  fd.append('kategoriya_po', $('f-kat-po').value);
  fd.append('god',           $('f-god').value);
  fd.append('mesyats_s',     $('f-mes-s').value  || 0);
  fd.append('mesyats_po',    $('f-mes-po').value || 0);
  fd.append('tip_sudna',     normalizeText($('f-tip-sudna').value));
  fd.append('gorod',         normalizeText($('f-gorod').value));
  fd.append('kommentarii',   normalizeText($('f-kommentarii').value));
  fd.append('zagruzil_imya', normalizeText($('f-zagruzil').value));
  // Блок «Загрузивший отчёт» — только если видим (т.е. для администратора)
  const uploaderWrap = $('uploader-info-wrap');
  if (uploaderWrap && uploaderWrap.style.display !== 'none') {
    if (uploaderCleared) {
      fd.append('uploader_cleared', 'true');
    } else if (uploaderResolvedId != null) {
      fd.append('uploader_id', String(uploaderResolvedId));
    }
  }
  Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  return fd;
}

async function doSubmitNew() {
  const notice = $('form-notice');
  hideNotice(notice);

  if (validateRequiredFields()) return;

  const file = $('f-file').files[0];
  if (!file) {
    $('err-file').textContent = 'Прикрепите файл .zip или .pdf';
    return;
  }
  if (!/\.(zip|pdf)$/i.test(file.name)) {
    $('err-file').textContent = 'Допустимы только файлы .zip или .pdf';
    $('f-file').classList.add('error');
    return;
  }
  $('err-file').textContent = '';
  $('f-file').classList.remove('error');

  const saveBtn = $('saveBtn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Сохранение…';

  const fd = buildFormData();
  fd.append('file', file);

  showProgress();
  try {
    const res  = await uploadWithProgress('/api/upload/submit', fd, updateProgress, setProcessing);
    const data = res.data;
    if (res.ok && data.ok) {
      resetFormToNew();
      showNotice(notice, 'Отчёт отправлен на рассмотрение библиотекарям.', 'success');
      if (currentUser && currentUser.role === 'admin') loadStagedList();
    } else if (data.code_taken) {
      $('err-code').textContent = 'Этот Шифр-ДопШифр уже занят — выберите другой';
      $('f-shifr').classList.add('error');
      $('f-dopshifr').classList.add('error');
      showNotice(notice, 'Шифр-ДопШифр занят. Попробуйте с другим номером.', 'error');
    } else if (res.status === 507) {
      showNotice(notice, data.error || 'На сервере недостаточно места — загрузка временно недоступна.', 'error');
      _showDiskBanner();
    } else {
      showNotice(notice, data.error || 'Ошибка сохранения', 'error');
    }
  } catch (e) {
    showNotice(notice, e.message || 'Ошибка сети', 'error');
  } finally {
    hideProgress();
    saveBtn.disabled = false;
    saveBtn.textContent = 'Сохранить';
    updateActionBtns();
  }
}

async function doPublish() {
  const notice = $('form-notice');
  hideNotice(notice);

  if (validateRequiredFields()) return;

  if (!hasFileForSubmit()) {
    $('err-file').textContent = 'Нельзя опубликовать без файла';
    return;
  }

  const publishBtn = $('publishBtn');
  publishBtn.disabled = true;
  publishBtn.textContent = 'Публикация…';

  const fd = buildFormData({
    id:            editOrigId,
    admin_comment: $('f-admin-comment').value.trim(),
    remove_file:   fileRemoved ? 'true' : 'false',
    no_email:      $('f-no-email').checked ? 'true' : 'false',
  });

  const newFile = $('f-file').files[0];
  if (newFile) fd.append('file', newFile);

  showProgress();
  try {
    const res  = await uploadWithProgress('/api/upload/publish', fd, updateProgress, setProcessing);
    const data = res.data;
    if (res.ok && data.ok) {
      showNotice(notice, `Отчёт ${data.id} опубликован и передан на обработку.`, 'success');
      setTimeout(() => {
        resetFormToNew();
        loadStagedList();
      }, 1500);
    } else if (data.no_file) {
      $('err-file').textContent = 'Нельзя опубликовать без файла';
      showNotice(notice, 'Нельзя опубликовать без файла отчёта.', 'error');
    } else if (data.code_taken) {
      $('err-code').textContent = 'Этот Шифр-ДопШифр уже занят';
      $('f-shifr').classList.add('error');
      $('f-dopshifr').classList.add('error');
      showNotice(notice, 'Шифр-ДопШифр занят — измените перед публикацией.', 'error');
    } else {
      showNotice(notice, data.error || 'Ошибка публикации', 'error');
    }
  } catch (e) {
    showNotice(notice, e.message || 'Ошибка сети', 'error');
  } finally {
    hideProgress();
    publishBtn.disabled = false;
    publishBtn.textContent = 'Опубликовать';
    updateActionBtns();
  }
}

async function doReject() {
  const notice = $('form-notice');
  hideNotice(notice);

  const comment = ($('f-admin-comment').value || '').trim();
  if (!comment && !confirm('Вы уверены, что хотите отклонить отчёт без комментария?')) return;

  const rejectBtn = $('rejectBtn');
  rejectBtn.disabled = true;
  rejectBtn.textContent = 'Отклонение…';

  try {
    const res  = await fetch('/api/upload/reject', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: editOrigId, admin_comment: comment, no_email: $('f-no-email').checked }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      showNotice(notice, 'Отчёт отклонён, автор уведомлён.', 'info');
      setTimeout(() => {
        resetFormToNew();
        loadStagedList();
      }, 1500);
    } else {
      showNotice(notice, data.error || 'Ошибка отклонения', 'error');
    }
  } catch (e) {
    showNotice(notice, 'Ошибка сети: ' + e.message, 'error');
  } finally {
    rejectBtn.disabled = false;
    rejectBtn.textContent = 'Отклонить';
  }
}

// ---------------------------------------------------------------------------
// Режим «edit_published» — редактирование опубликованного отчёта
// ---------------------------------------------------------------------------

async function enterEditPublishedMode(id, opts = {}) {
  hideNotice($('form-notice'));
  const _rn2 = $('replace-notice');
  if (_rn2) { _rn2.textContent = ''; _rn2.style.display = ''; }

  let data, has_archive, archive_ext, file_status, uploader_id, uploader_name, uploader_email,
      pending_delete, delete_reason, delete_requested_by_email, delete_requested_at;
  try {
    const res = await fetch(`/api/upload/published-item?id=${encodeURIComponent(id)}`);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      showNotice($('form-notice'), d.error || 'Ошибка загрузки отчёта', 'error');
      return;
    }
    ({ data, has_archive, archive_ext, file_status, uploader_id, uploader_name, uploader_email,
       pending_delete, delete_reason, delete_requested_by_email, delete_requested_at
    } = await res.json());
  } catch (e) {
    showNotice($('form-notice'), 'Ошибка сети: ' + e.message, 'error');
    return;
  }

  formMode        = 'edit_published';
  editPublishedId = id;
  fileRemoved     = false;
  currentHasFile  = !!has_archive;
  codeOk          = true;

  $('form-title').textContent = `Редактирование отчёта: ${id}`;
  $('edit-orig-id').value     = id;

  setFormFields(data);
  $('code-hint').textContent = '✓ текущий';
  $('code-hint').style.color = '#888';

  // Файловый виджет
  $('f-file').value = '';
  $('file-info').textContent = '';
  hide($('file-pick-clear'));
  if (has_archive) {
    const fileName = `${id}.${archive_ext || ''}`;
    const sizeStr  = data['РазмерАрхива'] ? ` (${formatBytes(data['РазмерАрхива'])})` : '';
    $('file-current-name').textContent = fileName + sizeStr;
    $('file-current-dl').href     = `/data/${encodeURIComponent(fileName)}`;
    $('file-current-dl').download = fileName;
    showFlex($('file-current'));
    hide($('file-pick'));
  } else {
    hide($('file-current'));
    show($('file-pick'));
  }

  // Кнопки: если админ вошёл через строку таблицы с запросом удаления → режим ревью
  const reviewDelete = isAdminUser() && opts.reviewDelete;
  hide($('publishBtn'));
  hide($('rejectBtn'));
  hide($('admin-comment-wrap'));
  hide($('no-email-wrap'));
  setEditBtnEnabled(false);

  if (reviewDelete) {
    hide($('saveBtn'));
    hide($('deleteReportBtn'));
    show($('confirmDeleteBtn'));
    show($('rejectDeleteBtn'));
    showBlock($('admin-comment-wrap'));
    $('f-admin-comment').value = '';
    showBlock($('no-email-wrap'));
    $('f-no-email').checked = false;
  } else {
    show($('saveBtn'));
    show($('deleteReportBtn'));
    hide($('confirmDeleteBtn'));
    hide($('rejectDeleteBtn'));
  }

  showUploaderInfo(uploader_id ?? data['ЗагрузилID'] ?? null, uploader_name, uploader_email);
  showDeleteReason(pending_delete ? delete_reason : null);
  showFileMismatchNote(file_status && file_status.mismatch ? file_status.message : null);
  updateActionBtns();

  $('report-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
}


async function doSubmitEdit() {
  const notice = $('form-notice');
  hideNotice(notice);

  if (validateRequiredFields()) return;

  if (!hasFileForSubmit()) {
    $('err-file').textContent = 'Нельзя сохранить без файла отчёта';
    return;
  }

  const saveBtn = $('saveBtn');
  saveBtn.disabled = true;
  saveBtn.textContent = 'Сохранение…';

  const fd = buildFormData({
    edit_orig_id: editPublishedId,
    remove_file:  fileRemoved ? 'true' : 'false',
  });

  const newFile = $('f-file').files[0];
  if (newFile) {
    if (!/\.(zip|pdf)$/i.test(newFile.name)) {
      $('err-file').textContent = 'Допустимы только файлы .zip или .pdf';
      $('f-file').classList.add('error');
      saveBtn.disabled = false;
      saveBtn.textContent = 'Сохранить';
      return;
    }
    fd.append('file', newFile);
  }

  showProgress();
  try {
    const res  = await uploadWithProgress('/api/upload/submit-edit', fd, updateProgress, setProcessing);
    const data = res.data;
    if (res.ok && data.ok && data.orphan) {
      resetFormToNew();
      showNotice(notice, data.warning || 'Изменения сохранены (без файла — потребуется прикрепить файл вручную).', 'warning');
    } else if (res.ok && data.ok) {
      resetFormToNew();
      showNotice(notice, 'Изменения отправлены на рассмотрение библиотекарям.', 'success');
    } else if (data.code_taken) {
      $('err-code').textContent = 'Этот Шифр-ДопШифр уже занят — выберите другой';
      $('f-shifr').classList.add('error');
      $('f-dopshifr').classList.add('error');
      showNotice(notice, 'Шифр-ДопШифр занят.', 'error');
    } else if (data.no_file) {
      $('err-file').textContent = 'Нельзя сохранить без файла отчёта';
      showNotice(notice, 'Прикрепите файл отчёта.', 'error');
    } else if (res.status === 507) {
      showNotice(notice, data.error || 'На сервере недостаточно места — загрузка временно недоступна.', 'error');
      _showDiskBanner();
    } else {
      showNotice(notice, data.error || 'Ошибка сохранения', 'error');
    }
  } catch (e) {
    showNotice(notice, e.message || 'Ошибка сети', 'error');
  } finally {
    hideProgress();
    saveBtn.disabled = false;
    saveBtn.textContent = 'Сохранить';
    updateActionBtns();
  }
}


// ---------------------------------------------------------------------------
// Диалог подтверждения удаления
// ---------------------------------------------------------------------------

function initDeleteConfirmDialog() {
  const overlay   = $('delete-confirm-overlay');
  const input     = $('delete-confirm-input');
  const reasonEl  = $('delete-confirm-reason');
  const errEl     = $('delete-confirm-err');
  const okBtn     = $('delete-confirm-ok-btn');
  const cancelBtn = $('delete-confirm-cancel-btn');

  if (!overlay) return;

  function _resetDialog() {
    overlay.classList.remove('visible');
    input.value = '';
    if (reasonEl) reasonEl.value = '';
    errEl.textContent = '';
    input.classList.remove('error');
    if (reasonEl) reasonEl.classList.remove('error');
  }

  cancelBtn.addEventListener('click', _resetDialog);

  // Закрытие по клику на фон
  overlay.addEventListener('click', (e) => { if (e.target === overlay) _resetDialog(); });

  okBtn.addEventListener('click', async () => {
    const entered = (input.value || '').trim().toUpperCase().replace(/\s+/g, '');
    const reason  = (reasonEl ? reasonEl.value : '').trim();

    if (!entered) {
      errEl.textContent = 'Введите Шифр-ДопШифр для подтверждения';
      input.classList.add('error');
      return;
    }
    if (!reason) {
      errEl.textContent = 'Укажите причину удаления';
      if (reasonEl) { reasonEl.classList.add('error'); reasonEl.focus(); }
      return;
    }

    okBtn.disabled = true;
    okBtn.textContent = 'Отправка…';
    errEl.textContent = '';
    input.classList.remove('error');
    if (reasonEl) reasonEl.classList.remove('error');

    try {
      const res  = await fetch('/api/upload/request-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: editPublishedId, confirm_code: entered, reason }),
      });
      const data = await res.json();
      if (res.ok && data.ok) {
        _resetDialog();
        resetFormToNew();
        showNotice($('form-notice'), 'Запрос на удаление отправлен администраторам.', 'info');
      } else if (data.code_mismatch) {
        errEl.textContent = 'Введённый код не совпадает. Проверьте Шифр-ДопШифр.';
        input.classList.add('error');
      } else {
        errEl.textContent = data.error || 'Ошибка отправки запроса';
      }
    } catch (e) {
      errEl.textContent = 'Ошибка сети: ' + e.message;
    } finally {
      okBtn.disabled = false;
      okBtn.textContent = 'Удалить';
    }
  });
}

function openDeleteConfirmDialog() {
  const overlay = $('delete-confirm-overlay');
  const input    = $('delete-confirm-input');
  const reasonEl = $('delete-confirm-reason');
  const errEl    = $('delete-confirm-err');
  if (!overlay) return;
  input.value = '';
  if (reasonEl) reasonEl.value = '';
  errEl.textContent = '';
  input.classList.remove('error');
  if (reasonEl) reasonEl.classList.remove('error');
  overlay.classList.add('visible');
  setTimeout(() => input.focus(), 50);
}


// ---------------------------------------------------------------------------
// Таблица 10_up (только для админов)
// ---------------------------------------------------------------------------

async function loadStagedList() {
  const loading = $('staged-loading');
  const table   = $('staged-table');
  const empty   = $('staged-empty');
  const tbody   = $('staged-tbody');

  showBlock(loading);
  hide(table);
  hide(empty);

  try {
    const res  = await fetch('/api/upload/list');
    if (!res.ok) { hide(loading); showBlock(empty); return; }
    const data = await res.json();
    hide(loading);

    if (!data.reports || data.reports.length === 0) { showBlock(empty); return; }

    tbody.innerHTML = '';
    data.reports.forEach(r => {
      const tr = document.createElement('tr');
      tr.dataset.id = r.id;

      if (r.type === 'delete_request') {
        // Строка запроса на удаление — как обычный отчёт + бейдж + кнопки
        tr.className = 'row-link';
        const dlCell = r.has_archive
          ? dlIconLink(`/data/${encodeURIComponent(r.id + '.' + r.tip_faila)}`, true)
          : '';
        tr.innerHTML = `
          <td class="td-id">${escHtml(r.id)}<span class="badge badge-delete">удаление</span></td>
          <td class="td-route">${escHtml(r.marshrut || '—')}</td>
          <td>${escHtml(r.autor    || '—')}</td>
          <td>${escHtml(r.zagruzil || '—')}</td>
          <td>${r.god || '—'}</td>
          <td class="td-date">${formatDate(r.data_zagruzki)}</td>
          <td>${r.tip_faila ? r.tip_faila.toUpperCase() + (r.razmer ? ' ' + formatBytes(r.razmer) : '') : '—'}</td>
          <td>${dlCell}</td>
        `;
        // Клик по строке — открыть отчёт в режиме ревью удаления
        tr.addEventListener('click', (e) => {
          if (e.target.closest('.dl-icon')) return;
          enterEditPublishedMode(r.id, { reviewDelete: true });
        });
      } else {
        // Обычная строка отчёта
        tr.className = 'row-link';
        let editBadge = '';
        if (r.is_edit) {
          if (r.orig_id) {
            const tip = `Правка опубликованного отчёта ${r.orig_id}; при публикации старый отчёт будет удалён`;
            editBadge = `<span class="badge badge-edit" title="${escHtml(tip)}">ред.\u2190${escHtml(r.orig_id)}</span>`;
          } else {
            editBadge = '<span class="badge badge-edit">ред.</span>';
          }
        }
        const dlCell = r.has_archive
          ? dlIconLink(`/api/upload/file?id=${encodeURIComponent(r.id)}`)
          : '';
        tr.innerHTML = `
          <td class="td-id">${escHtml(r.id)}${editBadge}</td>
          <td class="td-route">${escHtml(r.marshrut || '—')}</td>
          <td>${escHtml(r.autor    || '—')}</td>
          <td>${escHtml(r.zagruzil || '—')}</td>
          <td>${r.god || '—'}</td>
          <td class="td-date">${formatDate(r.data_zagruzki)}</td>
          <td>${r.tip_faila ? r.tip_faila.toUpperCase() + (r.razmer ? ' ' + formatBytes(r.razmer) : '') : '—'}</td>
          <td>${dlCell}</td>
        `;
        tr.addEventListener('click', (e) => {
          if (e.target.closest('.dl-icon')) return;
          enterEditMode(r.id, { isEdit: !!r.is_edit });
        });
      }

      tbody.appendChild(tr);
    });

    show(table);
  } catch {
    hide(loading);
    showBlock(empty);
  }
}

// ---------------------------------------------------------------------------
// Раздел «Скрытые отчёты» (только для админов)
// ---------------------------------------------------------------------------

function initHiddenReportsBlock() {
  const textarea = $('hidden-reports-text');
  const saveBtn  = $('hidden-reports-save');
  if (!textarea || !saveBtn) return;

  textarea.addEventListener('input', () => {
    saveBtn.disabled = textarea.value.trim() === hiddenReportsOriginal.trim();
  });

  saveBtn.addEventListener('click', async () => {
    const notice = $('hidden-reports-notice');
    hideNotice(notice);
    saveBtn.disabled = true;
    try {
      const res = await fetch('/api/admin/hidden-reports', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: textarea.value }),
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        showNotice(notice, data.error || 'Ошибка сохранения списка', 'error');
        saveBtn.disabled = false;
        return;
      }
      hiddenReportsOriginal = data.text || '';
      textarea.value = hiddenReportsOriginal;
      showNotice(notice, 'Список скрытых отчётов сохранён.', 'success');
    } catch (e) {
      showNotice(notice, 'Ошибка сети: ' + e.message, 'error');
      saveBtn.disabled = false;
    }
  });
}

async function loadHiddenReports() {
  const textarea = $('hidden-reports-text');
  const saveBtn  = $('hidden-reports-save');
  if (!textarea || !saveBtn) return;
  try {
    const res = await fetch('/api/admin/hidden-reports');
    if (!res.ok) return;
    const data = await res.json();
    hiddenReportsOriginal = data.text || '';
    textarea.value = hiddenReportsOriginal;
    saveBtn.disabled = true;
  } catch {
    // Сеть недоступна — раздел останется с предыдущим/пустым содержимым, кнопка неактивна.
  }
}

// ---------------------------------------------------------------------------
// Раздел «Мои отчёты» (опубликованные отчёты текущего пользователя)
// ---------------------------------------------------------------------------

function initMyReportsCollapse() {
  const header = $('my-reports-header');
  if (!header) return;
  header.addEventListener('click', () => {
    const body  = $('my-reports-body');
    const arrow = $('my-reports-arrow');
    const collapsed = body.style.display === 'none';
    body.style.display = collapsed ? '' : 'none';
    if (arrow) arrow.classList.toggle('expanded', collapsed);
  });
}

async function loadMyReports() {
  const section = $('my-reports-section');
  const tbody   = $('my-reports-tbody');
  const countEl = $('my-reports-count');
  if (!section || !currentUser) return;

  try {
    const res = await fetch('/api/upload/my-reports');
    if (!res.ok) { hide(section); return; }
    const data = await res.json();
    const reports = (data.ok && data.reports) ? data.reports : [];

    if (reports.length === 0) { hide(section); return; }

    tbody.innerHTML = '';
    reports.forEach(r => {
      const tr = document.createElement('tr');
      const dlCell = r.has_archive
        ? dlIconLink(`/data/${encodeURIComponent(r.id + '.' + r.tip_faila)}`)
        : '';
      tr.innerHTML = `
        <td class="td-id">${escHtml(r.id)}</td>
        <td class="td-route">${escHtml(r.marshrut || '—')}</td>
        <td>${escHtml(r.autor || '—')}</td>
        <td>${r.god || '—'}</td>
        <td class="td-date">${formatDate(r.data_zagruzki)}</td>
        <td>${r.tip_faila ? r.tip_faila.toUpperCase() + (r.razmer ? ' ' + formatBytes(r.razmer) : '') : '—'} ${dlCell}</td>
        <td><button type="button" class="my-reports-edit-btn" data-id="${escHtml(r.id)}">Редактировать</button></td>
      `;
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll('.my-reports-edit-btn').forEach(btn => {
      btn.addEventListener('click', () => enterEditPublishedMode(btn.dataset.id));
    });

    if (countEl) countEl.textContent = ` (${reports.length})`;
    showBlock(section);
  } catch {
    hide(section);
  }
}

// ---------------------------------------------------------------------------
// Действия администратора над запросами на удаление
// ---------------------------------------------------------------------------

async function doConfirmDelete(id) {
  if (!confirm(`Подтвердить удаление отчёта ${id} из библиотеки?\nЭто действие необратимо.`)) return;
  const adminComment = ($('f-admin-comment') ? $('f-admin-comment').value : '').trim();
  try {
    const res  = await fetch('/api/upload/confirm-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, admin_comment: adminComment, no_email: ($('f-no-email') ? $('f-no-email').checked : false) }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      resetFormToNew();
      showNotice($('form-notice'), `Удаление отчёта ${id} запущено. File Watcher обработает задачу.`, 'info');
      loadStagedList();
    } else {
      showNotice($('form-notice'), data.error || 'Ошибка подтверждения удаления', 'error');
    }
  } catch (e) {
    showNotice($('form-notice'), 'Ошибка сети: ' + e.message, 'error');
  }
}

async function doRejectDelete(id) {
  const adminComment = ($('f-admin-comment') ? $('f-admin-comment').value : '').trim();
  try {
    const res  = await fetch('/api/upload/reject-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, admin_comment: adminComment, no_email: ($('f-no-email') ? $('f-no-email').checked : false) }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      resetFormToNew();
      showNotice($('form-notice'), `Запрос на удаление отчёта ${id} отклонён.`, 'info');
      loadStagedList();
    } else {
      showNotice($('form-notice'), data.error || 'Ошибка отклонения запроса', 'error');
    }
  } catch (e) {
    showNotice($('form-notice'), 'Ошибка сети: ' + e.message, 'error');
  }
}


// ---------------------------------------------------------------------------
// Инициализация
// ---------------------------------------------------------------------------

// Применяет состояние «пользователь авторизован»: показывает форму, подгружает шифр, обновляет кнопки.
// Вызывается как при первоначальной загрузке, так и при повторной проверке при возврате на вкладку.
async function applyAuthenticatedState() {
  renderAuthState(currentUser);
  await loadDefaultCode();
  $('f-zagruzil').value = currentUser.name || '';
  if (isAdminUser()) {
    showUploaderInfo(currentUser.id ?? null, currentUser.name || '', currentUser.email || '');
  }
  updateActionBtns();
}

// Повторно проверяет авторизацию — вызывается при возврате на вкладку.
// Если пользователь уже авторизован — запрос не делается.
async function recheckAuthOnReturn() {
  if (currentUser) return;
  const user = await checkAuth();
  if (!user) return;
  currentUser = user;
  await applyAuthenticatedState();
}

async function init() {
  initAuthForm();
  initCodeFields();
  initFileWidget();
  initReportForm();
  initUploaderBlock();
  initHiddenReportsBlock();
  initMyReportsCollapse();

  await Promise.all([
    loadReferenceLists(),
    (async () => {
      currentUser = await checkAuth();
      if (currentUser) {
        await applyAuthenticatedState();
      } else {
        renderAuthState(null);
      }
    })(),
  ]);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') recheckAuthOnReturn();
  });
  window.addEventListener('focus', recheckAuthOnReturn);
}

init();
