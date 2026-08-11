"""TC-L4(50m 중복 억제) / TC-L7(속도 이상치) 자동 검증 + 진단.

수동 Enter 없이 HTTP 요청과 DB 확인을 번갈아 수행한다.
실행 (backend-fastapi, venv 활성화, uvicorn + proxy 기동 상태):
    python scripts/verify_l4_l7.py
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app import core  # noqa: E402

URL = "http://127.0.0.1:8000/api/v1/locations"
UID = core.get_settings().dev_uid

AJOU_LAT, AJOU_LNG = 37.2803, 127.0433
D30 = 0.00027   # 위도 약 30m (50m 억제 임계값 미만)
D60 = 0.00054   # 위도 약 60m (임계값 초과, 속도는 216km/h 수준)
BUSAN_LAT, BUSAN_LNG = 35.1796, 129.0756


def hav(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    a = (sin(radians(lat2 - lat1) / 2) ** 2
         + cos(p1) * cos(p2) * sin(radians(lng2 - lng1) / 2) ** 2)
    return 2 * r * asin(sqrt(a))


def post(lat, lng, source="PERIODIC", accuracy=12.5):
    body = json.dumps({
        "points": [{
            "lat": lat, "lng": lng, "accuracy_m": accuracy,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": source,
        }]
    }).encode()
    req = urllib.request.Request(
        URL, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, json.loads(res.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def db_state():
    with core.get_engine().connect() as conn:
        row = conn.execute(
            text(
                "SELECT ST_Y(last_location) AS lat, ST_X(last_location) AS lng,"
                "       last_location_at AS at, now() AS db_now "
                "FROM users WHERE uid = :uid"
            ),
            {"uid": UID},
        ).fetchone()
    return dict(row._mapping) if row else None


def reset():
    with core.get_engine().begin() as conn:
        conn.execute(
            text("UPDATE users SET last_location=NULL, last_location_at=NULL "
                 "WHERE uid=:uid"),
            {"uid": UID},
        )


def brief(tag, status, data):
    reasons = [r["reason"] for r in data.get("rejected_reasons", [])]
    print(f"  {tag}: HTTP {status} accepted={data.get('accepted')} "
          f"rejected={data.get('rejected')} {reasons} "
          f"matches={len(data.get('matches', []))}")


print("=" * 72)
print("0) 클라이언트 시계 vs DB 시계")
print("=" * 72)
st = db_state()
if st:
    skew = (datetime.now(timezone.utc) - st["db_now"]).total_seconds()
    print(f"  local(UTC) - db.now() = {skew:+.2f}s")
    if skew < -1.0:
        print("  ** 로컬 시계가 DB보다 느리다. recorded_at 기반 속도 판정이 무력화된다.")

print()
print("=" * 72)
print("TC-L4  50m 중복 억제")
print("=" * 72)
reset()

s, d = post(AJOU_LAT, AJOU_LNG)
brief("A 아주대", s, d)
a0 = db_state()
print(f"     last_location_at = {a0['at']}")

time.sleep(1.5)
s, d = post(AJOU_LAT + D30, AJOU_LNG)
brief("B +30m ", s, d)
a1 = db_state()
print(f"     last_location_at = {a1['at']}")
print(f"  → 30m 이동: 갱신 {'생략됨 (PASS)' if a1['at'] == a0['at'] else '발생함 (FAIL)'}")

time.sleep(1.5)
s, d = post(AJOU_LAT + D60, AJOU_LNG)
brief("C +60m ", s, d)
a2 = db_state()
print(f"     last_location_at = {a2['at']}")
print(f"  → 60m 이동: 갱신 {'발생함 (PASS)' if a2['at'] != a1['at'] else '생략됨 (FAIL)'}")

print()
print("=" * 72)
print("TC-L7  속도 이상치 진단")
print("=" * 72)
before = db_state()
moved = hav(before["lat"], before["lng"], BUSAN_LAT, BUSAN_LNG)
elapsed = (datetime.now(timezone.utc) - before["at"]).total_seconds()
kmh = (moved / 1000.0) / (elapsed / 3600.0) if elapsed > 0 else float("nan")
print(f"  직전 좌표 ({before['lat']:.5f}, {before['lng']:.5f}) @ {before['at']}")
print(f"  이동 {moved/1000:.1f}km / 경과 {elapsed:+.2f}s → {kmh:,.0f} km/h")
if elapsed <= 0:
    print("  ** 경과시간이 0 이하다. 코드의 `elapsed_h > 0` 가드가 검사를 건너뛴다.")

s, d = post(BUSAN_LAT, BUSAN_LNG)
brief("부산 PERIODIC      ", s, d)
s, d = post(BUSAN_LAT, BUSAN_LNG, source="MANUAL_REFRESH")
brief("부산 MANUAL_REFRESH", s, d)

print()
print("=" * 72)
print("TC-L5 후보 — 300m 내에 쿠폰 보유 브랜드가 없는 매장")
print("=" * 72)
with core.get_engine().connect() as conn:
    owned = [r[0] for r in conn.execute(
        text("SELECT DISTINCT brand_id FROM coupons "
             "WHERE uid=:uid AND status='COMPLETED' AND is_used=false "
             "AND brand_id IS NOT NULL AND expires_at >= current_date"),
        {"uid": UID},
    ).fetchall()]
    print(f"  보유 브랜드: {owned}")
    rows = conn.execute(
        text("""
            SELECT s.store_name, s.brand_id,
                   ST_Y(s.geom) AS lat, ST_X(s.geom) AS lng
            FROM stores s
            WHERE s.is_active
              AND s.brand_id <> ALL(:owned)
              AND NOT EXISTS (
                  SELECT 1 FROM stores t
                  WHERE t.is_active AND t.brand_id = ANY(:owned)
                    AND ST_DWithin(t.geom::geography, s.geom::geography, 300)
              )
            LIMIT 5
        """),
        {"owned": owned},
    ).fetchall()
    for r in rows:
        m = r._mapping
        print(f"  {m['brand_id']:<12} {m['store_name']:<28} "
              f"$FAR_LAT = {m['lat']:.6f} ; $FAR_LNG = {m['lng']:.6f}")

        