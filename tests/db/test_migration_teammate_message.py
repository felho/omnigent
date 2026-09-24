"""Upgrade and rollback checks for the teammate-message item constraint."""

from __future__ import annotations

import re
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import _build_alembic_config


def _allows_code_12(engine: sa.Engine) -> bool:
    checks = sa.inspect(engine).get_check_constraints("conversation_items")
    constraint = next(row for row in checks if row["name"] == "ck_conversation_items_type")
    return re.search(r"\b12\b", constraint["sqltext"]) is not None


def test_teammate_item_check_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    uri = f"sqlite:///{tmp_path / 'teammate.db'}"
    engine = sa.create_engine(uri)
    config = _build_alembic_config(uri)

    for revision, allows_code_12 in (
        ("head", True),
        ("ll1a2b3c4d5e", False),
        ("head", True),
    ):
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            if revision == "ll1a2b3c4d5e":
                command.downgrade(config, revision)
            else:
                command.upgrade(config, revision)
        assert _allows_code_12(engine) is allows_code_12

    engine.dispose()
