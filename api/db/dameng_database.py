import logging
import re

import peewee
from peewee import (
    ColumnMetadata,
    Database,
    ImproperlyConfigured,
    IndexMetadata,
    OperationalError,
    SQL,
    fn,
)
from playhouse.pool import PooledDatabase  # type: ignore  # playhouse has no type stubs

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(identifier):
    if not _IDENTIFIER_RE.match(identifier or ""):
        raise OperationalError(f"Invalid DaMeng identifier: {identifier!r}")
    return f'"{identifier.upper()}"'


def _require_dmPython():
    try:
        import dmPython  # noqa: F401

        return dmPython
    except ImportError as e:
        raise ImproperlyConfigured(
            "dmPython is required to use PooledDamengDatabase. "
            "Install it with the dmpython package."
        ) from e


class DamengDatabase(Database):
    """
    Peewee database backend for DaMeng using dmPython.

    Accepted constructor kwargs are pulled from the service_conf.yaml
    "dameng" section: host, port, user, password, schema,
    max_connections, stale_timeout, and max_allowed_packet.
    """

    param = "?"

    field_types = {
        **peewee.FIELD,
        "AUTO": "INT IDENTITY(1, 1)",
        "BIGAUTO": "BIGINT IDENTITY(1, 1)",
        "BOOL": "BIT",
        "BLOB": "BLOB",
        "LONGBLOB": "BLOB",
        "TEXT": "TEXT",
        "LONGTEXT": "TEXT",
        "DATETIME": "TIMESTAMP",
        "TIMESTAMP": "TIMESTAMP",
        "UUID": "VARCHAR(40)",
    }

    def __init__(self, database, **kwargs):
        self._dm_host = kwargs.pop("host", "localhost")
        self._dm_port = int(kwargs.pop("port", 5236))
        self._dm_user = kwargs.pop("user", "SYSDBA")
        self._dm_password = kwargs.pop("password", "")
        self._dm_schema = kwargs.pop("schema", "SYSDBA")

        kwargs.pop("max_allowed_packet", None)
        kwargs.pop("max_connections", None)
        kwargs.pop("max_retries", None)
        kwargs.pop("retry_delay", None)

        super().__init__(database, **kwargs)

    def _connect(self):
        dmPython = _require_dmPython()
        try:
            conn = dmPython.connect(
                user=self._dm_user,
                password=self._dm_password,
                server=self._dm_host,
                port=self._dm_port,
            )
            if self._dm_schema:
                try:
                    cursor = conn.cursor()
                    cursor.execute(f"SET SCHEMA {_quote_identifier(self._dm_schema)}")
                    cursor.close()
                except Exception as exc:
                    logger.debug("SET SCHEMA failed (ignored): %s", exc)
            return conn
        except Exception as exc:
            raise OperationalError(f"DaMeng connect failed: {exc}") from exc

    def _close(self, conn):
        try:
            conn.close()
        except Exception:
            pass

    def _is_closed(self, conn):
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM DUAL")
            cursor.close()
            return False
        except Exception:
            return True

    def last_insert_id(self, cursor, query_type=None):
        try:
            return cursor.lastrowid
        except Exception:
            return None

    def rows_affected(self, cursor):
        return cursor.rowcount

    def _owner(self, schema=None):
        return (schema or self._dm_schema or self._dm_user).upper()

    def _table_candidates(self, table):
        names = [table]
        lower = table.lower()
        upper = table.upper()
        if lower not in names:
            names.append(lower)
        if upper not in names:
            names.append(upper)
        return names

    def get_tables(self, schema=None):
        cursor = self.execute_sql(
            "SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER = ? ORDER BY TABLE_NAME",
            (self._owner(schema),),
        )
        return [row[0].lower() for row in cursor.fetchall()]

    def get_columns(self, table, schema=None):
        table_names = self._table_candidates(table)
        cursor = self.execute_sql(
            "SELECT COLUMN_NAME, DATA_TYPE, NULLABLE, DATA_DEFAULT "
            "FROM ALL_TAB_COLUMNS "
            "WHERE OWNER = ? AND TABLE_NAME IN ({}) "
            "ORDER BY COLUMN_ID".format(", ".join(["?"] * len(table_names))),
            (self._owner(schema), *table_names),
        )
        primary_keys = set(self.get_primary_keys(table, schema=schema))
        return [
            ColumnMetadata(
                row[0].lower(),
                row[1],
                row[2] == "Y",
                row[0].lower() in primary_keys,
                table,
                row[3],
            )
            for row in cursor.fetchall()
        ]

    def get_indexes(self, table, schema=None):
        table_names = self._table_candidates(table)
        cursor = self.execute_sql(
            "SELECT I.INDEX_NAME, I.UNIQUENESS, C.COLUMN_NAME "
            "FROM ALL_INDEXES I "
            "JOIN ALL_IND_COLUMNS C "
            "  ON I.OWNER = C.INDEX_OWNER "
            " AND I.INDEX_NAME = C.INDEX_NAME "
            " AND I.TABLE_NAME = C.TABLE_NAME "
            "WHERE I.OWNER = ? AND I.TABLE_NAME IN ({}) "
            "ORDER BY I.INDEX_NAME, C.COLUMN_POSITION".format(", ".join(["?"] * len(table_names))),
            (self._owner(schema), *table_names),
        )
        indexes = {}
        unique = {}
        for index_name, uniqueness, column_name in cursor.fetchall():
            name = index_name.lower()
            indexes.setdefault(name, []).append(column_name.lower())
            unique[name] = uniqueness == "UNIQUE"
        return [
            IndexMetadata(name, None, columns, unique[name], table)
            for name, columns in indexes.items()
        ]

    def get_foreign_keys(self, table, schema=None):
        return []

    def table_exists(self, table, schema=None):
        table_names = self._table_candidates(table)
        cursor = self.execute_sql(
            "SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = ? AND TABLE_NAME IN ({})".format(
                ", ".join(["?"] * len(table_names))
            ),
            (self._owner(schema), *table_names),
        )
        return cursor.fetchone()[0] > 0

    def get_primary_keys(self, table, schema=None):
        table_names = self._table_candidates(table)
        cursor = self.execute_sql(
            "SELECT COLS.COLUMN_NAME "
            "FROM ALL_CONSTRAINTS CONS, ALL_CONS_COLUMNS COLS "
            "WHERE CONS.CONSTRAINT_TYPE = 'P' "
            "  AND CONS.CONSTRAINT_NAME = COLS.CONSTRAINT_NAME "
            "  AND CONS.OWNER = COLS.OWNER "
            "  AND CONS.OWNER = ? "
            "  AND COLS.TABLE_NAME IN ({})".format(", ".join(["?"] * len(table_names))),
            (self._owner(schema), *table_names),
        )
        return [row[0].lower() for row in cursor.fetchall()]

    def conflict_statement(self, on_conflict, query):
        action = on_conflict._action.lower() if on_conflict._action else ""
        if action in {"", "update", "ignore", "nothing"}:
            return None
        raise ValueError("DaMeng only supports explicit duplicate handling in application code.")

    def conflict_update(self, oc, query):
        if oc._preserve or oc._update or oc._where or oc._conflict_target or oc._conflict_constraint:
            raise ValueError(
                "DaMeng upsert is handled by api.db.db_utils.bulk_insert_into_db; "
                "Peewee on_conflict() is not supported by this backend."
            )
        return None

    def begin(self):
        # dmPython starts a transaction implicitly when autocommit is disabled.
        pass

    def get_binary_type(self):
        return bytes

    def extract_date(self, date_part, date_field):
        return fn.EXTRACT(SQL(date_part.upper()), date_field)

    def truncate_date(self, date_part, date_field):
        return fn.TRUNC(date_field, SQL("'%s'" % date_part.upper()))


class PooledDamengDatabase(PooledDatabase, DamengDatabase):
    def _is_closed(self, conn):
        return DamengDatabase._is_closed(self, conn)
