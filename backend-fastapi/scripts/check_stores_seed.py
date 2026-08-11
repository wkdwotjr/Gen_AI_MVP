# scripts/check_stores_seed.py
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
df = pd.read_csv(ROOT / "db/stores_seed.csv", encoding="utf-8",
                 dtype={"external_id": str})

print("총", len(df), "건\n")
print("--- 결측 ---")
print(df.isna().sum(), "\n")
print("--- 좌표 범위 (수원: lng 126.9~127.1 / lat 37.2~37.4) ---")
print(df[["lng", "lat"]].describe().loc[["min", "max"]], "\n")
print("external_id 중복:", df["external_id"].duplicated().sum())
print("store_id  중복:", df["store_id"].duplicated().sum(), "\n")
print("--- 샘플 15건 ---")
print(df.sample(15, random_state=0)[["brand_id", "store_name", "road_address"]].to_string())