"""
CLI script to export canonical live state from source database (SQLite 3 WAL)
into the deterministic migration artifact phase7_3_runtime_migration_evidence.json.
"""
import json
import sys
from pathlib import Path

# Ensure backend is on sys.path
root_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root_dir / "backend"))
sys.path.insert(0, str(root_dir))

from app.db.session import SessionLocal
from scripts.migration.state_migrator import CanonicalStateExporter


def main():
    print("Connecting to live source database...")
    with SessionLocal() as session:
        payload = CanonicalStateExporter.export_state(session)

    out_path = Path(__file__).resolve().parents[2] / "phase7_3_runtime_migration_evidence.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print(f"Exported {payload['summary']['predictions']} predictions successfully!")
    print(f"Migration evidence written to: {out_path}")


if __name__ == "__main__":
    main()
