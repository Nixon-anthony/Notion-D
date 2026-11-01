"""Configuration loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def system_timezone() -> str:
    """This computer's IANA time zone (e.g. Asia/Taipei), or UTC if unknown."""
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            name = target.split("zoneinfo/", 1)[1]
            ZoneInfo(name)
            return name
    except Exception:
        pass
    return "UTC"


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader: KEY=VALUE lines, '#' comments, optional quotes.

    Existing environment variables win over the file.
    """
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    channel_secret: str
    channel_access_token: str
    owner_user_id: str
    tz_name: str
    db_path: str
    notion_token: str = ""
    # Notion data source ids (the collection:// ids, not the database page ids).
    notion_diary_ds: str = ""
    notion_shopping_ds: str = ""
    notion_cooking_ds: str = ""
    notion_ideas_ds: str = ""
    notion_lists_ds: str = ""
    currency: str = ""  # shown before prices, e.g. NT$

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def notion_configured(self) -> bool:
        return bool(self.notion_token and self.notion_diary_ds
                    and self.notion_shopping_ds and self.notion_cooking_ds
                    and self.notion_ideas_ds and self.notion_lists_ds)


def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        channel_secret=os.environ.get("LINE_CHANNEL_SECRET", ""),
        channel_access_token=os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", ""),
        owner_user_id=os.environ.get("OWNER_USER_ID", "").strip(),
        tz_name=os.environ.get("TZ") or system_timezone(),
        db_path=os.environ.get("DB_PATH", str(PROJECT_ROOT / "diary.db")),
        notion_token=os.environ.get("NOTION_TOKEN", "").strip(),
        notion_diary_ds=os.environ.get("NOTION_DIARY_DS", "").strip(),
        notion_shopping_ds=os.environ.get("NOTION_SHOPPING_DS", "").strip(),
        notion_cooking_ds=os.environ.get("NOTION_COOKING_DS", "").strip(),
        notion_ideas_ds=os.environ.get("NOTION_IDEAS_DS", "").strip(),
        notion_lists_ds=os.environ.get("NOTION_LISTS_DS", "").strip(),
        currency=os.environ.get("CURRENCY", ""),
    )
