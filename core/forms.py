"""Base form classes that render with Tailwind styling."""
from django import forms

INPUT_CLASSES = (
    "w-full rounded-lg border-ink-300 border px-3 py-2 text-sm text-ink-900 bg-white "
    "placeholder-ink-400 shadow-2xs focus:outline-none focus:ring-2 focus:ring-brand-600/20 "
    "focus:border-brand-600 transition disabled:bg-ink-100 disabled:text-ink-500"
)


class StyledFormMixin:
    """Applies Tailwind classes to every widget automatically."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            existing = widget.attrs.get("class", "")
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = (existing + " rounded border-ink-300 text-brand-600 "
                                         "focus:ring-brand-500/40 h-4 w-4").strip()
            elif isinstance(widget, forms.RadioSelect):
                widget.attrs["class"] = (existing + " space-y-2").strip()
            else:
                widget.attrs["class"] = (existing + " " + INPUT_CLASSES).strip()
            if field.required:
                widget.attrs.setdefault("aria-required", "true")


class StyledModelForm(StyledFormMixin, forms.ModelForm):
    pass


class StyledForm(StyledFormMixin, forms.Form):
    pass
