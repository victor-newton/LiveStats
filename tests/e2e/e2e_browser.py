# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""Chromium (Playwright) contra a pilha real. Sem duble: o painel fala com a api verdadeira atraves do Nginx."""

from __future__ import annotations

from contextlib import contextmanager

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page


@contextmanager
def chromium_context(**kwargs):
    from playwright.sync_api import sync_playwright  # import tardio: os testes de rede (dind) nao precisam do Playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="pt-BR", **kwargs)
        try:
            yield ctx
        finally:
            ctx.close()
            browser.close()


def watch_dialogs(page: Page) -> list[str]:
    """Registra (e fecha) qualquer alert/confirm/prompt; um alert disparado por XSS apareceria aqui."""
    seen: list[str] = []

    def on_dialog(dialog):
        seen.append(f"{dialog.type}:{dialog.message}")
        dialog.dismiss()

    page.on("dialog", on_dialog)
    return seen


def ui_login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(f"{base_url}/")
    page.locator("#loginUser").fill(username)
    page.locator("#loginPass").fill(password)
    page.locator("#loginBtn").click()
    page.locator("#panelView").wait_for(state="visible", timeout=20000)
