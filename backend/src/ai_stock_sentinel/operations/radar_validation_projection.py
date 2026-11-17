"""Install the versioned projection with metadata-created PostgreSQL tables."""
from importlib.resources import files


def install_projection(target, connection, **kwargs):
    if connection.dialect.name == 'postgresql':
        connection.exec_driver_sql(files('ai_stock_sentinel.operations').joinpath(
            'radar_validation_projection_v1.sql').read_text())
