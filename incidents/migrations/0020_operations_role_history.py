from django.db import migrations


def role(apps, schema_editor):
    alias = schema_editor.connection.alias
    ct, _ = apps.get_model("contenttypes", "ContentType").objects.using(alias).get_or_create(app_label="incidents", model="incident")
    perm, _ = apps.get_model("auth", "Permission").objects.using(alias).get_or_create(content_type=ct, codename="manage_operations", defaults={"name":"Gestionar operaciones de todas las sucursales"})
    group, _ = apps.get_model("auth", "Group").objects.using(alias).get_or_create(name="operations_manager")
    group.permissions.add(perm)


SQL = """
CREATE FUNCTION gymplanet_immutable_operations() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'El historial operativo es inmutable'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER protect_report_activity BEFORE UPDATE OR DELETE ON report_activity
FOR EACH ROW EXECUTE FUNCTION gymplanet_immutable_operations();
CREATE TRIGGER protect_report_assignments BEFORE UPDATE OR DELETE ON report_assignments
FOR EACH ROW EXECUTE FUNCTION gymplanet_immutable_operations();
"""
REVERSE = """
DROP TRIGGER protect_report_activity ON report_activity;
DROP TRIGGER protect_report_assignments ON report_assignments;
DROP FUNCTION gymplanet_immutable_operations();
"""


class Migration(migrations.Migration):
    dependencies = [("incidents", "0019_operational_management"), ("auth", "0012_alter_user_first_name_max_length")]
    operations = [migrations.RunPython(role, migrations.RunPython.noop), migrations.RunSQL(SQL, REVERSE)]
