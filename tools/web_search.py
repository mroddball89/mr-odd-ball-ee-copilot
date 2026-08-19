from langchain_core.tools import tool
from langchain_community.tools import DuckDuckGoSearchRun

@tool
def perform_web_search(query: str) -> str:
    """
    Searches the web for up-to-date information, news, or external documentation.
    Pass a concise search string as the query.
    """
    search = DuckDuckGoSearchRun()
    try:
        return search.invoke(query)
    except Exception as e:
        return f"Web Search Failed: {str(e)}"