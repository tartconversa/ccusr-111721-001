import sqlite3


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, owner TEXT NOT NULL, jurisdiction TEXT NOT NULL,
    classification TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS fields (
    source_id TEXT NOT NULL REFERENCES sources(id), name TEXT NOT NULL,
    meaning TEXT NOT NULL, kind TEXT NOT NULL, sensitive INTEGER NOT NULL,
    PRIMARY KEY(source_id,name)
);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    schema_version INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
    received_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS grants (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, valid_until TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS policies (
    jurisdiction TEXT NOT NULL, classification TEXT NOT NULL, purpose TEXT NOT NULL,
    decision TEXT NOT NULL, revision INTEGER NOT NULL,
    PRIMARY KEY(jurisdiction,classification,purpose)
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT NOT NULL, revision INTEGER NOT NULL, name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL,
    PRIMARY KEY(id,revision)
);
CREATE TABLE IF NOT EXISTS product_inputs (
    product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    PRIMARY KEY(product_id,product_revision,asset_id),
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    recipient TEXT NOT NULL, purpose TEXT NOT NULL, created_at TEXT NOT NULL,
    input_digest TEXT NOT NULL, policy_revisions TEXT NOT NULL,
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision)
);
CREATE TABLE IF NOT EXISTS handovers (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL, product_revision INTEGER NOT NULL,
    party_a TEXT NOT NULL, party_b TEXT NOT NULL, purpose TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending', opened_at TEXT NOT NULL,
    input_digest TEXT NOT NULL,
    FOREIGN KEY(product_id,product_revision) REFERENCES products(id,revision),
    CHECK(party_a <> party_b),
    CHECK(state IN ('pending','completed','rejected'))
);
CREATE TABLE IF NOT EXISTS handover_decisions (
    handover_id TEXT NOT NULL REFERENCES handovers(id), party TEXT NOT NULL,
    decision TEXT NOT NULL, decided_at TEXT NOT NULL,
    grant_basis TEXT NOT NULL, policy_basis TEXT NOT NULL,
    input_digest TEXT NOT NULL, reasons TEXT NOT NULL,
    PRIMARY KEY(handover_id,party),
    CHECK(decision IN ('confirmed','rejected'))
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, subject TEXT NOT NULL, details TEXT NOT NULL
);
"""


def connect(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return connection
