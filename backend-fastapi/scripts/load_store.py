"""
db/stores_seed.csv (build_stores_seed.py 산출물, 431건) → stores 테이블 적재.

전제:
  - init.sql / seed.sql이 먼저 실행되어 있어야 한다 (테이블 존재 확인).
  - DATABASE_URL 환경변수가 로컬 Docker PostGIS를 가리켜야 한다
    (docs/03_CLOUD_SQL_SETUP.md §5.2 형식, 예:
     postgresql://postgres:localdevpw@127.0.0.1:5432/couponkok)

동작:
  - external_id 기준 UPSERT. 몇 번을 다시 돌려도 안전하다 (재실행 시 좌표·주소 갱신).
  - store_id는 CSV에 이미 고정되어 있으므로(02_DB_SCHEMA.md §0.1-6) 여기서 새로 만들지 않는다.
  - ST_MakePoint 인자는 (경도, 위도) 순서 — 뒤집으면 남극으로 간다.

실행:
  cd backend-fastapi
  python scripts/load_stores.py
"""
import csv
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "db" / "stores_seed.csv"

UPSERT_SQL = """
INSERT INTO stores (store_id, brand_id, store_name, road_address, geom, store_type, source, external_id)
VALUES (
    %(store_id)s, %(brand_id)s, %(store_name)s, %(road_address)s,
    ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326),
    %(store_type)s, %(source)s, %(external_id)s
)
ON CONFLICT (external_id) DO UPDATE SET
    brand_id     = EXCLUDED.brand_id,
    store_name   = EXCLUDED.store_name,
    road_address = EXCLUDED.road_address,
    geom         = EXCLUDED.geom,
    store_type   = EXCLUDED.store_type,
    updated_at   = now();
"""


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("[error] DATABASE_URL 환경변수가 없습니다. .env를 확인하세요.")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")

    if not CSV_PATH.exists():
        sys.exit(f"[error] {CSV_PATH} 가 없습니다. 먼저 scripts/build_stores_seed.py를 실행하세요.")

    with open(CSV_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"[info] {len(rows)}건 적재 시작 → {CSV_PATH.name}")

    inserted = 0
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_SQL, {
                    "store_id":     row["store_id"],
                    "brand_id":     row["brand_id"],
                    "store_name":   row["store_name"],
                    "road_address": row["road_address"] or None,
                    "lng":          float(row["lng"]),
                    "lat":          float(row["lat"]),
                    "store_type":   row["store_type"],
                    "source":       row["source"],
                    "external_id":  row["external_id"],
                })
                inserted += 1
        conn.commit()

    print(f"[ok] {inserted}건 upsert 완료")


if __name__ == "__main__":
    main()