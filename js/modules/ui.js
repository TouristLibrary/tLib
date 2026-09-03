// Version 1.0 - 07.01.2026 09:55:58 GMT
// Описание: Фасадный модуль UI. Реэкспортирует публичные компоненты UI (контроллер, рендер результатов, менеджеры формы/кнопок),
//           сохраняя обратную совместимость для импортов из js/modules/ui.js.

export { ButtonManager } from './ui/form.js';
export { FormManager } from './ui/form.js';
export { ResultsRenderer } from './ui/results/index.js';
export { UIController } from './ui/controller.js';

