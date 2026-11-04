from alembic import command
from tests.conftest import alembic_config


def test_models_match_migrations(migrated_database: str) -> None:
    """Fails when a model changes without a matching Alembic migration."""
    command.check(alembic_config())


def test_migrations_downgrade_and_upgrade_cleanly(migrated_database: str) -> None:
    config = alembic_config()
    try:
        command.downgrade(config, "base")
    finally:
        command.upgrade(config, "head")
