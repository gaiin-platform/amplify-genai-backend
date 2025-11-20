# Copyright (c) 2024 Vanderbilt University
# Authors: Jules White, Allen Karns, Karely Rodriguez, Max Moundas

from datetime import datetime, timedelta
import os
import re
import boto3
import json
import requests
import xmltodict
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from pycommon.const import APIAccessType
from pycommon.api.files import upload_file, delete_file
from pycommon.encoders import SmartDecimalEncoder

# Initialize AWS services
dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

from pycommon.api.ops import api_tool
from pycommon.authz import validated, setup_validated, add_api_access_types
from schemata.schema_validation_rules import rules
from schemata.permissions import get_permission_checker

setup_validated(rules, get_permission_checker)
add_api_access_types([APIAccessType.ASSISTANTS.value])


from service.core import get_most_recent_assistant_version

# Disallowed file extensions  
DISALLOWED_EXTENSIONS = [
    "mp3", "wav", "mp4", "mov", "avi", "mkv",
    "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", 
    "tif", "tiff", "bmp", "eps", "ps", "ai",
    "psd", "heic", "heif", "ico", "zip", "epub"
]

def get_file_extension_from_content_type(content_type):
    """Map MIME type to file extension."""
    mime_to_ext = {
        # Documents
        "application/pdf": "pdf",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.ms-excel": "xls", 
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.ms-powerpoint": "ppt",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
        "text/plain": "txt",
        "text/csv": "csv",
        "application/rtf": "rtf",
        "text/markdown": "md",
        
        # Code/Markup
        "text/html": "html",
        "application/xml": "xml", 
        "text/xml": "xml",
        "application/json": "json",
        "application/javascript": "js",
        "text/css": "css",
        "text/x-python": "py",
        
        # Images (supported)
        "image/jpeg": "jpg",
        "image/png": "png", 
        "image/gif": "gif",
        "image/webp": "webp",
    }
    
    # Handle content type with charset (e.g., "text/plain; charset=utf-8")
    clean_content_type = content_type.split(';')[0].strip()
    return mime_to_ext.get(clean_content_type, None)

def is_supported_file_type(content_type, url):
    """Check if file type is supported and not disallowed."""
    extension = get_file_extension_from_content_type(content_type)
    if not extension:
        return False
    
    # Check if extension is disallowed
    if extension.lower() in DISALLOWED_EXTENSIONS:
        print(f"Skipping disallowed file type: {extension} for URL: {url}")
        return False
    
    return True

def apply_exclusions(urls, exclusions=None):
    """Apply exclusions to a list of URLs."""
    if not exclusions:
        return urls
    
    filtered_urls = urls
    
    # Apply excludedUrls (exact matches)
    if exclusions.get("excludedUrls"):
        filtered_urls = [url for url in filtered_urls if url not in exclusions["excludedUrls"]]
        print(f"  Excluded {len(urls) - len(filtered_urls)} URLs via excludedUrls")
    
    # Apply excludeKeywords (case-insensitive substring matching)
    if exclusions.get("excludeKeywords"):
        original_count = len(filtered_urls)
        filtered_urls = [
            url for url in filtered_urls 
            if not any(keyword.lower() in url.lower() for keyword in exclusions["excludeKeywords"])
        ]
        print(f"  Excluded {original_count - len(filtered_urls)} URLs via excludeKeywords")
    
    # Apply excludePatterns (wildcard patterns)
    if exclusions.get("excludePatterns"):
        original_count = len(filtered_urls)
        for pattern in exclusions["excludePatterns"]:
            regex_pattern = pattern.replace("*", ".*")
            filtered_urls = [url for url in filtered_urls if not re.match(regex_pattern, url)]
        print(f"  Excluded {original_count - len(filtered_urls)} URLs via excludePatterns")
    
    return filtered_urls

