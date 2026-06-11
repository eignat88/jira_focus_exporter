import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from .actual_tasks_models import DevGroupUser

USER_COLUMNS = ["account_id", "name", "key", "display_name", "email"]


def _is_truthy(value: object) -> bool:
    if value is None or pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "да"}


def _cell(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _matches_exclude_patterns(user: DevGroupUser, patterns: Iterable[str]) -> bool:
    values = [identity.lower() for identity in user.identities]
    for raw_pattern in patterns:
        pattern = raw_pattern.strip().lower()
        if pattern and any(pattern in value for value in values):
            return True
    return False


def load_devax12_users(
    file_path: str | Path,
    exclude_inactive: bool = True,
    exclude_patterns: Iterable[str] | None = None,
) -> list[DevGroupUser]:
    path = Path(file_path)
    if not path.exists():
        logging.error("Файл пользователей DEVAX12 не найден: %s", path)
        raise FileNotFoundError(f"Файл пользователей DEVAX12 не найден: {path}")

    try:
        df = pd.read_excel(path)
    except Exception as error:
        logging.exception("Не удалось прочитать Excel со списком DEVAX12: %s", path)
        raise RuntimeError(f"Не удалось прочитать Excel: {path}") from error

    missing_columns = [column for column in USER_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(
            "В файле пользователей DEVAX12 отсутствуют колонки: "
            + ", ".join(missing_columns)
        )

    users: list[DevGroupUser] = []
    patterns = list(exclude_patterns or [])
    for _, row in df.iterrows():
        user = DevGroupUser(
            account_id=_cell(row.get("account_id")),
            name=_cell(row.get("name")),
            key=_cell(row.get("key")),
            display_name=_cell(row.get("display_name")),
            email=_cell(row.get("email")),
            active=_is_truthy(row.get("active")) if "active" in df.columns else True,
        )
        if exclude_inactive and not user.active:
            continue
        if _matches_exclude_patterns(user, patterns):
            continue
        if user.identities:
            users.append(user)

    logging.info("Загружено пользователей DEVAX12: %s", len(users))
    return users


def build_identity_set(users: Iterable[DevGroupUser]) -> set[str]:
    identities: set[str] = set()
    for user in users:
        identities.update(user.identities)
    return identities


def build_jql_user_values(users: Iterable[DevGroupUser]) -> list[str]:
    values = []
    seen = set()
    for user in users:
        for value in (
            user.account_id,
            user.name,
            user.key,
            user.email,
            user.display_name,
        ):
            normalized = value.strip().lower()
            if value and normalized not in seen:
                values.append(value)
                seen.add(normalized)
                break
    return values
