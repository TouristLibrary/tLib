// Version 1.0 - 24.09.2026 19:45:00 GMT
// Описание: Промис «на странице был жест пользователя». Headless-боты исполняют наш JS,
//           но не двигают мышь и не касаются экрана, поэтому всё, что запускает конвертацию
//           PDF→PNG на сервере (cacheWarmService, pdfViewer), ждёт этот промис.
//           Слушатели ставятся при загрузке модуля, чтобы учесть жесты до первого вызова.
//           scroll намеренно не учитывается: его эмулируют скраперы для lazy-load.
//           Программный element.click() жестом не считается: он не порождает pointerdown.

const GESTURE_EVENTS = ['pointerdown', 'pointermove', 'wheel', 'touchstart', 'keydown'];

const gesture = new Promise(resolve => {
    if (navigator.userActivation?.hasBeenActive) {
        resolve();
        return;
    }
    const options = { capture: true, passive: true };
    const onGesture = () => {
        GESTURE_EVENTS.forEach(type => document.removeEventListener(type, onGesture, options));
        resolve();
    };
    GESTURE_EVENTS.forEach(type => document.addEventListener(type, onGesture, options));
});

/**
 * Возвращает общий промис, который резолвится при первом жесте пользователя.
 * @returns {Promise<void>}
 */
export function whenUserGesture() {
    return gesture;
}
