import os
from unittest.mock import patch

import pytest

from app.config import Settings

ENV_FILE_TESTS = ".env-inexistente-para-tests"


def settings_con_env(env: dict[str, str]) -> Settings:
    with patch.dict(os.environ, env):
        return Settings(_env_file=ENV_FILE_TESTS)


class TestDefaultsDesarrollo:
    def test_defaults_dev_sin_env(self):
        s = settings_con_env({})
        assert s.cors_origins == ["http://localhost:5173", "http://localhost:3000"]
        assert s.cookie_secure is False
        assert s.cookie_samesite == "lax"
        assert s.debug is True


class TestAliasAllowedOrigins:
    def test_allowed_origins_json_se_parsea_a_lista(self):
        s = settings_con_env({"ALLOWED_ORIGINS": '["https://a.vercel.app"]'})
        assert s.cors_origins == ["https://a.vercel.app"]

    def test_cors_origins_alias_actual_sigue_funcionando(self):
        s = settings_con_env({"CORS_ORIGINS": '["https://b.vercel.app"]'})
        assert s.cors_origins == ["https://b.vercel.app"]

    def test_varios_origenes_en_json(self):
        s = settings_con_env(
            {"ALLOWED_ORIGINS": '["https://a.vercel.app", "https://preview.vercel.app"]'}
        )
        assert s.cors_origins == ["https://a.vercel.app", "https://preview.vercel.app"]

    def test_allowed_origins_mal_formado_falla_fast(self):
        # Spec: JSON inválido → error de arranque, no CORS abierto ni vacío.
        with pytest.raises(ValueError):
            settings_con_env({"ALLOWED_ORIGINS": "no-es-json"})


class TestCookieConfigurable:
    def test_cookie_samesite_none_por_env(self):
        s = settings_con_env({"COOKIE_SAMESITE": "none"})
        assert s.cookie_samesite == "none"

    def test_cookie_samesite_default_lax(self):
        s = settings_con_env({})
        assert s.cookie_samesite == "lax"

    def test_cookie_secure_true_por_env(self):
        s = settings_con_env({"COOKIE_SECURE": "true"})
        assert s.cookie_secure is True


class TestDebugConfigurable:
    def test_debug_false_por_env(self):
        s = settings_con_env({"DEBUG": "false"})
        assert s.debug is False

    def test_debug_default_true(self):
        s = settings_con_env({})
        assert s.debug is True