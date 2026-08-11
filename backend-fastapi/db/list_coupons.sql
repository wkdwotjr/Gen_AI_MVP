SELECT coupon_id, brand_id, coupon_type, product_name,
       face_value, expires_at, status, created_at
FROM coupons
WHERE uid = 'dev-uid-0001'
ORDER BY created_at DESC;