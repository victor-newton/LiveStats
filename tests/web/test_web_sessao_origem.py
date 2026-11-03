"""WEB-SES-01..03 (D37): a aba Inscritos mostra se os cookies foram colados no painel ou renovados pelo worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from playwright.sync_api import Page, expect
from web_helpers import do_login


def agora_menos(minutos: int) -> str:
    t = datetime.now(timezone.utc) - timedelta(minutes=minutos)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def cookies_status(source: str | None, created_at: str | None, renewed_from: int | None = None) -> dict:
    return {
        "configured": source is not None, "count": 12 if source else 0, "uploaded_at": created_at, "applied": True,
        "source": source, "created_at": created_at, "renewed_from": renewed_from,
    }


def test_web_ses_01_colado_no_painel_mostra_a_data(page: Page, api, base_url):
    api.cookies = cookies_status("admin", "2026-11-02T10:00:00Z")
    do_login(page, base_url, api)
    origem = page.locator("#cookiesOrigin")
    expect(origem).to_be_visible()
    expect(origem).to_contain_text("Colado no painel em")
    expect(origem).to_contain_text("2026")
    expect(origem).not_to_contain_text("Renovado")


def test_web_ses_02_renovado_pelo_worker_mostra_ha_quanto_tempo(page: Page, api, base_url):
    api.cookies = cookies_status("worker", agora_menos(5), renewed_from=3)
    do_login(page, base_url, api)
    origem = page.locator("#cookiesOrigin")
    expect(origem).to_have_text("Renovado automaticamente há 5 min")
    expect(origem).not_to_contain_text("Colado")


def test_web_ses_03_sem_cookies_nada_aparece(page: Page, api, base_url):
    api.cookies = cookies_status(None, None)
    do_login(page, base_url, api)
    expect(page.locator("#cookiesOrigin")).to_be_hidden()
