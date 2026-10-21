from django.db import migrations
from importlib import import_module


previous=import_module('incidents.migrations.0024_request_products_documents').REQUEST_INTEGRITY
body=previous.split('CREATE FUNCTION gp_request_document_guard()',1)[1].split('CREATE TRIGGER gp_request_document_integrity',1)[0]
restore='CREATE OR REPLACE FUNCTION gp_request_document_guard()'+body
protect=restore.replace("BEGIN\n", "BEGIN\n    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Original history is protected' USING ERRCODE='23514'; END IF;\n",1)
trigger='DROP TRIGGER gp_request_document_integrity ON request_documents; CREATE TRIGGER gp_request_document_integrity BEFORE INSERT OR UPDATE{delete} ON request_documents FOR EACH ROW EXECUTE FUNCTION gp_request_document_guard();'


class Migration(migrations.Migration):
    dependencies=[('incidents','0024_request_products_documents')]
    operations=[migrations.RunSQL(protect+trigger.format(delete=' OR DELETE'),restore+trigger.format(delete=''))]
