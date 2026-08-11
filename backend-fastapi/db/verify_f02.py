"""
db/verify_f02.py — 매장 적재 후 F-02(반경 매칭) 검증

확인 항목
  1. pgvector 사용 가능 여부 (F-04 착수 전 확인용, 설치는 하지 않음)
  2. 적재 현황: 브랜드별 건수 / source별 건수 / 좌표 이상치
  3. 테스트 쿠폰 1건 삽입 (dev-uid-0001, starbucks)
  4. 02_DB_SCHEMA.md §7 매칭 쿼리 실행 → matches 확인
  5. EXPLAIN ANALYZE → idx_stores_geog(Index Scan)를 타는지 확인   ★핵심

사용:
    python db/verify_f02.py
    python db/verify_f02.py --cleanup     # 테스트 쿠폰 삭제
"""

from __future__ import annotations

import os
import sys
import getpass

try:
    import psycopg
except ImportError:
    sys.exit('psycopg가 없습니다.  pip install "psycopg[binary]" python-dotenv')

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

for s in (sys.stdout, sys.stderr):
    try:
        s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 아주대점 좌표 (시연 기준점) — (경도, 위도) 순서 주의
TEST_LNG, TEST_LAT = 127.0439, 37.2815
RADIUS_M = 300
TEST_UID = "dev-uid-0001"
TEST_CPN = "cpn_verify_0001"

MATCH_SQL = """
SELECT
    s.store_id, s.store_name, s.brand_id, s.store_type, s.source,
    ROUND(ST_Distance(s.geom::geography, %(pt)s::geography))::int AS distance_m,
    json_agg(
        json_build_object(
            'coupon_id',    c.coupon_id,
            'product_name', c.product_name,
            'expires_at',   c.expires_at,
            'days_left',    (c.expires_at - (now() AT TIME ZONE 'Asia/Seoul')::date)
        ) ORDER BY c.expires_at ASC
    ) AS available_coupons
FROM stores s
JOIN coupons c
  ON  c.brand_id = s.brand_id
  AND c.uid      = %(uid)s
  AND c.status   = 'COMPLETED'
  AND c.is_used  = false
  AND c.expires_at >= (now() AT TIME ZONE 'Asia/Seoul')::date
WHERE s.is_active
  AND ST_DWithin(s.geom::geography, %(pt)s::geography, %(radius)s)
GROUP BY s.store_id
ORDER BY distance_m ASC
LIMIT 20;
"""


def connect() -> psycopg.Connection:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "couponkok")
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD") or getpass.getpass(f"{user}@{host}:{port} 비밀번호: ")
    return psycopg.connect(
        host=host, port=port, dbname=name, user=user, password=pw, connect_timeout=10
    )


def show(cur, title: str, sql: str, params=None) -> list:
    print(f"\n[{title}]")
    cur.execute(sql, params)  # type: ignore[arg-type]
    rows = cur.fetchall()
    if not rows:
        print("  (없음)")
    for r in rows:
        print("  " + "  |  ".join(str(c) for c in r))
    return rows


def main() -> int:
    cleanup = "--cleanup" in sys.argv
    pt = f"SRID=4326;POINT({TEST_LNG} {TEST_LAT})"
    params = {"pt": pt, "uid": TEST_UID, "radius": RADIUS_M}

    with connect() as conn, conn.cursor() as cur:
        if cleanup:
            cur.execute("DELETE FROM coupons WHERE coupon_id = %s", (TEST_CPN,))
            conn.commit()
            print(f"테스트 쿠폰 삭제 완료 ({cur.rowcount}건)")
            return 0

        # ── 1. pgvector 사용 가능 여부 (설치는 하지 않음)
        show(
            cur,
            "1. pgvector 사용 가능 여부",
            """SELECT name, default_version, installed_version
               FROM pg_available_extensions WHERE name = 'vector'""",
        )

        # ── 2. 적재 현황
        show(
            cur,
            "2-a. source별 건수",
            "SELECT source, count(*) FROM stores GROUP BY source ORDER BY 2 DESC",
        )
        show(
            cur,
            "2-b. 브랜드별 건수",
            "SELECT brand_id, count(*) FROM stores GROUP BY brand_id ORDER BY 2 DESC",
        )
        show(
            cur,
            "2-c. store_type 분포 (C-6: 전량 NORMAL이 정상)",
            "SELECT store_type, count(*) FROM stores GROUP BY store_type ORDER BY 2 DESC",
        )
        show(
            cur,
            "2-d. 좌표 이상치 (한반도 범위 밖 = 경위도 뒤바뀜 의심, 0건이어야 정상)",
            """SELECT store_id, store_name, ST_X(geom) lng, ST_Y(geom) lat
               FROM stores
               WHERE ST_X(geom) NOT BETWEEN 124 AND 132
                  OR ST_Y(geom) NOT BETWEEN 33 AND 39""",
        )

        # ── 3. 테스트 쿠폰 (ck_coupons_completed 때문에 completed_at 필수)
        cur.execute(
            """
            INSERT INTO coupons (coupon_id, uid, client_upload_id, status,
                                 brand_id, brand_name, product_name, expires_at,
                                 barcode_masked, barcode_format, confidence,
                                 completed_at)
            VALUES (%s, %s, gen_random_uuid(), 'COMPLETED',
                    'starbucks', '스타벅스', '아이스 카페 아메리카노 T',
                    (now() AT TIME ZONE 'Asia/Seoul')::date + 30,
                    '8912****＊***3401', 'CODE128',
                    '{"brand":0.98,"product_name":0.91,"expires_at":0.87}'::jsonb,
                    now())
            ON CONFLICT (coupon_id) DO NOTHING
            """,
            (TEST_CPN, TEST_UID),
        )
        conn.commit()
        print(f"\n[3. 테스트 쿠폰] {TEST_CPN} (starbucks, D+30) 준비 완료")

        # ── 4. 매칭 쿼리
        rows = show(
            cur,
            f"4. 매칭 결과 — 아주대점 좌표 반경 {RADIUS_M}m",
            MATCH_SQL,
            params,
        )
        if not rows:
            print("  ※ 0건이면 원인은 셋 중 하나입니다:")
            print("     - stores에 starbucks brand_id가 없음 (브랜드 정규화 사전 문제)")
            print("     - 반경 300m 안에 해당 브랜드 매장이 없음")
            print("     - 좌표 (경도, 위도) 순서가 뒤바뀜")

        # ── 5. 실행 계획  ★가장 중요
        print(f"\n[5. EXPLAIN ANALYZE] — 'Index Scan using idx_stores_geog'가 보여야 정상")
        cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + MATCH_SQL, params)  # type: ignore[arg-type]
        plan = "\n".join(r[0] for r in cur.fetchall())
        print(plan)

        if "idx_stores_geog" in plan:
            print("\n  ✅ geography 인덱스를 타고 있습니다.")
        else:
            print("\n  ⚠️  idx_stores_geog가 계획에 없습니다.")
            print("     431건 규모에서는 플래너가 Seq Scan을 고르는 게 정상일 수 있습니다.")
            print("     인덱스 정의 자체가 잘못됐는지 확인하려면 아래로 강제 검증:")
            print("       SET enable_seqscan = off;  -- 후 EXPLAIN 재실행")

        print(f"\n정리하려면: python db/verify_f02.py --cleanup")
    return 0


if __name__ == "__main__":
    sys.exit(main())