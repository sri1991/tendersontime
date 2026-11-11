"""Offline credential regressions. No database, API, or deployment calls."""
import ast
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "app" if (ROOT / "app/config.py").exists() else "src"
SPEC = importlib.util.spec_from_file_location("credential_config", ROOT / PACKAGE / "credential_config.py")
CONFIG = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONFIG)


class CredentialConfigurationTests(unittest.TestCase):
    def test_missing_or_blank_database_url_is_rejected(self):
        for value in (None, "", " ", "\t\n"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "DATABASE_URL is required"):
                CONFIG.require_database_url(value)

    def test_explicit_database_url_is_preserved(self):
        value = "postgresql+asyncpg://example-user:test-only@localhost:5432/example"
        self.assertEqual(CONFIG.require_database_url(" " + value + " "), value)

    def test_compose_requires_nonempty_password_and_url(self):
        source = (ROOT / "docker-compose.yml").read_text()
        password_lines = [line for line in source.splitlines() if "POSTGRES_PASSWORD:" in line]
        url_lines = [line for line in source.splitlines() if "- DATABASE_URL=" in line]
        self.assertTrue(password_lines)
        self.assertTrue(url_lines)
        self.assertTrue(all("${POSTGRES_PASSWORD:?" in line for line in password_lines))
        self.assertTrue(all("${DATABASE_URL:?" in line for line in url_lines))
        self.assertNotIn("${POSTGRES_PASSWORD:-", source)

    def test_example_credentials_are_empty(self):
        example = ROOT / (".env.example" if PACKAGE == "app" else "deploy/.env.example")
        values = dict(line.split("=", 1) for line in example.read_text().splitlines()
                      if "=" in line and not line.startswith("#"))
        for key in ("GEMINI_API_KEY", "POSTGRES_PASSWORD", "DATABASE_URL"):
            self.assertEqual(values[key], "", key)

    def test_environment_files_are_ignored_but_examples_are_not(self):
        paths = [".env", ".env.production", "deploy/.env.staging", "local.env",
                 ".env.example", "deploy/.env.example"]
        result = subprocess.run(["git", "check-ignore", "--no-index", "--stdin"],
                                cwd=ROOT, input="\n".join(paths), text=True,
                                capture_output=True)
        self.assertIn(result.returncode, (0, 1))
        self.assertEqual(set(result.stdout.splitlines()), set(paths[:4]))

    def test_no_literal_database_passwords_or_google_keys(self):
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        google_pattern = re.compile("AI" + "za" + r"[A-Za-z0-9_-]{35}")
        url_pattern = re.compile(r"postgres(?:ql)?(?:\+asyncpg)?://[^:\s/@]+:([^@\s]+)@")
        for name in tracked:
            if not name or name.startswith("tests/"):
                continue
            path = ROOT / name
            try:
                source = path.read_text()
            except (UnicodeDecodeError, IsADirectoryError):
                continue
            self.assertIsNone(google_pattern.search(source), f"Google-key-shaped value in {name}")
            for match in url_pattern.finditer(source):
                self.assertTrue("$" in match[1] or "<" in match[1],
                                f"Literal database password in {name}")

    def test_runtime_uses_required_database_url(self):
        if PACKAGE == "app":
            from app.config import Settings
            from pydantic import ValidationError
            with patch.dict(os.environ, {}, clear=True):
                for kwargs in ({}, {"database_url": ""}, {"database_url": "  "}):
                    with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                        Settings(_env_file=None, gemini_api_key="test-only", **kwargs)
                value = "postgresql+asyncpg://example-user:test-only@localhost:5432/example"
                settings = Settings(_env_file=None, gemini_api_key="test-only", database_url=value)
                self.assertEqual(settings.database_url, value)
        else:
            # Evaluate the actual configuration block without importing database drivers.
            module = ast.parse((ROOT / "src/db/schema.py").read_text())
            assignment = next(node for node in module.body if isinstance(node, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == "DATABASE_URL" for t in node.targets))
            self.assertIsInstance(assignment.value, ast.Call)
            self.assertEqual(assignment.value.func.id, "require_database_url")
            code = compile(ast.Module(body=[assignment], type_ignores=[]), "schema configuration", "exec")
            for env in ({}, {"DATABASE_URL": ""}, {"DATABASE_URL": "  "}):
                with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
                    exec(code, {"os": os, "require_database_url": CONFIG.require_database_url})
            value = "postgresql+asyncpg://example-user:test-only@localhost:5432/example"
            with patch.dict(os.environ, {"DATABASE_URL": value}, clear=True):
                namespace = {"os": os, "require_database_url": CONFIG.require_database_url}
                exec(code, namespace)
                self.assertEqual(namespace["DATABASE_URL"], value)
            source = (ROOT / "src/db/schema.py").read_text()
            self.assertLess(source.index("load_dotenv()"), source.index("DATABASE_URL ="))
            self.assertLess(source.index("DATABASE_URL ="), source.index("engine = create_async_engine"))

    def test_benchmark_has_no_database_fallback(self):
        path = ROOT / "scripts/benchmark_vs_solr.py"
        if not path.exists():
            self.skipTest("Benchmark is not on this branch")
        source = path.read_text()
        self.assertIn('DB_URL = require_database_url(os.getenv("DATABASE_URL"))', source)

    def test_deploy_scripts_use_generated_passwords(self):
        path = ROOT / "deploy/gcp_deploy.sh"
        if not path.exists():
            self.skipTest("GCP deployment script is not on this branch")
        source = path.read_text()
        self.assertIn("openssl rand", source)
        self.assertIn("POSTGRES_PASSWORD=${DB_PASSWORD}", source)
        self.assertNotIn("sed -i 's/POSTGRES_PASSWORD", source)
        alternate = ROOT / "scripts/deploy_gcp.sh"
        if alternate.exists():
            source = alternate.read_text()
            self.assertIn("openssl rand -hex 24", source)
            self.assertIn("POSTGRES_PASSWORD=${DB_PASSWORD}", source)
            self.assertIn("DATABASE_URL=postgresql+asyncpg://tender:${DB_PASSWORD}@", source)


if __name__ == "__main__":
    unittest.main()
