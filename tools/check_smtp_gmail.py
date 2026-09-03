# Version 1.1 - 12.06.2026 18:00:00 GMT
# Утилита ручной проверки отправки email через Gmail SMTP
# Описание: Интерактивный скрипт для проверки корректности SMTP-настроек Gmail.
#           Запрашивает email отправителя, App Password и email получателя,
#           отправляет тестовое письмо и выводит результат.
#           Запуск: python tools/check_smtp_gmail.py
#           Требует: Gmail аккаунт с включённой 2FA и App Password.

import smtplib
from email.mime.text import MIMEText


def main():
    print("=== Тест отправки email через Gmail SMTP ===\n")

    sender   = input("Gmail отправителя : ").strip()
    password = input("App Password      : ").strip()
    to       = input("Email получателя  : ").strip() or sender

    msg = MIMEText("Это тестовое письмо от tLib.\nSMTP работает.", "plain", "utf-8")
    msg["From"]    = sender
    msg["To"]      = to
    msg["Subject"] = "tLib — тест SMTP"

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as srv:
            srv.starttls()
            srv.login(sender, password)
            srv.sendmail(sender, to, msg.as_string())
        print("\n✓ Письмо отправлено успешно.")
    except smtplib.SMTPAuthenticationError:
        print("\n✗ Ошибка аутентификации. Проверьте App Password и что 2FA включена.")
    except smtplib.SMTPException as e:
        print(f"\n✗ SMTP ошибка: {e}")
    except OSError as e:
        print(f"\n✗ Сетевая ошибка: {e}")


if __name__ == "__main__":
    main()
