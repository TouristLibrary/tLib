// Version 1.5 - 21.06.2026
// Login page JS для TlibWebApp
// Описание: Magic link + цифровой код авторизации.
//           Состояния: загрузка → форма email → форма кода → авторизован.
//           При загрузке страницы проверяет /api/auth/me и переключает состояние.
// Изменения v1.4: убран no-op 'use strict' (ESM-модуль — strict включён всегда).
// Изменения v1.5: кнопка «Выйти» заменена dropdown-меню (выход / выход со всех устройств).

import { getCurrentUser, requestLink, verifyCode, logout } from './services/authService.js';
import { attachLogoutMenu } from './modules/logoutMenu.js';

const $ = id => document.getElementById(id);

function showState(state) {
  $('stateLoading').style.display = 'none';
  $('stateLogin').style.display   = state === 'login' ? 'block' : 'none';
  $('stateAuth').style.display    = state === 'auth'  ? 'block' : 'none';
}

function showMessage(text, type = 'error') {
  const el = $('formMessage');
  el.textContent = text;
  el.className = `message ${type}`;
  el.style.display = 'block';
}

function hideMessage() {
  $('formMessage').style.display = 'none';
}

// ---- проверка авторизации при загрузке ----

async function checkAuth() {
  const user = await getCurrentUser();
  if (user) {
    showAuthState(user);
  } else {
    const params = new URLSearchParams(window.location.search);
    if (params.get('error') === 'expired') {
      showState('login');
      showMessage('Ссылка устарела или уже была использована. Запросите новую.', 'warn');
    } else {
      showState('login');
    }
  }
}

function showAuthState(user) {
  $('userName').textContent  = user.name  || '';
  $('userEmail').textContent = user.email || '';
  if (user.role) {
    $('userRole').textContent     = user.role;
    $('userRoleWrap').style.display = 'block';
  } else {
    $('userRoleWrap').style.display = 'none';
  }
  showState('auth');
}

// ---- переключение между шагами формы ----

let _pendingEmail = '';

function showCodeStep(email) {
  _pendingEmail = email;
  $('emailGroup').style.display = 'none';
  $('codeGroup').style.display  = 'block';
  $('loginSubtitle').textContent = `Код отправлен на ${email}`;
  hideMessage();
  $('codeInput').value = '';
  $('codeInput').focus();
}

function showEmailStep() {
  _pendingEmail = '';
  $('emailGroup').style.display = 'block';
  $('codeGroup').style.display  = 'none';
  $('loginSubtitle').textContent = 'Введите email — мы пришлём ссылку и код для входа';
  hideMessage();
}

// ---- форма входа: шаг 1 — запрос ссылки ----

$('loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  hideMessage();

  const email = $('emailInput').value.trim();
  const name  = $('nameInput').value.trim();

  if (!email) {
    showMessage('Введите email');
    return;
  }

  const btn = $('submitBtn');
  btn.disabled = true;
  btn.textContent = 'Отправляем…';

  try {
    const result = await requestLink({ email, name });

    if (result.ok) {
      showCodeStep(email);
    } else if (result.status === 429) {
      showMessage(result.data.error || 'Ссылка уже была отправлена. Подождите минуту.', 'warn');
    } else if (result.status === 403) {
      showMessage(result.data.error || 'Доступ заблокирован.', 'error');
    } else {
      showMessage(result.data.error || 'Не удалось отправить письмо. Попробуйте позже.', 'error');
    }
  } catch {
    showMessage('Ошибка сети. Проверьте соединение.', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Получить ссылку';
  }
});

// ---- форма входа: шаг 2 — проверка кода ----

async function submitCode() {
  hideMessage();

  const code = $('codeInput').value.trim();
  if (!code) {
    showMessage('Введите код из письма');
    return;
  }

  const btn = $('verifyBtn');
  btn.disabled = true;
  btn.textContent = 'Проверяем…';

  try {
    const result = await verifyCode(_pendingEmail, code);

    if (result.ok) {
      window.location.reload();
    } else {
      showMessage(result.data.error || 'Неверный или устаревший код.', 'error');
      $('codeInput').value = '';
      $('codeInput').focus();
    }
  } catch {
    showMessage('Ошибка сети. Проверьте соединение.', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Войти';
  }
}

$('verifyBtn').addEventListener('click', submitCode);

$('codeInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    submitCode();
  }
});

// ---- кнопка "Запросить новый код" ----

$('resendBtn').addEventListener('click', () => {
  showEmailStep();
});

// ---- кнопка выхода (dropdown) ----

attachLogoutMenu($('logoutBtn'), {
  onLogout: async () => {
    $('logoutBtn').disabled = true;
    await logout();
    window.location.reload();
  },
  onLogoutAll: async () => {
    $('logoutBtn').disabled = true;
    await logout({ all: true });
    window.location.reload();
  },
});

// ---- старт ----

checkAuth();