def sanitize_and_validate_url(url):
    """
    Sanitize and validate a URL before scraping.
    
    Returns:
        tuple: (is_valid, sanitized_url, error_message)
    """
    if not url or not isinstance(url, str):
        return False, None, "URL is empty or not a string"
    
    # Strip whitespace
    url = url.strip()
    
    if not url:
        return False, None, "URL is empty after stripping whitespace"
    
    # Add protocol if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        # Parse URL to validate structure
        parsed = urlparse(url)
        
        # Check if we have a valid scheme and netloc
        if not parsed.scheme or not parsed.netloc:
            return False, None, "Invalid URL structure - missing scheme or domain"
        
        # Check for valid schemes
        if parsed.scheme not in ['http', 'https']:
            return False, None, f"Unsupported URL scheme: {parsed.scheme}"
        
        # Check for valid domain (basic validation)
        domain = parsed.netloc.lower()
        
        # Remove port if present for validation
        if ':' in domain:
            domain = domain.split(':')[0]
        
        # Basic domain validation - must contain at least one dot and valid characters
        if '.' not in domain:
            return False, None, "Invalid domain - must contain at least one dot"
        
        # Check for valid domain characters
        if not re.match(r'^[a-zA-Z0-9.-]+$', domain):
            return False, None, "Invalid domain - contains invalid characters"
        
        # Check domain parts
        domain_parts = domain.split('.')
        for part in domain_parts:
            if not part:  # Empty part (e.g., double dots)
                return False, None, "Invalid domain - contains empty parts"
            if len(part) > 63:  # DNS label length limit
                return False, None, "Invalid domain - label too long"
        
        # Reconstruct the URL to ensure it's properly formatted
        sanitized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            sanitized_url += f"?{parsed.query}"
        if parsed.fragment:
            sanitized_url += f"#{parsed.fragment}"
        print(f"Url is valid: {sanitized_url}")
        return True, sanitized_url, None
        
    except Exception as e:
        return False, None, f"URL parsing error: {str(e)}"

def scrape_website_content(url, access_token, is_sitemap=False, max_pages=None, exclusions=None):
    """Helper function to scrape a website and return the data source key"""
    try:
        # Ensure max_pages is an integer or None for unlimited
        if max_pages is not None:
            max_pages = int(max_pages)
        print(f"Attempting to scrape {'sitemap' if is_sitemap else 'website'}: {url}")
        
        # Validate and sanitize the URL first
        is_valid, sanitized_url, error_msg = sanitize_and_validate_url(url)
        if not is_valid:
            return {
                "success": False,
                "message": f"Invalid URL: {error_msg}",
                "error": error_msg,
            }
        
        # Use the sanitized URL for scraping
        url = sanitized_url
        print(f"Using sanitized URL: {url}")

        # Determine if single URL or sitemap
        urls_to_scrape = []
        if is_sitemap:
            urls_to_scrape = extract_urls_from_sitemap(url, max_pages, exclusions)
            print(f"Extracted {len(urls_to_scrape)} URLs from sitemap")
            if not urls_to_scrape:
                return {
                    "success": False,
                    "message": f"Could not extract any URLs from sitemap at {url}",
                    "error": "Empty sitemap or parsing error",
                }
        else:
            urls_to_scrape = [url]
            print(f"Set up to scrape single URL: {url}")

        # Scrape content from URLs - each URL gets its own data source
        scraped_ds = []
        for url_to_scrape in urls_to_scrape:
            # Note: URLs from sitemaps are already validated during extraction
            print(f"Fetching and parsing URL: {url_to_scrape}")
            content = fetch_and_parse_url(url_to_scrape)
            if content:
                print(f"Successfully parsed content from {url_to_scrape}")
                
                current_data = {
                    "url": url_to_scrape, 
                    "url_name": extract_name_from_url(url_to_scrape),
                    "scrapedAt": datetime.now().isoformat(),
                    "content": content,
                    "fromSitemap": url if is_sitemap else None
                }

                # Save each URL as its own data source
                try:
                    data_source_data = save_scraped_content(current_data, access_token)
                    scraped_ds.append(data_source_data)
                    print(f"Saved data source for {url_to_scrape} with ID: {data_source_data.get('id')}")
                except Exception as save_error:
                    print(f"Error saving scraped content for {url_to_scrape}: {save_error}")
                    # Continue with other URLs even if one fails
                    continue
            else:
                print(f"Failed to parse content from {url_to_scrape}")

        # Check if any URLs were successfully scraped
        if not scraped_ds:
            print("No content was successfully scraped from any URL")
            return {
                "success": False,
                "message": "Failed to scrape any content from the provided URLs",
                "error": "All URL requests failed or returned no content",
            }

        print(f"Successfully scraped {len(scraped_ds)} URLs, each saved as individual data sources")

        return {
            "success": True,
            "message": f"Successfully scraped {len(scraped_ds)} URLs",
            "data": {
                "dataSourceKeys": [item["id"] for item in scraped_ds],  # Return array of individual data sources
                "dataSources": scraped_ds,
                "urlsScraped": len(scraped_ds),
            },
        }

    except Exception as e:
        print(f"Error scraping website: {e}")
        return {
            "success": False,
            "message": f"Failed to scrape website: {str(e)}",
            "error": str(e),
        }


