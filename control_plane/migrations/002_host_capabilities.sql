ALTER TABLE hosts ADD COLUMN os_family TEXT;
ALTER TABLE hosts ADD COLUMN os_version TEXT;
ALTER TABLE hosts ADD COLUMN qemu_binary TEXT;
ALTER TABLE hosts ADD COLUMN supported_accels TEXT;
ALTER TABLE hosts ADD COLUMN selected_accel TEXT;
