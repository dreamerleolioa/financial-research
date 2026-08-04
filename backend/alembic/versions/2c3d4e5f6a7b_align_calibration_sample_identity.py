"""align calibration sample identity with the active config cohort

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-08-04 00:00:00.000000

"""
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


def _deduplicate_samples(identity_column: str) -> None:
    partition_columns = ", ".join(
        f"samples.{column}"
        for column in (*_BASE_IDENTITY_COLUMNS, identity_column)
    )
    op.execute(
        sa.text(
            f"""
            WITH ranked_results AS (
                SELECT
                    results.id,
                    ROW_NUMBER() OVER (
                        PARTITION BY {partition_columns},
                                     results.window_days,
                                     results.validation_version
                        ORDER BY samples.id ASC, results.id ASC
                    ) AS row_number
                FROM analysis_forward_validation_results AS results
                JOIN analysis_calibration_samples AS samples
                  ON samples.id = results.sample_id
            )
            DELETE FROM analysis_forward_validation_results AS results
            USING ranked_results
            WHERE results.id = ranked_results.id
              AND ranked_results.row_number > 1
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH canonical_samples AS (
                SELECT
                    samples.id,
                    MIN(samples.id) OVER (
                        PARTITION BY {partition_columns}
                    ) AS canonical_id
                FROM analysis_calibration_samples AS samples
            )
            UPDATE analysis_forward_validation_results AS results
            SET sample_id = canonical_samples.canonical_id
            FROM canonical_samples
            WHERE results.sample_id = canonical_samples.id
              AND canonical_samples.id <> canonical_samples.canonical_id
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH duplicate_samples AS (
                SELECT id
                FROM (
                    SELECT
                        samples.id,
                        ROW_NUMBER() OVER (
                            PARTITION BY {partition_columns}
                            ORDER BY samples.id ASC
                        ) AS row_number
                    FROM analysis_calibration_samples AS samples
                ) AS ranked_samples
                WHERE ranked_samples.row_number > 1
            )
            DELETE FROM analysis_calibration_samples AS samples
            USING duplicate_samples
            WHERE samples.id = duplicate_samples.id
            """
        )
    )


def upgrade() -> None:
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
    _deduplicate_samples("input_hash")
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
            "input_hash",
        ],
    )
