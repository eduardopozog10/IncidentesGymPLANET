from incidents.services.operations import manage_report


def update_report_status(pk, value, *, actor=None, solution="", reason=""):
    return manage_report(pk, actor, "status", status=value, solution=solution, reason=reason)
