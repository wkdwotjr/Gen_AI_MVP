"""users.last_location 을 NULL 로 되돌린다 (테스트용).

속도 이상치 필터는 직전 좌표를 기준으로 판정하므로, 멀리 떨어진 좌표를
연속으로 던지는 테스트에서는 그 사이에 이 스크립트로 기준점을 지워야 한다.

실행: python scripts/reset_last_location.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app import core  # noqa: E402

UID = core.get_settings().dev_uid

with core.get_engine().begin() as conn:
    conn.execute(
        text(
            "UPDATE users SET last_location = NULL, last_location_at = NULL "
            "WHERE uid = :uid"
        ),
        {"uid": UID},
    )
print(f"last_location 초기화 완료 (uid={UID})")