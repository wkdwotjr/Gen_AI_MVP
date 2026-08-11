-- 1) 특정 쿠폰 하나
--DELETE FROM coupons WHERE coupon_id = 'cpn_여기에붙여넣기';

-- 2) 브랜드 단위 (컴포즈만 정리)
 DELETE FROM coupons WHERE uid = 'dev-uid-0001' AND brand_id = 'compose';

-- 3) 전체 초기화 (쿠폰 등록 처음부터 테스트할 때)
-- DELETE FROM coupons WHERE uid = 'dev-uid-0001';