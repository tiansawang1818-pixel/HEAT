#!/usr/bin/env python3
"""Back up a validated dump to encrypted Restic storage, then verify recovery.

Never initializes, deletes, prunes, or restores into a live database. Repository
credentials stay in environment/private files and are never included in output.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


class BackupError(Exception):
    def __init__(self, stage, message):
        self.stage, self.message = stage, message


def prepare_password(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(secrets.token_urlsafe(48) + "\n")


def validate_configuration(env, allow_local_test=False):
    if any(env.get(key) for key in ("RESTIC_PASSWORD", "RESTIC_PASSWORD_COMMAND", "RESTIC_REPOSITORY_FILE")):
        raise BackupError("configuration", "ใช้ RESTIC_REPOSITORY และ RESTIC_PASSWORD_FILE โดยไม่ผสมวิธีส่งรหัสผ่านอื่น")
    repository = env.get("RESTIC_REPOSITORY", "")
    local_test = allow_local_test and env.get("CI") == "true" and env.get("ENVIRONMENT") == "test"
    if local_test:
        path = Path(repository)
        if not path.is_absolute() or not path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise BackupError("configuration", "repository ทดสอบต้องอยู่ใน temporary directory แยก")
    else:
        if not repository.startswith("s3:https://"):
            raise BackupError("configuration", "ต้องตั้ง S3-compatible repository ผ่าน HTTPS ก่อนเริ่มสำรองนอกเครื่อง")
        url = urlparse(repository[3:])
        if not url.hostname or url.username or url.password or url.query or url.fragment or not url.path.strip("/"):
            raise BackupError("configuration", "รูปแบบ repository ไม่ถูกต้อง ห้ามใส่รหัสผ่านหรือ token ลง URL")
        if url.hostname in {"example.org", "example.com", "example.net"} or url.hostname.endswith((".example.org", ".example.com", ".example.net", ".invalid")):
            raise BackupError("configuration", "ต้องเปลี่ยน storage ตัวอย่างเป็นปลายทางที่ได้รับอนุมัติจริง")
        try:
            loopback = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            loopback = url.hostname.lower().rstrip(".") == "localhost"
        if loopback:
            raise BackupError("configuration", "repository นอกเครื่องต้องไม่ชี้กลับ localhost")
    password_file = Path(env.get("RESTIC_PASSWORD_FILE", ""))
    if password_file.is_symlink() or not password_file.is_file():
        raise BackupError("configuration", "ต้องมีไฟล์รหัสผ่านส่วนตัวของ repository")
    info = password_file.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077 or len(password_file.read_bytes().strip()) < 32:
        raise BackupError("configuration", "ไฟล์รหัสผ่านต้องเป็นของผู้รัน อ่านได้เฉพาะเจ้าของ และมีค่าสุ่มอย่างน้อย 32 ตัวอักษร")
    return local_test


@contextmanager
def backup_lock():
    path = Path(os.environ.get("HEATSHIFT_BACKUP_LOCK_FILE", str(Path(tempfile.gettempdir()) / ("heatshift-offsite-%s.lock" % os.geteuid()))))
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if os.fstat(descriptor).st_uid != os.geteuid():
            raise BackupError("lock", "ไม่สามารถใช้ lock file ของผู้ใช้อื่น")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("lock", "มีงานสำรองนอกเครื่องกำลังทำงานอยู่")
        yield
    finally:
        os.close(descriptor)


def run(arguments, stage, *, env, stdin=None):
    try:
        result = subprocess.run(arguments, stdin=stdin, capture_output=True, env=env, cwd=ROOT, timeout=3600)
    except (OSError, subprocess.SubprocessError):
        raise BackupError(stage, "คำสั่งสำรองไม่สำเร็จหรือเกินเวลา โปรดตรวจบริการและสิทธิ์โดยไม่เผยแพร่รหัสผ่าน")
    if result.returncode:
        raise BackupError(stage, "ขั้นตอนสำรองไม่สำเร็จ จึงยังไม่รับรองการกู้คืน (exit code %s)" % result.returncode)
    return result.stdout


def digest(path):
    with path.open("rb") as source:
        checksum = hashlib.sha256()
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
        return checksum.hexdigest()


def compose_arguments(env):
    arguments = ["docker", "compose"]
    if env.get("HEATSHIFT_COMPOSE_ENV") == "production":
        arguments += ["--env-file", env.get("HEATSHIFT_COMPOSE_ENV_FILE", ".env.production"), "-f", "docker-compose.yml", "-f", "docker-compose.production.yml"]
    return arguments


def backup_and_verify(env, local_test=False):
    for name in ("restic", "docker"):
        if not shutil.which(name):
            raise BackupError("configuration", "ยังไม่ได้ติดตั้ง %s สำหรับงานสำรอง" % name)
    with backup_lock(), tempfile.TemporaryDirectory(prefix="heatshift-offsite-") as directory:
        temporary = Path(directory)
        env = {**env, "BACKUP_DIR": str(temporary / "dump")}
        # Confirm an existing encrypted repository before spending work on a dump.
        run(["restic", "cat", "config"], "repository_access", env=env)
        run(["sh", str(ROOT / "scripts/backup_postgres.sh")], "database_dump", env=env)
        dumps = list((temporary / "dump").glob("*.dump"))
        if len(dumps) != 1 or not dumps[0].stat().st_size:
            raise BackupError("database_dump", "ไม่พบ dump ที่สมบูรณ์หนึ่งไฟล์")
        original = dumps[0]
        expected = digest(original)
        if original.with_suffix(".dump.sha256").read_text().split()[0] != expected:
            raise BackupError("database_dump", "checksum ของ dump ไม่ตรงก่อนส่งสำรอง")
        with original.open("rb") as source:
            raw = run(["restic", "backup", "--stdin", "--stdin-filename", "heatshift.dump", "--host", "heatshift", "--tag", "heatshift-postgres", "--json"], "encrypted_upload", env=env, stdin=source)
        try:
            summaries = [value for value in (json.loads(line) for line in raw.splitlines()) if value.get("message_type") == "summary"]
            snapshot = summaries[-1]["snapshot_id"]
            if not re.fullmatch(r"[a-f0-9]{64}", snapshot):
                raise ValueError("invalid snapshot")
        except (ValueError, KeyError, IndexError, TypeError):
            raise BackupError("encrypted_upload", "ไม่พบ snapshot ID ที่สมบูรณ์จากระบบสำรอง")
        restored_directory = temporary / "recovery"
        run(["restic", "restore", snapshot, "--target", str(restored_directory)], "download_and_decrypt", env=env)
        restored = restored_directory / "heatshift.dump"
        if not restored.is_file() or restored.is_symlink() or digest(restored) != expected:
            raise BackupError("recovery_verification", "ไฟล์ที่กู้คืนไม่ตรงกับ dump ต้นฉบับ")
        with restored.open("rb") as source:
            run([*compose_arguments(env), "exec", "-T", "postgres", "pg_restore", "--list"], "archive_verification", env=env, stdin=source)
        return {"status": "verified", "verified_at": datetime.now(timezone.utc).isoformat(),
                "scope": "isolated_local_repository_test" if local_test else "configured_encrypted_remote_repository",
                "snapshot_id": snapshot, "sha256": expected, "archive_bytes": original.stat().st_size,
                "download_checksum_matches": True, "archive_readable": True,
                "physical_offsite_location_verified": False, "live_database_modified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-password-file", type=Path)
    parser.add_argument("--allow-local-test", action="store_true", help="Only permitted inside isolated CI with ENVIRONMENT=test")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.prepare_password_file:
            prepare_password(args.prepare_password_file)
            print("สร้างไฟล์รหัสผ่านสำรองส่วนตัวแล้ว โดยไม่พิมพ์ค่า ไม่เขียนทับไฟล์เดิม และไม่เชื่อมต่อ storage")
            return 0
        env = dict(os.environ)
        local_test = validate_configuration(env, args.allow_local_test)
        env["RESTIC_PASSWORD_FILE"] = str(Path(env["RESTIC_PASSWORD_FILE"]).resolve())
        report = backup_and_verify(env, local_test)
        print(json.dumps(report))
        return 0
    except BackupError as error:
        print(json.dumps({"status": "failed", "stage": error.stage, "reason_th": error.message}, ensure_ascii=False), file=sys.stderr)
    except FileExistsError:
        print("มีไฟล์รหัสผ่านอยู่แล้ว ระบบเก็บไฟล์เดิมไว้", file=sys.stderr)
    except OSError:
        print("ไม่สามารถอ่านหรือเขียนไฟล์ส่วนตัวสำหรับงานสำรอง", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
