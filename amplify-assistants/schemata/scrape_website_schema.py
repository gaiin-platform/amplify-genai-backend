scrape_website_schema = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The URL to scrape"},
        "isSitemap": {"type": "boolean", "description": "Whether the URL is a sitemap"},
        "maxPages": {
            "type": "integer",
            "description": "Maximum pages to scrape from sitemap",
        },
    },
    "required": ["url"],
}
