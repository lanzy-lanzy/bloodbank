from django import forms

from appointments.models import Appointment
from core.forms import StyledModelForm


class AppointmentForm(StyledModelForm):
    """Staff-created appointment (any status permitted by workflow)."""

    class Meta:
        model = Appointment
        fields = ["donor", "date", "time", "location", "status", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()
        donor, date, time = cleaned.get("donor"), cleaned.get("date"), cleaned.get("time")
        if donor and date and time and Appointment.has_conflict(
            donor, date, time, exclude_pk=self.instance.pk if self.instance.pk else None
        ):
            raise forms.ValidationError("The donor already has an open appointment at that date and time.")
        return cleaned


class AppointmentSelfBookForm(StyledModelForm):
    """Donor self-booking — always created as REQUESTED pending staff confirmation."""

    class Meta:
        model = Appointment
        fields = ["date", "time", "location", "notes"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, donor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.donor = donor

    def clean_date(self):
        from django.utils import timezone
        date = self.cleaned_data["date"]
        if date < timezone.localdate():
            raise forms.ValidationError("Choose a future date.")
        return date

    def clean(self):
        cleaned = super().clean()
        if self.donor and cleaned.get("date") and cleaned.get("time"):
            if Appointment.has_conflict(self.donor, cleaned["date"], cleaned["time"],
                                        exclude_pk=self.instance.pk if self.instance.pk else None):
                raise forms.ValidationError("You already have an open appointment at that date and time.")
        return cleaned

    def save(self, commit=True):
        appointment = super().save(commit=False)
        appointment.donor = self.donor
        appointment.status = Appointment.Status.REQUESTED
        if commit:
            appointment.save()
        return appointment
