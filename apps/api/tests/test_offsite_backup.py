import importlib.util
from pathlib import Path
import stat

import pytest

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("offsite_backup", root / "scripts/backup_offsite.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def _configuration(tmp_path):
    password = tmp_path / "password"
    backup.prepare_password(password)
    return {"RESTIC_REPOSITORY": "s3:https://s3.us-east-1.amazonaws.com/heatshift-unit-test", "RESTIC_PASSWORD_FILE": str(password)}


def test_backup_password_is_private_random_and_never_overwritten(tmp_path):
    env = _configuration(tmp_path)
    path = Path(env["RESTIC_PASSWORD_FILE"])
    before = path.read_bytes()
    assert len(before.strip()) >= 32 and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert backup.validate_configuration(env) is False
    with pytest.raises(FileExistsError):
        backup.prepare_password(path)
    assert path.read_bytes() == before
    path.chmod(0o644)
    with pytest.raises(backup.BackupError):
        backup.validate_configuration(env)


@pytest.mark.parametrize("repository", [
    "/tmp/not-offsite", "s3:http://storage.example.org/bucket", "s3:https://localhost/bucket",
    "s3:https://127.0.0.1/bucket", "s3:https://u:unit-secret@storage.example.org/bucket",
    "s3:https://storage.example.org/bucket?token=unit-secret",
])
def test_backup_refuses_unapproved_local_insecure_or_secret_bearing_destinations(tmp_path, repository):
    env = {**_configuration(tmp_path), "RESTIC_REPOSITORY": repository}
    with pytest.raises(backup.BackupError) as error:
        backup.validate_configuration(env)
    assert "unit-secret" not in error.value.message


def test_local_repository_mode_is_explicit_and_cannot_claim_offsite_verification(tmp_path):
    env = {**_configuration(tmp_path), "RESTIC_REPOSITORY": str(tmp_path / "ci-repository")}
    with pytest.raises(backup.BackupError):
        backup.validate_configuration(env, allow_local_test=True)
    assert backup.validate_configuration({**env, "CI": "true", "ENVIRONMENT": "test"}, allow_local_test=True) is True
    with pytest.raises(backup.BackupError):
        backup.validate_configuration({**env, "CI": "true", "ENVIRONMENT": "production"}, allow_local_test=True)


def test_concurrent_offsite_jobs_are_rejected_without_overwriting_the_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("HEATSHIFT_BACKUP_LOCK_FILE", str(tmp_path / "backup.lock"))
    with backup.backup_lock():
        with pytest.raises(backup.BackupError, match="lock"):
            with backup.backup_lock():
                pytest.fail("The duplicate backup acquired the lock")
