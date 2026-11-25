// Описание: Промис «на странице был жест пользователя». Headless-боты исполняют наш JS,
//           но не двигают мышь и не касаются экрана, поэтому всё, что запускает конвертацию
//           PDF→PNG на сервере (cacheWarmService, pdfViewer), ждёт этот промис.
//           Первый жест за загрузку страницы ещё и шлёт маячок /api/gesture: по нему
//           статистика считает IP человеком. Слушатели ставятся при загрузке модуля,
//           чтобы учесть жесты до первого вызова.
//           scroll намеренно не учитывается: его эмулируют скраперы для lazy-load.
//           Программный element.click() жестом не считается: он не порождает pointerdown.

import { API } from '../config/api.config.js';

const GESTURE_EVENTS = ['pointerdown', 'pointermove', 'wheel', 'touchstart', 'keydown'];

let happened = false;

function sendGestureBeacon() {
    if (typeof navigator.sendBeacon === 'function') {
        navigator.sendBeacon(API.GESTURE);
    }
}

const gesture = new Promise(resolve => {
    if (navigator.userActivation?.hasBeenActive) {
        happened = true;
        sendGestureBeacon();
        resolve();
        return;
    }
    const options = { capture: true, passive: true };
    const onGesture = () => {
        GESTURE_EVENTS.forEach(type => document.removeEventListener(type, onGesture, options));
        happened = true;
        sendGestureBeacon();
        resolve();
    };
    GESTURE_EVENTS.forEach(type => document.addEventListener(type, onGesture, options));
});

/**
 * Был ли жест пользователя к этому моменту (синхронно, без ожидания промиса).
 * @returns {boolean}
 */
export function hasUserGesture() {
    return happened;
}

/**
 * Возвращает общий промис, который резолвится при первом жесте пользователя.
 * @returns {Promise<void>}
 */
export function whenUserGesture() {
    return gesture;
}