def extract_urls_from_sitemap(sitemap_url, max_pages=None, exclusions=None):
    """Extract URLs from a sitemap XML file."""
    try:
        # Ensure max_pages is an integer or None for unlimited
        if max_pages is not None:
            max_pages = int(max_pages)
        response = requests.get(sitemap_url, timeout=30)
        response.raise_for_status()

        sitemap_content = response.content
        sitemap_dict = xmltodict.parse(sitemap_content)

        # Handle nested sitemaps
        if "sitemapindex" in sitemap_dict:
            all_urls = []
            sitemaps_to_process = sitemap_dict["sitemapindex"]["sitemap"] if max_pages is None else sitemap_dict["sitemapindex"]["sitemap"][:max_pages]
            for sitemap in sitemaps_to_process:
                sitemap_loc = sitemap["loc"]
                sub_urls = extract_urls_from_sitemap(sitemap_loc, max_pages, exclusions)
                all_urls.extend(sub_urls)
                if max_pages is not None and len(all_urls) >= max_pages:
                    return all_urls[:max_pages]
            return all_urls

        # Extract URLs from urlset
        urls = []
        if "urlset" in sitemap_dict and "url" in sitemap_dict["urlset"]:
            url_entries = sitemap_dict["urlset"]["url"]
            # Handle single URL case
            if isinstance(url_entries, dict):
                url_loc = url_entries["loc"]
                # Validate the extracted URL
                is_valid, sanitized_url, error_msg = sanitize_and_validate_url(url_loc)
                if is_valid:
                    urls.append(sanitized_url)
                else:
                    print(f"Skipping invalid URL from sitemap: {url_loc} - {error_msg}")
            else:
                entries_to_process = url_entries if max_pages is None else url_entries[:max_pages]
                for url_entry in entries_to_process:
                    url_loc = url_entry["loc"]
                    # Validate the extracted URL
                    is_valid, sanitized_url, error_msg = sanitize_and_validate_url(url_loc)
                    if is_valid:
                        urls.append(sanitized_url)
                    else:
                        print(f"Skipping invalid URL from sitemap: {url_loc} - {error_msg}")

        # Apply exclusions before limiting by max_pages
        urls = apply_exclusions(urls, exclusions)
        return urls if max_pages is None else urls[:max_pages]

    except Exception as e:
        print(f"Error extracting URLs from sitemap: {e}")
        return []


