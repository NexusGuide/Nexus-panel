"""Guard the migrations against the one thing MySQL alone refuses.

MySQL rejects a literal DEFAULT on a BLOB, TEXT, GEOMETRY or JSON column:

    (1101, "BLOB, TEXT, GEOMETRY or JSON column 'fields' can't have a
     default value")

MariaDB has allowed it since 10.2, and SQLite and PostgreSQL always have. So a
migration with that mistake passes four of this project's five backends and
fails only the fifth - which is how one reached main: four green ticks made the
single red one look like a flaky runner, while a fresh MySQL install could not
run the migration at all.

This reads the migration files rather than a database, so the mistake is caught
on every backend and without waiting five minutes for CI.

Two things it deliberately does not flag, because MySQL accepts both and the
migrations here rely on it:

* a default inside a branch MySQL never reaches - upstream's migrations
  routinely write `if dialect == 'mysql': ... else: ...` and put the default in
  the other half;
* a default given as an expression rather than a literal - `sa.text("'[]'")`
  compiles to a DEFAULT expression, which MySQL 8.0.13 and later allow on these
  types.
"""

import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parent.parent / "app" / "db" / "migrations" / "versions"

# Types MySQL will not accept a literal DEFAULT on. Names only - the migrations
# spell them sa.Text(), sa.JSON(), mysql.LONGTEXT() and so on.
UNDEFAULTABLE = {
    "Text", "UnicodeText", "JSON", "JSONB", "LargeBinary", "BLOB", "CLOB",
    "LONGTEXT", "MEDIUMTEXT", "TINYTEXT", "LONGBLOB", "MEDIUMBLOB", "TINYBLOB",
}

# What a dialect branch tests against. Anything naming one of these and not
# mysql is a branch MySQL does not take.
OTHER_DIALECTS = {"postgresql", "postgres", "sqlite", "mariadb", "is_postgres", "is_sqlite"}


def _names_in(node: ast.AST) -> set[str]:
    """Every attribute, name and string constant in an expression.

    Written to see through the wrappers the migrations use: `sa.Text()`,
    `sa.Text`, and `sa.BigInteger().with_variant(sa.Integer(), "sqlite")` all
    resolve to the names they are built from.
    """
    found: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute):
            found.add(child.attr)
        elif isinstance(child, ast.Name):
            found.add(child.id)
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            found.add(child.value)
    return found


def _unreachable_by_mysql(path_to_root: list[tuple[ast.AST, str]]) -> bool:
    """True when a dialect branch on the way to this node excludes MySQL."""
    for node, field in path_to_root:
        if not isinstance(node, ast.If):
            continue
        test = _names_in(node.test)
        mentions_mysql = "mysql" in test
        if field == "orelse" and mentions_mysql:
            return True  # the else of `if dialect == 'mysql'`
        if field == "body" and not mentions_mysql and (test & OTHER_DIALECTS):
            return True  # the body of `if dialect == 'postgresql'`
    return False


def _literal_default(call: ast.Call) -> bool:
    """True when server_default is a literal, which is the form MySQL refuses."""
    for kw in call.keywords:
        if kw.arg != "server_default":
            continue
        return isinstance(kw.value, ast.Constant)
    return False


def _offending_columns(path: Path) -> list[tuple[str, str, int]]:
    """(column name, type name, line) for each column MySQL would reject."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # parent chain for every node, so a column can be told which branch it is in
    parents: dict[int, tuple[ast.AST, str]] = {}
    for node in ast.walk(tree):
        for field, value in ast.iter_fields(node):
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    parents[id(child)] = (node, field)

    def chain(node: ast.AST) -> list[tuple[ast.AST, str]]:
        out = []
        while id(node) in parents:
            parent, field = parents[id(node)]
            out.append((parent, field))
            node = parent
        return out

    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "Column"):
            continue
        if len(node.args) < 2 or not _literal_default(node):
            continue
        hit = _names_in(node.args[1]) & UNDEFAULTABLE
        if not hit:
            continue
        if _unreachable_by_mysql(chain(node)):
            continue
        column = node.args[0].value if isinstance(node.args[0], ast.Constant) else "?"
        found.append((column, sorted(hit)[0], node.lineno))
    return found


def migration_files() -> list[Path]:
    return sorted(VERSIONS.glob("*.py"))


def test_there_are_migrations_to_check():
    """A wrong path would make every test below pass by finding nothing."""
    assert len(migration_files()) > 10


@pytest.mark.parametrize("path", migration_files(), ids=lambda p: p.name)
def test_no_literal_default_on_a_text_or_json_column(path):
    offenders = _offending_columns(path)
    assert not offenders, "MySQL rejects a literal DEFAULT on these columns: " + ", ".join(
        f"{name} ({type_name}) at {path.name}:{line}" for name, type_name, line in offenders
    )


# --------------------------------------------------------------------------- #
# the checker's own behaviour
# --------------------------------------------------------------------------- #


def _probe(source: str) -> list[tuple[str, str]]:
    tmp = VERSIONS.parent / "_checker_probe.py"
    tmp.write_text("import sqlalchemy as sa\nfrom alembic import op\n" + source, encoding="utf-8")
    try:
        return [(name, type_name) for name, type_name, _ in _offending_columns(tmp)]
    finally:
        tmp.unlink()


def test_it_catches_the_mistake_it_exists_for():
    assert _probe(
        "def upgrade():\n"
        "    op.create_table('t',\n"
        "        sa.Column('ok', sa.String(length=8), nullable=False, server_default=''),\n"
        "        sa.Column('bad', sa.Text(), nullable=False, server_default='{}'),\n"
        "    )\n"
    ) == [("bad", "Text")]


def test_it_allows_a_default_mysql_never_reaches():
    assert _probe(
        "def upgrade():\n"
        "    dialect = op.get_bind().dialect.name\n"
        "    if dialect == 'mysql':\n"
        "        op.add_column('t', sa.Column('c', sa.JSON(), nullable=True))\n"
        "    else:\n"
        "        op.add_column('t', sa.Column('c', sa.JSON(), server_default='{}'))\n"
    ) == []


def test_it_allows_a_postgres_only_default():
    assert _probe(
        "def upgrade():\n"
        "    if is_postgres:\n"
        "        op.add_column('t', sa.Column('c', sa.JSON(), server_default='{}'))\n"
    ) == []


def test_it_allows_an_expression_default():
    assert _probe(
        "def upgrade():\n"
        "    op.add_column('t', sa.Column('c', sa.JSON(), server_default=sa.text(\"'[]'\")))\n"
    ) == []


def test_it_still_catches_one_inside_a_mysql_branch():
    assert _probe(
        "def upgrade():\n"
        "    dialect = op.get_bind().dialect.name\n"
        "    if dialect == 'mysql':\n"
        "        op.add_column('t', sa.Column('c', sa.Text(), server_default='x'))\n"
    ) == [("c", "Text")]
