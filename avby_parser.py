"""
Парсер av.by: собирает соответствие "марка -> список моделей".

Версия с Selenium: страницы av.by открываются в настоящем Chrome,
поэтому скрипт не использует requests для загрузки каталога.

Перед первым запуском:
    py -m pip install selenium beautifulsoup4

Запуск:
    py avby_scraper.py

Результат:
    av_by_brand_models.json

Скрипт можно безопасно перезапускать: после каждой марки результат
сохраняется в JSON, уже обработанные марки пропускаются.
"""

import json
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


BASE = "https://av.by"
CATALOG_URL = f"{BASE}/catalog"
OUTPUT_FILE = Path("av_by_brand_models.json")

# Пауза между переходами, чтобы не создавать лишнюю нагрузку на сайт.
REQUEST_DELAY_RANGE = (2.0, 4.0)

# Таймаут ожидания загрузки страницы.
PAGE_LOAD_TIMEOUT = 30


DISALLOWED_PREFIXES = [
    "/model_catalog",
    "/parts_catalog",
    "/hello_world",
    "/registration",
    "/login",
    "/static/",
    "/view/verification/",
]


def is_allowed(path: str) -> bool:
    for prefix in DISALLOWED_PREFIXES:
        if path.startswith(prefix):
            return False

    if "?sort=" in path or "event=9" in path or path.startswith("/filter"):
        return False

    return True


def create_driver() -> webdriver.Chrome:
    options = Options()

    # Обычный видимый Chrome. Это важно для av.by: страницу можно
    # открыть так же, как обычный пользователь.
    options.add_argument("--start-maximized")
    options.add_argument("--lang=ru-RU")

    # Не используем headless.
    # Не отключаем проверки безопасности сайта и не пытаемся обходить
    # CAPTCHA/проверку пользователя.

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def wait_for_page(driver: webdriver.Chrome) -> None:
    WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )

    # Небольшая дополнительная пауза для JS, который может дорисовывать каталог.
    time.sleep(1.5)


def fetch(driver: webdriver.Chrome, url: str, retries: int = 3) -> str | None:
    for attempt in range(1, retries + 1):
        try:
            print(f"  Открываю: {url}")
            driver.get(url)
            wait_for_page(driver)

            current_url = driver.current_url
            page_source = driver.page_source

            # Не просим пользователя нажимать Enter.
            # Если страница загрузилась, сразу продолжаем обработку.
            # CAPTCHA/проверку пользователя скрипт не пытается обходить.
            page_source = driver.page_source

            if current_url.startswith(BASE) and len(page_source) > 1000:
                return page_source

            print(
                f"  [!] Страница выглядит пустой или неожиданной "
                f"(попытка {attempt}/{retries})",
                file=sys.stderr,
            )

        except TimeoutException:
            print(
                f"  [!] Таймаут загрузки (попытка {attempt}/{retries})",
                file=sys.stderr,
            )
        except WebDriverException as exc:
            print(
                f"  [!] Ошибка Chrome/Selenium: {exc}",
                file=sys.stderr,
            )

        if attempt < retries:
            time.sleep(2 * attempt)

    return None


def get_brands(driver: webdriver.Chrome) -> list[tuple[str, str, str]]:
    """
    Возвращает список:
        (название_марки, slug_марки, полный_url)
    со страницы /catalog.
    """
    html = fetch(driver, CATALOG_URL)

    if not html:
        raise RuntimeError("Не удалось загрузить страницу /catalog")

    soup = BeautifulSoup(html, "html.parser")
    brands: list[tuple[str, str, str]] = []

    # Основной вариант: ссылки вида /catalog/audi.
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()

        if not href:
            continue

        # Приводим абсолютную ссылку к path.
        if href.startswith(BASE):
            href_path = href[len(BASE):]
        else:
            href_path = href

        href_path = href_path.split("#", 1)[0]

        match = re.match(
            r"^/catalog/([a-z0-9\-]+)/?(?:\?.*)?$",
            href_path,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        slug = match.group(1).lower()
        name = a.get_text(" ", strip=True)

        if not name or not is_allowed(href_path):
            continue

        full_url = urljoin(BASE, f"/catalog/{slug}")
        brands.append((name, slug, full_url))

    # Убираем дубликаты, сохраняя порядок.
    unique = []
    seen = set()

    for item in brands:
        if item[1] not in seen:
            seen.add(item[1])
            unique.append(item)

    return unique


def get_models_for_brand(
    driver: webdriver.Chrome,
    brand_slug: str,
    brand_url: str,
) -> list[str]:
    """
    Возвращает список названий моделей для одной марки.
    """
    html = fetch(driver, brand_url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    models: list[str] = []

    pattern = re.compile(
        rf"^/catalog/{re.escape(brand_slug)}_[a-z0-9\-]+/?(?:\?.*)?$",
        flags=re.IGNORECASE,
    )

    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()

        if href.startswith(BASE):
            href_path = href[len(BASE):]
        else:
            href_path = href

        href_path = href_path.split("#", 1)[0]

        if not pattern.match(href_path):
            continue

        name = a.get_text(" ", strip=True)

        if name:
            models.append(name)

    # Убираем дубликаты.
    unique = []
    seen = set()

    for model in models:
        if model not in seen:
            seen.add(model)
            unique.append(model)

    return unique


def load_existing() -> dict:
    if not OUTPUT_FILE.exists():
        return {}

    try:
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(
            f"[!] Файл {OUTPUT_FILE} содержит некорректный JSON. "
            "Начинаю с пустого результата.",
            file=sys.stderr,
        )
        return {}


def save(data: dict) -> None:
    OUTPUT_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    result = load_existing()
    print(f"Уже есть в файле: {len(result)} марок")

    driver = None

    try:
        driver = create_driver()

        brands = get_brands(driver)

        if not brands:
            raise RuntimeError(
                "Марки не найдены на странице /catalog. "
                "Возможно, HTML-разметка каталога изменилась."
            )

        print(f"Найдено марок на странице каталога: {len(brands)}")

        for i, (name, slug, url) in enumerate(brands, start=1):
            if name in result:
                print(f"[{i}/{len(brands)}] {name} — уже есть, пропускаю")
                continue

            print(f"[{i}/{len(brands)}] {name} ({url})")

            models = get_models_for_brand(driver, slug, url)

            result[name] = models
            save(result)

            print(f"      Моделей найдено: {len(models)}")

            time.sleep(random.uniform(*REQUEST_DELAY_RANGE))

        print(f"Готово. Всего марок в файле: {len(result)}")
        print(f"Результат сохранён в {OUTPUT_FILE.resolve()}")

    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        print("Уже собранные данные сохранены в JSON.")

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
