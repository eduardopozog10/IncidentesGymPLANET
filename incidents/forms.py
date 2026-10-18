from django import forms

from .models import BranchArea, Incident


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
