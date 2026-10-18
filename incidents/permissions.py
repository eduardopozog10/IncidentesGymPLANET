"""Temporary restrictive policy until supervisor branch scopes are implemented."""
from rest_framework.permissions import BasePermission


class IsActiveSuperuser(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


class CanManageOperations(BasePermission):
    def has_permission(self, request, view):
        from incidents.services.operations import can_operate
        return can_operate(request.user)
