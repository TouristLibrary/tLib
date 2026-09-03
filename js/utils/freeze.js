/**
 * Deep freeze для иммутабельности вложенных объектов
 * НЕ замораживает RegExp объекты (они имеют изменяемое свойство lastIndex)
 */
export const deepFreeze = (obj) => {
    Object.keys(obj).forEach(key => {
        const value = obj[key];
        if (value && typeof value === 'object' && !(value instanceof RegExp) && !Object.isFrozen(value)) {
            deepFreeze(value);
        }
    });
    return Object.freeze(obj);
};

/**
 * Возвращает Promise, который резолвится через указанное количество миллисекунд
 */
export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}
