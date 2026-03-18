import re
import json
from urllib.parse import urljoin
import os
from apify import Actor
from playwright.async_api import async_playwright

import asyncio
import random

MIN_WAIT = 1.2
MAX_WAIT = 3.8

async def human_wait(min_s=MIN_WAIT, max_s=MAX_WAIT):
    await asyncio.sleep(random.uniform(min_s, max_s))

START_URLS = [
    "https://www.handyverkauf.net/addons/livesearch.php?q=iphone",
   # "https://www.handyverkauf.net/addons/livesearch.php?q=Samsung",
   # "https://www.handyverkauf.net/addons/livesearch.php?q=google",
   # "https://www.handyverkauf.net/addons/livesearch.php?q=Xiaomi",
   # "https://www.handyverkauf.net/addons/livesearch.php?q=Huawei",
]


async def main():
    async with Actor:
        proxy_configuration = await Actor.create_proxy_configuration(
            groups=['RESIDENTIAL'],
            country_code="DE"
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            proxy_info = await proxy_configuration.new_proxy_info()

            proxy_settings = {"server": proxy_info.url}
            if proxy_info.username:
                proxy_settings["username"] = proxy_info.username
            if proxy_info.password:
                proxy_settings["password"] = proxy_info.password

            context = await browser.new_context(proxy=proxy_settings)

            page = await context.new_page()

            # =========================
            # STEP 1: SEARCH PAGES
            # =========================
            for url in START_URLS:
                await human_wait(2.0, 5.0)
                response = await page.goto(url, timeout=60000)
                await page.wait_for_load_state("domcontentloaded")
                await human_wait(1.0, 2.5)

                if response:
                    Actor.log.info(f"SEARCH response: {response.status} {response.url}")
                else:
                    Actor.log.warning(f"SEARCH response is None for {url}")

                if "samsung" in url.lower():
                    product_links = await page.locator(
                        '//li[contains(.,"Handy")]//a[contains(.,"GB")]/@href'
                    ).all()
                else:
                    product_links = await page.locator(
                        '//li[contains(.,"Handy")]//a/@href'
                    ).all()

                hrefs = []
                for el in product_links[:5]:
                    href = await el.get_attribute("href")
                    if href:
                        hrefs.append(urljoin("https://www.handyverkauf.net", href))

                # =========================
                # STEP 2: PRODUCT PAGE
                # =========================
                for product_url in hrefs:
                    await human_wait(2.0, 5.0)
                    await page.goto(url, timeout=60000)
                    await page.wait_for_load_state("domcontentloaded")
                    await human_wait(1.0, 2.5)

                    device_name = await page.locator(
                        '//*[@class="handy_name"]'
                    ).text_content()

                    variant = await page.locator(
                        '//*[@class="handy_variation"]'
                    ).text_content()

                    data_mk = await page.locator('//input').get_attribute("data-mk")
                    product_id = product_url.split("_")[-1]

                    # =========================
                    # CONDITIONS
                    # =========================
                    condition_rows = page.locator('//ul[@id="dropdownZustand"]/li')
                    count = await condition_rows.count()

                    condition_dict = {}

                    for i in range(count):
                        row = condition_rows.nth(i)

                        cls = await row.get_attribute("class")
                        cls = cls.split(" ")[0] if cls else None

                        texts = await row.locator(".//a").all_text_contents()
                        condition = "".join([t.strip() for t in texts if t.strip()])

                        condition_dict[cls] = condition

                    # =========================
                    # STEP 3: API CALL
                    # =========================
                    for condition_class, condition in condition_dict.items():

                        if "Schlecht" in condition:
                            continue

                        api_url = f"https://www.handyverkauf.net/addons/pausgabe_neu.php?id={product_id}&w=1&z={condition_class}&s=0&mode=handy&mk={data_mk}"

                        api_page = await context.new_page()
                        await api_page.goto(api_url)

                        json_text = await api_page.text_content("body")

                        try:
                            json_data = json.loads(json_text)
                        except:
                            await api_page.close()
                            continue

                        vergleich_html = json_data.get("vergleich", "")

                        # =========================
                        # PRICE EXTRACTION
                        # =========================
                        price_matches = re.findall(r'>([\d,.]+)€<', vergleich_html)

                        price1 = float(price_matches[0].replace(",", ".")) if len(price_matches) >= 1 else None
                        price2 = float(price_matches[1].replace(",", ".")) if len(price_matches) >= 2 else None

                        # =========================
                        # SELLER
                        # =========================
                        seller_name = None
                        seller_url = None

                        url_match = re.search(r'/go/\?anbieter=([^&"]+)&id=(\d+)', vergleich_html)
                        if url_match:
                            seller_name = url_match.group(1)
                            seller_url = f"https://www.handyverkauf.net/go/?anbieter={url_match.group(1)}&id={url_match.group(2)}"

                        # =========================
                        # STORAGE / RAM / COLOR
                        # =========================
                        storage = None
                        ram = None
                        color = None

                        try:
                            storage, color = variant.split("GB")
                            storage = f"{storage}GB"
                        except:
                            storage_match = re.search(r'(\d{2,3}GB|\dTB)', variant)
                            ram_match = re.search(r'(\d{1,2}GB) RAM', variant)

                            if storage_match:
                                storage = storage_match.group(0)

                            if ram_match:
                                ram = ram_match.group(0)

                            if "RAM" in variant:
                                color = variant.split("RAM")[-1]
                            elif "TB" in variant:
                                color = variant.split("TB")[-1]

                        # =========================
                        # FINAL OUTPUT
                        # =========================
                        if price1:
                            item = {
                                "device_name": device_name,
                                "storage": storage,
                                "ram": ram,
                                "condition": condition,
                                "price": price1,
                                "price2": price2,
                                "product_url": product_url,
                                "seller_name": seller_name,
                                "seller_url": seller_url,
                                "color": color,
                                "cracks": "No",
                                "display_functional": "Yes",
                                "battery_capacity": "Battery working properly",
                            }

                            await Actor.push_data(item)

                        await api_page.close()

            await browser.close()