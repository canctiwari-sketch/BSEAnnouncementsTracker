import asyncio
from playwright.async_api import async_playwright
import json

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        async def handle_request(request):
            if "bseindia" in request.url and ("Ann" in request.url or "api" in request.url):
                print("BSE API Request URL:", request.url)
        
        page.on("request", handle_request)
        print("Navigating to BSE Announcements...")
        try:
            await page.goto("https://www.bseindia.com/corporates/ann.html", wait_until="networkidle", timeout=15000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            print("Navigation timed out or error:", e)
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
