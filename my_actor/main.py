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


user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
extra_http_headers = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "upgrade-insecure-requests": "1",
    "referer": "https://www.handyverkauf.net/",
    "cache-control": "max-age=0",
}

async def main():
    async with Actor:
        proxy_configuration = await Actor.create_proxy_configuration(
            groups=['RESIDENTIAL'],
            country_code='FR'
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)

            for url in START_URLS:
                # Get fresh proxy info for each request
                proxy_info = await proxy_configuration.new_proxy_info()
                proxy_settings = {"server": proxy_info.url}
                if proxy_info.username:
                    proxy_settings["username"] = proxy_info.username
                if proxy_info.password:
                    proxy_settings["password"] = proxy_info.password
                
                # Create new context with fresh proxy
                context = await browser.new_context(proxy=proxy_settings,
                                                    user_agent=user_agent,
                                                    extra_http_headers=extra_http_headers,
                                                    locale="de-DE",
                                                    is_mobile=True,
                                                    has_touch=True,
                                                    device_scale_factor=3,
                                                    viewport={"width": 390, "height": 844},
                                                    )
                page = await context.new_page()
                
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

                    proxy_info = await proxy_configuration.new_proxy_info()
                    proxy_settings = {"server": proxy_info.url}
                    if proxy_info.username:
                        proxy_settings["username"] = proxy_info.username
                    if proxy_info.password:
                        proxy_settings["password"] = proxy_info.password

                    # New context for each product
                    product_context = await browser.new_context(proxy=proxy_settings)
                    product_page = await product_context.new_page()

                    await human_wait(2.0, 5.0)
                    await product_page.goto(product_url, timeout=60000)  # Use product_url, not url
                    await product_page.wait_for_load_state("domcontentloaded")

                    device_name = await product_page.locator('//*[@class="handy_name"]').text_content()
                    variant = await product_page.locator('//*[@class="handy_variation"]').text_content()

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

                        api_page = await product_context.new_page()
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
                        await product_page.close()
                        await product_context.close()

            await browser.close()