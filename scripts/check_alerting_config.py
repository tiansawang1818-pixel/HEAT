#!/usr/bin/env python3
"""Reject deployment placeholders without echoing webhook credentials."""
import os
from pathlib import Path
import re
import sys


def validate_text(text):
    failures = []
    if re.search(r"CHANGE[_-]?ME|\.invalid\b|example\.(org|com)", text, re.IGNORECASE):
        failures.append("ยังมี receiver ตัวอย่าง ต้องตั้งปลายทางที่ได้รับอนุมัติก่อนเริ่มส่งแจ้งเตือน")
    if re.search(r'''(?m)^\s*-?\s*url:\s*["']?http://''', text):
        failures.append("webhook ที่ออกจาก production ต้องใช้ HTTPS")
    return failures


def main():
    path = os.environ.get("ALERTMANAGER_CONFIG_FILE", "")
    if not path or not Path(path).is_file():
        print("ต้องกำหนด ALERTMANAGER_CONFIG_FILE เป็นไฟล์ receiver ที่เก็บนอก Git", file=sys.stderr)
        return 2
    failures = validate_text(Path(path).read_text())
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 2
    print("ไม่พบค่า receiver ตัวอย่าง; ต้องตรวจโครงสร้างต่อด้วย amtool และทดสอบปลายทางจริง")
    return 0


if __name__ == "__main__":
    sys.exit(main())
