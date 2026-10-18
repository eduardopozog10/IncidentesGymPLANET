from django.db import migrations


FORWARD = """
ALTER TABLE gym_assets ADD CONSTRAINT asset_area_matches_branch
    FOREIGN KEY (branch_area_id, branch_id) REFERENCES branch_areas (id, branch_id);
ALTER TABLE asset_movements ADD CONSTRAINT movement_from_area_matches_branch
    FOREIGN KEY (from_area_id, from_branch_id) REFERENCES branch_areas (id, branch_id);
ALTER TABLE asset_movements ADD CONSTRAINT movement_to_area_matches_branch
    FOREIGN KEY (to_area_id, to_branch_id) REFERENCES branch_areas (id, branch_id);

CREATE FUNCTION gymplanet_validate_incident_asset() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE a record;
BEGIN
    IF NEW.gym_asset_id IS NOT NULL AND (
        TG_OP = 'INSERT' OR
        ROW(NEW.gym_asset_id, NEW.branch_id, NEW.branch_area_id) IS DISTINCT FROM
        ROW(OLD.gym_asset_id, OLD.branch_id, OLD.branch_area_id)
    ) THEN
        SELECT branch_id, branch_area_id INTO a FROM gym_assets
            WHERE id = NEW.gym_asset_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Unknown gym asset' USING ERRCODE = '23503';
        END IF;
        IF NEW.branch_id IS DISTINCT FROM a.branch_id OR
            (NEW.branch_area_id IS NOT NULL AND a.branch_area_id IS NOT NULL
             AND NEW.branch_area_id <> a.branch_area_id) THEN
            RAISE EXCEPTION 'Incident asset does not match branch or area'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    IF TG_OP = 'UPDATE' AND EXISTS (
        SELECT 1 FROM maintenance_records m WHERE m.incident_id = NEW.id
        AND (m.branch_id IS DISTINCT FROM NEW.branch_id OR
             (NEW.gym_asset_id IS NOT NULL AND m.asset_id <> NEW.gym_asset_id))
    ) THEN
        RAISE EXCEPTION 'Incident change would invalidate maintenance history'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER incident_asset_integrity BEFORE INSERT OR UPDATE ON incidents
    FOR EACH ROW EXECUTE FUNCTION gymplanet_validate_incident_asset();

CREATE FUNCTION gymplanet_validate_maintenance() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE a record; i record;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT branch_id INTO a FROM gym_assets WHERE id = NEW.asset_id FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Unknown maintenance asset' USING ERRCODE = '23503';
        END IF;
        NEW.branch_id := a.branch_id;
    ELSIF NEW.asset_id IS DISTINCT FROM OLD.asset_id OR
          NEW.branch_id IS DISTINCT FROM OLD.branch_id THEN
        RAISE EXCEPTION 'Maintenance asset and branch snapshot cannot be rewritten'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.incident_id IS NOT NULL AND (TG_OP = 'INSERT' OR
       NEW.incident_id IS DISTINCT FROM OLD.incident_id) THEN
            SELECT branch_id, gym_asset_id INTO i FROM incidents
                WHERE id = NEW.incident_id FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Unknown maintenance incident' USING ERRCODE = '23503';
            END IF;
            IF i.branch_id IS DISTINCT FROM NEW.branch_id OR
               (i.gym_asset_id IS NOT NULL AND i.gym_asset_id <> NEW.asset_id) THEN
                RAISE EXCEPTION 'Maintenance incident does not match asset and branch'
                    USING ERRCODE = '23514';
            END IF;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER maintenance_integrity BEFORE INSERT OR UPDATE ON maintenance_records
    FOR EACH ROW EXECUTE FUNCTION gymplanet_validate_maintenance();

CREATE FUNCTION gymplanet_record_asset_movement() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF ROW(OLD.branch_id, OLD.branch_area_id, OLD.code) IS DISTINCT FROM
       ROW(NEW.branch_id, NEW.branch_area_id, NEW.code) THEN
        INSERT INTO asset_movements
            (asset_id, from_branch_id, to_branch_id, from_area_id, to_area_id,
             from_code, to_code, created_at)
        VALUES (NEW.id, OLD.branch_id, NEW.branch_id, OLD.branch_area_id,
                NEW.branch_area_id, OLD.code, NEW.code, statement_timestamp());
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER asset_movement_history AFTER UPDATE ON gym_assets
    FOR EACH ROW EXECUTE FUNCTION gymplanet_record_asset_movement();

CREATE FUNCTION gymplanet_preserve_history() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION 'Historical records cannot be removed or rewritten'
        USING ERRCODE = '23514';
END;
$$;
CREATE TRIGGER immutable_asset_movements BEFORE UPDATE OR DELETE ON asset_movements
    FOR EACH ROW EXECUTE FUNCTION gymplanet_preserve_history();
CREATE TRIGGER preserve_maintenance BEFORE DELETE ON maintenance_records
    FOR EACH ROW EXECUTE FUNCTION gymplanet_preserve_history();
"""

REVERSE = """
DROP TRIGGER preserve_maintenance ON maintenance_records;
DROP TRIGGER immutable_asset_movements ON asset_movements;
DROP FUNCTION gymplanet_preserve_history();
DROP TRIGGER asset_movement_history ON gym_assets;
DROP FUNCTION gymplanet_record_asset_movement();
DROP TRIGGER maintenance_integrity ON maintenance_records;
DROP FUNCTION gymplanet_validate_maintenance();
DROP TRIGGER incident_asset_integrity ON incidents;
DROP FUNCTION gymplanet_validate_incident_asset();
ALTER TABLE asset_movements DROP CONSTRAINT movement_to_area_matches_branch;
ALTER TABLE asset_movements DROP CONSTRAINT movement_from_area_matches_branch;
ALTER TABLE gym_assets DROP CONSTRAINT asset_area_matches_branch;
"""


class Migration(migrations.Migration):
    dependencies = [("incidents", "0013_inventory_maintenance_requests")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
