ALTER TABLE hosts ADD COLUMN available_images_json TEXT;

ALTER TABLE leases ADD COLUMN guest_image TEXT;
ALTER TABLE leases ADD COLUMN base_image_id TEXT;
