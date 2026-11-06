#!/usr/bin/env python3
"""Create private local server configuration without inventing provider accounts."""
import argparse
import os
from pathlib import Path
import secrets
import sys


def create_configuration(output, template):
    password = secrets.token_hex(32)
    values = {
        "POSTGRES_PASSWORD": password,
        "DATABASE_URL": "postgresql+psycopg://heatshift:%s@postgres:5432/heatshift" % password,
        "AUTH_SESSION_SECRET": secrets.token_hex(32),
        "HEATSHIFT_DOMAIN": "", "ACME_EMAIL": "", "OIDC_ISSUER": "", "OIDC_JWKS_URL": "",
        "OIDC_CLIENT_ID": "", "GEE_PROJECT": "", "NEXT_PUBLIC_MAP_STYLE_URL": "",
    }
    lines = []
    for line in template.read_text().splitlines():
        key = line.split("=", 1)[0]
        lines.append(key + "=" + values[key] if key in values else line)
    # Exclusive creation also refuses symlinks; never overwrite existing secrets.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as destination:
        destination.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".env.production"))
    args = parser.parse_args()
    template = Path(__file__).resolve().parents[1] / ".env.production.example"
    try:
        create_configuration(args.output, template)
    except FileExistsError:
        print("มีไฟล์ค่าตั้งค่าอยู่แล้ว ระบบเก็บไฟล์เดิมไว้โดยไม่เขียนทับ", file=sys.stderr)
        return 2
    print("สร้างไฟล์ค่าตั้งค่าส่วนตัวแบบ 0600 พร้อมรหัสสุ่มแล้ว ค่าบัญชีและโดเมนยังเว้นว่าง และไม่มีการเริ่มบริการ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
