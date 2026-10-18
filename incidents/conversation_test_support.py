"""Explicit branch evidence for legacy conversational regression scenarios."""
from incidents.models import Branch


class LegacyBranchFixture:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Branch.objects.create(code="TEST-LEGACY", name="Sucursal de prueba")


def with_test_branch(text):
    return f"Sucursal TEST-LEGACY; {text}"


def legacy_draft_data(data):
    return {
        **data,
        "branch_id": Branch.objects.get(code="TEST-LEGACY").pk,
        "branch": "Sucursal de prueba (TEST-LEGACY)",
        "report_type": "incident",
        # These explicit test fixtures represent a choice already made by the reporter.
        "_priority_explicit": bool(data.get("priority")),
    }
