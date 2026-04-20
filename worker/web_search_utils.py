from ddgs import DDGS
import time

def fetch_web_intel(stock_name):
    """
    Fetches external intelligence from DuckDuckGo.
    Returns a formatted string of news and basic info.
    """
    print(f"  [Web] Searching for latest news on {stock_name}...")
    combined_text = "### EXTERNAL WEB INTELLIGENCE (LATEST NEWS & SENTIMENT) ###\n\n"

    try:
        with DDGS() as ddgs:
            # 1. Latest News
            news_results = list(ddgs.news(f"{stock_name} stock news India", max_results=5))
            if news_results:
                combined_text += "**Latest News Headlines:**\n"
                for item in news_results:
                    title = item.get('title', 'No Title')
                    date = item.get('date', 'Unknown Date')
                    source = item.get('source', 'Unknown Source')
                    combined_text += f"- [{date}] {title} ({source})\n"
                combined_text += "\n"

            # 2. Basic Info / Screener (General Search)
            # We search for "Stock Name screener" to potentially get a snippet about basics
            # capturing snippets from search results
            general_results = list(ddgs.text(f"{stock_name} share price analysis outlook", max_results=3))
            if general_results:
                combined_text += "**Market Sentiment & Analysis Snippets:**\n"
                for item in general_results:
                     body = item.get('body', '')
                     combined_text += f"- {body}\n"

    except Exception as e:
        print(f"  [Web] Error fetching web intel: {e}")
        return combined_text + f"Error fetching web data: {e}\n"

    return combined_text
