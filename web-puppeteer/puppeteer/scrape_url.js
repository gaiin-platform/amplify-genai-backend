const { scrape } = require('./execute_puppeteer');
const { getLogger } = require('../common/logging');

// Initialize logger for this module
const logger = getLogger('scrape_url');

async function scrapeUrl(url) {
    // Script to return the text content of the body
    const script = 'return document.body.innerText;';

    try {
        // Call the scrape function directly with the url and script
        const result = await scrape(url, script);
        return result;
    } catch (error) {
        logger.error('Error scraping URL:', error);
        throw error;
    }
}

// Export the scrapeUrl function to be used in other modules
module.exports = { scrapeUrl };
