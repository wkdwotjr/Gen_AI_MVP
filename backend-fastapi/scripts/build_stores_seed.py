# scripts/build_stores_seed.py
from pathlib import Path
import pandas as pd
from ulid import ULID          # pip install python-ulid

ROOT = Path(__file__).resolve().parent.parent      # backend-fastapi/
SRC  = ROOT / "data/raw/소상공인시장진흥공단_상가(상권)정보_경기_202603.csv"
DST  = ROOT / "db/stores_seed.csv"

BRANDS = {
    "메가엠지씨커피": "megamgc",
    "컴포즈커피":     "compose",
    "이디야":         "ediya",
    "빽다방":         "paikdabang",
    "배스킨라빈스":   "baskinrobbins",
    "파리바게뜨":     "paris",
    "뚜레쥬르":       "tourlesjours",
    "투썸플레이스":   "twosome",
}
COLS = ["상가업소번호", "상호명", "지점명", "도로명주소", "경도", "위도", "시군구명"]

df = pd.read_csv(SRC, encoding="utf-8", low_memory=False,
                 usecols=COLS, dtype={"상가업소번호": str})

sw = df[df["시군구명"].astype(str).str.startswith("수원")].copy()

sw["brand_id"] = None
for kw, bid in BRANDS.items():
    sw.loc[sw["상호명"].str.contains(kw, na=False), "brand_id"] = bid

out = sw[sw["brand_id"].notna()].copy()

out["store_id"]   = [f"str_{ULID()}" for _ in range(len(out))]
out["store_name"] = (out["상호명"].fillna("") + " " + out["지점명"].fillna("")).str.strip()
out["store_type"] = "NORMAL"
out["source"]     = "PUBLIC_DATA"

out = out.rename(columns={"상가업소번호": "external_id",
                          "도로명주소": "road_address",
                          "경도": "lng", "위도": "lat"})
out = out[["store_id", "brand_id", "store_name", "road_address",
           "lng", "lat", "store_type", "source", "external_id"]]

DST.parent.mkdir(parents=True, exist_ok=True)
out.to_csv(DST, index=False, encoding="utf-8")

print(len(out), "건 →", DST)
print(out.groupby("brand_id").size())