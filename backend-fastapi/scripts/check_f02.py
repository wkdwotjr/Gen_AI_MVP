"""F-02(POST /api/v1/locations) 검증 전/후 상태 점검.

실행 (backend-fastapi 디렉터리에서, venv 활성화 + Cloud SQL Proxy 켠 상태):
    python scripts/check_f02.py

출력:
  1) dev-uid-0001 의 쿠폰 목록          — 매칭 가능한 쿠폰이 있는지
  2) 아주대 좌표 300m 내 매장 브랜드 분포 — 어떤 브랜드가 잡히는지
  3) 매칭 후보 매장 최근접 5곳          — TC-L1 기대값 계산용
  4) 쿠폰 없는 브랜드 매장 1곳          — TC-L5 좌표
  5) users.last_location / last_location_at — TC-L4 판정 기준
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app import core  # noqa: E402

UID = core.get_settings().dev_uid
AJOU_LAT, AJOU_LNG = 37.2803, 127.0433
RADIUS = core.get_settings().search_radius_m

PT = "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography"


def section(title: str) -> None:
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def main() -> None:
    eng = core.get_engine()
    with eng.connect() as conn:
        section(f"1) 쿠폰 목록  (uid={UID})")
        rows = conn.execute(
            text(
                """
                SELECT coupon_id, brand_id, brand_name, coupon_type,
                       status, is_used, expires_at,
                       (expires_at - (now() AT TIME ZONE 'Asia/Seoul')::date) AS days_left
                FROM coupons WHERE uid = :uid ORDER BY created_at DESC
                """
            ),
            {"uid": UID},
        ).fetchall()
        if not rows:
            print("  (없음) → 매칭 결과가 반드시 빈 배열이 된다. 쿠폰부터 등록할 것.")
        for r in rows:
            m = r._mapping
            days = m["days_left"]
            usable_row = (
                m["status"] == "COMPLETED"
                and not m["is_used"]
                and m["brand_id"]
                and days is not None
                and days >= 0
            )
            flag = "OK " if usable_row else "-- "
            days_s = f"D{days:+}" if days is not None else "D(expires_at NULL)"
            print(f"  {flag}{(m['brand_id'] or '(brand_id NULL)'):<12} "
                  f"{m['status']:<11} used={m['is_used']!s:<5} "
                  f"exp={m['expires_at']} {days_s} {m['coupon_id']}")
        usable = [
            r._mapping["brand_id"] for r in rows
            if r._mapping["status"] == "COMPLETED"
            and not r._mapping["is_used"]
            and r._mapping["brand_id"]
            and r._mapping["days_left"] is not None
            and r._mapping["days_left"] >= 0
        ]

        section(f"2) 아주대({AJOU_LAT}, {AJOU_LNG}) {RADIUS}m 내 매장 브랜드 분포")
        rows = conn.execute(
            text(
                f"""
                SELECT brand_id, count(*) AS n
                FROM stores
                WHERE is_active AND ST_DWithin(geom::geography, {PT}, :r)
                GROUP BY brand_id ORDER BY n DESC
                """
            ),
            {"lat": AJOU_LAT, "lng": AJOU_LNG, "r": RADIUS},
        ).fetchall()
        if not rows:
            print(f"  (없음) → 반경을 넓히거나 좌표를 조정해야 한다.")
        for r in rows:
            mark = " ★쿠폰보유" if r._mapping["brand_id"] in usable else ""
            print(f"  {r._mapping['brand_id']:<14} {r._mapping['n']:>3}건{mark}")

        section("3) 매칭 후보 매장 (쿠폰 보유 브랜드) 최근접 5곳")
        if not usable:
            print("  건너뜀 — 사용 가능한 쿠폰이 없다.")
        else:
            rows = conn.execute(
                text(
                    f"""
                    SELECT store_name, brand_id, road_address, source,
                           ROUND(ST_Distance(geom::geography, {PT}))::int AS d,
                           ST_Y(geom) AS lat, ST_X(geom) AS lng
                    FROM stores
                    WHERE is_active AND brand_id = ANY(:brands)
                    ORDER BY geom::geography <-> {PT}
                    LIMIT 5
                    """
                ),
                {"lat": AJOU_LAT, "lng": AJOU_LNG, "brands": list(set(usable))},
            ).fetchall()
            for r in rows:
                m = r._mapping
                inr = "IN " if m["d"] <= RADIUS else "out"
                print(f"  {inr} {m['d']:>5}m  {m['store_name']:<24} "
                      f"({m['lat']:.6f}, {m['lng']:.6f})  {m['source']}")

        section("4) TC-L5 좌표 — 매장은 있으나 쿠폰이 없는 브랜드")
        rows = conn.execute(
            text(
                """
                SELECT store_name, brand_id, ST_Y(geom) AS lat, ST_X(geom) AS lng
                FROM stores
                WHERE is_active
                  AND brand_id <> ALL(COALESCE(:brands, ARRAY[]::text[]))
                LIMIT 3
                """
            ),
            {"brands": list(set(usable)) or None},
        ).fetchall()
        for r in rows:
            m = r._mapping
            print(f"  {m['brand_id']:<14} {m['store_name']:<24} "
                  f"lat={m['lat']:.6f} lng={m['lng']:.6f}")

        section("5) users.last_location  (TC-L4 판정 기준)")
        row = conn.execute(
            text(
                """
                SELECT ST_AsText(last_location) AS g, last_location_at
                FROM users WHERE uid = :uid
                """
            ),
            {"uid": UID},
        ).fetchone()
        if row is None:
            print("  사용자 행 없음 → get_last_location()은 None")
        else:
            print(f"  geom = {row._mapping['g']}")
            print(f"  at   = {row._mapping['last_location_at']}")

    print()
    print("core.get_last_location() 반환:", __import__("asyncio").run(
        core.get_last_location(UID)))


if __name__ == "__main__":
    main()