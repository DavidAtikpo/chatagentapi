import asyncio

from app.services.session_extractor import dedupe_sessions, extract_sessions_from_html, formation_page_urls
import httpx


async def main():
    urls = formation_page_urls("https://cides.tf")
    all_sessions = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for url in urls:
            r = await client.get(url, headers={"User-Agent": "ChatbotSaaS-Crawler/1.0"})
            sessions = extract_sessions_from_html(r.text, url)
            print(url, "->", len(sessions), "sessions")
            for s in sessions[:3]:
                print(" ", s["region"], s["label"][:50], s["url"])
            all_sessions.extend(sessions)
    print("Total unique:", len(dedupe_sessions(all_sessions)))


if __name__ == "__main__":
    asyncio.run(main())
