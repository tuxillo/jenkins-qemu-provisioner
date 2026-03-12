ALTER TABLE hosts ADD COLUMN cpu_allocatable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE hosts ADD COLUMN ram_allocatable_mb INTEGER NOT NULL DEFAULT 0;

UPDATE hosts
SET
  cpu_allocatable = CASE
    WHEN cpu_allocatable < 1 THEN cpu_total
    ELSE cpu_allocatable
  END,
  ram_allocatable_mb = CASE
    WHEN ram_allocatable_mb < 1 THEN ram_total_mb
    ELSE ram_allocatable_mb
  END;
