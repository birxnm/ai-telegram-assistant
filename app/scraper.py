"""Утилита парсинга сайта (опционально). Скачивает страницы, чистит HTML и сохраняет
сырой текст в data/_scraped_raw.md для ручного ревью.

Запуск: python -m app.scraper

Файл сохраняется с префиксом "_", поэтому ingest его НЕ берёт автоматически: сначала
просмотрите/почистите вывод и при желании перенесите нужное в data/company.md.
"""

import os
import time
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://centr-krasok.kz"
PAGES = [
    "/",
    "/about/",
    "/about/delivery/",
    "/about/contacts/",
    "/designers/",
    "/for_builders/",
    "/brands/",
]
OUTPUT = os.path.join("data", "_scraped_raw.md")


def clean_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def main() -> None:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CentrKrasokBot/1.0)"}
    sections = []
    for page in PAGES:
        url = urljoin(BASE_URL, page)
        try:
            resp = requests.get(url, timeout=20, headers=headers)
            resp.raise_for_status()
            sections.append(f"## Страница: {url}\n\n{clean_html(resp.text)}")
            print(f"  ok: {url}")
        except Exception as exc:  # noqa: BLE001 — утилита, логируем и продолжаем
            print(f"  skip: {url} ({exc})")
        time.sleep(1)

    os.makedirs("data", exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n\n".join(sections))
    print(f"Сохранено в {OUTPUT}. Проверьте и при необходимости перенесите в data/company.md.")


if __name__ == "__main__":
    main()
