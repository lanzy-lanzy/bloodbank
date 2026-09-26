"""Shared helpers for modal-based CRUD.

The same URL serves two renderings:

* a normal full page (direct navigation, bookmarks, tests, the e2e walk) —
  template extends ``base.html``;
* a modal fragment (when the request is an HTMX request) — the very same
  template extends ``components/modal_shell.html`` instead, via the ``layout``
  context variable.

So a "Create"/"Edit"/"Detail"/"Delete" link becomes an ``hx-get`` trigger that
targets ``#modal-root``; the server returns the panel content and the browser
never leaves the current page. Successful ``hx-post`` form submissions reply
with an ``HX-Trigger: bb:modal-success`` header that the base.html controller
uses to close the modal and re-fetch the underlying page content in place.
"""
import json

from django.http import HttpResponse
from django.shortcuts import render

from core.mixins import is_htmx

MODAL_LAYOUT = "components/modal_shell.html"


def render_any(request, template, context=None, *, modal_title=""):
    """Render ``template`` as a modal fragment for HTMX requests, else full page."""
    context = dict(context or {})
    if is_htmx(request):
        context["layout"] = MODAL_LAYOUT
        context.setdefault("modal_title", modal_title)
        return render(request, template, context)
    return render(request, template, context)


def modal_success(request, *, fallback_redirect, redirect=None):
    """Response for a successful form POST.

    HTMX: empty body (swapping it into #modal-root closes the modal) +
    ``bb:modal-success`` trigger (JS refreshes the page in place; queued
    ``messages`` show on refresh).
    Plain POST: a normal ``redirect()`` to ``fallback_redirect`` (or ``redirect``).
    """
    if is_htmx(request):
        resp = HttpResponse("")
        payload = {"bb:modal-success": {"redirect": redirect or ""}}
        resp["HX-Trigger"] = json.dumps(payload)
        return resp
    from django.shortcuts import redirect as _redirect
    return _redirect(redirect or fallback_redirect)


class ModalFormMixin:
    """CBV support for ModalFormView-style create/edit views.

    Set ``modal_title`` on the subclass. The class' template must use
    ``{% extends layout|default:"base.html" %}`` and its ``<form>`` must add
    the htmx attributes when ``layout`` is present (see ``components/form.html``).
    """

    modal_title = ""

    def is_modal(self):
        return is_htmx(self.request)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)  # type: ignore[mgr]
        if self.is_modal():
            ctx["layout"] = MODAL_LAYOUT
            ctx.setdefault("modal_title", self.modal_title)
        return ctx

    def form_valid(self, form):
        result = super().form_valid(form)  # type: ignore[mgr]
        if self.is_modal():
            return modal_success(self.request, fallback_redirect=self.get_success_url())
        return result
