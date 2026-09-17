# -*- coding: utf-8 -*-
"""
赛前预测报告邮件推送 (send_report_email.py)
====================================================================
在 generate_unified_report.py 生成报告后，把当日汇总 _summary.md 作为邮件正文、
各场次 markdown 报告作为附件，通过 QQ 邮箱 SMTP 推送到指定收件人。

用法：
  python scripts/send_report_email.py --date 2026-08-30
  python scripts/send_report_email.py --date 2026-08-30 --no-attach
  python scripts/send_report_email.py --test     # 发送一封测试邮件校验配置

配置：scripts/email_config.json（含 SMTP 授权码，已加入 .gitignore，不进版本库）
"""
from __future__ import annotations

import argparse
import json
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "email_config.json"
CN_TZ = timezone(timedelta(hours=8))

DEFAULT_CONFIG = {
    "smtp_host": "smtp.qq.com",
    "smtp_port": 465,
    "use_ssl": True,
    "sender": "534244854@qq.com",
    "auth_code": "",
    "recipients": ["534244854@qq.com"],
    "sender_name": "赛前预测报告",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[邮件] 配置读取失败({CONFIG_PATH}): {e}")
    else:
        print(f"[邮件] 未找到 {CONFIG_PATH}，使用默认配置（auth_code 为空将跳过发送）")
    return cfg


def report_dir_for(date_str: str) -> Path:
    return BASE_DIR / "docs" / "prematch_reports" / date_str.replace("-", "")


def send_email(cfg: dict, subject: str, body_text: str, attachments=None) -> bool:
    host = cfg["smtp_host"]
    port = int(cfg["smtp_port"])
    sender = cfg["sender"]
    auth_code = cfg.get("auth_code", "")
    recipients = cfg["recipients"]
    sender_name = cfg.get("sender_name", "")

    if not auth_code:
        print("[邮件] 未配置 auth_code，跳过发送")
        return False

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    if attachments:
        for p in attachments:
            if not p.exists():
                print(f"[邮件] 附件不存在，跳过: {p.name}")
                continue
            part = MIMEApplication(p.read_bytes())
            part.add_header("Content-Disposition", "attachment", filename=p.name)
            msg.attach(part)

    try:
        if cfg.get("use_ssl", True):
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(sender, auth_code)
        server.sendmail(sender, recipients, msg.as_string())
        server.quit()
    except Exception as e:
        print(f"[邮件] FAIL 发送失败: {type(e).__name__}: {e}")
        return False
    print(f"[邮件] OK 已发送到 {', '.join(recipients)}")
    return True


def run(args):
    date_str = args.date or datetime.now(CN_TZ).strftime("%Y-%m-%d")
    cfg = load_config()

    if args.test:
        subj = f"[测试] 赛前预测邮件推送校验 {datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
        body = "这是一封测试邮件，用于校验 QQ 邮箱 SMTP 推送配置是否正常。若收到此信，说明自动推送链路可用。"
        sys.exit(0 if send_email(cfg, subj, body) else 1)

    # 覆盖的比赛日范围（默认当天，--days 2 则含次日）
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_dirs = [
        report_dir_for((start_dt + timedelta(days=off)).strftime("%Y-%m-%d"))
        for off in range(args.days)
    ]

    summaries = [d / "_summary.md" for d in day_dirs]
    existing = [s for s in summaries if s.exists()]
    if not existing:
        print(f"[邮件] 未找到汇总报告，请先运行 generate_unified_report.py")
        sys.exit(1)

    body_parts = []
    n = 0
    for s in existing:
        text = s.read_text(encoding="utf-8")
        body_parts.append(text)
        n += sum(
            1
            for line in text.splitlines()
            if line.startswith("| ") and " vs " in line and not line.startswith("| #") and not line.startswith("|:")
        )
    body = "\n\n---\n\n".join(body_parts)

    subject = f"赛前预测报告 {date_str}（{n} 场）"
    # 汇总含需回踩的缺失告警（缺漂移/无时序）→ 主题加 [缺数据] 红标。
    # 「竞彩未开售胜平负盘（非缺失）」属玩法缺位，回踩无效，不计入红标。
    if "仅1条快照缺漂移" in body or "无竞彩WDL时序" in body:
        subject = f"[缺数据] {subject}"

    attachments = []
    if not args.no_attach:
        for d in day_dirs:
            if not d.exists():
                continue
            attachments.extend(
                sorted((p for p in d.glob("*.md") if p.name != "_summary.md"), key=lambda p: p.name)
            )

    sys.exit(0 if send_email(cfg, subject, body, attachments) else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="赛前预测报告邮件推送")
    parser.add_argument("--date", help="报告日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=1, help="覆盖天数（默认1，对应 generate_unified_report.py 的 --days）")
    parser.add_argument("--no-attach", action="store_true", help="不附带各场次 .md 附件")
    parser.add_argument("--test", action="store_true", help="发送一封测试邮件校验配置")
    run(parser.parse_args())