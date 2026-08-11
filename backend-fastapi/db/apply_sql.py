"""
db/apply_sql.py — psql 대체 SQL 실행기

psql이 없는 Windows 환경에서 init.sql / seed.sql / reset.sql을 실행한다.
Cloud SQL Auth Proxy가 127.0.0.1:5432 에 떠 있는 상태를 전제로 한다.

사용:
    python db/apply_sql.py db/init.sql
    python db/apply_sql.py db/init.sql db/seed.sql
    python db/apply_sql.py --check            # 스키마 상태만 점검

접속 정보는 .env 또는 환경변수에서 읽는다:
    DB_HOST(기본 127.0.0.1) / DB_PORT(5432) / DB_NAME(couponkok)
    DB_USER(postgres) / DB_PASSWORD(없으면 실행 시 입력받음)

설치:
    pip install "psycopg[binary]" python-dotenv
"""

from __future__ import annotations

import os
import sys
import getpass
import pathlib

try:
    import psycopg
except ImportError:
    sys.exit('psycopg가 없습니다.  pip install "psycopg[binary]" python-dotenv')

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# 한글 출력 깨짐 방지 (PowerShell cp949 대응)
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────
# 1. SQL 파일 → 문장 단위 분할
#    문장별로 실행해야 "몇 번째 문장에서 왜 죽었는지"가 나온다.
#    파일 전체를 한 번에 던지면 에러 위치를 알 수 없다.
# ──────────────────────────────────────────────────────────────
def split_statements(sql: str) -> list[str]:
    stmts, buf = [], []
    i, n = 0, len(sql)
    in_single = in_line_comment = in_block_comment = False
    dollar_tag: str | None = None

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 1
                in_block_comment = False
        elif dollar_tag:
            buf.append(ch)
            if sql.startswith(dollar_tag, i):
                buf.extend(dollar_tag[1:])
                i += len(dollar_tag) - 1
                dollar_tag = None
        elif in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":          # '' 이스케이프
                    buf.append(nxt)
                    i += 1
                else:
                    in_single = False
        else:
            if ch == "-" and nxt == "-":
                in_line_comment = True
                buf.append(ch)
            elif ch == "/" and nxt == "*":
                in_block_comment = True
                buf.append(ch)
            elif ch == "'":
                in_single = True
                buf.append(ch)
            elif ch == "$":
                end = sql.find("$", i + 1)
                tag = sql[i : end + 1] if end != -1 else ""
                # $$ 또는 $tag$ 형태만 달러 인용으로 취급
                if tag and (len(tag) == 2 or tag[1:-1].replace("_", "").isalnum()):
                    dollar_tag = tag
                    buf.append(tag)
                    i = end
                else:
                    buf.append(ch)
            elif ch == ";":
                stmts.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)

    # 주석만 남은 조각은 버린다
    out = []
    for s in stmts:
        body = "\n".join(
            ln for ln in s.splitlines() if ln.strip() and not ln.strip().startswith("--")
        ).strip()
        if body:
            out.append(s)
    return out


def first_line(stmt: str, width: int = 78) -> str:
    for ln in stmt.splitlines():
        t = ln.strip()
        if t and not t.startswith("--"):
            return t[:width] + ("…" if len(t) > width else "")
    return stmt[:width]


# ──────────────────────────────────────────────────────────────
# 2. 접속
# ──────────────────────────────────────────────────────────────
def connect() -> psycopg.Connection:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "couponkok")
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD") or getpass.getpass(f"{user}@{host}:{port} 비밀번호: ")

    print(f"접속: {user}@{host}:{port}/{name}")
    return psycopg.connect(
        host=host, port=port, dbname=name, user=user, password=pw, connect_timeout=10
    )


# ──────────────────────────────────────────────────────────────
# 3. 실행
# ──────────────────────────────────────────────────────────────
def run_file(conn: psycopg.Connection, path: pathlib.Path) -> bool:
    sql = path.read_text(encoding="utf-8")
    stmts = split_statements(sql)
    print(f"\n── {path}  ({len(stmts)}개 문장)")

    with conn.cursor() as cur:
        for idx, stmt in enumerate(stmts, 1):
            label = first_line(stmt)
            try:
                cur.execute(stmt)  # type: ignore[arg-type]
                print(f"  [{idx:>2}/{len(stmts)}] OK    {label}")
            except Exception as e:
                conn.rollback()
                print(f"  [{idx:>2}/{len(stmts)}] FAIL  {label}")
                print(f"\n{type(e).__name__}: {e}")
                print("\n트랜잭션을 롤백했습니다. 이 파일의 변경은 반영되지 않았습니다.")
                return False
    conn.commit()
    print(f"── {path} 완료 (커밋)")
    return True


CHECKS = [
    ("확장", "SELECT extname, extversion FROM pg_extension ORDER BY extname"),
    (
        "테이블",
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
    ),
    (
        "인덱스",
        "SELECT indexname FROM pg_indexes WHERE schemaname='public' ORDER BY indexname",
    ),
    (
        "행 수",
        """SELECT 'users' t, count(*) FROM users
           UNION ALL SELECT 'coupons', count(*) FROM coupons
           UNION ALL SELECT 'stores',  count(*) FROM stores""",
    ),
    ("타임존", "SHOW timezone"),
    ("서버 버전", "SELECT version()"),
]


def check(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        for title, q in CHECKS:
            print(f"\n[{title}]")
            try:
                cur.execute(q)  # type: ignore[arg-type]
                rows = cur.fetchall()
                if not rows:
                    print("  (없음)")
                for r in rows:
                    print("  " + "  ".join(str(c) for c in r))
            except Exception as e:
                conn.rollback()
                print(f"  조회 실패: {str(e).splitlines()[0]}")


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1

    check_only = "--check" in args
    paths = [pathlib.Path(a) for a in args if not a.startswith("--")]

    for p in paths:
        if not p.exists():
            print(f"파일 없음: {p}")
            return 1

    with connect() as conn:
        for p in paths:
            if not run_file(conn, p):
                return 1
        if check_only or paths:
            print("\n" + "=" * 60)
            check(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())