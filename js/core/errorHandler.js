// Version 1.6 - 14.06.2026
// Система обработки ошибок с типизацией
// Описание: Предоставляет базовый класс ошибок AppError
//           и класс ErrorHandler для централизованной обработки ошибок. ErrorHandler обрабатывает ошибки различных типов,
//           генерирует понятные сообщения для пользователя, логирует детали ошибок в консоль, отображает ошибки в UI.
//           Поддерживает sticky-сообщения (например, ошибка обновления справочников), которые остаются видимыми после clearError().
// 1.6: удалён класс NetworkError (нигде не создавался) и недостижимая ветка instanceof NetworkError.

import { CONSTANTS } from '../config/constants.js';

/**
 * Базовый класс ошибок
 */
class AppError extends Error {
    constructor(message, type = 'GENERIC', originalError = null) {
        super(message);
        this.name = 'AppError';
        this.type = type;
        this.originalError = originalError;
        this.timestamp = new Date().toISOString();
    }
}

/**
 * Обработчик ошибок
 */
class ErrorHandler {
    static instance = null;

    constructor() {
        if (ErrorHandler.instance) {
            return ErrorHandler.instance;
        }
        ErrorHandler.instance = this;
        this.errorElement = null;
        this.transientMessage = '';
        this.stickyMessage = '';
        this.initializeErrorElement();
    }

    /**
     * Инициализирует элемент для ошибок
     */
    initializeErrorElement() {
        this.errorElement = document.querySelector(CONSTANTS.SELECTORS.ERROR_MSG);
        if (!this.errorElement) {
            console.warn('ErrorHandler: элемент для отображения ошибок не найден');
        }
    }

    /**
     * Обновляет отображаемое сообщение в UI (transient имеет приоритет над sticky).
     */
    render() {
        const message = this.transientMessage || this.stickyMessage || '';
        if (this.errorElement) {
            this.errorElement.textContent = message;
        }
    }

    /**
     * Обрабатывает ошибку
     */
    handle(error, showToUser = true) {
        const errorInfo = this.processError(error);
        
        this.logError(errorInfo);
        
        if (showToUser) {
            this.showToUser(errorInfo.userMessage);
        }

        return errorInfo;
    }

    /**
     * Обрабатывает ошибку и извлекает информацию
     */
    processError(error) {
        if (typeof error === 'string') {
            return {
                message: error,
                userMessage: error,
                type: 'GENERIC',
                timestamp: new Date().toISOString()
            };
        }

        if (error instanceof AppError) {
            return {
                message: error.message,
                userMessage: this.getUserFriendlyMessage(error),
                type: error.type,
                originalError: error.originalError,
                timestamp: error.timestamp
            };
        }

        if (error instanceof Error) {
            return {
                message: error.message,
                userMessage: this.getUserFriendlyMessage(error),
                type: 'GENERIC',
                originalError: error,
                timestamp: new Date().toISOString()
            };
        }

        return {
            message: CONSTANTS.MESSAGES.UNKNOWN_ERROR,
            userMessage: CONSTANTS.MESSAGES.UNKNOWN_ERROR,
            type: 'GENERIC',
            timestamp: new Date().toISOString()
        };
    }

    /**
     * Генерирует сообщение для пользователя
     */
    getUserFriendlyMessage(error) {
        return error.message || CONSTANTS.MESSAGES.GENERIC_ERROR;
    }

    /**
     * Логирует ошибку
     */
    logError(errorInfo) {
        const logMessage = `[${errorInfo.timestamp}] ${errorInfo.type}: ${errorInfo.message}`;
        
        if (errorInfo.originalError) {
            console.error(logMessage, errorInfo.originalError);
        } else {
            console.error(logMessage);
        }
    }

    /**
     * Показывает ошибку пользователю
     */
    showToUser(message) {
        this.transientMessage = message || '';
        if (!this.errorElement) {
            alert(message);
            return;
        }
        this.render();
    }

    /**
     * Устанавливает sticky-сообщение (не очищается через clearError()).
     */
    setSticky(message) {
        this.stickyMessage = message || '';
        this.render();
    }

    /**
     * Очищает sticky-сообщение.
     */
    clearSticky() {
        this.stickyMessage = '';
        this.render();
    }

    /**
     * Очищает сообщение об ошибке
     */
    clearError() {
        this.transientMessage = '';
        this.render();
    }
}

// Создаем единственный экземпляр обработчика ошибок
export const errorHandler = new ErrorHandler();
