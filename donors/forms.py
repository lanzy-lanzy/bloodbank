from django import forms
from django.db.models import Q

from core.forms import INPUT_CLASSES, StyledFormMixin, StyledModelForm
from core.validators import validate_ph_mobile
from donors.interview import ALL_ITEMS, FEMALE_SECTION, SECTION_BY_KEY
from donors.models import Donor, DonorInterviewSheet, DonorScreening, ScreeningQuestion
from inventory.models import BloodType


class DonorForm(StyledModelForm):
    """Staff/admin donor registration & editing."""

    contact_number = forms.CharField(max_length=30, label="Contact number (mobile)",
                                     validators=[validate_ph_mobile])

    class Meta:
        model = Donor
        fields = [
            "first_name", "middle_name", "last_name", "suffix", "date_of_birth", "sex",
            "contact_number", "email", "address", "municipality", "province",
            "emergency_contact_name", "emergency_contact_phone", "blood_type", "status",
        ]
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}),
            # The two dropdowns are styled as one control family in
            # static/css/input.css; mark them on both registration surfaces.
            "sex": forms.Select(attrs={"class": "bb-select"}),
            "blood_type": forms.Select(attrs={"class": "bb-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Self-explanatory placeholders on both dropdowns (a bare "---------"
        # is neither readable nor accessible), and the type list follows the
        # public sign-up: active blood types only, plus any type already on
        # the record so a legacy row can still be re-saved unchanged.
        self.fields["sex"].choices = [("", "Select…")] + list(Donor.Sex.choices)
        active_or_current = Q(is_active=True)
        if self.instance.pk and self.instance.blood_type_id:
            active_or_current |= Q(pk=self.instance.blood_type_id)
        self.fields["blood_type"].queryset = BloodType.objects.filter(active_or_current)
        self.fields["blood_type"].empty_label = "Not on file"


class DonorSelfProfileForm(StyledModelForm):
    """Donor self-service: only permitted personal information (no medical/status fields)."""

    contact_number = forms.CharField(max_length=30, label="Contact number (mobile)",
                                     validators=[validate_ph_mobile])

    class Meta:
        model = Donor
        fields = ["contact_number", "email", "address", "municipality", "province",
                  "emergency_contact_name", "emergency_contact_phone"]


class DonorStatusForm(StyledModelForm):
    class Meta:
        model = Donor
        fields = ["status", "deferral_reason", "deferred_until"]
        widgets = {"deferred_until": forms.DateInput(attrs={"type": "date"})}

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        if status in ("TEMP_DEFERRED", "PERM_DEFERRED", "BLACKLISTED") and not cleaned.get("deferral_reason"):
            self.add_error("deferral_reason", "A reason is required when deferring or restricting a donor.")
        if status == "TEMP_DEFERRED" and not cleaned.get("deferred_until"):
            self.add_error("deferred_until", "Temporary deferrals need an end date.")
        return cleaned


class ScreeningForm(StyledModelForm):
    class Meta:
        model = DonorScreening
        fields = ["identity_verified", "consent_given", "weight_kg", "temperature_c",
                  "bp_systolic", "bp_diastolic", "pulse_bpm", "hemoglobin_gdl",
                  "result", "deferral_days", "notes"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("result") == "CLEARED" and not (cleaned.get("identity_verified") and cleaned.get("consent_given")):
            raise forms.ValidationError(
                "Identity verification and consent are required before a donor can be CLEARED."
            )
        if cleaned.get("result") == "DEFERRED" and not cleaned.get("deferral_days"):
            self.add_error("deferral_days", "Specify the staff-decided deferral length in days.")
        return cleaned


class DonorInterviewSheetForm(StyledFormMixin, forms.Form):
    """The editable PRC Blood Donor Interview Sheet.

    A plain ``forms.Form`` (never ``StyledModelForm`` — see agents.md): the
    questionnaire rows are generated from ``donors.interview`` and stored as
    child ``InterviewAnswer`` rows, so one POST drives the sheet and its
    answers. Saving a draft is always allowed; *completing* the sheet requires
    a YES/NO on every item, the CUE declaration and both signatures.

    A blank answer is deliberately distinct from NO: an unanswered item means
    "not asked / not answered", and the printed form leaves it blank rather
    than claiming a denial the donor never made.
    """

    TEXT_FIELDS = {
        "prc_donor_number": ("PRC blood donor number", 40),
        "doh_barcode": ("DOH NBBets barcode", 80),
        "nationality": ("Nationality", 60),
        "religion": ("Religion", 60),
        "education": ("Education", 80),
        "occupation": ("Occupation", 80),
        "office_address": ("Office address", 255),
        "address_no": ("House / lot no.", 60),
        "address_street": ("Street", 150),
        "address_barangay": ("Barangay", 120),
        "address_zip": ("Zip code", 10),
        "telephone_number": ("Telephone no.", 30),
        "id_school": ("School ID", 60),
        "id_company": ("Company ID", 60),
        "id_prc": ("PRC ID", 60),
        "id_drivers": ("Driver's licence", 60),
        "id_sss": ("SSS / GSIS / BIR", 60),
        "id_others": ("Other ID", 60),
        "cue_unit_serial": ("Unit serial number", 60),
        "cue_place_of_donation": ("Place of donation", 160),
        "donor_signature": ("Donor signature (printed name)", 120),
        "interviewer_signature": ("Interviewer signature (printed name)", 120),
    }

    civil_status = forms.ChoiceField(required=False, label="Civil status")
    cue_choice = forms.ChoiceField(
        required=False, label="I believe that my blood is",
        choices=DonorInterviewSheet.CueChoice.choices, widget=forms.RadioSelect)
    cue_date_of_donation = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))
    donor_signed_on = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))
    interviewer_signed_on = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}))
    mark_complete = forms.BooleanField(
        required=False, label="Interview completed and signed")

    def __init__(self, *args, donor=None, **kwargs):
        super().__init__(*args, **kwargs)
        # The donor decides which items the female-donors block applies to and
        # is what the header summary shows; it is never edited through this form.
        self.donor = donor
        # Header fields: declared here so the labels stay in one place.
        for name, (label, max_length) in self.TEXT_FIELDS.items():
            self.fields[name] = forms.CharField(
                label=label, required=False, max_length=max_length)
            self.fields[name].widget.attrs["class"] = INPUT_CLASSES
        self.fields["civil_status"].choices = [
            ("", "—")] + list(DonorInterviewSheet.CivilStatus.choices)
        # Questionnaire rows: one YES/NO choice plus a remarks box per item.
        # No blank radio: an item the donor was not asked stays unanswered on
        # the server (a missing key posts as ""), and a blank is not a "No".
        for item in ALL_ITEMS:
            if item.kind == "YESNO":
                self.fields[f"answer_{item.key}"] = forms.ChoiceField(
                    label=f"Q{item.number:02d}", required=False,
                    choices=[("YES", "Yes"), ("NO", "No")],
                    widget=forms.RadioSelect)
            self.fields[f"remarks_{item.key}"] = forms.CharField(
                label=f"Q{item.number:02d} remarks", required=False, max_length=500,
                widget=forms.TextInput)
        # StyledFormMixin ran before the dynamic fields existed; give the
        # compact table inputs the sheet's own (light, dense) styles instead
        # of the page-level input classes.
        for item in ALL_ITEMS:
            self.fields[f"remarks_{item.key}"].widget.attrs["class"] = "bb-sheet-input"
            if item.kind == "YESNO":
                self.fields[f"answer_{item.key}"].widget.attrs["class"] = "bb-tick-input"
        self.fields["cue_choice"].widget.attrs["class"] = "bb-cue-input"

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("mark_complete"):
            return cleaned  # a draft may be saved at any stage
        # The female-donor block is only required of female donors; for anybody
        # else those items stay unanswered on the record (as on the paper form).
        female = self.donor is not None and self.donor.sex == "FEMALE"
        required = [item for item in ALL_ITEMS
                    if item.kind == "YESNO"
                    and (female or SECTION_BY_KEY.get(item.key) != FEMALE_SECTION)]
        missing = [item.number for item in required
                   if not cleaned.get(f"answer_{item.key}")]
        if missing:
            listed = ", ".join(f"Q{number:02d}" for number in missing[:8])
            if len(missing) > 8:
                listed += f" (+{len(missing) - 8} more)"
            self.add_error("mark_complete",
                           "Completing the sheet requires a YES or NO on every item. "
                           f"Still blank: {listed}.")
        if not cleaned.get("cue_choice"):
            self.add_error("cue_choice",
                           "Select the donor's CUE declaration before completing the sheet.")
        if not (cleaned.get("donor_signature") or "").strip():
            self.add_error("donor_signature",
                           "Record the donor's printed name to complete the sheet.")
        if not cleaned.get("donor_signed_on"):
            self.add_error("donor_signed_on", "Enter the date the donor signed.")
        if not (cleaned.get("interviewer_signature") or "").strip():
            self.add_error("interviewer_signature",
                           "Record the interviewer's printed name to complete the sheet.")
        if not cleaned.get("interviewer_signed_on"):
            self.add_error("interviewer_signed_on", "Enter the date the interview was signed.")
        return cleaned

    def sections(self):
        """Sections/rows for the template, bound to this form instance.

        ``yes_widget`` / ``no_widget`` are the two BoundWidgets of the radio
        group, so the template can print one radio per table column. Date-only
        items have no YES/NO column on the paper form: their answer lives in
        the remarks box.
        """
        from donors.interview import SECTIONS
        prepared = []
        for section in SECTIONS:
            rows = []
            for item in section.items:
                answer_field = (self[f"answer_{item.key}"]
                                if item.kind == "YESNO" else None)
                widgets = {}
                if answer_field is not None:
                    widgets = {widget.data["value"]: widget for widget in answer_field}
                rows.append({
                    "key": item.key,
                    "number": item.number,
                    "text": item.text,
                    "kind": item.kind,
                    "has_answer": item.kind == "YESNO",
                    "answer_field": answer_field,
                    "yes_widget": widgets.get("YES"),
                    "no_widget": widgets.get("NO"),
                    "remarks_field": self[f"remarks_{item.key}"],
                    "remarks_date": item.kind == "DATE",
                })
            prepared.append({"key": section.key, "title": section.title,
                             "note": section.note, "rows": rows})
        return prepared


def question_forms(prefix="q"):
    """One simple form per active screening question (configurable questionnaire)."""
    forms_list = []
    for question in ScreeningQuestion.objects.filter(is_active=True):
        if question.answer_type == "YESNO":
            field = forms.ChoiceField(choices=[("", "—"), ("YES", "Yes"), ("NO", "No")],
                                      required=question.required, label=question.text)
        elif question.answer_type == "NUMBER":
            field = forms.CharField(required=question.required, label=question.text)
        elif question.answer_type == "CHOICE":
            field = forms.ChoiceField(
                choices=[("", "—")] + [(c, c) for c in question.choices],
                required=question.required, label=question.text)
        else:
            field = forms.CharField(required=question.required, label=question.text,
                                    widget=forms.Textarea(attrs={"rows": 2}))
        from core.forms import StyledFormMixin
        form_class = type(f"Q{question.pk}Form", (StyledFormMixin, forms.Form), {f"field_{question.pk}": field})
        forms_list.append({"question": question, "form": form_class(prefix=f"{prefix}{question.pk}")})
    return forms_list