def fetch_and_parse_url(url):
    """Fetch and parse content from a URL."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

        # Parse URL to get the fragment
        parsed_url = urlparse(url)
        fragment = parsed_url.fragment
        base_url = url.replace(f"#{fragment}", "") if fragment else url

        response = requests.get(base_url, headers=headers, timeout=30)

        # Handle HTTP errors explicitly
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            print(f"Error fetching URL {url}: {e}")
            return None

        # Check if content is HTML
        content_type = response.headers.get("Content-Type", "")
        if (
            "text/html" not in content_type
            and "application/xhtml+xml" not in content_type
        ):
            print(f"URL {url} returned non-HTML content: {content_type}")

            # Check if this is a supported file type
            if is_supported_file_type(content_type, url):
                print(f"Processing supported file type: {content_type}")
                return {
                    "metadata": {
                        "title": url.split("/")[-1],
                        "url": url,
                        "contentType": content_type,
                        "scrapedAt": datetime.now().isoformat(),
                    },
                    "file_bytes": response.content,
                    "file_type": content_type,
                }
            else:
                # Unsupported file type - skip it gracefully
                print(f"Skipping unsupported file type: {content_type} for URL: {url}")
                return None

        # Parse HTML
        soup = BeautifulSoup(response.content, "lxml")

        # Remove script, style, header, footer, nav, and other non-content elements
        for element in soup(["script", "style", "meta", "noscript", "iframe", "header", "footer", "nav", "aside", "form", "input", "button", "select", "textarea", "canvas", "dialog", ]):
            #"embed", "object", "applet", "template", "slot"
            element.decompose()

        # Extract title
        title = soup.title.string if soup.title else url.split("/")[-1]

        # Build a more structured extraction of content
        main_content = ""
        section_title = ""

        # If we have a fragment, try to find the specific section
        if fragment:
            # Try different ways to find the section
            section = (
                soup.find(id=fragment)
                or soup.find(attrs={"name": fragment})
                or soup.find(id=lambda x: x and fragment in x)
                or soup.find(class_=lambda x: x and fragment in x)
            )

            if section:
                # Get the section title if available
                heading = section.find(["h1", "h2", "h3", "h4", "h5", "h6"])
                if heading:
                    section_title = heading.get_text(strip=True)

                # Get the content of the section
                main_content = section.get_text(separator=" ", strip=True)
            else:
                print(f"Could not find section with fragment: {fragment}")

        # If no specific section was found or no fragment was provided, get the main content
        if not main_content:
            # Try to find main content containers
            main_elements = soup.find_all(["main", "article", "div", "section"])
            if main_elements:
                for element in main_elements:
                    if (
                        len(element.get_text(strip=True)) > 200
                    ):  # Only substantial blocks
                        main_content += (
                            element.get_text(separator=" ", strip=True) + "\n\n"
                        )

        # If no main content found, just get the body text
        if not main_content:
            main_content = soup.get_text(separator=" ", strip=True)

        # Clean up whitespace
        main_content = re.sub(r"\s+", " ", main_content).strip()

        # Add a headline with the title and section title if available
        formatted_text = (
            f"# {title} - {section_title}\n\n{main_content}"
            if section_title
            else f"# {title}\n\n{main_content}"
        )

        # Extract metadata
        metadata = {
            "title": title,
            "url": url,
            "contentType": "text/html",
            "scrapedAt": datetime.now().isoformat(),
        }

        # Return structured content
        return {
            "metadata": metadata,
            "text": formatted_text,
        }

    except Exception as e:
        print(f"Error processing URL {url}: {e}")
        return None

def save_scraped_content(scraped_data, access_token):
    print(f"Saving scraped content as DS: {scraped_data['url']}")
    timestamp = scraped_data["scrapedAt"]
    
    print(f"Scraped data keys: {list(scraped_data.keys())}")
    
    # Ensure all data passed to upload_file is JSON-safe using SmartDecimalEncoder
    safe_data_props = json.loads(json.dumps({
        "type": "assistant-web-content", # assistant- type prevent them from appearing in the file manager
        "sourceUrl": scraped_data["url"],
        "scrapedAt": timestamp,
        "isScrapedContent": True, 
        "fromSitemap": scraped_data.get('fromSitemap')
    }, cls=SmartDecimalEncoder))
    
    # Handle different file types dynamically
    content_file_type = scraped_data['content'].get('file_type')
    if content_file_type and scraped_data['content'].get('file_bytes'):
        # For file content: pass raw bytes with proper file type
        file_contents = scraped_data['content']['file_bytes']
        file_type = content_file_type
        file_extension = get_file_extension_from_content_type(content_file_type)
    else:
        # For regular HTML content: JSON as before
        file_contents = json.dumps(scraped_data['content'], cls=SmartDecimalEncoder)
        file_type = "application/json"
        file_extension = "json"
    
    file_resp = upload_file(
        access_token = access_token,
        file_name = f"{scraped_data['url_name']}_{timestamp}.{file_extension}",
        file_contents = file_contents,
        file_type = file_type,
        tags = ["website", "scraped"],
        data_props = safe_data_props,
        enter_rag_pipeline = True,
        groupId = None, # note: group system user would be the one making the request, no need to provide the groupId in this case
    )
    if not file_resp or not file_resp.get('id'):
        print(f"Upload failed for scraped content. Response: {file_resp}")
        raise Exception("Failed to upload scraped content as a file")
    
    print("DS content saved to s3 with key: ", file_resp['id'])
    return {"id": "s3://" + file_resp['id'], # Mark as already processed with s3:// prefix
            "name": file_resp['name'],
            "metadata": {**file_resp['data'], "userDataSourceId": file_resp['id']}, # Preserve original ID
            "tags": file_resp['tags'], 
            "type": "website/url"}
              


@api_tool(
    path="/assistant/rescan_websites",
    name="rescanWebsites",
    method="POST",
    tags=["default", "rescan-websites"],
    description="""Rescan websites associated with an assistant.""",
    parameters={
        "type": "object",
        "properties": {
            "assistantId": {
                "type": "string",
                "description": "ID of the assistant to update website content for.",
            },
        },
        "required": ["assistantId"],
    },
    output={
        "type": "object",
        "properties": {
            "success": {
                "type": "boolean",
                "description": "Whether the website rescan was initiated successfully",
            },
            "message": {
                "type": "string",
                "description": "Status message describing the result",
            },
            "data": {
                "type": "object",
                "properties": {
                    "dataSourceKeys": {
                        "type": "array",
                        "description": "Array of data source IDs that were created",
                        "items": {"type": "string"}
                    },
                    "dataSources": {
                        "type": "array",
                        "description": "Array of full data source objects that were created",
                        "items": {"type": "object"}
                    },
                },
                "required": ["dataSourceKeys", "dataSources"],
            },
        },
        "required": ["success", "message", "data"],
    },
)
@validated(op="rescan_websites")
def rescan_websites(event, context, current_user, name, data=None):
    """
    Lambda function to rescan websites associated with assistants.
    """
    access_token = data["access_token"]
    try:
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])

        # Get assistantId (public ID) from request data
        assistant_public_id = data["data"]["assistantId"]
        force_rescan = data["data"].get("forceRescan", False)
        if force_rescan:
            print("Force rescanning all websites for assistant: ", assistant_public_id)
        
        # Get the most recent version of the assistant using public ID
        latest_assistant = get_most_recent_assistant_version(
            assistants_table, assistant_public_id
        )

        if not latest_assistant:
            return {"success": False, "message": "Assistant not found"}

        result = process_assistant_websites(latest_assistant, access_token, force_rescan)

        return {
            "success": result["success"],
            "message": result["message"],
            "data": result.get("data", {}),
        }

    except Exception as e:
        print(f"Error rescanning websites: {e}")
        return {"success": False, "message": f"Failed to rescan websites: {str(e)}"}


def process_assistant_websites(assistant, access_token, force_rescan=False):
    """Process websites for an assistant and update data sources with proper cleanup."""
    try:
        website_urls = assistant.get("data", {}).get("websiteUrls", [])
        print(f"Found {len(website_urls)} website URLs to check for rescanning")
        if not website_urls:
            return {
                "success": True,
                "message": "No websites to process for this assistant",
            }
        
        # Get existing data sources
        existing_data_sources = assistant.get("dataSources", [])
        existing_web_ds = [
            ds for ds in existing_data_sources
            if ds.get("metadata", {}).get("type") == "assistant-web-content"
        ]
        print(f"Found {len(existing_web_ds)} existing web data sources")
        
        # Determine which URLs need rescanning based on frequency
        urls_to_rescan = []
        updated_website_urls = []
        
        for website_url_entry in website_urls:
            url = website_url_entry["url"]
            scan_frequency = website_url_entry.get("scanFrequency")
            last_scanned = website_url_entry.get("lastScanned")
            
            print(f"Checking URL: {url}")
            print(f"  scanFrequency: {scan_frequency}")
            print(f"  lastScanned: {last_scanned}")
            
            # If force rescan is enabled, rescan all URLs regardless of scanFrequency
            if force_rescan:
                needs_rescan = True
                print(f"  -> Needs rescan: {needs_rescan} (force rescan enabled)")
            else:
                # Skip if no scanFrequency is defined (only when not force rescanning)
                if scan_frequency is None:
                    print(f"  -> Skipping (no scanFrequency defined)")
                    updated_website_urls.append(website_url_entry)
                    continue
                
                # Check if rescanning is needed based on frequency
                needs_rescan = True
                if last_scanned:
                    last_scan_date = datetime.fromisoformat(last_scanned)
                    time_since_scan = datetime.now() - last_scan_date
                    needs_rescan = time_since_scan >= timedelta(days=int(scan_frequency))
                    print(f"  -> Time since last scan: {time_since_scan.days} days")
                    print(f"  -> Needs rescan: {needs_rescan} (frequency: {scan_frequency} days)")
                else:
                    print(f"  -> Needs rescan: {needs_rescan} (never scanned)")
            
            if needs_rescan:
                urls_to_rescan.append(website_url_entry)
                print(f"  -> Added to rescan queue")
            
            updated_website_urls.append(website_url_entry)
        
        print(f"URLs to rescan: {len(urls_to_rescan)}")
        if not urls_to_rescan:
            return {
                "success": True,
                "message": "No websites due for rescanning based on scanFrequency",
            }
        
        # Get existing data sources that might need deletion (but don't delete yet)
        urls_being_rescanned = [entry["url"] for entry in urls_to_rescan]
        print(f"URLs being rescanned: {urls_being_rescanned}")
        old_ds_to_delete = []
        
        for ds in existing_web_ds:
            ds_metadata = ds.get("metadata", {})
            ds_source_url = ds_metadata.get("sourceUrl")
            ds_from_sitemap = ds_metadata.get("fromSitemap")
            
            # Delete if:
            # 1. Direct URL match (sourceUrl in rescanned URLs)
            # 2. Sitemap-derived content (fromSitemap in rescanned URLs)
            should_delete = (
                ds_source_url in urls_being_rescanned or 
                ds_from_sitemap in urls_being_rescanned
            )
            
            if should_delete:
                old_ds_to_delete.append(ds)
                print(f"  Marked for deletion: {ds['id']} (sourceUrl: {ds_source_url}, fromSitemap: {ds_from_sitemap})")
            else:
                print(f"  Keeping: {ds['id']} (sourceUrl: {ds_source_url}, fromSitemap: {ds_from_sitemap})")
        
        print(f"Found {len(old_ds_to_delete)} old data sources that will be deleted if scraping succeeds")
        
        # Scrape content from URLs that need rescanning
        scraped_ds = []
        successful_urls = []  # Track which URLs were successfully scraped
        print(f"Starting scraping process for {len(urls_to_rescan)} URLs")
        for website_url_entry in urls_to_rescan:
            url = website_url_entry["url"]
            is_sitemap = website_url_entry.get("type") == "website/sitemap"
            max_pages = website_url_entry.get("maxPages")
            if max_pages is not None:
                max_pages = int(max_pages)
            
            print(f"Processing URL: {url} (sitemap: {is_sitemap}, maxPages: {max_pages})")
            
            # Validate and sanitize the URL first
            is_valid, sanitized_url, error_msg = sanitize_and_validate_url(url)
            if not is_valid:
                print(f"Skipping invalid URL: {url} - {error_msg}")
                continue
            
            # Use the sanitized URL for scraping
            url = sanitized_url
            print(f"Using sanitized URL: {url}")
            
            # Track scraped data sources for this specific URL
            url_scraped_ds = []

            if is_sitemap:
                # For sitemaps, extract all sub-URLs and create separate data sources for each
                exclusions = website_url_entry.get("exclusions")
                urls = extract_urls_from_sitemap(url, max_pages, exclusions)
                print(f"  Extracted {len(urls)} URLs from sitemap")
                for sub_url in urls:
                    # Note: URLs from sitemaps are already validated during extraction
                    content = fetch_and_parse_url(sub_url)
                    if content:
                        # Create separate data object for each sub-URL
                        current_data = {
                            "url": sub_url, 
                            "url_name": extract_name_from_url(sub_url),
                            "scrapedAt": datetime.now().isoformat(),
                            "content": content,
                            "fromSitemap": url
                        }
                        
                        # Save each sub-URL as its own data source
                        try:
                            data_source_data = save_scraped_content(current_data, access_token)
                            # Add metadata updates like core.py does
                            scan_frequency = website_url_entry.get("scanFrequency")
                            url_max_pages = website_url_entry.get("maxPages")
                            metadata_updates = {
                                "scanFrequency": scan_frequency, 
                                "contentKey": data_source_data['id']
                            }
                            if url_max_pages is not None:
                                metadata_updates["maxPages"] = url_max_pages
                            data_source_data.get("metadata", {}).update(metadata_updates)
                            scraped_ds.append(data_source_data)
                            url_scraped_ds.append(data_source_data)
                            print(f"  Saved data source for: {sub_url}")
                        except Exception as save_error:
                            print(f"Error saving scraped content for {sub_url}: {save_error}")
                            continue
                    else:
                        print(f"  Failed to fetch content from: {sub_url}")
            else:
                # For single URLs, create one data source
                content = fetch_and_parse_url(url)
                if content:
                    current_data = {
                        "url": url, 
                        "url_name": extract_name_from_url(url),
                        "scrapedAt": datetime.now().isoformat(),
                        "content": content,
                        "fromSitemap": None  # Explicitly set to None for non-sitemap URLs
                    }
                    # Save single URL as data source
                    try:
                        data_source_data = save_scraped_content(current_data, access_token)
                        # Add metadata updates like core.py does
                        scan_frequency = website_url_entry.get("scanFrequency")
                        data_source_data.get("metadata", {}).update({
                            "scanFrequency": scan_frequency, 
                            "contentKey": data_source_data['id']
                        })
                        scraped_ds.append(data_source_data)
                        url_scraped_ds.append(data_source_data)
                        print(f"  Saved data source for: {url}")
                    except Exception as save_error:
                        print(f"Error saving scraped content for {url}: {save_error}")
                        continue
                else:
                    print(f"  Failed to fetch content from: {url}")
            
            # Track successful URLs and update their lastScanned timestamp
            if url_scraped_ds:
                successful_urls.append(url)
                # Use the scrapedAt from the first data source for this URL
                latest_scraped_at = url_scraped_ds[0].get("metadata", {}).get("scrapedAt", datetime.now().isoformat())
                for entry in updated_website_urls:
                    if entry["url"] == url:
                        entry["lastScanned"] = latest_scraped_at
                        print(f"  Updated lastScanned for {url} to {latest_scraped_at}")
                        break
            else:
                print(f"  No data sources created for {url} - keeping existing data")

        if not scraped_ds:
            print("No new content was successfully scraped - keeping all existing data sources")
            return {
                "success": False,
                "message": "Failed to scrape any content from the websites",
            }
        
        # Only delete old data sources for URLs that were successfully rescraped
        print(f"Deleting old data sources for {len(successful_urls)} successfully rescraped URLs")
        for old_ds in old_ds_to_delete:
            ds_metadata = old_ds.get("metadata", {})
            old_ds_source_url = ds_metadata.get("sourceUrl")
            old_ds_from_sitemap = ds_metadata.get("fromSitemap")
            
            # Delete if the URL that created this data source was successfully rescraped:
            # 1. For direct URLs: sourceUrl matches a successful URL
            # 2. For sitemap-derived: fromSitemap matches a successful URL
            should_delete = (
                old_ds_source_url in successful_urls or 
                old_ds_from_sitemap in successful_urls
            )
            
            if should_delete:
                try:
                    delete_file(access_token, old_ds["id"])
                    print(f"Deleted old web data source: {old_ds['id']} (sourceUrl: {old_ds_source_url}, fromSitemap: {old_ds_from_sitemap})")
                except Exception as e:
                    print(f"Error deleting old web data source: {e}")
            else:
                print(f"Keeping old data source: {old_ds['id']} (sourceUrl: {old_ds_source_url}, fromSitemap: {old_ds_from_sitemap}) - parent URL rescraping failed")

        # Keep non-web data sources + web data sources not being rescanned
        non_web_data_sources = [
            ds for ds in existing_data_sources
            if ds.get("metadata", {}).get("type") != "assistant-web-content"
        ]
        
        # Keep web data sources that are NOT marked for deletion
        kept_web_ds = []
        old_ds_ids_to_delete = {ds["id"] for ds in old_ds_to_delete}
        
        for ds in existing_web_ds:
            if ds["id"] not in old_ds_ids_to_delete:
                kept_web_ds.append(ds)
                print(f"  Keeping existing web data source: {ds['id']} (not being rescanned)")
        
        # Combine: non-web + kept web + new scraped
        final_data_sources = non_web_data_sources + kept_web_ds + scraped_ds

        # Update assistant with new data sources and updated website URLs
        assistants_table = dynamodb.Table(os.environ["ASSISTANTS_DYNAMODB_TABLE"])
        assistants_table.update_item(
            Key={"id": assistant["id"]},
            UpdateExpression="SET dataSources = :ds, #data.websiteUrls = :urls",
            ExpressionAttributeNames={"#data": "data"},
            ExpressionAttributeValues={
                ":ds": final_data_sources,
                ":urls": updated_website_urls,
            },
        )

        return {
            "success": True,
            "message": f"Successfully rescanned {len(urls_to_rescan)} website URLs with {len(scraped_ds)} new data sources",
            "data": {
                "dataSourceKeys": [ds["id"] for ds in scraped_ds],
                "dataSources": scraped_ds
            },
        }

    except Exception as e:
        print(f"Error processing assistant websites: {e}")
        return {
            "success": False,
            "message": f"Failed to process assistant websites: {str(e)}",
        }


@validated(op="extract_sitemap_urls")
def extract_sitemap_urls(event, context, current_user, name, data):
    """
    Lambda function to extract URLs from a sitemap without scraping them.
    """
    try:
        sitemap_url = data["data"]["sitemap"]
        max_pages = data["data"].get("maxPages")
        if max_pages is not None:
            max_pages = int(max_pages)
        
        # Validate and sanitize the sitemap URL
        is_valid, sanitized_url, error_msg = sanitize_and_validate_url(sitemap_url)
        if not is_valid:
            return {
                "success": False,
                "message": f"Invalid sitemap URL: {error_msg}",
                "error": error_msg,
            }
        
        # Extract URLs from the sitemap
        exclusions = data["data"].get("exclusions")
        urls = extract_urls_from_sitemap(sanitized_url, max_pages, exclusions)
        
        return {
            "success": True,
            "message": f"Successfully extracted {len(urls)} URLs from sitemap",
            "data": {
                "urls": urls,
                "totalUrls": len(urls),
                "maxPages": max_pages
            }
        }
        
    except Exception as e:
        print(f"Error extracting URLs from sitemap: {e}")
    
    return {
            "success": False,
            "message": "No URLs found in sitemap or sitemap could not be processed",
            "data": {"urls": []}
            }


@validated(op="scrape_website")
def scrape_website(event, context, current_user, name, data):
    """
    Lambda function to scrape a website and create a data source.
    """
    url = data["data"]["url"]
    is_sitemap = data["data"].get("isSitemap", False)
    max_pages = data["data"].get("maxPages")
    if max_pages is not None:
        max_pages = int(max_pages)
    exclusions = data["data"].get("exclusions")

    return scrape_website_content(url, data["access_token"], is_sitemap, max_pages, exclusions)


def extract_name_from_url(url):
    """
    Extract a clean name from a URL by removing protocol, www, domain extensions, etc.
    
    Examples:
    - "https://www.example.com" -> "example"
    - "https://docs.github.com/en/actions" -> "docs-github"
    - "https://api.openai.com/v1/chat/completions" -> "api-openai"
    - "https://stackoverflow.com/questions/12345" -> "stackoverflow"
    - "https://subdomain.example.co.uk/path" -> "subdomain-example"
    """
    if not url:
        return "unknown"
    
    try:
        # Parse the URL
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Remove www. prefix
        if domain.startswith('www.'):
            domain = domain[4:]
        
        # Split domain into parts
        domain_parts = domain.split('.')
        
        # Remove common TLDs and keep meaningful parts
        # Common TLDs to remove
        tlds = {'com', 'org', 'net', 'edu', 'gov', 'co', 'uk', 'ca', 'au', 'de', 'fr', 'jp', 'cn', 'io', 'ai', 'ly', 'me', 'tv'}
        
        # Filter out TLDs and empty parts
        meaningful_parts = []
        for part in domain_parts:
            if part and part not in tlds:
                meaningful_parts.append(part)
        
        # If we have meaningful parts, join them with hyphens
        if meaningful_parts:
            name = '-'.join(meaningful_parts)
        else:
            # Fallback: use the first part of the domain
            name = domain_parts[0] if domain_parts else 'unknown'
        
        # Clean up the name: remove special characters, limit length
        name = re.sub(r'[^a-zA-Z0-9\-]', '', name)
        name = re.sub(r'-+', '-', name)  # Replace multiple hyphens with single
        name = name.strip('-')  # Remove leading/trailing hyphens
        
        # Limit length and ensure it's not empty
        if len(name) > 50:
            name = name[:50]
        
        return name if name else 'unknown'
        
    except Exception:
        return 'unknown'
    
