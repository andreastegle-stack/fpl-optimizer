"""Deadline email to Andreas, sent from GitHub Actions.

Credentials only come from environment variables set from GitHub Actions secrets
(``SMTP_USER``, ``SMTP_PASSWORD``, optional ``MAIL_TO``). Nothing is stored in the repo.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage

from .report import CHIP_NO, no_datetime

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def compose(advice: dict, url: str, last_review: dict | None = None, changed: bool = False) -> EmailMessage:
    players = {str(k): v for k, v in advice["players"].items()}
    name = lambda p: players[str(p)]["name"]
    rec = advice["recommended"]
    week = rec["weeks"][0]
    gw = advice["gw"]
    if week["sells"]:
        moves = ", ".join(f"{name(o)} → {name(i)}" for o, i in zip(week["sells"], week["buys"]))
        what = f"Bytt {moves}" + (f" (−{4 * week['hits']})" if week["hits"] else "")
    else:
        what = "Ingen bytter"
    chip = f", bruk {CHIP_NO[week['chip']]}" if week["chip"] else ""

    msg = EmailMessage()
    msg["Subject"] = ("Endret råd – " if changed else "") + f"FPL GW{gw}: {what}{chip}, kaptein {name(week['captain'])}"
    lines = [
        f"FPL-råd for GW{gw} – frist {no_datetime(advice['deadline'])}.",
        *(["Rådet er endret siden forrige e-post, mest sannsynlig på grunn av ny skadeinformasjon.", ""] if changed else [""]),
        f"{what}{chip}.",
        f"Kaptein: {name(week['captain'])}. Visekaptein: {name(week['vice'])}.",
        "Benk: " + ", ".join(name(p) for p in week["bench"]) + ".",
        f"Forventede poeng: {rec['xp_next']:.1f} denne runden.",
        "",
    ]
    if last_review and last_review.get("summary"):
        lines.append("Forrige runde:")
        lines += [f"- {s}" for s in last_review["summary"]]
        lines.append("")
    if advice["flags"]:
        lines.append("Skader/tvil i troppen: " + ", ".join(f"{name(f['element'])} ({f['news'] or f['status']})" for f in advice["flags"]) + ".")
        lines.append("")
    lines += [f"Alternativer og detaljer: {url}", "", "Verktøyet anbefaler, du bestemmer."]
    msg.set_content("\n".join(lines))
    return msg


def send(advice: dict, url: str, last_review: dict | None = None, changed: bool = False) -> bool:
    """Send the deadline email. Returns False (and sends nothing) if secrets are missing."""
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    to = os.environ.get("MAIL_TO") or user
    if not (user and password and to):
        print("Email skipped: SMTP_USER / SMTP_PASSWORD secrets are not set.")
        return False
    msg = compose(advice, url, last_review, changed)
    msg["From"] = user
    msg["To"] = to
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context()) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print("Email sent.")
    return True
