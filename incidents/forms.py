from django import forms
from django.db.models import Q

from .models import BranchArea, GymAsset, Incident, MaintenanceRecord


class IncidentAdminForm(forms.ModelForm):
    class Meta:
        model = Incident
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "branch_area" not in self.fields:
            return

        # Include inactive areas so historical assignments remain editable.
        areas = BranchArea.objects.select_related("branch")
        if self.is_bound:
            branch_id = self.data.get(self.add_prefix("branch"))
            try:
                branch_id = int(branch_id)
            except (TypeError, ValueError):
                areas = areas.none()
            else:
                areas = areas.filter(branch_id=branch_id)
        elif self.instance.branch_id:
            areas = areas.filter(branch_id=self.instance.branch_id)
        # On a new unbound form show all areas, labeled with their branch code.
        # A bound form and Incident.clean() both reject cross-branch choices.
        self.fields["branch_area"].queryset = areas
        if "gym_asset" in self.fields:
            assets = GymAsset.objects.select_related("branch")
            # Keep the original asset selectable after a historical transfer.
            if self.is_bound:
                try:
                    branch_id = int(self.data.get(self.add_prefix("branch")))
                except (TypeError, ValueError):
                    assets = assets.none()
                else:
                    selection = Q(branch_id=branch_id)
                    if self.instance.pk and self.instance.branch_id == branch_id:
                        selection |= Q(pk=self.instance.gym_asset_id)
                    assets = assets.filter(selection)
            elif self.instance.branch_id:
                assets = assets.filter(
                    Q(branch_id=self.instance.branch_id) | Q(pk=self.instance.gym_asset_id)
                )
            self.fields["gym_asset"].queryset = assets


class GymAssetAdminForm(forms.ModelForm):
    class Meta:
        model = GymAsset
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        areas = BranchArea.objects.select_related("branch")
        if self.is_bound:
            try:
                branch_id = int(self.data.get(self.add_prefix("branch")))
            except (TypeError, ValueError):
                areas = areas.none()
            else:
                areas = areas.filter(branch_id=branch_id)
        elif self.instance.branch_id:
            areas = areas.filter(branch_id=self.instance.branch_id)
        self.fields["branch_area"].queryset = areas


class MaintenanceAdminForm(forms.ModelForm):
    class Meta:
        model = MaintenanceRecord
        fields = "__all__"
