"""Local deployment tooling.

These assert the operational scripts stay correct, since a broken installer is only
discovered at the moment someone needs it.
"""
import os
import stat
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class CliTests(unittest.TestCase):
    def test_cli_is_executable(self):
        cli = ROOT / "winstonctl"
        self.assertTrue(cli.exists())
        self.assertTrue(cli.stat().st_mode & stat.S_IXUSR, "winstonctl is not executable")

    def test_cli_does_not_shadow_the_package(self):
        """An executable named `winston` would shadow winston/ on the import path."""
        self.assertFalse((ROOT / "winston").is_file())
        self.assertTrue((ROOT / "winston").is_dir())

    def test_help_lists_every_command(self):
        result = subprocess.run(["./winstonctl"], cwd=ROOT, capture_output=True, text=True)
        for command in ("start", "stop", "restart", "status", "open", "logs",
                        "doctor", "backup", "install", "uninstall"):
            self.assertIn(command, result.stdout)

    def test_scripts_are_executable(self):
        for name in ("install_winston_service.sh", "uninstall_winston_service.sh", "doctor.py"):
            script = ROOT / "scripts" / name
            self.assertTrue(script.exists(), f"{name} missing")
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR, f"{name} is not executable")


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "scripts" / "install_winston_service.sh").read_text()

    def test_installer_does_not_hardcode_a_username(self):
        self.assertNotIn("/Users/kevinedwards", self.source,
                         "the installer must work for any user")

    def test_installer_validates_the_plist(self):
        self.assertIn("plutil -lint", self.source)

    def test_service_binds_to_localhost_only(self):
        """Winston holds prospect PII and an SMTP credential."""
        self.assertIn("127.0.0.1", self.source)
        self.assertNotIn("0.0.0.0", self.source)

    def test_restart_policy_cannot_spin(self):
        self.assertIn("ThrottleInterval", self.source)
        self.assertIn("SuccessfulExit", self.source)

    def test_uninstaller_preserves_data(self):
        source = (ROOT / "scripts" / "uninstall_winston_service.sh").read_text()
        self.assertNotIn("rm -rf", source)
        self.assertNotIn("winston.db", source.replace("not touched", ""))


class BackupTests(unittest.TestCase):
    def test_backup_uses_sqlite_backup_not_copy(self):
        """cp against a live WAL database produces a torn file."""
        source = (ROOT / "winstonctl").read_text()
        self.assertIn(".backup", source)
        self.assertIn("integrity_check", source)

    def test_backup_retains_history(self):
        source = (ROOT / "winstonctl").read_text()
        self.assertIn("tail -n +15", source, "backups must not be reduced to one copy")


class DoctorTests(unittest.TestCase):
    def test_doctor_runs_and_reports(self):
        result = subprocess.run(
            [str(ROOT / "venv" / "bin" / "python3") if (ROOT / "venv" / "bin" / "python3").exists()
             else "python3", str(ROOT / "scripts" / "doctor.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertIn("Environment", result.stdout)
        self.assertIn("Database", result.stdout)
        self.assertIn("Local AI", result.stdout)
        self.assertIn("Service", result.stdout)
        self.assertRegex(result.stdout, r"\d+ passed, \d+ warnings, \d+ failures")

    def test_doctor_checks_are_real_not_assumed(self):
        source = (ROOT / "scripts" / "doctor.py").read_text()
        self.assertIn("integrity_check", source)
        self.assertIn("/api/tags", source, "Ollama must actually be contacted")
        self.assertIn("connect_ex", source, "the port must actually be probed")


if __name__ == "__main__":
    unittest.main()
