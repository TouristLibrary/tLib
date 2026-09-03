# Version 1.0 - 24.06.2026 23:00:00 GMT
# Integration smoke-тесты для CLI-утилиты tools/manage_users.py
# Описание: Проверяют, что модуль импортируется и ключевые команды работают на временной auth.db.
#           Ловят регрессии вида «удалили функцию в auth_db, которую использует утилита» —
#           именно такая ошибка возникла с get_all_sessions() в v1.10.
#           Импорт модуля на уровне файла → ошибка коллекции pytest при сломанном импорте.

import tools.manage_users as mu
from services.auth.auth_db import create_session, find_or_create_user
from tests.integration.conftest import auth_db_path  # noqa: F401 (фикстура)


class TestManageUsersCli:
    def test_module_imports(self):
        # Доступность всех публичных команд означает, что импорты в модуле не сломаны
        assert callable(mu.cmd_list)
        assert callable(mu.cmd_sessions)
        assert callable(mu.cmd_deactivate)
        assert callable(mu.cmd_activate)
        assert callable(mu.cmd_delete)
        assert callable(mu.cmd_grant)
        assert callable(mu.cmd_revoke)

    def test_cmd_list_smoke(self, auth_db_path, capsys):
        find_or_create_user("cli_user@test", "CLI User")
        mu.cmd_list()
        assert "cli_user@test" in capsys.readouterr().out

    def test_cmd_list_empty(self, auth_db_path, capsys):
        mu.cmd_list()
        assert "Пользователей нет" in capsys.readouterr().out

    def test_cmd_sessions_smoke(self, auth_db_path, capsys):
        user = find_or_create_user("cli_sess@test", "CLI Sess")
        create_session(user["id"], ip="1.2.3.4")
        mu.cmd_sessions()
        out = capsys.readouterr().out
        assert "cli_sess@test" in out
        assert "1.2.3.4" in out

    def test_cmd_sessions_empty(self, auth_db_path, capsys):
        mu.cmd_sessions()
        assert "Активных сессий нет" in capsys.readouterr().out
