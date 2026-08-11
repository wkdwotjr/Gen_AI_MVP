-- cleanup.sql : 스키마 수정 전 등록돼 필드가 빈 쿠폰 제거
DELETE FROM coupons
WHERE uid = 'dev-uid-0001'
  AND status = 'COMPLETED'
  AND product_name IS NULL
  AND coupon_type = 'UNKNOWN';