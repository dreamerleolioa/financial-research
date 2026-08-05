"""align calibration sample identity with the active config cohort

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-08-04 00:00:00.000000

"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2c3d4e5f6a7b"
down_revision: Union[str, Sequence[str], None] = "1b2c3d4e5f6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CONSTRAINT_NAME = "uq_analysis_calibration_sample_identity"
_BASE_IDENTITY_COLUMNS = (
    "analysis_type",
    "market",
    "symbol",
    "record_date",
    "strategy_version",
)
_CANONICAL_MAPPING_TABLE = "calibration_sample_identity_canonical"
_BACKUP_CONFIRMATION_ENV = "CALIBRATION_MIGRATION_BACKUP_CONFIRMED"
_LOCK_TIMEOUT = "10s"
_STATEMENT_TIMEOUT = "5min"


def _canonical_samples_sql(identity_column: str) -> str:
    partition_columns = ", ".join(
        f"samples.{column}"
        for column in (*_BASE_IDENTITY_COLUMNS, identity_column)
    )
    return f"""
        WITH sample_quality AS (
            SELECT
                samples.id,
                SUM(
                    CASE WHEN results.status = 'validated' THEN 1 ELSE 0 END
                ) AS validated_result_count,
                COUNT(results.id) AS evaluated_result_count
            FROM analysis_calibration_samples AS samples
            LEFT JOIN analysis_forward_validation_results AS results
              ON results.sample_id = samples.id
            GROUP BY samples.id
        )
        SELECT
            samples.id,
            FIRST_VALUE(samples.id) OVER (
                PARTITION BY {partition_columns}
                ORDER BY
                    sample_quality.validated_result_count DESC,
                    sample_quality.evaluated_result_count DESC,
                    samples.id ASC
            ) AS canonical_id
        FROM analysis_calibration_samples AS samples
        JOIN sample_quality ON sample_quality.id = samples.id
    """


def _deduplicate_samples(identity_column: str) -> None:
    op.execute(
        sa.text(
            f"""
            CREATE TEMPORARY TABLE {_CANONICAL_MAPPING_TABLE}
            ON COMMIT DROP
            AS {_canonical_samples_sql(identity_column)}
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM analysis_forward_validation_results AS results
            USING {_CANONICAL_MAPPING_TABLE} AS mapping
            WHERE results.sample_id = mapping.id
              AND mapping.id <> mapping.canonical_id
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            DELETE FROM analysis_calibration_samples AS samples
            USING {_CANONICAL_MAPPING_TABLE} AS mapping
            WHERE samples.id = mapping.id
              AND mapping.id <> mapping.canonical_id
            """
        )
    )


def upgrade() -> None:
    if os.getenv(_BACKUP_CONFIRMATION_ENV) != revision:
        raise RuntimeError(
            f"Set {_BACKUP_CONFIRMATION_ENV}={revision} only after creating "
            "a restorable pre-migration database backup."
        )
    op.execute(sa.text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))
    op.execute(sa.text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
    op.execute(
        sa.text(
            "LOCK TABLE analysis_calibration_samples, "
            "analysis_forward_validation_results IN ACCESS EXCLUSIVE MODE"
        )
    )
    _deduplicate_samples("confidence_config_version")
    op.drop_constraint(
        _CONSTRAINT_NAME,
        "analysis_calibration_samples",
        type_="unique",
    )
    op.create_unique_constraint(
        _CONSTRAINT_NAME,
        "analysis_calibration_samples",
        [
            "analysis_type",
            "market",
            "symbol",
            "record_date",
            "strategy_version",
            "confidence_config_version",
        ],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Revision 2c3d4e5f6a7b canonicalizes historical calibration samples "
        "and is intentionally irreversible. Restore the pre-migration "
        "database backup instead of running alembic downgrade."
    )
