import os
import smtplib
from email.message import EmailMessage


class OmniFocusService:
    def __init__(self):
        self.maildrop = os.getenv("OMNIFOCUS_MAILDROP")
        self.smtp_host = os.getenv("OMNIFOCUS_SMTP_HOST")
        self.smtp_port = int(os.getenv("OMNIFOCUS_SMTP_PORT", "587"))
        self.smtp_username = os.getenv("OMNIFOCUS_SMTP_USERNAME")
        self.smtp_password = os.getenv("OMNIFOCUS_SMTP_PASSWORD")
        self.from_address = os.getenv(
            "OMNIFOCUS_FROM_ADDRESS",
            self.smtp_username,
        )

    def _validate_config(self):
        required = {
            "OMNIFOCUS_MAILDROP": self.maildrop,
            "OMNIFOCUS_SMTP_HOST": self.smtp_host,
            "OMNIFOCUS_SMTP_USERNAME": self.smtp_username,
            "OMNIFOCUS_SMTP_PASSWORD": self.smtp_password,
            "OMNIFOCUS_FROM_ADDRESS": self.from_address,
        }

        missing = [name for name, value in required.items() if not value]

        if missing:
            raise RuntimeError(
                "OmniFocus integration is not configured: "
                + ", ".join(missing)
            )

    def create_task(self, title: str, note: str = "") -> dict:
        self._validate_config()

        title = title.strip()
        note = note.strip()

        if not title:
            raise ValueError("Task title cannot be empty")

        if len(title) > 250:
            raise ValueError("Task title must be 250 characters or fewer")

        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = self.maildrop
        message["Subject"] = title
        message.set_content(note or "Captured by Homelab Control Plane.")

        with smtplib.SMTP(
            self.smtp_host,
            self.smtp_port,
            timeout=15,
        ) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(
                self.smtp_username,
                self.smtp_password,
            )
            smtp.send_message(message)

        return {
            "accepted": True,
            "title": title,
            "destination": "omnifocus",
        }
