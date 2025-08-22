extract_sitemap_urls_schema = {
    "type": "object",
    "properties": {
        "sitemap": {"type": "string", "description": "The URL of the sitemap to extract URLs from."},
        "maxPages": {"type": "integer", "description": "The maximum number of pages to extract URLs from."},
    },
    "required": ["sitemap"],
}