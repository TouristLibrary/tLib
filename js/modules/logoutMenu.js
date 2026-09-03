// Version 1.0 - 21.06.2026
// Мини-меню выхода (dropdown) для кнопки «Выйти»
// Описание: Создаёт dropdown с двумя пунктами («Выйти» / «Выйти со всех устройств»)
//           рядом с переданной кнопкой. Закрывается по клику вне и по Escape.
//           DRY-модуль: подключается к login.js, admin.js, upload.js без дублирования логики.
//           Не трогает sidebarAuth (иконка в сайдбаре — одиночный выход).

/**
 * Прикрепляет dropdown-меню выхода к кнопке «Выйти».
 *
 * @param {HTMLElement} buttonEl - кнопка «Выйти», к которой привязывается меню.
 * @param {{ onLogout: () => void, onLogoutAll: () => void }} callbacks
 *   onLogout    — обработчик выхода с текущего устройства.
 *   onLogoutAll — обработчик выхода со всех устройств.
 */
export function attachLogoutMenu(buttonEl, { onLogout, onLogoutAll }) {
    if (!buttonEl) return;

    let menu = null;

    function _closeMenu() {
        if (menu) {
            menu.remove();
            menu = null;
        }
    }

    function _openMenu() {
        _closeMenu();

        menu = document.createElement('div');
        menu.className = 'logout-dropdown';
        menu.setAttribute('role', 'menu');

        const itemLogout = document.createElement('button');
        itemLogout.className = 'logout-dropdown-item';
        itemLogout.type = 'button';
        itemLogout.textContent = 'Выйти';
        itemLogout.setAttribute('role', 'menuitem');
        itemLogout.addEventListener('click', () => {
            _closeMenu();
            onLogout();
        });

        const itemLogoutAll = document.createElement('button');
        itemLogoutAll.className = 'logout-dropdown-item';
        itemLogoutAll.type = 'button';
        itemLogoutAll.textContent = 'Выйти со всех устройств';
        itemLogoutAll.setAttribute('role', 'menuitem');
        itemLogoutAll.addEventListener('click', () => {
            _closeMenu();
            onLogoutAll();
        });

        menu.appendChild(itemLogout);
        menu.appendChild(itemLogoutAll);

        // Позиционируем под кнопкой
        const rect = buttonEl.getBoundingClientRect();
        menu.style.position = 'fixed';
        menu.style.top = `${rect.bottom + 4}px`;
        menu.style.left = `${rect.left}px`;
        menu.style.minWidth = `${Math.max(rect.width, 200)}px`;
        menu.style.zIndex = '9999';

        document.body.appendChild(menu);
    }

    buttonEl.addEventListener('click', (e) => {
        e.stopPropagation();
        if (menu) {
            _closeMenu();
        } else {
            _openMenu();
        }
    });

    document.addEventListener('click', _closeMenu);

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') _closeMenu();
    });
}
