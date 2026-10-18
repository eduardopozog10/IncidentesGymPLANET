"""Authorization derived from a persisted transport account, never message text/AI."""
from incidents.models import Branch, TechnicianChannel


def authorized_branch(message):
    if not message.channel_account_id:
        return None
    account = TechnicianChannel.objects.select_for_update().filter(
        pk=message.channel_account_id, provider=message.provider,
        technician_id=message.technician_id, is_active=True, technician__is_active=True,
    ).first()
    if account is None or not account.branch_id:
        return None
    if message.external_sender_id and message.external_sender_id != account.external_user_id:
        return None
    return Branch.objects.select_for_update().filter(pk=account.branch_id, is_active=True).first()
